from datetime import UTC, datetime
from pathlib import Path

from wow_auction_tracker.auction import AuctionListing, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.crafting import CraftOpportunityObservation
from wow_auction_tracker.features.opportunities import BuyOpportunityObservation
from wow_auction_tracker.features.player import AddonImportResult, PlayerAuctionOutcome, PlayerAuctionPost
from wow_auction_tracker.features.player import PlayerGoldSnapshot
from wow_auction_tracker.features.player import PlayerAuctionPurchase
from wow_auction_tracker.features.dashboard.server import (
    DASHBOARD_HTML,
    DashboardConfig,
    DashboardDataStore,
    _apply_dev_buy_opportunities,
    _crafting_quality_from_raw_json,
    _dashboard_timezone,
    _has_buy_opportunity,
    create_dashboard_app,
    format_file_size,
)
from wow_auction_tracker.storage import AuctionRepository, create_db_engine, init_db


def test_dashboard_overview_returns_latest_counts_and_items(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {
            "connected_realm_id": 3683,
            "items": [
                {"id": 210930, "name": "Bismuth", "market": "commodity"},
                {"id": 210931, "name": "Bismuth", "market": "commodity"},
                {"id": 210932, "name": "Bismuth", "market": "commodity"},
            ],
        }
    )
    first_run_id = repository.start_fetch_run(config)
    first_listings = [
        AuctionListing(
            auction_id=1,
            item_id=210930,
            market=Market.COMMODITY,
            quantity=10,
            unit_price=20000,
            buyout=None,
            bid=None,
            time_left="LONG",
            raw={"id": 1, "item": {"id": 210930}},
        )
    ]
    repository.complete_fetch_run(first_run_id, first_listings, summarize_listings(first_listings))
    second_run_id = repository.start_fetch_run(config)
    second_listings = [
        AuctionListing(
            auction_id=2,
            item_id=210930,
            market=Market.COMMODITY,
            quantity=5,
            unit_price=15000,
            buyout=None,
            bid=None,
            time_left="SHORT",
            raw={"id": 2, "item": {"id": 210930}},
        )
    ]
    repository.complete_fetch_run(second_run_id, second_listings, summarize_listings(second_listings))
    third_run_id = repository.start_fetch_run(config)
    third_listings = [
        AuctionListing(
            auction_id=3,
            item_id=210930,
            market=Market.COMMODITY,
            quantity=5,
            unit_price=15000,
            buyout=None,
            bid=None,
            time_left="SHORT",
            raw={"id": 3, "item": {"id": 210930}},
        )
    ]
    repository.complete_fetch_run(third_run_id, third_listings, summarize_listings(third_listings))

    payload = DashboardDataStore(f"sqlite:///{db_path}").overview()

    assert payload["counts"]["fetch_runs"] == 3
    assert payload["counts"]["auction_listings"] == 3
    assert payload["latest_run"]["id"] == third_run_id
    assert payload["snapshots"]["latest"]["item_count"] == 1
    assert payload["snapshots"]["latest"]["listing_count"] == 1
    assert payload["snapshots"]["latest"]["total_quantity"] == 5
    assert payload["snapshots"]["latest"]["lowest_unit_price"] == 15000
    assert payload["snapshots"]["sell_through"]["disappeared_quantity"] == 0
    assert payload["recent_runs"][0]["summary_count"] == 1
    assert payload["recent_runs"][0]["total_quantity"] == 5
    assert payload["items"][0]["name"] == "Bismuth"
    assert payload["items"][0]["min_unit_price"] == 15000
    assert payload["items"][0]["first_quartile_unit_price"] == 15000
    assert payload["items"][0]["third_quartile_unit_price"] == 15000
    assert payload["items"][0]["previous_min_unit_price"] == 20000
    assert payload["items"][0]["recommended_buy_price"] is None
    assert payload["items"][0]["recommended_sell_price"] is None
    assert payload["items"][0]["recommended_sell_price_source"] is None
    assert payload["items"][0]["crafting_quality"] == "1"
    assert payload["craft_opportunities"] == []
    assert payload["player_activity"]["summary"]["listing_count"] == 0


