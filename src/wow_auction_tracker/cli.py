from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from wow_auction_tracker.clients.blizzard import BlizzardClient
from wow_auction_tracker.config import load_config
from wow_auction_tracker.features.dashboard import DashboardConfig, serve_dashboard
from wow_auction_tracker.features.recommendations import Recommendation, RecommendationEngine
from wow_auction_tracker.features.dashboard import DashboardDataStore
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

    if args.command == "report":
        store = DashboardDataStore(args.database_url)
        if args.report_command == "latest":
            _print_latest_report(store.overview(), args.limit)
            return 0
        if args.report_command == "item":
            _print_item_report(store.item_history(args.item_id))
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
            recommendations = RecommendationEngine(args.database_url).recommend(limit=args.limit)
            _write_csv(
                _recommendation_rows_for_export(recommendations),
                args.output,
                fieldnames=_RECOMMENDATION_EXPORT_FIELDNAMES,
            )
            return 0
        raise ValueError(f"unsupported export command {args.export_command}")

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

    print("Action  Score  Conf  Item ID  Name                 Min       Sell At    Reasons")
    for item in recommendations:
        reasons = "; ".join(item.reasons)
        print(
            f"{item.action:<6}  "
            f"{item.score:>5}  "
            f"{item.confidence:>4}  "
            f"{item.item_id:<7}  "
            f"{item.name[:20]:<20} "
            f"{_format_copper(item.latest_min_unit_price):>9} "
            f"{_format_copper(item.recommended_sell_price):>9}  "
            f"{reasons}"
        )


def _print_latest_report(overview: dict[str, object], limit: int) -> None:
    items = overview.get("items", [])
    if not isinstance(items, list) or not items:
        print("No item summaries available")
        return

    print("Item ID  Name                 Market      Listings  Quantity  Min       Q1        Median    Q3        Confidence  Action")
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        recommendation = _recommendation_for_item(overview, int(item["item_id"]))
        confidence = recommendation["confidence"] if recommendation else "-"
        action = recommendation["action"] if recommendation else "-"
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
    print("Run ID  Started At            Listings  Quantity  Min       Q1        Median    Q3")
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
    "recommended_sell_price",
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
    "latest_median_unit_price",
    "recommended_sell_price",
    "average_first_quartile_unit_price",
    "average_median_unit_price",
    "average_third_quartile_unit_price",
    "average_weighted_unit_price",
    "estimated_demand_score",
    "average_sell_through_ratio",
    "average_sell_through_confidence",
    "reasons",
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
        recommendation = _recommendation_for_item(overview, int(item["item_id"]))
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
                "recommendation_action": recommendation["action"] if recommendation else "",
                "recommendation_score": recommendation["score"] if recommendation else "",
                "recommendation_confidence": recommendation["confidence"] if recommendation else "",
                "recommended_sell_price": recommendation["recommended_sell_price"] if recommendation else "",
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
            "latest_median_unit_price": item.latest_median_unit_price,
            "recommended_sell_price": item.recommended_sell_price,
            "average_first_quartile_unit_price": item.average_first_quartile_unit_price,
            "average_median_unit_price": item.average_median_unit_price,
            "average_third_quartile_unit_price": item.average_third_quartile_unit_price,
            "average_weighted_unit_price": item.average_weighted_unit_price,
            "estimated_demand_score": item.estimated_demand_score,
            "average_sell_through_ratio": item.average_sell_through_ratio,
            "average_sell_through_confidence": item.average_sell_through_confidence,
            "reasons": "; ".join(item.reasons),
        }
        for item in recommendations
    ]


def _recommendation_for_item(overview: dict[str, object], item_id: int) -> dict[str, object] | None:
    recommendations = overview.get("recommendations", [])
    if not isinstance(recommendations, list):
        return None
    for recommendation in recommendations:
        if isinstance(recommendation, dict) and int(recommendation.get("item_id", -1)) == item_id:
            return recommendation
    return None


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
