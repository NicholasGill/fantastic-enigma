from pathlib import Path

from wow_auction_tracker.auction import AuctionListing, calculate_item_history_metrics, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.backtesting import BacktestEngine, BacktestStrategy, backtest_trade_rows
from wow_auction_tracker.storage import AuctionRepository, create_db_engine, init_db


def test_backtest_buys_discount_and_sells_target(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    _store_price_series(db_path, [100, 100, 100, 70, 120])

    result = BacktestEngine(
        f"sqlite:///{db_path}",
        BacktestStrategy(
            lookback_runs=3,
            buy_discount_bps=1500,
            sell_markup_bps=1000,
            max_position_quantity=5,
            starting_cash=100_000,
        ),
    ).run()

    assert result.snapshot_count == 5
    assert result.closed_trade_count == 1
    assert result.open_position_count == 0
    assert result.realized_profit == 220
    assert result.total_profit == 220
    assert result.return_bps == 22
    assert result.trades[0].buy_unit_price == 70
    assert result.trades[0].sell_unit_price == 120
    assert result.trades[0].exit_reason == "target"


def test_backtest_marks_unsold_positions_to_market(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    _store_price_series(db_path, [100, 100, 100, 70])

    result = BacktestEngine(
        f"sqlite:///{db_path}",
        BacktestStrategy(
            lookback_runs=3,
            buy_discount_bps=1500,
            max_position_quantity=5,
            starting_cash=100_000,
        ),
    ).run()

    assert result.closed_trade_count == 0
    assert result.open_position_count == 1
    assert result.realized_profit == 0
    assert result.unrealized_profit == -17
    assert result.total_profit == -17


def test_backtest_trade_rows_are_csv_ready(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    _store_price_series(db_path, [100, 100, 100, 70, 120])

    result = BacktestEngine(
        f"sqlite:///{db_path}",
        BacktestStrategy(lookback_runs=3, max_position_quantity=5, starting_cash=100_000),
    ).run()
    rows = backtest_trade_rows(result)

    assert rows[0]["item_id"] == 210930
    assert rows[0]["name"] == "Bismuth"
    assert rows[0]["buy_unit_price"] == 70
    assert rows[0]["net_profit"] == 220


def _store_price_series(db_path: Path, prices: list[int]) -> None:
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]}
    )

    for index, price in enumerate(prices, start=1):
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