def test_dashboard_items_default_to_recommendation_score_order(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {
            "items": [
                {"id": 210930, "name": "Bismuth", "market": "commodity"},
                {"id": 210933, "name": "Aqirite", "market": "commodity"},
            ]
        }
    )

    for run_index, (bismuth_price, aqirite_price) in enumerate(
        [(10000, 1000), (10000, 1000), (5000, 1000)],
        start=1,
    ):
        run_id = repository.start_fetch_run(config)
        listings = [
            AuctionListing(
                auction_id=run_index * 10,
                item_id=210930,
                market=Market.COMMODITY,
                quantity=10,
                unit_price=bismuth_price,
                buyout=None,
                bid=None,
                time_left="LONG",
                raw={"id": run_index * 10, "item": {"id": 210930}},
            ),
            AuctionListing(
                auction_id=(run_index * 10) + 1,
                item_id=210933,
                market=Market.COMMODITY,
                quantity=10,
                unit_price=aqirite_price,
                buyout=None,
                bid=None,
                time_left="LONG",
                raw={"id": (run_index * 10) + 1, "item": {"id": 210933}},
            ),
        ]
        repository.complete_fetch_run(run_id, listings, summarize_listings(listings))

    payload = DashboardDataStore(f"sqlite:///{db_path}").overview()

    assert payload["items"][0]["item_id"] == 210930
    assert payload["items"][0]["recommendation_score"] > payload["items"][1]["recommendation_score"]


def test_dashboard_item_history_returns_rows_in_fetch_order(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(
            auction_id=1,
            item_id=210930,
            market=Market.COMMODITY,
            quantity=10,
            unit_price=20000,
            buyout=None,
            bid=None,
            time_left="LONG",
            raw={"id": 1, "item": {"id": 210930}},
        )
    ]
    repository.complete_fetch_run(run_id, listings, summarize_listings(listings))

    payload = DashboardDataStore(f"sqlite:///{db_path}").item_history(210930)

    assert payload["item"]["name"] == "Bismuth"
    assert [row["fetch_run_id"] for row in payload["history"]] == [run_id]
    assert payload["history"][0]["first_quartile_unit_price"] == 20000


