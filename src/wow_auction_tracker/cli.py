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
from wow_auction_tracker.features.lifecycle import build_listing_observations
from wow_auction_tracker.features.player import import_saved_variables
from wow_auction_tracker.features.recommendations import Recommendation, RecommendationEngine
from wow_auction_tracker.features.dashboard import DashboardDataStore
from wow_auction_tracker.features.scheduler import run_snapshot_schedule
from wow_auction_tracker.features.sellthrough import build_sell_through_metrics
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
    subparsers.add_parser("recompute-inference", help="Recompute derived listing lifecycle and sell-through rows.")
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

    if args.command == "dashboard":
        serve_dashboard(
            DashboardConfig(
                database_url=args.database_url,
                host=args.host,
                port=args.port,
                dev_mode=args.dev_mode,
                reload=args.reload,
                addon_saved_variables_path=args.addon_saved_variables,
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
        raise ValueError(f"unsupported export command {args.export_command}")

    if args.command == "import-addon":
        repository = AuctionRepository(engine)
        result = import_saved_variables(args.saved_variables)
        import_id = repository.import_addon_data(result)
        print(
            f"Imported addon data {import_id}: "
            f"{len(result.posts)} owned auction rows, {len(result.outcomes)} mail rows, "
            f"{len(result.purchases)} purchase rows"
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
        observations = build_listing_observations(
            listings,
            previous_listings,
            elapsed_seconds=_elapsed_seconds_between_runs(repository, previous_run_id, run_id),
        )
        metrics = build_sell_through_metrics(observations)
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
                "recommended_buy_price": recommendation["recommended_buy_price"] if recommendation else "",
                "recommended_sell_price": recommendation["recommended_sell_price"] if recommendation else "",
                "recommended_buy_unit_price": recommendation["recommended_buy_unit_price"] if recommendation else "",
                "recommended_sell_unit_price": recommendation["recommended_sell_unit_price"] if recommendation else "",
                "recommended_sell_price_source": recommendation["recommended_sell_price_source"] if recommendation else "",
                "vendor_sell_unit_price": recommendation["vendor_sell_unit_price"] if recommendation else "",
                "auction_deposit_unit_price": recommendation["auction_deposit_unit_price"] if recommendation else "",
                "estimated_profit_unit_price": recommendation["estimated_profit_unit_price"] if recommendation else "",
                "price_trend_score": recommendation["price_trend_score"] if recommendation else "",
                "price_trend_ratio": recommendation["price_trend_ratio"] if recommendation else "",
                "best_buy_time": recommendation["best_buy_time"] if recommendation else "",
                "best_sell_time": recommendation["best_sell_time"] if recommendation else "",
                "historical_buy_price": recommendation["historical_buy_price"] if recommendation else "",
                "historical_sell_price": recommendation["historical_sell_price"] if recommendation else "",
                "historical_timing_confidence": recommendation["historical_timing_confidence"] if recommendation else "",
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
