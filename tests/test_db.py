from sqlalchemy import select

from wow_auction_tracker.auction import filter_auctions, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.db import (
    AuctionListingRecord,
    AuctionRepository,
    ItemSummaryRecord,
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
