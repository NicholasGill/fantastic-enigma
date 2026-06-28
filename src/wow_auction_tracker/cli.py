from __future__ import annotations

import argparse
import os
from pathlib import Path

from wow_auction_tracker.blizzard import BlizzardClient
from wow_auction_tracker.config import load_config
from wow_auction_tracker.db import AuctionRepository, create_db_engine, init_db
from wow_auction_tracker.pipeline import FetchResult, fetch_and_store
from wow_auction_tracker.scheduler import run_snapshot_schedule


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
                _print_fetch_result(result)
                return 0

            interval_seconds = args.interval_minutes * 60
            print(f"Starting scheduled snapshots every {args.interval_minutes} minute(s)")
            run_snapshot_schedule(
                lambda: fetch_and_store(config, client, repository),
                interval_seconds=interval_seconds,
                max_runs=args.max_runs,
                run_immediately=not args.no_run_immediately,
                on_success=_print_fetch_result,
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


def _print_fetch_result(result: FetchResult) -> None:
    print(
        "Stored fetch run "
        f"{result.fetch_run_id}: {result.listing_count} listings, "
        f"{result.summary_count} item summaries"
    )


if __name__ == "__main__":
    raise SystemExit(main())