def test_dashboard_flask_app_serves_html_and_json(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    app = create_dashboard_app(
        DashboardConfig(
            database_url=f"sqlite:///{db_path}",
            host="127.0.0.1",
            port=8000,
        )
    )

    client = app.test_client()
    html_response = client.get("/")
    player_html_response = client.get("/my-auctions")
    profit_html_response = client.get("/profit-loss")
    snapshots_html_response = client.get("/snapshots")
    overview_response = client.get("/api/overview")
    missing_history_response = client.get("/api/history")

    assert html_response.status_code == 200
    assert player_html_response.status_code == 200
    assert profit_html_response.status_code == 200
    assert snapshots_html_response.status_code == 200
    assert b"WoW Auction Tracker" in html_response.data
    assert b"My Auctions" in player_html_response.data
    assert b"Profit / Loss" in profit_html_response.data
    assert b"Snapshot Runs" in snapshots_html_response.data
    assert overview_response.status_code == 200
    assert overview_response.get_json()["counts"]["fetch_runs"] == 0
    assert missing_history_response.status_code == 400


def test_dashboard_can_import_addon_saved_variables(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    saved_variables = tmp_path / "WowAuctionTracker.lua"
    saved_variables.write_text(
        """
        WowAuctionTrackerDB = {
          ["version"] = 1,
          ["owned_snapshots"] = {
            {
              ["observed_at"] = 1710000000,
              ["snapshot_id"] = "Alice-1710000000",
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["auction_id"] = 42,
              ["item_id"] = 210930,
              ["quantity"] = 5,
              ["unit_price"] = 10000,
            },
          },
          ["mail_events"] = {
            {
              ["observed_at"] = 1710000300,
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["mail_index"] = 1,
              ["outcome"] = "sold",
              ["money"] = 45000,
              ["subject"] = "Auction successful: Bismuth (5)",
            },
          },
          ["purchase_events"] = {
            {
              ["observed_at"] = 1710000400,
              ["event_type"] = "commodity_purchase_succeeded",
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["market"] = "commodity",
              ["item_id"] = 210930,
              ["quantity"] = 5,
              ["unit_price"] = 9000,
              ["total_price"] = 45000,
            },
          },
        }
        """,
        encoding="utf-8",
    )
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    app = create_dashboard_app(
        DashboardConfig(
            database_url=f"sqlite:///{db_path}",
            host="127.0.0.1",
            port=8000,
            addon_saved_variables_path=saved_variables,
        )
    )

    client = app.test_client()
    import_response = client.post("/api/import-addon")
    overview_response = client.get("/api/overview")
    activity = overview_response.get_json()["player_activity"]

    assert import_response.status_code == 200
    assert import_response.get_json()["owned_snapshot_count"] == 1
    assert import_response.get_json()["purchase_event_count"] == 1
    assert activity["summary"]["listing_count"] == 1
    assert activity["summary"]["sold_count"] == 1
    assert activity["summary"]["purchase_count"] == 1
    assert activity["outcomes"][0]["money"] == 45000
    assert activity["outcomes"][0]["name"] == "Bismuth"
    assert activity["outcomes"][0]["item_count"] == 5
    assert activity["purchases"][0]["total_price"] == 45000


def test_dashboard_overview_returns_player_activity(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 219946, "name": "Storm Dust", "market": "commodity"}]}
    )
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(
            auction_id=1,
            item_id=219946,
            market=Market.COMMODITY,
            quantity=10,
            unit_price=10000,
            buyout=None,
            bid=None,
            time_left="LONG",
            raw={"id": 1, "item": {"id": 219946}},
        )
    ]
    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        buy_opportunity_observations=[
            BuyOpportunityObservation(
                item_id=219946,
                market="commodity",
                auction_id=1,
                unit_price=10000,
                quantity=10,
                buy_target_unit_price=11000,
                sell_target_unit_price=15000,
                potential_profit=50000,
                available_quantity_at_or_below_buy_target=10,
                recommendation_score=60,
                recommendation_confidence=75,
                listing_status="new",
            )
        ],
        craft_opportunity_observations=[
            CraftOpportunityObservation(
                recipe_id="enchant-dust",
                recipe_name="Make Storm Dust",
                output_item_id=219946,
                output_market="commodity",
                output_quantity=1,
                craft_cost=7000,
                craft_cost_unit_price=7000,
                output_min_unit_price=10000,
                sell_target_unit_price=12000,
                auction_deposit_unit_price=100,
                ah_savings=3000,
                expected_profit=4900,
                max_craft_quantity=3,
                confidence=75,
                reasons=["profitable craft"],
            )
        ],
    )
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "WowAuctionTracker.lua",
            addon_version=1,
            posts=[
                PlayerAuctionPost(
                    observed_at=datetime(2026, 7, 1, 4, 55, tzinfo=UTC),
                    snapshot_id="snapshot-1",
                    reason="auction_created",
                    character="Arces",
                    realm="Dalaran",
                    auction_id=100,
                    item_id=219946,
                    quantity=1000,
                    unit_price=86,
                    buyout=86000,
                    bid_amount=None,
                    time_left_seconds=172800,
                    status="0",
                    raw={"auction_id": 100},
                )
            ],
            outcomes=[
                PlayerAuctionOutcome(
                    observed_at=datetime(2026, 7, 1, 5, 30, tzinfo=UTC),
                    character="Arces",
                    realm="Dalaran",
                    mail_index=1,
                    item_id=219946,
                    item_name="Storm Dust",
                    item_count=1000,
                    outcome="sold",
                    money=86000,
                    raw={"outcome": "sold"},
                )
            ],
            purchases=[
                PlayerAuctionPurchase(
                    observed_at=datetime(2026, 7, 1, 5, 20, tzinfo=UTC),
                    event_type="commodity_purchase_succeeded",
                    character="Arces",
                    realm="Dalaran",
                    market="commodity",
                    auction_id=None,
                    item_id=219946,
                    quantity=100,
                    unit_price=80,
                    total_price=8000,
                    raw={"event_type": "commodity_purchase_succeeded"},
                )
            ],
            gold_snapshots=[
                PlayerGoldSnapshot(
                    observed_at=datetime(2026, 7, 1, 4, 50, tzinfo=UTC),
                    reason="player_login",
                    character="Arces",
                    realm="Dalaran",
                    money=100000,
                    raw={"reason": "player_login"},
                ),
                PlayerGoldSnapshot(
                    observed_at=datetime(2026, 7, 1, 5, 40, tzinfo=UTC),
                    reason="player_money",
                    character="Arces",
                    realm="Dalaran",
                    money=178000,
                    raw={"reason": "player_money"},
                ),
            ],
        )
    )

    payload = DashboardDataStore(f"sqlite:///{db_path}").overview()
    activity = payload["player_activity"]

    assert activity["latest_import"]["owned_snapshot_count"] == 1
    assert activity["summary"]["listing_count"] == 1
    assert activity["summary"]["sold_count"] == 1
    assert activity["summary"]["purchase_count"] == 1
    assert activity["listings"][0]["name"] == "Storm Dust"
    assert activity["listings"][0]["unit_price"] == 86
    assert activity["purchases"][0]["name"] == "Storm Dust"
    assert activity["purchases"][0]["total_price"] == 8000
    assert activity["outcomes"][0]["outcome"] == "sold"
    assert activity["profit_loss"]["summary"]["revenue"] == 86000
    assert activity["profit_loss"]["summary"]["cost"] == 8000
    assert activity["profit_loss"]["summary"]["net_profit"] == 78000
    assert activity["profit_loss"]["items"][0]["name"] == "Storm Dust"
    assert activity["profit_loss"]["items"][0]["net_profit"] == 78000
    assert activity["profit_loss"]["items"][0]["cost_basis_status"] == "complete"
    assert activity["gold"]["first"]["money"] == 100000
    assert activity["gold"]["latest"]["money"] == 178000
    assert activity["gold"]["delta"] == 78000
    assert activity["buy_opportunities"][0]["potential_profit"] == 50000
    assert payload["craft_opportunities"][0]["recipe_id"] == "enchant-dust"
    assert payload["craft_opportunities"][0]["output_name"] == "Storm Dust"
    assert payload["craft_opportunities"][0]["reasons"] == ["profitable craft"]


