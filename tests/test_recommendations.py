from datetime import datetime, timedelta
from pathlib import Path

from wow_auction_tracker.auction import AuctionListing, calculate_item_history_metrics, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.lifecycle import build_listing_observations
from wow_auction_tracker.features.recommendations import RecommendationEngine
from wow_auction_tracker.features.sellthrough import build_sell_through_metrics
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
    assert recommendations[0].recommended_buy_price == 5000
    assert recommendations[0].recommended_sell_price == 8333
    assert recommendations[0].average_first_quartile_unit_price == 8333
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
    assert recommendations[0].recommended_buy_price is not None
    assert recommendations[0].recommended_sell_price is not None
    assert recommendations[0].reasons != ["needs at least 3 snapshots"]


def test_recommendations_use_inferred_sell_through(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )

    previous_snapshots = []
    for index, auction_ids in enumerate([[1, 2], [2], [2]], start=1):
        run_id = repository.start_fetch_run(config)
        listings = [
            AuctionListing(
                auction_id=auction_id,
                item_id=210930,
                market=Market.COMMODITY,
                quantity=10,
                unit_price=10000,
                buyout=None,
                bid=None,
                time_left="LONG",
                raw={"id": auction_id, "item": {"id": 210930}},
            )
            for auction_id in auction_ids
        ]
        observations = build_listing_observations(listings, previous_snapshots)
        repository.complete_fetch_run(
            run_id,
            listings,
            summarize_listings(listings),
            calculate_item_history_metrics(listings),
            observations,
            build_sell_through_metrics(observations),
        )
        previous_snapshots = repository.list_listing_snapshots(run_id)

    recommendation = RecommendationEngine(f"sqlite:///{db_path}", lookback_runs=3).recommend()[0]

    assert recommendation.estimated_demand_score > 0
    assert recommendation.average_sell_through_ratio > 0
    assert any("inferred sell-through" in reason for reason in recommendation.reasons)


def test_recommendations_prefer_player_sale_outcomes(tmp_path: Path) -> None:
    from wow_auction_tracker.features.player import import_saved_variables

    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )

    for index, price in enumerate([10000, 9500, 9000], start=1):
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
        repository.complete_fetch_run(
            run_id,
            listings,
            summarize_listings(listings),
            calculate_item_history_metrics(listings),
        )

    saved_variables = tmp_path / "WowAuctionTracker.lua"
    saved_variables.write_text(
        """
        WowAuctionTrackerDB = {
          ["version"] = 1,
          ["owned_snapshots"] = {
            { ["item_id"] = 210930 }, { ["item_id"] = 210930 }, { ["item_id"] = 210930 },
            { ["item_id"] = 210930 }, { ["item_id"] = 210930 }, { ["item_id"] = 210930 },
            { ["item_id"] = 210930 }, { ["item_id"] = 210930 }, { ["item_id"] = 210930 },
            { ["item_id"] = 210930 },
          },
          ["mail_events"] = {
            { ["outcome"] = "sold", ["money"] = 100000, ["first_item_id"] = 210930 },
            { ["outcome"] = "sold", ["money"] = 90000, ["first_item_id"] = 210930 },
            { ["outcome"] = "sold", ["money"] = 80000, ["first_item_id"] = 210930 },
            { ["outcome"] = "sold", ["money"] = 70000, ["first_item_id"] = 210930 },
            { ["outcome"] = "sold", ["money"] = 60000, ["first_item_id"] = 210930 },
            { ["outcome"] = "sold", ["money"] = 50000, ["first_item_id"] = 210930 },
            { ["outcome"] = "sold", ["money"] = 40000, ["first_item_id"] = 210930 },
          },
        }
        """,
        encoding="utf-8",
    )
    repository.import_addon_data(import_saved_variables(saved_variables))

    recommendation = RecommendationEngine(f"sqlite:///{db_path}", lookback_runs=3).recommend()[0]

    assert recommendation.player_post_count == 10
    assert recommendation.player_sold_count == 7
    assert recommendation.player_sale_rate == 0.7
    assert recommendation.estimated_demand_score == 70
    assert recommendation.confidence == 100
    assert any("personal sale rate" in reason for reason in recommendation.reasons)


def test_recommendations_include_historical_timing_windows(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )
    first_monday = datetime(2026, 1, 5)
    snapshots = []
    for week in range(4):
        snapshots.append((first_monday + timedelta(days=(week * 7) + 1, hours=2), 5000))
        snapshots.append((first_monday + timedelta(days=(week * 7) + 4, hours=20), 12000))

    for index, (started_at, price) in enumerate(snapshots, start=1):
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
        repository.complete_fetch_run(
            run_id,
            listings,
            summarize_listings(listings),
            calculate_item_history_metrics(listings),
        )
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "update fetch_runs set started_at = ? where id = ?",
                (started_at.isoformat(sep=" "), run_id),
            )

    recommendation = RecommendationEngine(f"sqlite:///{db_path}", lookback_runs=8).recommend()[0]

    assert recommendation.best_buy_time == "Mon 21:00 EST"
    assert recommendation.best_sell_time == "Fri 15:00 EST"
    assert recommendation.historical_buy_price == 5000
    assert recommendation.historical_sell_price == 12000
    assert recommendation.historical_timing_confidence > 0
    assert any("historically best buy window" in reason for reason in recommendation.reasons)

    utc_recommendation = RecommendationEngine(
        f"sqlite:///{db_path}",
        lookback_runs=8,
        display_timezone="UTC",
    ).recommend()[0]

    assert utc_recommendation.best_buy_time == "Tue 02:00 UTC"
    assert utc_recommendation.best_sell_time == "Fri 20:00 UTC"
