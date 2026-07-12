from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from wow_auction_tracker.clients.blizzard import BlizzardClient
from wow_auction_tracker.config import load_config
from wow_auction_tracker.features.backtesting import (
    BacktestEngine,
    BacktestResult,
    BacktestStrategy,
    backtest_result_rows,
    backtest_trade_rows,
)
from wow_auction_tracker.features.dashboard import DashboardConfig, serve_dashboard
from wow_auction_tracker.features.lifecycle import build_listing_observations
from wow_auction_tracker.features.player import import_saved_variables
from wow_auction_tracker.features.recommendations import Recommendation, RecommendationEngine
from wow_auction_tracker.features.dashboard import DashboardDataStore
from wow_auction_tracker.features.scheduler import run_snapshot_schedule
from wow_auction_tracker.features.sellthrough import build_sell_through_metrics
from wow_auction_tracker.features.snapshots import FetchResult, fetch_and_store, replay_raw_fetch_run
from wow_auction_tracker.storage import AuctionRepository, create_db_engine, init_db


DEFAULT_CONFIG_PATH = Path("config/items.yaml")
DEFAULT_DATABASE_URL = "sqlite:///data/auction_tracker.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track World of Warcraft auction snapshots.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to item config YAML. Defaults to {DEFAULT_CONFIG_PATH}.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="SQLAlchemy database URL. Defaults to DATABASE_URL or local SQLite.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Create database tables.")
    subparsers.add_parser("recompute-inference", help="Recompute derived listing lifecycle and sell-through rows.")
    replay_parser = subparsers.add_parser("replay-raw", help="Rebuild derived rows from preserved raw auction snapshots.")
    replay_parser.add_argument(
        "--from-run-id",
        type=_positive_int,
        required=True,
        help="First fetch run ID to replay.",
    )
    replay_parser.add_argument(
        "--to-run-id",
        type=_positive_int,
        required=True,
        help="Last fetch run ID to replay.",
    )
    db_parser = subparsers.add_parser("db", help="Inspect and maintain the local database.")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_subparsers.add_parser("stats", help="Show table sizes and snapshot range.")
    db_subparsers.add_parser("vacuum", help="Run SQLite VACUUM to reclaim space.")
    prune_parser = db_subparsers.add_parser(
        "prune-raw-listings",
        help="Delete old raw auction listings while preserving summaries and rollups.",
    )
    prune_parser.add_argument(
        "--before-days",
        type=_non_negative_int,
        required=True,
        help="Delete raw listings from successful fetch runs older than this many days.",
    )
    subparsers.add_parser("fetch", help="Fetch configured auction listings and store a snapshot.")
    dashboard_parser = subparsers.add_parser("dashboard", help="Start the local dashboard web server.")
    dashboard_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Dashboard bind host. Defaults to 127.0.0.1.",
    )
    dashboard_parser.add_argument(
        "--port",
        type=_positive_int,
        default=8000,
        help="Dashboard port. Defaults to 8000.",
    )
    dashboard_parser.add_argument(
        "--dev-mode",
        action="store_true",
        help="Show display-only dashboard demo states such as buy-opportunity highlighting.",
    )
    dashboard_parser.add_argument(
        "--reload",
        action="store_true",
        help="Run the dashboard with Flask's development reloader.",
    )
    dashboard_parser.add_argument(
        "--addon-saved-variables",
        type=Path,
        default=os.getenv("WOW_AUCTION_TRACKER_SAVED_VARIABLES"),
        help="Path to WowAuctionTracker.lua for dashboard addon imports.",
    )
    recommend_parser = subparsers.add_parser("recommend", help="Rank tracked items from snapshot history.")
    recommend_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        help="Number of recommendations to show. Defaults to 10.",
    )
    recommend_parser.add_argument(
        "--lookback-runs",
        type=_positive_int,
        default=12,
        help="Number of recent successful snapshots to score. Defaults to 12.",
    )
    recommend_parser.add_argument(
        "--min-snapshots",
        type=_positive_int,
        default=3,
        help="Minimum snapshots required before scoring an item. Defaults to 3.",
    )
    recommend_parser.add_argument(
        "--timezone",
        default="America/New_York",
        help="Timezone for historical timing labels. Defaults to America/New_York.",
    )
    report_parser = subparsers.add_parser("report", help="Print saved snapshot data in the terminal.")
    report_subparsers = report_parser.add_subparsers(dest="report_command", required=True)
    latest_parser = report_subparsers.add_parser("latest", help="Show the latest item summaries.")
    latest_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        help="Number of items to show. Defaults to 10.",
    )
    report_item_parser = report_subparsers.add_parser("item", help="Show snapshot history for one item.")
    report_item_parser.add_argument("--item-id", type=_positive_int, required=True, help="Item ID to show.")
    report_crafts_parser = report_subparsers.add_parser("crafts", help="Show current profitable craft signals.")
    report_crafts_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        help="Number of craft signals to show. Defaults to 10.",
    )
    report_anomalies_parser = report_subparsers.add_parser("anomalies", help="Show recent detected market anomalies.")
    report_anomalies_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        help="Number of anomaly rows to show. Defaults to 10.",
    )
    report_quality_parser = report_subparsers.add_parser("quality", help="Show recent market data quality events.")
    report_quality_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        help="Number of quality events to show. Defaults to 10.",
    )
    report_player_parser = report_subparsers.add_parser("player", help="Show personal auction performance.")
    report_player_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        help="Number of player performance rows to show. Defaults to 10.",
    )
    report_player_parser.add_argument(
        "--days",
        type=_positive_int,
        default=None,
        help="Only include outcomes from the last N days.",
    )
    export_parser = subparsers.add_parser("export", help="Export stored data to CSV.")
    export_subparsers = export_parser.add_subparsers(dest="export_command", required=True)
    export_latest_parser = export_subparsers.add_parser("latest", help="Export the latest item summaries to CSV.")
    export_latest_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write CSV to a file instead of stdout.",
    )
    export_item_parser = export_subparsers.add_parser("item", help="Export one item's snapshot history to CSV.")
    export_item_parser.add_argument("--item-id", type=_positive_int, required=True, help="Item ID to export.")
    export_item_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write CSV to a file instead of stdout.",
    )
    export_recommendations_parser = export_subparsers.add_parser(
        "recommendations",
        help="Export current recommendations to CSV.",
    )
    export_recommendations_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=10,
        help="Number of recommendations to export. Defaults to 10.",
    )
    export_recommendations_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write CSV to a file instead of stdout.",
    )
    export_recommendations_parser.add_argument(
        "--timezone",
        default="America/New_York",
        help="Timezone for historical timing labels. Defaults to America/New_York.",
    )
    export_crafts_parser = export_subparsers.add_parser("crafts", help="Export current craft signals to CSV.")
    export_crafts_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write CSV to a file instead of stdout.",
    )
    export_player_parser = export_subparsers.add_parser(
        "player-performance",
        help="Export personal auction performance to CSV.",
    )
    export_player_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write CSV to a file instead of stdout.",
    )
    export_player_parser.add_argument(
        "--days",
        type=_positive_int,
        default=None,
        help="Only include outcomes from the last N days.",
    )
    backtest_parser = subparsers.add_parser("backtest", help="Backtest a simple snapshot-history trading strategy.")
    backtest_parser.add_argument(
        "--lookback-runs",
        type=_positive_int,
        default=6,
        help="Historical snapshots used for the rolling baseline. Defaults to 6.",
    )
    backtest_parser.add_argument(
        "--buy-discount-bps",
        type=_non_negative_int,
        default=1500,
        help="Buy when current low is this many basis points below baseline. Defaults to 1500.",
    )
    backtest_parser.add_argument(
        "--sell-markup-bps",
        type=_non_negative_int,
        default=1000,
        help="Sell target markup over baseline in basis points. Defaults to 1000.",
    )
    backtest_parser.add_argument(
        "--stop-loss-bps",
        type=_non_negative_int,
        default=2000,
        help="Exit if sell price falls this many basis points below buy price. Defaults to 2000.",
    )
    backtest_parser.add_argument(
        "--min-sell-through-bps",
        type=_non_negative_int,
        default=0,
        help="Minimum per-snapshot inferred sell-through ratio required to buy. Defaults to 0.",
    )
    backtest_parser.add_argument(
        "--max-position-quantity",
        type=_positive_int,
        default=20,
        help="Maximum quantity to hold per item. Defaults to 20.",
    )
    backtest_parser.add_argument(
        "--max-holding-runs",
        type=_positive_int,
        default=8,
        help="Maximum snapshots to hold before selling at the current sell price. Defaults to 8.",
    )
    backtest_parser.add_argument(
        "--starting-cash",
        type=_positive_int,
        default=1_000_000_000,
        help="Starting cash in copper. Defaults to 1000000000.",
    )
    backtest_parser.add_argument(
        "--ah-cut-bps",
        type=_non_negative_int,
        default=500,
        help="Auction house cut in basis points. Defaults to 500.",
    )
    backtest_parser.add_argument(
        "--auction-duration-hours",
        type=_positive_int,
        default=48,
        help="Auction duration used for deposit estimates. Defaults to 48.",
    )
    backtest_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write one-row backtest summary CSV to this path.",
    )
    backtest_parser.add_argument(
        "--trades-output",
        type=Path,
        default=None,
        help="Write closed trade rows to this CSV path.",
    )
    import_parser = subparsers.add_parser("import-addon", help="Import companion addon SavedVariables.")
    import_parser.add_argument(
        "--saved-variables",
        type=Path,
        required=True,
        help="Path to WowAuctionTracker.lua SavedVariables.",
    )
    schedule_parser = subparsers.add_parser("schedule", help="Fetch snapshots repeatedly at a fixed interval.")
    schedule_parser.add_argument(
        "--interval-minutes",
        type=_positive_int,
        required=True,
        help="Minutes to wait between snapshot fetches.",
    )
    schedule_parser.add_argument(
        "--max-runs",
        type=_non_negative_int,
        default=None,
        help="Optional number of snapshots to fetch before exiting.",
    )
    schedule_parser.add_argument(
        "--no-run-immediately",
        action="store_true",
        help="Wait one interval before the first snapshot.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = create_db_engine(args.database_url)
    init_db(engine)

    if args.command == "init-db":
        print(f"Initialized database at {args.database_url}")
        return 0

    if args.command == "recompute-inference":
        repository = AuctionRepository(engine)
        result = _recompute_inference(repository)
        print(
            "Recomputed inference for "
            f"{result['run_count']} fetch runs: "
            f"{result['observation_count']} observations, "
            f"{result['sell_through_count']} sell-through rows"
        )
        return 0

    if args.command == "replay-raw":
        if args.to_run_id < args.from_run_id:
            raise ValueError("--to-run-id must be greater than or equal to --from-run-id")
        config = load_config(args.config)
        repository = AuctionRepository(engine)
        replayed_count = 0
        listing_count = 0
        summary_count = 0
        for run_id in repository.successful_fetch_run_ids():
            if args.from_run_id <= run_id <= args.to_run_id:
                result = replay_raw_fetch_run(config, repository, run_id)
                replayed_count += 1
                listing_count += result.listing_count
                summary_count += result.summary_count
        print(
            f"Replayed {replayed_count} raw fetch run(s): "
            f"{listing_count} listings, {summary_count} item summaries"
        )
        return 0

    if args.command == "db":
        repository = AuctionRepository(engine)
        if args.db_command == "stats":
            _print_db_stats(repository.database_stats())
            return 0
        if args.db_command == "vacuum":
            repository.vacuum()
            print("Vacuumed SQLite database")
            return 0
        if args.db_command == "prune-raw-listings":
            before = datetime.now(UTC) - timedelta(days=args.before_days)
            deleted_count = repository.prune_raw_listings_before(before)
            print(f"Deleted {deleted_count} raw auction listings older than {args.before_days} day(s)")
            return 0
        raise ValueError(f"unsupported db command {args.db_command}")

    if args.command == "dashboard":
        config = load_config(args.config)
        serve_dashboard(
            DashboardConfig(
                database_url=args.database_url,
                host=args.host,
                port=args.port,
                dev_mode=args.dev_mode,
                reload=args.reload,
                addon_saved_variables_path=args.addon_saved_variables,
                tracked_item_ids=frozenset(item.id for item in config.all_tracked_items),
            )
        )
        return 0

    if args.command == "recommend":
        recommendations = RecommendationEngine(
            args.database_url,
            lookback_runs=args.lookback_runs,
            min_snapshots=args.min_snapshots,
            display_timezone=args.timezone,
        ).recommend(limit=args.limit)
        _print_recommendations(recommendations)
        return 0

    if args.command == "backtest":
        strategy = BacktestStrategy(
            lookback_runs=args.lookback_runs,
            buy_discount_bps=args.buy_discount_bps,
            sell_markup_bps=args.sell_markup_bps,
            stop_loss_bps=args.stop_loss_bps,
            min_sell_through_bps=args.min_sell_through_bps,
            max_position_quantity=args.max_position_quantity,
            max_holding_runs=args.max_holding_runs,
            starting_cash=args.starting_cash,
            ah_cut_bps=args.ah_cut_bps,
            auction_duration_hours=args.auction_duration_hours,
        )
        result = BacktestEngine(args.database_url, strategy).run()
        if args.output is not None:
            _write_csv(backtest_result_rows(result), args.output, fieldnames=_BACKTEST_SUMMARY_FIELDNAMES)
        if args.trades_output is not None:
            _write_csv(backtest_trade_rows(result), args.trades_output, fieldnames=_BACKTEST_TRADE_FIELDNAMES)
        _print_backtest_result(result)
        return 0

    if args.command == "report":
        store = DashboardDataStore(args.database_url)
        if args.report_command == "latest":
            _print_latest_report(store.overview(), args.limit)
            return 0
        if args.report_command == "item":
            _print_item_report(store.item_history(args.item_id))
            return 0
        if args.report_command == "crafts":
            _print_craft_report(store.overview(), args.limit)
            return 0
        if args.report_command == "anomalies":
            _print_anomaly_report(AuctionRepository(engine).list_recent_anomalies(limit=args.limit))
            return 0
        if args.report_command == "quality":
            _print_quality_report(AuctionRepository(engine).list_recent_quality_events(limit=args.limit))
            return 0
        if args.report_command == "player":
            _print_player_report(store.player_performance(window_days=args.days), args.limit)
            return 0
        raise ValueError(f"unsupported report command {args.report_command}")

    if args.command == "export":
        store = DashboardDataStore(args.database_url)
        if args.export_command == "latest":
            _write_csv(
                _latest_rows_for_export(store.overview()),
                args.output,
                fieldnames=_LATEST_EXPORT_FIELDNAMES,
            )
            return 0
        if args.export_command == "item":
            _write_csv(
                _item_history_rows_for_export(store.item_history(args.item_id)),
                args.output,
                fieldnames=_ITEM_HISTORY_EXPORT_FIELDNAMES,
            )
            return 0
        if args.export_command == "recommendations":
            recommendations = RecommendationEngine(
                args.database_url,
                display_timezone=args.timezone,
            ).recommend(limit=args.limit)
            _write_csv(
                _recommendation_rows_for_export(recommendations),
                args.output,
                fieldnames=_RECOMMENDATION_EXPORT_FIELDNAMES,
            )
            return 0
        if args.export_command == "crafts":
            _write_csv(
                _craft_rows_for_export(store.overview()),
                args.output,
                fieldnames=_CRAFT_EXPORT_FIELDNAMES,
            )
            return 0
        if args.export_command == "player-performance":
            _write_csv(
                store.player_performance(window_days=args.days),
                args.output,
                fieldnames=_PLAYER_PERFORMANCE_EXPORT_FIELDNAMES,
            )
            return 0
        raise ValueError(f"unsupported export command {args.export_command}")

    if args.command == "import-addon":
        repository = AuctionRepository(engine)
        result = import_saved_variables(args.saved_variables)
        import_id = repository.import_addon_data(result)
        import_counts = DashboardDataStore(args.database_url).overview()["player_activity"].get("latest_import", {})
        print(
            f"Imported addon data {import_id}: "
            f"{len(result.posts)} owned auction rows, {len(result.outcomes)} mail rows, "
            f"{len(result.purchases)} purchase rows, {len(result.gold_snapshots or [])} gold rows, "
            f"{import_counts.get('inserted_row_count', 0)} inserted, "
            f"{import_counts.get('skipped_duplicate_count', 0)} skipped duplicates, "
            f"{import_counts.get('malformed_row_count', 0)} malformed"
        )
        return 0

    if args.command in {"fetch", "schedule"}:
        config = load_config(args.config)
        client_id = _required_env("BLIZZARD_CLIENT_ID")
        client_secret = _required_env("BLIZZARD_CLIENT_SECRET")
        repository = AuctionRepository(engine)

        with BlizzardClient(
            client_id=client_id,
            client_secret=client_secret,
            region=config.region,
            locale=config.locale,
        ) as client:
            if args.command == "fetch":
                result = fetch_and_store(config, client, repository)
                _print_fetch_result(result, args.database_url)
                return 0

            interval_seconds = args.interval_minutes * 60
            print(f"Starting scheduled snapshots every {args.interval_minutes} minute(s)")
            run_snapshot_schedule(
                lambda: fetch_and_store(
                    config,
                    client,
                    repository,
                    expected_interval_seconds=interval_seconds,
                ),
                interval_seconds=interval_seconds,
                max_runs=args.max_runs,
                run_immediately=not args.no_run_immediately,
                on_success=lambda result: _print_fetch_result(result, args.database_url),
            )
            return 0

    raise ValueError(f"unsupported command {args.command}")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to 0")
    return parsed


def _print_fetch_result(result: FetchResult, database_url: str) -> None:
    message = (
        "Stored fetch run "
        f"{result.fetch_run_id}: {result.listing_count} listings, "
        f"{result.summary_count} item summaries"
    )
    database_size = _database_size_label(database_url)
    if database_size is not None:
        message = f"{message}, database size {database_size}"
    print(message)


def _recompute_inference(repository: AuctionRepository) -> dict[str, int]:
    run_ids = repository.successful_fetch_run_ids()
    observation_count = 0
    sell_through_count = 0
    previous_run_id: int | None = None
    for run_id in run_ids:
        listings = repository.list_auction_listings(run_id)
        previous_listings = repository.list_listing_snapshots(previous_run_id) if previous_run_id is not None else []
        elapsed_seconds = _elapsed_seconds_between_runs(repository, previous_run_id, run_id)
        observations = build_listing_observations(
            listings,
            previous_listings,
            elapsed_seconds=elapsed_seconds,
        )
        metrics = build_sell_through_metrics(
            observations,
            elapsed_seconds=elapsed_seconds,
            expected_interval_seconds=repository.fetch_run_expected_interval_seconds(run_id),
        )
        repository.replace_inference(run_id, observations, metrics)
        observation_count += len(observations)
        sell_through_count += len(metrics)
        previous_run_id = run_id

    return {
        "run_count": len(run_ids),
        "observation_count": observation_count,
        "sell_through_count": sell_through_count,
    }


def _elapsed_seconds_between_runs(
    repository: AuctionRepository,
    previous_fetch_run_id: int | None,
    current_fetch_run_id: int,
) -> int | None:
    if previous_fetch_run_id is None:
        return None
    previous_started_at = repository.fetch_run_started_at(previous_fetch_run_id)
    current_started_at = repository.fetch_run_started_at(current_fetch_run_id)
    if previous_started_at is None or current_started_at is None:
        return None
    return max(0, round((current_started_at - previous_started_at).total_seconds()))


def _print_db_stats(stats: dict[str, object]) -> None:
    print(f"Successful fetch runs: {stats.get('successful_fetch_runs', 0)}")
    print(f"Oldest snapshot: {stats.get('oldest_snapshot') or '-'}")
    print(f"Newest snapshot: {stats.get('newest_snapshot') or '-'}")
    print("Table counts:")
    table_counts = stats.get("table_counts", {})
    if isinstance(table_counts, dict):
        for table_name in sorted(table_counts):
            print(f"  {table_name}: {table_counts[table_name]}")


def _print_recommendations(recommendations: list[Recommendation]) -> None:
    if not recommendations:
        print("No recommendations available")
        return

    print(
        "Action  Score  Trend  Conf  Item ID  Name                 "
        "Buy/Unit  Sell/Unit  Deposit  Profit    Sell Source    Reasons"
    )
    for item in recommendations:
        reasons = "; ".join(item.reasons)
        print(
            f"{item.action:<6}  "
            f"{item.score:>5}  "
            f"{item.price_trend_score:>5}  "
            f"{item.confidence:>4}  "
            f"{item.item_id:<7}  "
            f"{item.name[:20]:<20} "
            f"{_format_copper(item.recommended_buy_price):>9} "
            f"{_format_copper(item.recommended_sell_price):>9}  "
            f"{_format_copper(item.auction_deposit_unit_price):>7} "
            f"{_format_copper(item.estimated_profit_unit_price):>8}  "
            f"{(item.recommended_sell_price_source or '-'):<13} "
            f"{reasons}"
        )


def _print_latest_report(overview: dict[str, object], limit: int) -> None:
    items = overview.get("items", [])
    if not isinstance(items, list) or not items:
        print("No item summaries available")
        return

    print("Item ID  Name                 Market      Listings  Quantity  Min/Unit  Q1/Unit   Med/Unit  Q3/Unit   Confidence  Action")
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        confidence = item.get("recommendation_confidence") or "-"
        action = item.get("recommendation_action") or "-"
        print(
            f"{int(item['item_id']):<7}  "
            f"{str(item['name'])[:20]:<20} "
            f"{str(item['market']):<10} "
            f"{int(item['listing_count']):>8}  "
            f"{int(item['total_quantity']):>8}  "
            f"{_format_copper(item['min_unit_price']):>9}  "
            f"{_format_copper(item.get('first_quartile_unit_price')):>9}  "
            f"{_format_copper(item['median_unit_price']):>9}  "
            f"{_format_copper(item.get('third_quartile_unit_price')):>9}  "
            f"{confidence!s:>10}  "
            f"{action}"
        )


def _print_item_report(item_history: dict[str, object]) -> None:
    item = item_history.get("item", {})
    history = item_history.get("history", [])
    if not isinstance(item, dict) or not isinstance(history, list):
        print("No item history available")
        return

    print(f"{item.get('name', 'Unknown')} ({item.get('item_id')})")
    print("Run ID  Started At            Listings  Quantity  Min/Unit  Q1/Unit   Med/Unit  Q3/Unit")
    for row in history:
        if not isinstance(row, dict):
            continue
        print(
            f"{int(row['fetch_run_id']):<6}  "
            f"{str(row['started_at'])[:19]:<19} "
            f"{int(row['listing_count']):>8}  "
            f"{int(row['total_quantity']):>8}  "
            f"{_format_copper(row['min_unit_price']):>9}  "
            f"{_format_copper(row.get('first_quartile_unit_price')):>9}  "
            f"{_format_copper(row['median_unit_price']):>9}"
            f"  {_format_copper(row.get('third_quartile_unit_price')):>9}"
        )


def _print_craft_report(overview: dict[str, object], limit: int) -> None:
    rows = overview.get("craft_opportunities", [])
    if not isinstance(rows, list) or not rows:
        print("No craft signals available")
        return

    print("Recipe              Output               Craft/Unit  AH/Unit   Sell/Unit  Profit    Max  Confidence")
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        print(
            f"{str(row.get('recipe_name') or row.get('recipe_id'))[:18]:<18} "
            f"{str(row.get('output_name'))[:20]:<20} "
            f"{_format_copper(row.get('craft_cost_unit_price')):>10} "
            f"{_format_copper(row.get('output_min_unit_price')):>8}  "
            f"{_format_copper(row.get('sell_target_unit_price')):>9} "
            f"{_format_copper(row.get('expected_profit')):>8}  "
            f"{int(row.get('max_craft_quantity') or 0):>3}  "
            f"{int(row.get('confidence') or 0):>10}"
        )


def _print_anomaly_report(rows: list[dict[str, object]]) -> None:
    if not rows:
        print("No anomalies available")
        return

    print("Run ID  Detected At          Type               Sev  Item ID  Name                 Observed  Baseline  Explanation")
    for row in rows:
        print(
            f"{int(row.get('fetch_run_id') or 0):<6}  "
            f"{str(row.get('detected_at') or '-')[:19]:<19} "
            f"{str(row.get('anomaly_type') or '-')[:18]:<18} "
            f"{int(row.get('severity') or 0):>3}  "
            f"{int(row.get('item_id') or 0):<7}  "
            f"{str(row.get('name') or 'Unknown')[:20]:<20} "
            f"{_format_copper(_optional_export_int(row.get('observed_value'))):>8}  "
            f"{_format_copper(_optional_export_int(row.get('baseline_value'))):>8}  "
            f"{row.get('explanation') or ''}"
        )


def _print_quality_report(rows: list[dict[str, object]]) -> None:
    if not rows:
        print("No quality events available")
        return

    print("Run ID  Detected At          Type                    Sev  Market      Item ID  Observed        Expected        Explanation")
    for row in rows:
        print(
            f"{int(row.get('fetch_run_id') or 0):<6}  "
            f"{str(row.get('detected_at') or '-')[:19]:<19} "
            f"{str(row.get('event_type') or '-')[:23]:<23} "
            f"{int(row.get('severity') or 0):>3}  "
            f"{str(row.get('market') or '-')[:10]:<10} "
            f"{str(row.get('item_id') or '-'):<7}  "
            f"{str(row.get('observed_value') or '-')[:14]:<14}  "
            f"{str(row.get('expected_value') or '-')[:14]:<14}  "
            f"{row.get('explanation') or ''}"
        )


def _print_player_report(rows: list[dict[str, object]], limit: int) -> None:
    if not isinstance(rows, list) or not rows:
        print("No player auction performance available")
        return

    print("Item ID  Name                 Character     Sold  Expired  Cancelled  Sale %  Avg Sale  Avg Sale Time")
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        print(
            f"{str(row.get('item_id') or '-'):<7}  "
            f"{str(row.get('name') or 'Unknown')[:20]:<20} "
            f"{str(row.get('character') or '-')[:12]:<12} "
            f"{int(row.get('sold_count') or 0):>4}  "
            f"{int(row.get('expired_count') or 0):>7}  "
            f"{int(row.get('cancelled_count') or 0):>9}  "
            f"{(int(row.get('sale_rate_bps') or 0) / 100):>5.1f}%  "
            f"{_format_copper(_optional_export_int(row.get('average_proceeds'))):>8}  "
            f"{_format_duration(_optional_export_int(row.get('average_time_to_sale_seconds'))):>13}"
        )


def _print_backtest_result(result: BacktestResult) -> None:
    print("Backtest summary")
    print(f"Snapshots: {result.snapshot_count}")
    print(f"Items: {result.item_count}")
    print(f"Closed trades: {result.closed_trade_count}")
    print(f"Open positions: {result.open_position_count}")
    print(f"Win rate: {result.win_rate * 100:.1f}%")
    print(f"Starting cash: {_format_copper(result.starting_cash)}")
    print(f"Ending equity: {_format_copper(result.ending_cash)}")
    print(f"Realized profit: {_format_copper(result.realized_profit)}")
    print(f"Unrealized profit: {_format_copper(result.unrealized_profit)}")
    print(f"Total profit: {_format_copper(result.total_profit)}")
    print(f"Return: {result.return_bps / 100:.2f}%")
    print(f"Max drawdown: {_format_copper(result.max_drawdown)} ({result.max_drawdown_bps / 100:.2f}%)")
    print(f"Average holding runs: {result.average_holding_runs:.1f}")
    if not result.trades:
        print("No trades executed")
        return

    print()
    print("Item ID  Name                 Qty  Buy Run  Sell Run  Buy/Unit  Sell/Unit  Profit    Exit")
    for trade in result.trades[:20]:
        print(
            f"{trade.item_id:<7}  "
            f"{trade.name[:20]:<20} "
            f"{trade.quantity:>3}  "
            f"{trade.buy_run_id:<7}  "
            f"{str(trade.sell_run_id or '-'):<8} "
            f"{_format_copper(trade.buy_unit_price):>9}  "
            f"{_format_copper(trade.sell_unit_price):>9}  "
            f"{_format_copper(trade.net_profit):>8}  "
            f"{trade.exit_reason}"
        )
    if len(result.trades) > 20:
        print(f"... {len(result.trades) - 20} more trade(s)")


_LATEST_EXPORT_FIELDNAMES = [
    "item_id",
    "name",
    "market",
    "listing_count",
    "total_quantity",
    "min_unit_price",
    "first_quartile_unit_price",
    "median_unit_price",
    "third_quartile_unit_price",
    "sell_through_ratio_bps",
    "disappeared_quantity",
    "disappeared_value",
    "sell_through_confidence",
    "quality",
    "item_class",
    "item_subclass",
    "icon_url",
    "recommendation_action",
    "recommendation_score",
    "recommendation_confidence",
    "recommended_buy_price",
    "recommended_sell_price",
    "recommended_buy_unit_price",
    "recommended_sell_unit_price",
    "recommended_sell_price_source",
    "vendor_sell_unit_price",
    "auction_deposit_unit_price",
    "estimated_profit_unit_price",
    "price_trend_score",
    "price_trend_ratio",
    "best_buy_time",
    "best_sell_time",
    "historical_buy_price",
    "historical_sell_price",
    "historical_timing_confidence",
]
_ITEM_HISTORY_EXPORT_FIELDNAMES = [
    "item_id",
    "name",
    "market",
    "fetch_run_id",
    "started_at",
    "listing_count",
    "total_quantity",
    "min_unit_price",
    "first_quartile_unit_price",
    "median_unit_price",
    "third_quartile_unit_price",
    "sell_through_ratio_bps",
    "disappeared_quantity",
    "disappeared_value",
    "sell_through_confidence",
]
_RECOMMENDATION_EXPORT_FIELDNAMES = [
    "item_id",
    "name",
    "market",
    "action",
    "score",
    "confidence",
    "latest_min_unit_price",
    "latest_shifted_unit_price",
    "latest_median_unit_price",
    "recommended_buy_price",
    "recommended_sell_price",
    "recommended_buy_unit_price",
    "recommended_sell_unit_price",
    "recommended_sell_price_source",
    "vendor_sell_unit_price",
    "auction_deposit_unit_price",
    "estimated_profit_unit_price",
    "average_first_quartile_unit_price",
    "average_median_unit_price",
    "average_third_quartile_unit_price",
    "average_weighted_unit_price",
    "price_trend_score",
    "price_trend_ratio",
    "estimated_demand_score",
    "average_sell_through_ratio",
    "average_sell_through_confidence",
    "average_probable_sold_unit_price",
    "player_post_count",
    "player_sold_count",
    "player_expired_count",
    "player_cancelled_count",
    "player_sale_rate",
    "average_player_net_proceeds",
    "best_buy_time",
    "best_sell_time",
    "historical_buy_price",
    "historical_sell_price",
    "historical_timing_confidence",
    "reasons",
]
_CRAFT_EXPORT_FIELDNAMES = [
    "recipe_id",
    "recipe_name",
    "output_item_id",
    "output_name",
    "output_market",
    "output_quantity",
    "craft_cost",
    "craft_cost_unit_price",
    "output_min_unit_price",
    "sell_target_unit_price",
    "auction_deposit_unit_price",
    "ah_savings",
    "expected_profit",
    "max_craft_quantity",
    "confidence",
    "reasons",
]
_PLAYER_PERFORMANCE_EXPORT_FIELDNAMES = [
    "item_id",
    "name",
    "character",
    "realm",
    "outcome_count",
    "sold_count",
    "expired_count",
    "cancelled_count",
    "sold_quantity",
    "expired_quantity",
    "cancelled_quantity",
    "sale_rate_bps",
    "proceeds",
    "average_proceeds",
    "average_time_to_sale_seconds",
    "average_time_to_expiry_seconds",
    "average_match_confidence",
]
_BACKTEST_SUMMARY_FIELDNAMES = [
    "snapshot_count",
    "item_count",
    "trade_count",
    "closed_trade_count",
    "open_position_count",
    "winning_trade_count",
    "losing_trade_count",
    "win_rate_bps",
    "starting_cash",
    "ending_cash",
    "realized_profit",
    "unrealized_profit",
    "total_profit",
    "return_bps",
    "max_drawdown",
    "max_drawdown_bps",
    "average_holding_runs",
]
_BACKTEST_TRADE_FIELDNAMES = [
    "item_id",
    "name",
    "market",
    "buy_run_id",
    "buy_started_at",
    "sell_run_id",
    "sell_started_at",
    "quantity",
    "buy_unit_price",
    "target_unit_price",
    "sell_unit_price",
    "gross_profit",
    "auction_cut",
    "deposit_cost",
    "net_profit",
    "holding_runs",
    "exit_reason",
]


def _write_csv(
    rows: list[dict[str, object]],
    output: Path | None,
    *,
    fieldnames: list[str] | None = None,
) -> None:
    if not rows and fieldnames is None:
        return

    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    if output is None:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return

    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _latest_rows_for_export(overview: dict[str, object]) -> list[dict[str, object]]:
    rows = overview.get("items", [])
    if not isinstance(rows, list):
        return []

    export_rows: list[dict[str, object]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        export_rows.append(
            {
                "item_id": item["item_id"],
                "name": item["name"],
                "market": item["market"],
                "listing_count": item["listing_count"],
                "total_quantity": item["total_quantity"],
                "min_unit_price": item["min_unit_price"],
                "first_quartile_unit_price": item.get("first_quartile_unit_price"),
                "median_unit_price": item["median_unit_price"],
                "third_quartile_unit_price": item.get("third_quartile_unit_price"),
                "sell_through_ratio_bps": item.get("sell_through_ratio_bps"),
                "disappeared_quantity": item.get("disappeared_quantity"),
                "disappeared_value": item.get("disappeared_value"),
                "sell_through_confidence": item.get("sell_through_confidence"),
                "quality": item.get("quality"),
                "item_class": item.get("item_class"),
                "item_subclass": item.get("item_subclass"),
                "icon_url": item.get("icon_url"),
                "recommendation_action": item.get("recommendation_action", ""),
                "recommendation_score": item.get("recommendation_score", ""),
                "recommendation_confidence": item.get("recommendation_confidence", ""),
                "recommended_buy_price": item.get("recommended_buy_price", ""),
                "recommended_sell_price": item.get("recommended_sell_price", ""),
                "recommended_buy_unit_price": item.get("recommended_buy_price", ""),
                "recommended_sell_unit_price": item.get("recommended_sell_price", ""),
                "recommended_sell_price_source": item.get("recommended_sell_price_source", ""),
                "vendor_sell_unit_price": item.get("vendor_sell_unit_price", ""),
                "auction_deposit_unit_price": item.get("auction_deposit_unit_price", ""),
                "estimated_profit_unit_price": item.get("estimated_profit_unit_price", ""),
                "price_trend_score": item.get("price_trend_score", ""),
                "price_trend_ratio": item.get("price_trend_ratio", ""),
                "best_buy_time": item.get("best_buy_time", ""),
                "best_sell_time": item.get("best_sell_time", ""),
                "historical_buy_price": item.get("historical_buy_price", ""),
                "historical_sell_price": item.get("historical_sell_price", ""),
                "historical_timing_confidence": item.get("historical_timing_confidence", ""),
            }
        )
    return export_rows


def _item_history_rows_for_export(item_history: dict[str, object]) -> list[dict[str, object]]:
    item = item_history.get("item", {})
    history = item_history.get("history", [])
    if not isinstance(item, dict) or not isinstance(history, list):
        return []

    export_rows: list[dict[str, object]] = []
    for row in history:
        if not isinstance(row, dict):
            continue
        export_rows.append(
            {
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "market": item.get("market"),
                "fetch_run_id": row.get("fetch_run_id"),
                "started_at": row.get("started_at"),
                "listing_count": row.get("listing_count"),
                "total_quantity": row.get("total_quantity"),
                "min_unit_price": row.get("min_unit_price"),
                "first_quartile_unit_price": row.get("first_quartile_unit_price"),
                "median_unit_price": row.get("median_unit_price"),
                "third_quartile_unit_price": row.get("third_quartile_unit_price"),
                "sell_through_ratio_bps": row.get("sell_through_ratio_bps"),
                "disappeared_quantity": row.get("disappeared_quantity"),
                "disappeared_value": row.get("disappeared_value"),
                "sell_through_confidence": row.get("sell_through_confidence"),
            }
        )
    return export_rows


def _recommendation_rows_for_export(recommendations: list[Recommendation]) -> list[dict[str, object]]:
    return [
        {
            "item_id": item.item_id,
            "name": item.name,
            "market": item.market,
            "action": item.action,
            "score": item.score,
            "confidence": item.confidence,
            "latest_min_unit_price": item.latest_min_unit_price,
            "latest_shifted_unit_price": item.latest_shifted_unit_price,
            "latest_median_unit_price": item.latest_median_unit_price,
            "recommended_buy_price": item.recommended_buy_price,
            "recommended_sell_price": item.recommended_sell_price,
            "recommended_buy_unit_price": item.recommended_buy_price,
            "recommended_sell_unit_price": item.recommended_sell_price,
            "recommended_sell_price_source": item.recommended_sell_price_source,
            "vendor_sell_unit_price": item.vendor_sell_unit_price,
            "auction_deposit_unit_price": item.auction_deposit_unit_price,
            "estimated_profit_unit_price": item.estimated_profit_unit_price,
            "average_first_quartile_unit_price": item.average_first_quartile_unit_price,
            "average_median_unit_price": item.average_median_unit_price,
            "average_third_quartile_unit_price": item.average_third_quartile_unit_price,
            "average_weighted_unit_price": item.average_weighted_unit_price,
            "price_trend_score": item.price_trend_score,
            "price_trend_ratio": item.price_trend_ratio,
            "estimated_demand_score": item.estimated_demand_score,
            "average_sell_through_ratio": item.average_sell_through_ratio,
            "average_sell_through_confidence": item.average_sell_through_confidence,
            "average_probable_sold_unit_price": item.average_probable_sold_unit_price,
            "player_post_count": item.player_post_count,
            "player_sold_count": item.player_sold_count,
            "player_expired_count": item.player_expired_count,
            "player_cancelled_count": item.player_cancelled_count,
            "player_sale_rate": item.player_sale_rate,
            "average_player_net_proceeds": item.average_player_net_proceeds,
            "best_buy_time": item.best_buy_time,
            "best_sell_time": item.best_sell_time,
            "historical_buy_price": item.historical_buy_price,
            "historical_sell_price": item.historical_sell_price,
            "historical_timing_confidence": item.historical_timing_confidence,
            "reasons": "; ".join(item.reasons),
        }
        for item in recommendations
    ]


def _craft_rows_for_export(overview: dict[str, object]) -> list[dict[str, object]]:
    rows = overview.get("craft_opportunities", [])
    if not isinstance(rows, list):
        return []

    export_rows: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        reasons = row.get("reasons")
        export_rows.append(
            {
                "recipe_id": row.get("recipe_id"),
                "recipe_name": row.get("recipe_name"),
                "output_item_id": row.get("output_item_id"),
                "output_name": row.get("output_name"),
                "output_market": row.get("output_market"),
                "output_quantity": row.get("output_quantity"),
                "craft_cost": row.get("craft_cost"),
                "craft_cost_unit_price": row.get("craft_cost_unit_price"),
                "output_min_unit_price": row.get("output_min_unit_price"),
                "sell_target_unit_price": row.get("sell_target_unit_price"),
                "auction_deposit_unit_price": row.get("auction_deposit_unit_price"),
                "ah_savings": row.get("ah_savings"),
                "expected_profit": row.get("expected_profit"),
                "max_craft_quantity": row.get("max_craft_quantity"),
                "confidence": row.get("confidence"),
                "reasons": "; ".join(str(reason) for reason in reasons) if isinstance(reasons, list) else "",
            }
        )
    return export_rows


def _player_performance_rows_for_export(overview: dict[str, object]) -> list[dict[str, object]]:
    activity = overview.get("player_activity", {})
    rows = activity.get("performance", []) if isinstance(activity, dict) else []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _database_size_label(database_url: str) -> str | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or url.database in {None, "", ":memory:"}:
        return None

    db_path = Path(url.database)
    if not db_path.exists():
        return None
    return _format_file_size(db_path.stat().st_size)


def _format_file_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def _format_copper(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value / 10000:.2f}g"


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 3600:
        return f"{round(seconds / 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _optional_export_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


if __name__ == "__main__":
    raise SystemExit(main())
