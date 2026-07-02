from datetime import UTC, datetime
from pathlib import Path

from wow_auction_tracker.auction import AuctionListing, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.opportunities import BuyOpportunityObservation
from wow_auction_tracker.features.player import AddonImportResult, PlayerAuctionOutcome, PlayerAuctionPost
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
    assert payload["items"][0]["name"] == "Bismuth"
    assert payload["items"][0]["min_unit_price"] == 15000
    assert payload["items"][0]["first_quartile_unit_price"] == 15000
    assert payload["items"][0]["third_quartile_unit_price"] == 15000
    assert payload["items"][0]["previous_min_unit_price"] == 20000
    assert payload["items"][0]["recommended_buy_price"] is None
    assert payload["items"][0]["recommended_sell_price"] is None
    assert payload["items"][0]["recommended_sell_price_source"] is None
    assert payload["items"][0]["crafting_quality"] == "1"
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
    overview_response = client.get("/api/overview")
    missing_history_response = client.get("/api/history")

    assert html_response.status_code == 200
    assert b"WoW Auction Tracker" in html_response.data
    assert overview_response.status_code == 200
    assert overview_response.get_json()["counts"]["fetch_runs"] == 0
    assert missing_history_response.status_code == 400


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
        )
    )

    payload = DashboardDataStore(f"sqlite:///{db_path}").overview()
    activity = payload["player_activity"]

    assert activity["latest_import"]["owned_snapshot_count"] == 1
    assert activity["summary"]["listing_count"] == 1
    assert activity["summary"]["sold_count"] == 1
    assert activity["listings"][0]["name"] == "Storm Dust"
    assert activity["listings"][0]["unit_price"] == 86
    assert activity["outcomes"][0]["outcome"] == "sold"
    assert activity["buy_opportunities"][0]["potential_profit"] == 50000


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
    assert "Buy Signals" in DASHBOARD_HTML
    assert "Auction Outcomes" in DASHBOARD_HTML
    assert 'role="tablist"' in DASHBOARD_HTML
    assert 'data-tab="player"' in DASHBOARD_HTML
    assert 'id="player-panel"' in DASHBOARD_HTML
    assert "setActiveTab" in DASHBOARD_HTML
    assert "renderPlayerActivity" in DASHBOARD_HTML
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
