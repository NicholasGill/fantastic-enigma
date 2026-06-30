from pathlib import Path

from wow_auction_tracker.auction import AuctionListing, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.dashboard.server import (
    DASHBOARD_HTML,
    DashboardDataStore,
    _crafting_quality_from_raw_json,
    _dashboard_timezone,
    _has_buy_opportunity,
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
    assert payload["items"][0]["recommended_buy_price"] is not None
    assert payload["items"][0]["recommended_sell_price"] is not None
    assert payload["items"][0]["crafting_quality"] == "1"


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


def test_format_file_size() -> None:
    assert format_file_size(512) == "512 B"
    assert format_file_size(2048) == "2.0 KiB"


def test_dashboard_table_headers_have_tooltips() -> None:
    assert 'title="Crafting quality from auction listing modifiers' in DASHBOARD_HTML
    assert 'title="Recommended maximum buy price' in DASHBOARD_HTML
    assert 'title="Recommended conservative sell price' in DASHBOARD_HTML
    assert 'title="Potential per-item profit before fees' in DASHBOARD_HTML
    assert 'title="Estimated demand signal from recent disappeared listings' in DASHBOARD_HTML
    assert 'title="Change from the latest minimum price' in DASHBOARD_HTML
    assert 'quality-badge' in DASHBOARD_HTML
    assert "function profit" in DASHBOARD_HTML
    assert "buy-opportunity" in DASHBOARD_HTML


def test_dashboard_flags_buy_opportunities() -> None:
    assert _has_buy_opportunity(90, 100) is True
    assert _has_buy_opportunity(100, 100) is False
    assert _has_buy_opportunity(None, 100) is False
    assert _has_buy_opportunity(90, None) is False


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
    assert '<option value="America/New_York" selected>Eastern</option>' in DASHBOARD_HTML
    assert '<option value="UTC">UTC</option>' in DASHBOARD_HTML
    assert _dashboard_timezone("America/Los_Angeles") == "America/Los_Angeles"
    assert _dashboard_timezone("Bad/Zone") == "America/New_York"
