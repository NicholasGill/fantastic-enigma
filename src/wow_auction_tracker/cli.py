from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy.engine import make_url

from wow_auction_tracker.clients.blizzard import BlizzardClient
from wow_auction_tracker.config import load_config
from wow_auction_tracker.features.dashboard import DashboardConfig, serve_dashboard
from wow_auction_tracker.features.recommendations import Recommendation, RecommendationEngine
from wow_auction_tracker.features.scheduler import run_snapshot_schedule
from wow_auction_tracker.features.snapshots import FetchResult, fetch_and_store
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

    if args.command == "dashboard":
        serve_dashboard(
            DashboardConfig(
                database_url=args.database_url,
                host=args.host,
                port=args.port,
            )
        )
        return 0

    if args.command == "recommend":
        recommendations = RecommendationEngine(
            args.database_url,
            lookback_runs=args.lookback_runs,
            min_snapshots=args.min_snapshots,
        ).recommend(limit=args.limit)
        _print_recommendations(recommendations)
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
                lambda: fetch_and_store(config, client, repository),
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


def _print_recommendations(recommendations: list[Recommendation]) -> None:
    if not recommendations:
        print("No recommendations available")
        return

    print("Action  Score  Conf  Item ID  Name                 Min       Avg Price   Reasons")
    for item in recommendations:
        reasons = "; ".join(item.reasons)
        print(
            f"{item.action:<6}  "
            f"{item.score:>5}  "
            f"{item.confidence:>4}  "
            f"{item.item_id:<7}  "
            f"{item.name[:20]:<20} "
            f"{_format_copper(item.latest_min_unit_price):>9} "
            f"{_format_copper(item.average_weighted_unit_price or item.average_median_unit_price):>11}  "
            f"{reasons}"
        )


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


if __name__ == "__main__":
    raise SystemExit(main())
