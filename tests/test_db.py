from sqlalchemy import select
from sqlalchemy.orm import Session

from wow_auction_tracker.auction import calculate_item_history_metrics, filter_auctions, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.lifecycle import build_listing_observations
from wow_auction_tracker.features.metadata import ItemMetadata
from wow_auction_tracker.features.crafting import CraftOpportunityObservation
from wow_auction_tracker.features.opportunities import BuyOpportunityObservation
from wow_auction_tracker.features.sellthrough import build_sell_through_metrics
from wow_auction_tracker.storage import (
    AuctionListingRecord,
    AuctionRepository,
    BuyOpportunityObservationRecord,
    CraftOpportunityObservationRecord,
    FetchRun,
    ItemAnomalyRecord,
    ItemDailyMetricRecord,
    ItemHistoryMetricRecord,
    ItemMetadataRecord,
    ItemSummaryRecord,
    ListingObservationRecord,
    AddonImportRecord,
    PlayerAuctionMatchRecord,
    PlayerAuctionOutcomeRecord,
    PlayerAuctionPostRecord,
    PlayerAuctionPurchaseRecord,
    SellThroughMetricRecord,
    TrackedItemRecord,
    create_db_engine,
    init_db,
)


def test_repository_stores_fetch_run_listings_and_summaries() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {
            "connected_realm_id": 3678,
            "items": [{"id": 19019, "name": "Thunderfury", "market": "realm"}],
        }
    )

    run_id = repository.start_fetch_run(config)
    listings = filter_auctions(
        {
            "auctions": [
                {
                    "id": 500,
                    "item": {"id": 19019},
                    "quantity": 1,
                    "buyout": 9000000,
                    "bid": 8000000,
                    "time_left": "VERY_LONG",
                }
            ]
        },
        {19019},
        Market.REALM,
    )
    summaries = summarize_listings(listings)

    repository.complete_fetch_run(run_id, listings, summaries)

    with engine.connect() as connection:
        tracked_items = connection.execute(select(TrackedItemRecord)).all()
        stored_listings = connection.execute(select(AuctionListingRecord)).all()
        stored_summaries = connection.execute(select(ItemSummaryRecord)).all()

    assert len(tracked_items) == 1
    assert len(stored_listings) == 1
    assert len(stored_summaries) == 1

    loaded_summaries = repository.list_summaries(run_id)
    assert loaded_summaries[0].item_id == 19019
    assert loaded_summaries[0].min_unit_price == 9000000
    assert loaded_summaries[0].first_quartile_unit_price == 9000000
    assert loaded_summaries[0].third_quartile_unit_price == 9000000


def test_repository_stores_item_history_metrics() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )
    run_id = repository.start_fetch_run(config)
    listings = filter_auctions(
        {
            "auctions": [
                {"id": 1, "item": {"id": 210930}, "quantity": 2, "unit_price": 100},
                {"id": 2, "item": {"id": 210930}, "quantity": 8, "unit_price": 300},
            ]
        },
        {210930},
        Market.COMMODITY,
    )

    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        calculate_item_history_metrics(listings),
    )

    with Session(engine) as session:
        stored_metrics = session.scalars(select(ItemHistoryMetricRecord)).all()

    assert len(stored_metrics) == 1
    assert stored_metrics[0].first_quartile_unit_price == 100
    assert stored_metrics[0].third_quartile_unit_price == 300
    assert stored_metrics[0].weighted_average_unit_price == 260


def test_repository_upserts_item_metadata() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)

    repository.upsert_item_metadata(
        [
            ItemMetadata(
                item_id=210930,
                name="Bismuth",
                quality="COMMON",
                item_class="Tradeskill",
                item_subclass="Metal & Stone",
                inventory_type="NON_EQUIP",
                item_level=70,
                required_level=1,
                purchase_price=2500,
                sell_price=500,
                max_count=0,
                is_equippable=False,
                is_stackable=True,
                icon_url="https://example.test/icon.jpg",
            )
        ]
    )

    with Session(engine) as session:
        metadata = session.get(ItemMetadataRecord, 210930)

    assert metadata is not None
    assert metadata.name == "Bismuth"
    assert metadata.item_class == "Tradeskill"
    assert metadata.is_stackable is True
    assert repository.missing_metadata_item_ids({210930, 210931}) == {210931}