def test_dashboard_infers_outcome_item_from_owned_quantity_drop(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {
            "items": [
                {"id": 219946, "name": "Storm Dust", "market": "commodity"},
                {"id": 219947, "name": "Storm Dust", "market": "commodity"},
            ]
        }
    )
    run_id = repository.start_fetch_run(config)
    repository.complete_fetch_run(run_id, [], [])
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "WowAuctionTracker.lua",
            addon_version=1,
            posts=[
                PlayerAuctionPost(
                    observed_at=datetime(2026, 7, 2, 18, 38, tzinfo=UTC),
                    snapshot_id="snapshot-1",
                    reason="owned_auctions_updated",
                    character="Arces",
                    realm="Dalaran",
                    auction_id=100,
                    item_id=219946,
                    quantity=5647,
                    unit_price=13,
                    buyout=74400,
                    bid_amount=None,
                    time_left_seconds=43184,
                    status="0",
                    raw={"auction_id": 100},
                ),
                PlayerAuctionPost(
                    observed_at=datetime(2026, 7, 2, 22, 49, tzinfo=UTC),
                    snapshot_id="snapshot-2",
                    reason="owned_auctions_updated",
                    character="Arces",
                    realm="Dalaran",
                    auction_id=100,
                    item_id=219946,
                    quantity=3225,
                    unit_price=23,
                    buyout=74400,
                    bid_amount=None,
                    time_left_seconds=28137,
                    status="0",
                    raw={"auction_id": 100},
                ),
            ],
            outcomes=[
                PlayerAuctionOutcome(
                    observed_at=datetime(2026, 7, 2, 22, 49, tzinfo=UTC),
                    character="Arces",
                    realm="Dalaran",
                    mail_index=1,
                    item_id=None,
                    item_name=None,
                    item_count=None,
                    outcome="sold",
                    money=173911710,
                    raw={"subject": "Auction successful: Storm Dust (2422)"},
                )
            ],
            purchases=[],
        )
    )

    activity = DashboardDataStore(f"sqlite:///{db_path}").overview()["player_activity"]

    assert activity["outcomes"][0]["name"] == "Storm Dust"
    assert activity["outcomes"][0]["item_count"] == 2422
    assert activity["outcomes"][0]["item_id"] == 219946


