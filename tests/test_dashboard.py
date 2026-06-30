from pathlib import Path

from wow_auction_tracker.auction import AuctionListing, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.dashboard import DashboardDataStore, format_file_size
from wow_auction_tracker.storage import AuctionRepository, create_db_engine, init_db


def test_dashboard_overview_returns_latest_counts_and_items(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {
            "connected_realm_id": 3683,
            "items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}],
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

    payload = DashboardDataStore(f"sqlite:///{db_path}").overview()

    assert payload["counts"]["fetch_runs"] == 2
    assert payload["counts"]["auction_listings"] == 2
    assert payload["latest_run"]["id"] == second_run_id
    assert payload["items"][0]["name"] == "Bismuth"
    assert payload["items"][0]["min_unit_price"] == 15000
    assert payload["items"][0]["first_quartile_unit_price"] == 15000
    assert payload["items"][0]["third_quartile_unit_price"] == 15000
    assert payload["items"][0]["previous_min_unit_price"] == 20000


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


def test_format_file_size() -> None:
    assert format_file_size(512) == "512 B"
    assert format_file_size(2048) == "2.0 KiB"