def test_repository_stores_listing_observations() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )
    run_id = repository.start_fetch_run(config)
    listings = filter_auctions(
        {"auctions": [{"id": 1, "item": {"id": 210930}, "quantity": 2, "unit_price": 100, "time_left": "LONG"}]},
        {210930},
        Market.COMMODITY,
    )

    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        calculate_item_history_metrics(listings),
        build_listing_observations(listings, []),
    )

    with Session(engine) as session:
        observations = session.scalars(select(ListingObservationRecord)).all()

    assert len(observations) == 1
    assert observations[0].status == "new"
    assert observations[0].inferred_outcome is None


def test_repository_stores_sell_through_metrics() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )
    first_run_id = repository.start_fetch_run(config)
    first_listings = filter_auctions(
        {"auctions": [{"id": 1, "item": {"id": 210930}, "quantity": 2, "unit_price": 100, "time_left": "LONG"}]},
        {210930},
        Market.COMMODITY,
    )
    repository.complete_fetch_run(
        first_run_id,
        first_listings,
        summarize_listings(first_listings),
        calculate_item_history_metrics(first_listings),
        build_listing_observations(first_listings, []),
    )
    second_run_id = repository.start_fetch_run(config)
    observations = build_listing_observations(
        [],
        repository.list_listing_snapshots(first_run_id),
        elapsed_seconds=60,
    )

    repository.complete_fetch_run(
        second_run_id,
        [],
        [],
        (),
        observations,
        build_sell_through_metrics(observations),
    )

    with Session(engine) as session:
        metrics = session.scalars(select(SellThroughMetricRecord)).all()

    assert len(metrics) == 1
    assert metrics[0].probable_sold_listing_count == 1
    assert metrics[0].probable_sold_quantity == 2
    assert metrics[0].probable_sold_average_unit_price == 100
    assert metrics[0].disappeared_quantity == 2
    assert metrics[0].sell_through_ratio_bps == 10000


def test_repository_builds_daily_rollups_and_item_anomalies() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )

    for price in (100, 105, 95):
        run_id = repository.start_fetch_run(config)
        listings = filter_auctions(
            {"auctions": [{"id": run_id, "item": {"id": 210930}, "quantity": 20, "unit_price": price}]},
            {210930},
            Market.COMMODITY,
        )
        repository.complete_fetch_run(
            run_id,
            listings,
            summarize_listings(listings),
            calculate_item_history_metrics(listings),
        )

    run_id = repository.start_fetch_run(config)
    listings = filter_auctions(
        {"auctions": [{"id": 99, "item": {"id": 210930}, "quantity": 3, "unit_price": 200}]},
        {210930},
        Market.COMMODITY,
    )
    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        calculate_item_history_metrics(listings),
    )

    with Session(engine) as session:
        daily_metrics = session.scalars(select(ItemDailyMetricRecord)).all()
        anomalies = session.scalars(select(ItemAnomalyRecord).order_by(ItemAnomalyRecord.anomaly_type)).all()

    assert len(daily_metrics) == 1
    assert daily_metrics[0].snapshot_count == 4
    assert daily_metrics[0].low_unit_price == 95
    assert daily_metrics[0].last_fetch_run_id == run_id
    assert {anomaly.anomaly_type for anomaly in anomalies} == {"inventory_drought", "price_spike"}
    assert all(anomaly.fetch_run_id == run_id for anomaly in anomalies)


