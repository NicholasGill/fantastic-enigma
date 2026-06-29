from pathlib import Path

from wow_auction_tracker.auction import AuctionListing, calculate_item_history_metrics, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.recommendations import RecommendationEngine
from wow_auction_tracker.storage import AuctionRepository, create_db_engine, init_db


def test_recommendations_score_discounted_item_with_quantity_drop(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )

    for price, quantity in [(10000, 100), (10000, 80), (5000, 40)]:
        run_id = repository.start_fetch_run(config)
        listings = [
            AuctionListing(
                auction_id=run_id,
                item_id=210930,
                market=Market.COMMODITY,
                quantity=quantity,
                unit_price=price,
                buyout=None,
                bid=None,
                time_left="LONG",
                raw={"id": run_id, "item": {"id": 210930}},
            )
        ]
        repository.complete_fetch_run(
            run_id,
            listings,
            summarize_listings(listings),
            calculate_item_history_metrics(listings),
        )

    recommendations = RecommendationEngine(f"sqlite:///{db_path}", lookback_runs=3).recommend()

    assert recommendations[0].item_id == 210930
    assert recommendations[0].score >= 35
    assert recommendations[0].action in {"buy", "watch"}
    assert recommendations[0].estimated_demand_score > 0
    assert recommendations[0].average_weighted_unit_price is not None
    assert any("below recent median" in reason for reason in recommendations[0].reasons)


def test_recommendations_require_enough_snapshots(tmp_path: Path) -> None:
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

    recommendations = RecommendationEngine(f"sqlite:///{db_path}", min_snapshots=3).recommend()

    assert recommendations[0].action == "watch"
    assert recommendations[0].score == 0
    assert recommendations[0].reasons == ["needs at least 3 snapshots"]


def test_recommendations_fall_back_to_summaries_while_metrics_warm_up(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )

    for index, price in enumerate([10000, 10000, 5000], start=1):
        run_id = repository.start_fetch_run(config)
        listings = [
            AuctionListing(
                auction_id=index,
                item_id=210930,
                market=Market.COMMODITY,
                quantity=10,
                unit_price=price,
                buyout=None,
                bid=None,
                time_left="LONG",
                raw={"id": index, "item": {"id": 210930}},
            )
        ]
        metrics = calculate_item_history_metrics(listings) if index == 3 else ()
        repository.complete_fetch_run(run_id, listings, summarize_listings(listings), metrics)

    recommendations = RecommendationEngine(f"sqlite:///{db_path}", min_snapshots=3).recommend()

    assert recommendations[0].score > 0
    assert recommendations[0].reasons != ["needs at least 3 snapshots"]