def test_dashboard_profit_loss_does_not_count_sales_without_cost_as_profit(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    repository.start_fetch_run(
        TrackerConfig.model_validate(
            {"items": [{"id": 219946, "name": "Storm Dust", "market": "commodity"}]}
        )
    )
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "WowAuctionTracker.lua",
            addon_version=1,
            posts=[],
            outcomes=[
                PlayerAuctionOutcome(
                    observed_at=datetime(2026, 7, 1, 5, 30, tzinfo=UTC),
                    character="Arces",
                    realm="Dalaran",
                    mail_index=1,
                    item_id=219946,
                    item_name="Storm Dust",
                    item_count=1000,
                    outcome="sold",
                    money=86000,
                    raw={"outcome": "sold"},
                )
            ],
            purchases=[],
        )
    )

    activity = DashboardDataStore(f"sqlite:///{db_path}").overview()["player_activity"]

    assert activity["profit_loss"]["summary"]["revenue"] == 86000
    assert activity["profit_loss"]["summary"]["unmatched_revenue"] == 86000
    assert activity["profit_loss"]["summary"]["net_profit"] == 0
    assert activity["profit_loss"]["summary"]["margin_bps"] is None
    assert activity["profit_loss"]["items"][0]["net_profit"] is None
    assert activity["profit_loss"]["items"][0]["cost_basis_status"] == "missing_cost"


def test_dashboard_profit_loss_matches_name_only_sales_to_duplicate_item_names(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    run_id = repository.start_fetch_run(
        TrackerConfig.model_validate(
            {
                "items": [
                    {"id": 210933, "name": "Aqirite", "market": "commodity"},
                    {"id": 210934, "name": "Aqirite", "market": "commodity"},
                    {"id": 210935, "name": "Aqirite", "market": "commodity"},
                ]
            }
        )
    )
    repository.complete_fetch_run(run_id, [], [])
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "WowAuctionTracker.lua",
            addon_version=1,
            posts=[],
            outcomes=[
                PlayerAuctionOutcome(
                    observed_at=datetime(2026, 7, 1, 5, 30, tzinfo=UTC),
                    character="Arces",
                    realm="Dalaran",
                    mail_index=1,
                    item_id=None,
                    item_name="Aqirite",
                    item_count=100,
                    outcome="sold",
                    money=12000,
                    raw={"outcome": "sold"},
                )
            ],
            purchases=[
                PlayerAuctionPurchase(
                    observed_at=datetime(2026, 7, 1, 5, 20, tzinfo=UTC),
                    event_type="commodity_purchase_succeeded",
                    character="Arces",
                    realm="Dalaran",
                    market="commodity",
                    auction_id=None,
                    item_id=210934,
                    quantity=100,
                    unit_price=80,
                    total_price=8000,
                    raw={"event_type": "commodity_purchase_succeeded"},
                )
            ],
        )
    )

    activity = DashboardDataStore(f"sqlite:///{db_path}").overview()["player_activity"]

    assert activity["profit_loss"]["summary"]["revenue"] == 12000
    assert activity["profit_loss"]["summary"]["cost"] == 8000
    assert activity["profit_loss"]["summary"]["net_profit"] == 4000
    assert activity["profit_loss"]["items"][0]["item_id"] is None
    assert activity["profit_loss"]["items"][0]["name"] == "Aqirite"
    assert activity["profit_loss"]["items"][0]["cost_basis_status"] == "complete"


def test_dashboard_profit_loss_counts_open_purchases_as_negative_profit(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    repository.start_fetch_run(
        TrackerConfig.model_validate(
            {"items": [{"id": 219946, "name": "Storm Dust", "market": "commodity"}]}
        )
    )
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "WowAuctionTracker.lua",
            addon_version=1,
            posts=[],
            outcomes=[],
            purchases=[
                PlayerAuctionPurchase(
                    observed_at=datetime(2026, 7, 1, 5, 20, tzinfo=UTC),
                    event_type="commodity_purchase_succeeded",
                    character="Arces",
                    realm="Dalaran",
                    market="commodity",
                    auction_id=None,
                    item_id=219946,
                    quantity=100,
                    unit_price=80,
                    total_price=8000,
                    raw={"event_type": "commodity_purchase_succeeded"},
                )
            ],
        )
    )

    activity = DashboardDataStore(f"sqlite:///{db_path}").overview()["player_activity"]

    assert activity["profit_loss"]["summary"]["revenue"] == 0
    assert activity["profit_loss"]["summary"]["cost"] == 8000
    assert activity["profit_loss"]["summary"]["open_purchase_cost"] == 8000
    assert activity["profit_loss"]["summary"]["net_profit"] == -8000
    assert activity["profit_loss"]["summary"]["margin_bps"] is None
    assert activity["profit_loss"]["items"][0]["net_profit"] == -8000
    assert activity["profit_loss"]["items"][0]["cost_basis_status"] == "open_purchase"