def test_repository_stores_buy_opportunity_observations() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )
    run_id = repository.start_fetch_run(config)

    repository.complete_fetch_run(
        run_id,
        [],
        [],
        (),
        (),
        (),
        [
            BuyOpportunityObservation(
                item_id=210930,
                market="commodity",
                auction_id=42,
                unit_price=7900,
                quantity=5,
                buy_target_unit_price=8000,
                sell_target_unit_price=10000,
                potential_profit=10500,
                available_quantity_at_or_below_buy_target=8,
                recommendation_score=50,
                recommendation_confidence=75,
                listing_status="new",
            )
        ],
    )

    with Session(engine) as session:
        opportunities = session.scalars(select(BuyOpportunityObservationRecord)).all()

    assert len(opportunities) == 1
    assert opportunities[0].auction_id == 42
    assert opportunities[0].observed_at is not None
    assert opportunities[0].unit_price == 7900
    assert opportunities[0].buy_target_unit_price == 8000
    assert opportunities[0].potential_profit == 10500


def test_repository_stores_craft_opportunity_observations() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {
            "items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}],
            "recipes": [
                {
                    "id": "refine-bismuth",
                    "output": {"item_id": 210931, "name": "Bismuth", "market": "commodity", "quantity": 1},
                    "ingredients": [{"item_id": 210930, "name": "Bismuth", "market": "commodity", "quantity": 5}],
                }
            ],
        }
    )

    run_id = repository.start_fetch_run(config)
    repository.complete_fetch_run(
        run_id,
        [],
        [],
        craft_opportunity_observations=[
            CraftOpportunityObservation(
                recipe_id="refine-bismuth",
                recipe_name="Refine Bismuth",
                output_item_id=210931,
                output_market="commodity",
                output_quantity=1,
                craft_cost=560,
                craft_cost_unit_price=560,
                output_min_unit_price=900,
                sell_target_unit_price=1000,
                auction_deposit_unit_price=20,
                ah_savings=340,
                expected_profit=420,
                max_craft_quantity=2,
                confidence=80,
                reasons=["profitable craft"],
            )
        ],
    )

    with Session(engine) as session:
        opportunities = session.scalars(select(CraftOpportunityObservationRecord)).all()
        tracked_items = session.scalars(select(TrackedItemRecord).order_by(TrackedItemRecord.item_id)).all()

    assert [item.item_id for item in tracked_items] == [210930, 210931]
    assert len(opportunities) == 1
    assert opportunities[0].recipe_id == "refine-bismuth"
    assert opportunities[0].expected_profit == 420
    assert opportunities[0].reasons_json == '["profitable craft"]'


def test_repository_blocks_overlapping_fetch_runs_and_records_interval() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )

    run_id = repository.start_fetch_run(config, expected_interval_seconds=1800)

    try:
        repository.start_fetch_run(config)
    except RuntimeError as exc:
        assert f"fetch run {run_id} is already running" in str(exc)
    else:
        raise AssertionError("overlapping fetch run was allowed")

    repository.complete_fetch_run(run_id, [], [])
    with Session(engine) as session:
        stored_run = session.get(FetchRun, run_id)

    assert stored_run is not None
    assert stored_run.expected_interval_seconds == 1800


