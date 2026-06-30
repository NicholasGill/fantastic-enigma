from sqlalchemy import select
from sqlalchemy.orm import Session

from wow_auction_tracker.auction import calculate_item_history_metrics, filter_auctions, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.lifecycle import build_listing_observations
from wow_auction_tracker.features.metadata import ItemMetadata
from wow_auction_tracker.features.sellthrough import build_sell_through_metrics
from wow_auction_tracker.storage import (
    AuctionListingRecord,
    AuctionRepository,
    ItemHistoryMetricRecord,
    ItemMetadataRecord,
    ItemSummaryRecord,
    ListingObservationRecord,
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
        {"auctions": [{"id": 1, "item": {"id": 210930}, "quantity": 2, "unit_price": 100}]},
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


def test_repository_stores_sell_through_metrics() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )
    first_run_id = repository.start_fetch_run(config)
    first_listings = filter_auctions(
        {"auctions": [{"id": 1, "item": {"id": 210930}, "quantity": 2, "unit_price": 100}]},
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
    observations = build_listing_observations([], repository.list_listing_snapshots(first_run_id))

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
    assert metrics[0].disappeared_quantity == 2
    assert metrics[0].sell_through_ratio_bps == 10000