def test_dashboard_profit_loss_dedupes_reimported_sale_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    repository.start_fetch_run(
        TrackerConfig.model_validate(
            {"items": [{"id": 219946, "name": "Storm Dust", "market": "commodity"}]}
        )
    )
    sale = PlayerAuctionOutcome(
        observed_at=datetime(2026, 7, 1, 5, 30, tzinfo=UTC),
        character="Arces",
        realm="Dalaran",
        mail_index=1,
        item_id=219946,
        item_name="Storm Dust",
        item_count=1000,
        outcome="sold",
        money=86000,
        raw={"outcome": "sold"},
    )
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "first.lua",
            addon_version=1,
            posts=[],
            outcomes=[sale],
            purchases=[],
        )
    )
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "second.lua",
            addon_version=1,
            posts=[],
            outcomes=[
                PlayerAuctionOutcome(
                    observed_at=sale.observed_at,
                    character=sale.character,
                    realm=sale.realm,
                    mail_index=2,
                    item_id=sale.item_id,
                    item_name=sale.item_name,
                    item_count=sale.item_count,
                    outcome=sale.outcome,
                    money=sale.money,
                    raw={"outcome": "sold", "scan_id": "later"},
                )
            ],
            purchases=[],
        )
    )

    activity = DashboardDataStore(f"sqlite:///{db_path}").overview()["player_activity"]

    assert activity["profit_loss"]["summary"]["revenue"] == 86000
    assert activity["profit_loss"]["summary"]["sale_count"] == 1


def test_dashboard_profit_loss_excludes_untracked_addon_items(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    repository.start_fetch_run(
        TrackerConfig.model_validate(
            {"items": [{"id": 219946, "name": "Storm Dust", "market": "commodity"}]}
        )
    )
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "WowAuctionTracker.lua",
            addon_version=1,
            posts=[],
            outcomes=[
                PlayerAuctionOutcome(
                    observed_at=datetime(2026, 7, 1, 5, 30, tzinfo=UTC),
                    character="Arces",
                    realm="Dalaran",
                    mail_index=1,
                    item_id=219946,
                    item_name="Storm Dust",
                    item_count=1000,
                    outcome="sold",
                    money=86000,
                    raw={"outcome": "sold"},
                ),
                PlayerAuctionOutcome(
                    observed_at=datetime(2026, 7, 1, 5, 31, tzinfo=UTC),
                    character="Arces",
                    realm="Dalaran",
                    mail_index=2,
                    item_id=210810,
                    item_name="Arathor's Spear",
                    item_count=10,
                    outcome="sold",
                    money=50000,
                    raw={"outcome": "sold"},
                ),
            ],
            purchases=[
                PlayerAuctionPurchase(
                    observed_at=datetime(2026, 7, 1, 5, 20, tzinfo=UTC),
                    event_type="commodity_purchase_succeeded",
                    character="Arces",
                    realm="Dalaran",
                    market="commodity",
                    auction_id=None,
                    item_id=219946,
                    quantity=100,
                    unit_price=80,
                    total_price=8000,
                    raw={"event_type": "commodity_purchase_succeeded"},
                ),
                PlayerAuctionPurchase(
                    observed_at=datetime(2026, 7, 1, 5, 21, tzinfo=UTC),
                    event_type="commodity_purchase_succeeded",
                    character="Arces",
                    realm="Dalaran",
                    market="commodity",
                    auction_id=None,
                    item_id=210810,
                    quantity=10,
                    unit_price=1000,
                    total_price=10000,
                    raw={"event_type": "commodity_purchase_succeeded"},
                ),
            ],
        )
    )

    activity = DashboardDataStore(f"sqlite:///{db_path}").overview()["player_activity"]

    assert activity["profit_loss"]["summary"]["revenue"] == 86000
    assert activity["profit_loss"]["summary"]["cost"] == 8000
    assert activity["profit_loss"]["summary"]["net_profit"] == 78000
    assert [item["item_id"] for item in activity["profit_loss"]["items"]] == [219946]