def test_repository_imports_player_addon_rows(tmp_path) -> None:
    from wow_auction_tracker.features.player import import_saved_variables

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
              ["first_item_name"] = "Bismuth",
              ["first_item_id"] = 210930,
              ["first_item_count"] = 5,
            },
          },
          ["purchase_events"] = {
            {
              ["observed_at"] = 1710000600,
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
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)

    import_id = repository.import_addon_data(import_saved_variables(saved_variables))

    with Session(engine) as session:
        posts = session.scalars(select(PlayerAuctionPostRecord)).all()
        outcomes = session.scalars(select(PlayerAuctionOutcomeRecord)).all()
        purchases = session.scalars(select(PlayerAuctionPurchaseRecord)).all()

    assert import_id == 1
    assert len(posts) == 1
    assert posts[0].item_id == 210930
    assert len(outcomes) == 1
    assert outcomes[0].outcome == "sold"
    assert outcomes[0].money == 45000
    assert len(purchases) == 1
    assert purchases[0].event_type == "commodity_purchase_succeeded"
    assert purchases[0].total_price == 45000

    second_import_id = repository.import_addon_data(import_saved_variables(saved_variables))
    with Session(engine) as session:
        imports = session.scalars(select(AddonImportRecord).order_by(AddonImportRecord.id)).all()
        posts = session.scalars(select(PlayerAuctionPostRecord)).all()
        outcomes = session.scalars(select(PlayerAuctionOutcomeRecord)).all()
        purchases = session.scalars(select(PlayerAuctionPurchaseRecord)).all()
        matches = session.scalars(select(PlayerAuctionMatchRecord)).all()

    assert second_import_id == 2
    assert imports[0].inserted_row_count == 3
    assert imports[0].skipped_duplicate_count == 0
    assert imports[1].inserted_row_count == 0
    assert imports[1].skipped_duplicate_count == 3
    assert len(posts) == 1
    assert len(outcomes) == 1
    assert len(purchases) == 1
    assert len(matches) == 1
    assert matches[0].outcome == "sold"
    assert matches[0].elapsed_seconds == 300
    assert matches[0].confidence == 100


def test_import_addon_dedupes_mail_events_with_unstable_mail_index_and_raw_fields(tmp_path) -> None:
    from wow_auction_tracker.features.player import import_saved_variables

    saved_variables = tmp_path / "WowAuctionTracker.lua"
    saved_variables.write_text(
        """
        WowAuctionTrackerDB = {
          ["version"] = 3,
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
            {
              ["observed_at"] = 1710000300,
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["mail_index"] = 2,
              ["outcome"] = "sold",
              ["money"] = 45000,
              ["subject"] = "Auction successful: Bismuth (5)",
              ["scan_id"] = "later",
            },
          },
        }
        """,
        encoding="utf-8",
    )
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)

    repository.import_addon_data(import_saved_variables(saved_variables))

    with Session(engine) as session:
        outcomes = session.scalars(select(PlayerAuctionOutcomeRecord)).all()

    assert len(outcomes) == 1
    assert outcomes[0].item_name == "Bismuth"
    assert outcomes[0].item_count == 5


def test_import_addon_dedupes_purchase_events_and_skips_empty_completion(tmp_path) -> None:
    from wow_auction_tracker.features.player import import_saved_variables

    saved_variables = tmp_path / "WowAuctionTracker.lua"
    saved_variables.write_text(
        """
        WowAuctionTrackerDB = {
          ["version"] = 3,
          ["purchase_events"] = {
            {
              ["observed_at"] = 1710000600,
              ["event_type"] = "commodity_purchase_started",
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["market"] = "commodity",
              ["item_id"] = 210803,
              ["quantity"] = 6,
              ["unit_price"] = 100,
              ["total_price"] = 600,
            },
            {
              ["observed_at"] = 1710000600,
              ["event_type"] = "commodity_purchase_started",
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["market"] = "commodity",
              ["item_id"] = 210803,
              ["quantity"] = 6,
              ["unit_price"] = 100,
              ["total_price"] = 600,
            },
            {
              ["observed_at"] = 1710000601,
              ["event_type"] = "commodity_purchase_succeeded",
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["market"] = "commodity",
            },
            {
              ["observed_at"] = 1710000602,
              ["event_type"] = "auction_purchase_completed",
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["market"] = "realm",
              ["auction_id"] = 0,
            },
          },
        }
        """,
        encoding="utf-8",
    )
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)

    repository.import_addon_data(import_saved_variables(saved_variables))

    with Session(engine) as session:
        purchases = session.scalars(select(PlayerAuctionPurchaseRecord).order_by(PlayerAuctionPurchaseRecord.id)).all()

    assert [purchase.event_type for purchase in purchases] == [
        "commodity_purchase_started",
        "commodity_purchase_succeeded",
    ]
    assert purchases[0].unit_price == 100
    assert purchases[1].item_id == 210803
    assert purchases[1].quantity == 6
    assert purchases[1].total_price == 600