def test_dashboard_profit_loss_uses_current_config_ids_over_stale_tracked_items(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    repository.start_fetch_run(
        TrackerConfig.model_validate(
            {
                "items": [
                    {"id": 219946, "name": "Storm Dust", "market": "commodity"},
                    {"id": 210810, "name": "Arathor's Spear", "market": "commodity"},
                ]
            }
        )
    )
    repository.import_addon_data(
        AddonImportResult(
            source_path=tmp_path / "WowAuctionTracker.lua",
            addon_version=1,
            posts=[],
            outcomes=[],
            purchases=[
                PlayerAuctionPurchase(
                    observed_at=datetime(2026, 7, 1, 5, 20, tzinfo=UTC),
                    event_type="commodity_purchase_succeeded",
                    character="Arces",
                    realm="Dalaran",
                    market="commodity",
                    auction_id=None,
                    item_id=219946,
                    quantity=100,
                    unit_price=80,
                    total_price=8000,
                    raw={"event_type": "commodity_purchase_succeeded"},
                ),
                PlayerAuctionPurchase(
                    observed_at=datetime(2026, 7, 1, 5, 21, tzinfo=UTC),
                    event_type="commodity_purchase_succeeded",
                    character="Arces",
                    realm="Dalaran",
                    market="commodity",
                    auction_id=None,
                    item_id=210810,
                    quantity=10,
                    unit_price=1000,
                    total_price=10000,
                    raw={"event_type": "commodity_purchase_succeeded"},
                ),
            ],
        )
    )

    activity = DashboardDataStore(
        f"sqlite:///{db_path}",
        tracked_item_ids=frozenset({219946}),
    ).overview()["player_activity"]

    assert activity["profit_loss"]["summary"]["cost"] == 8000
    assert activity["profit_loss"]["summary"]["net_profit"] == -8000
    assert [item["item_id"] for item in activity["profit_loss"]["items"]] == [219946]


def test_format_file_size() -> None:
    assert format_file_size(512) == "512 B"
    assert format_file_size(2048) == "2.0 KiB"


def test_dashboard_table_headers_have_tooltips() -> None:
    assert 'title="Crafting quality from auction listing modifiers' in DASHBOARD_HTML
    assert 'title="Recommended maximum per-unit buy price' in DASHBOARD_HTML
    assert 'title="Recommended per-unit sell price from inferred sold listings' in DASHBOARD_HTML
    assert 'title="Estimated 48-hour auction deposit per unit' in DASHBOARD_HTML
    assert 'title="Potential per-unit profit after subtracting estimated 48-hour auction deposit' in DASHBOARD_HTML
    assert "Q1 / Unit" not in DASHBOARD_HTML
    assert "Median / Unit" not in DASHBOARD_HTML
    assert "Q3 / Unit" not in DASHBOARD_HTML
    assert "Min / Unit" in DASHBOARD_HTML
    assert 'title="Estimated demand signal from recent disappeared listings' in DASHBOARD_HTML
    assert 'title="Change from the latest minimum price' in DASHBOARD_HTML
    assert 'quality-badge' in DASHBOARD_HTML
    assert "function profit" in DASHBOARD_HTML
    assert "buy-opportunity" in DASHBOARD_HTML
    assert "My Listings" in DASHBOARD_HTML
    assert "My Buys" in DASHBOARD_HTML
    assert "Buy Signals" in DASHBOARD_HTML
    assert "Craft Signals" in DASHBOARD_HTML
    assert 'id="craft-signals-table"' in DASHBOARD_HTML
    assert "Auction Outcomes" in DASHBOARD_HTML
    assert '<nav class="top-tabs">' in DASHBOARD_HTML
    assert 'role="tablist"' in DASHBOARD_HTML
    assert 'data-tab="stats"' in DASHBOARD_HTML
    assert 'id="stats-panel"' in DASHBOARD_HTML
    assert "Fetch Stats" in DASHBOARD_HTML
    assert 'data-tab="player"' in DASHBOARD_HTML
    assert 'id="player-panel"' in DASHBOARD_HTML
    assert 'data-tab="profit"' in DASHBOARD_HTML
    assert 'id="profit-panel"' in DASHBOARD_HTML
    assert 'id="profit-loss-table"' in DASHBOARD_HTML
    assert "Profit / Loss" in DASHBOARD_HTML
    assert "Wallet Change" in DASHBOARD_HTML
    assert "Current Gold" in DASHBOARD_HTML
    assert "Known P/L" in DASHBOARD_HTML
    assert "costBasisLabel" in DASHBOARD_HTML
    assert "setActiveTab" in DASHBOARD_HTML
    assert "tabFromPath" in DASHBOARD_HTML
    assert "navigateTab" in DASHBOARD_HTML
    assert "renderPlayerActivity" in DASHBOARD_HTML
    assert "renderProfitLoss" in DASHBOARD_HTML
    assert "/profit-loss" in DASHBOARD_HTML
    assert "source-badge" in DASHBOARD_HTML
    assert "sellSourceBadge" in DASHBOARD_HTML
    assert "drawLine(ctx, points, x, y, 'min_unit_price'" in DASHBOARD_HTML
    assert "drawLine(ctx, points, x, y, 'median_unit_price'" not in DASHBOARD_HTML


def test_dashboard_flags_buy_opportunities() -> None:
    assert _has_buy_opportunity(90, 100) is True
    assert _has_buy_opportunity(100, 100) is False
    assert _has_buy_opportunity(None, 100) is False
    assert _has_buy_opportunity(90, None) is False


def test_dashboard_dev_mode_marks_display_only_buy_opportunities(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(
            auction_id=1,
            item_id=210930,
            market=Market.COMMODITY,
            quantity=10,
            unit_price=10000,
            buyout=None,
            bid=None,
            time_left="LONG",
            raw={"id": 1, "item": {"id": 210930}},
        )
    ]
    repository.complete_fetch_run(run_id, listings, summarize_listings(listings))

    payload = DashboardDataStore(f"sqlite:///{db_path}").overview(dev_mode=True)

    assert payload["dev_mode"] is True
    assert payload["items"][0]["has_buy_opportunity"] is True
    assert payload["items"][0]["dev_buy_opportunity"] is True
    assert payload["items"][0]["recommended_buy_price"] > payload["items"][0]["min_unit_price"]
    assert payload["items"][0]["recommended_sell_price"] is None


def test_dashboard_dev_opportunities_are_limited() -> None:
    items = [
        {"item_id": index, "min_unit_price": 10000, "recommended_buy_price": 8000, "recommended_sell_price": None}
        for index in range(5)
    ]

    _apply_dev_buy_opportunities(items, limit=2)

    assert sum(1 for item in items if item.get("dev_buy_opportunity")) == 2
    assert all(item["recommended_sell_price"] is None for item in items)


def test_dashboard_extracts_crafting_quality_from_listing_payload() -> None:
    assert _crafting_quality_from_raw_json('{"item": {"id": 1}}') is None
    assert _crafting_quality_from_raw_json('{"item": {"id": 1, "crafting_quality": 3}}') == "3"
    assert (
        _crafting_quality_from_raw_json(
            '{"item": {"id": 1, "modifiers": [{"type": "CRAFTING_QUALITY", "value": 5}]}}'
        )
        == "5"
    )


def test_dashboard_has_timezone_switch_defaulting_to_eastern() -> None:
    assert 'id="timezone"' in DASHBOARD_HTML
    assert 'id="auto-refresh"' in DASHBOARD_HTML
    assert "AUTO_REFRESH_MS = 30000" in DASHBOARD_HTML
    assert "startAutoRefresh()" in DASHBOARD_HTML
    assert 'id="dev-mode-status"' in DASHBOARD_HTML
    assert "dev-marker" in DASHBOARD_HTML
    assert '<option value="America/New_York" selected>Eastern</option>' in DASHBOARD_HTML
    assert '<option value="UTC">UTC</option>' in DASHBOARD_HTML
    assert _dashboard_timezone("America/Los_Angeles") == "America/Los_Angeles"
    assert _dashboard_timezone("Bad/Zone") == "America/New_York"
