from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request
from sqlalchemy.engine import make_url

from wow_auction_tracker.features.player import import_saved_variables, parse_auction_mail_subject
from wow_auction_tracker.features.recommendations import RecommendationEngine, recommendation_to_dict
from wow_auction_tracker.storage import AuctionRepository, create_db_engine

DEFAULT_DISPLAY_TIMEZONE = "America/New_York"
DASHBOARD_TIMEZONES = {
    "America/New_York": "Eastern",
    "UTC": "UTC",
    "America/Chicago": "Central",
    "America/Denver": "Mountain",
    "America/Los_Angeles": "Pacific",
    "America/Phoenix": "Arizona",
}


@dataclass(frozen=True)
class DashboardConfig:
    database_url: str
    host: str
    port: int
    dev_mode: bool = False
    reload: bool = False
    addon_saved_variables_path: Path | None = None


class DashboardDataStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.database_path = _sqlite_database_path(database_url)

    def overview(self, *, display_timezone: str = DEFAULT_DISPLAY_TIMEZONE, dev_mode: bool = False) -> dict[str, Any]:
        with self._connect() as connection:
            latest_run = connection.execute(
                """
                select id, started_at, finished_at, region, locale, connected_realm_id, status, error
                from fetch_runs
                order by id desc
                limit 1
                """
            ).fetchone()
            previous_run_id = None
            if latest_run is not None:
                previous = connection.execute(
                    """
                    select id
                    from fetch_runs
                    where id < ? and status = 'success'
                    order by id desc
                    limit 1
                    """,
                    (latest_run["id"],),
                ).fetchone()
                previous_run_id = previous["id"] if previous else None

            all_recommendations = [
                recommendation_to_dict(item)
                for item in RecommendationEngine(
                    self.database_url,
                    display_timezone=display_timezone,
                ).recommend()
            ]
            recommendations = all_recommendations[:8]
            recommendation_by_item_id = {
                int(item["item_id"]): item
                for item in all_recommendations
            }
            items = self._latest_items(connection, latest_run["id"] if latest_run else None, previous_run_id)
            for item in items:
                recommendation = recommendation_by_item_id.get(int(item["item_id"]))
                if recommendation is None:
                    continue
                item["recommended_buy_price"] = recommendation.get("recommended_buy_price")
                item["recommended_sell_price"] = recommendation.get("recommended_sell_price")
                item["recommended_sell_price_source"] = recommendation.get("recommended_sell_price_source")
                item["recommendation_action"] = recommendation.get("action")
                item["recommendation_score"] = recommendation.get("score")
                item["recommendation_confidence"] = recommendation.get("confidence")
                item["average_sell_through_ratio"] = recommendation.get("average_sell_through_ratio")
                item["average_sell_through_ratio_bps"] = round(
                    float(recommendation.get("average_sell_through_ratio") or 0) * 10000
                )
                item["average_probable_sold_unit_price"] = recommendation.get("average_probable_sold_unit_price")
                item["vendor_sell_unit_price"] = recommendation.get("vendor_sell_unit_price")
                item["auction_deposit_unit_price"] = recommendation.get("auction_deposit_unit_price")
                item["estimated_profit_unit_price"] = recommendation.get("estimated_profit_unit_price")
                item["price_trend_score"] = recommendation.get("price_trend_score")
                item["price_trend_ratio"] = recommendation.get("price_trend_ratio")
                item["best_buy_time"] = recommendation.get("best_buy_time")
                item["best_sell_time"] = recommendation.get("best_sell_time")
                item["historical_buy_price"] = recommendation.get("historical_buy_price")
                item["historical_sell_price"] = recommendation.get("historical_sell_price")
                item["historical_timing_confidence"] = recommendation.get("historical_timing_confidence")
                item["has_buy_opportunity"] = _has_buy_opportunity(
                    item.get("min_unit_price"),
                    recommendation.get("recommended_buy_price"),
                )
            if dev_mode:
                _apply_dev_buy_opportunities(items)
            items.sort(
                key=lambda item: (
                    int(item.get("recommendation_score") or 0),
                    int(item.get("recommendation_confidence") or 0),
                    int(item.get("average_sell_through_ratio_bps") or 0),
                    -int(item.get("min_unit_price") or 0),
                ),
                reverse=True,
            )

            return {
                "database": self._database_info(),
                "counts": self._table_counts(connection),
                "latest_run": dict(latest_run) if latest_run else None,
                "recent_runs": self._recent_runs(connection),
                "items": items,
                "latest_lifecycle": self._latest_lifecycle(connection, latest_run["id"] if latest_run else None),
                "recommendations": recommendations,
                "player_activity": self._player_activity(connection),
                "dev_mode": dev_mode,
            }

    def item_history(self, item_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            item = connection.execute(
                "select item_id, name, market from tracked_items where item_id = ?",
                (item_id,),
            ).fetchone()
            rows = connection.execute(
                """
                select
                    r.id as fetch_run_id,
                    r.started_at,
                    s.listing_count,
                    s.total_quantity,
                    s.min_unit_price,
                    s.median_unit_price,
                    s.first_quartile_unit_price,
                    s.third_quartile_unit_price,
                    st.sell_through_ratio_bps,
                    st.disappeared_quantity,
                    st.disappeared_value,
                    st.confidence as sell_through_confidence
                from item_summaries s
                join fetch_runs r on r.id = s.fetch_run_id
                left join sell_through_metrics st on st.fetch_run_id = s.fetch_run_id and st.item_id = s.item_id
                where s.item_id = ?
                order by r.id
                """,
                (item_id,),
            ).fetchall()

        return {
            "item": dict(item) if item else {"item_id": item_id, "name": None, "market": None},
            "history": [dict(row) for row in rows],
        }

    def import_addon_data(self, saved_variables_path: Path | None = None) -> dict[str, Any]:
        path = saved_variables_path or self._latest_addon_source_path()
        if path is None:
            raise ValueError("No addon SavedVariables path is configured or previously imported")
        if not path.exists():
            raise ValueError(f"Addon SavedVariables file does not exist: {path}")

        result = import_saved_variables(path)
        repository = AuctionRepository(create_db_engine(self.database_url))
        import_id = repository.import_addon_data(result)
        return {
            "import_id": import_id,
            "source_path": str(path),
            "owned_snapshot_count": len(result.posts),
            "mail_event_count": len(result.outcomes),
            "purchase_event_count": len(result.purchases),
        }

    def _latest_addon_source_path(self) -> Path | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                select source_path
                from addon_imports
                order by id desc
                limit 1
                """
            ).fetchone()
        if row is None or not row["source_path"]:
            return None
        return Path(str(row["source_path"]))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _database_info(self) -> dict[str, Any]:
        path = Path(self.database_path)
        size_bytes = path.stat().st_size if path.exists() else 0
        return {
            "path": str(path),
            "size_bytes": size_bytes,
            "size_label": format_file_size(size_bytes),
        }

    @staticmethod
    def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
        tables = (
            "fetch_runs",
            "tracked_items",
            "item_metadata",
            "auction_listings",
            "item_summaries",
            "item_history_metrics",
            "listing_observations",
            "sell_through_metrics",
            "buy_opportunity_observations",
            "addon_imports",
            "player_auction_posts",
            "player_auction_outcomes",
            "player_auction_purchases",
        )
        return {
            table: int(connection.execute(f"select count(*) from {table}").fetchone()[0])
            for table in tables
        }

    @staticmethod
    def _recent_runs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            select
                r.id,
                r.started_at,
                r.finished_at,
                r.status,
                r.connected_realm_id,
                count(distinct l.id) as listing_count,
                count(distinct s.id) as summary_count
            from fetch_runs r
            left join auction_listings l on l.fetch_run_id = r.id
            left join item_summaries s on s.fetch_run_id = r.id
            group by r.id
            order by r.id desc
            limit 12
            """
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _latest_lifecycle(connection: sqlite3.Connection, latest_run_id: int | None) -> dict[str, int]:
        if latest_run_id is None:
            return {}
        rows = connection.execute(
            """
            select status, count(*) as count
            from listing_observations
            where fetch_run_id = ?
            group by status
            order by status
            """,
            (latest_run_id,),
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    @staticmethod
    def _latest_items(
        connection: sqlite3.Connection,
        latest_run_id: int | None,
        previous_run_id: int | None,
    ) -> list[dict[str, Any]]:
        if latest_run_id is None:
            return []

        rows = connection.execute(
            """
            select
                s.item_id,
                coalesce(m.name, t.name, 'Item ' || s.item_id) as name,
                m.quality,
                m.item_class,
                m.item_subclass,
                m.icon_url,
                s.market,
                s.listing_count,
                s.total_quantity,
                s.min_unit_price,
                s.median_unit_price,
                s.first_quartile_unit_price,
                s.third_quartile_unit_price,
                st.sell_through_ratio_bps,
                st.disappeared_quantity,
                st.disappeared_value,
                st.confidence as sell_through_confidence,
                coalesce(
                    (
                        select ps.min_unit_price
                        from item_summaries ps
                        join fetch_runs pr on pr.id = ps.fetch_run_id
                        where ps.item_id = s.item_id
                            and ps.fetch_run_id < s.fetch_run_id
                            and pr.status = 'success'
                            and (
                                ps.min_unit_price is not s.min_unit_price
                                or (ps.min_unit_price is null and s.min_unit_price is not null)
                                or (ps.min_unit_price is not null and s.min_unit_price is null)
                            )
                        order by ps.fetch_run_id desc
                        limit 1
                    ),
                    p.min_unit_price
                ) as previous_min_unit_price,
                p.median_unit_price as previous_median_unit_price
            from item_summaries s
            left join tracked_items t on t.item_id = s.item_id
            left join item_metadata m on m.item_id = s.item_id
            left join sell_through_metrics st on st.fetch_run_id = s.fetch_run_id and st.item_id = s.item_id
            left join item_summaries p on p.item_id = s.item_id and p.fetch_run_id = ?
            where s.fetch_run_id = ?
            order by s.min_unit_price is null, s.min_unit_price, s.item_id
            """,
            (previous_run_id, latest_run_id),
        ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["crafting_quality"] = _latest_crafting_quality(
                connection,
                latest_run_id,
                int(item["item_id"]),
            ) or _crafting_quality_from_item_rank(connection, int(item["item_id"]))
        return items

    @staticmethod
    def _player_activity(connection: sqlite3.Connection) -> dict[str, Any]:
        latest_import = connection.execute(
            """
            select id, imported_at, source_path, owned_snapshot_count, mail_event_count, purchase_event_count
            from addon_imports
            order by id desc
            limit 1
            """
        ).fetchone()
        listings = connection.execute(
            """
            with ranked_posts as (
                select
                    p.*,
                    row_number() over (
                        partition by coalesce(cast(p.auction_id as text), 'row-' || cast(p.id as text))
                        order by p.observed_at desc, p.id desc
                    ) as post_rank
                from player_auction_posts p
            )
            select
                p.id,
                p.observed_at,
                p.reason,
                p.character,
                p.realm,
                p.auction_id,
                p.item_id,
                coalesce(m.name, t.name, 'Item ' || p.item_id) as name,
                p.quantity,
                p.unit_price,
                p.buyout,
                p.bid_amount,
                p.time_left_seconds,
                p.status
            from ranked_posts p
            left join item_metadata m on m.item_id = p.item_id
            left join tracked_items t on t.item_id = p.item_id
            where p.post_rank = 1
            order by p.observed_at desc, p.id desc
            limit 12
            """
        ).fetchall()
        outcomes = connection.execute(
            """
            select
                o.id,
                o.observed_at,
                o.character,
                o.realm,
                o.item_id,
                coalesce(m.name, o.item_name, t.name, 'Item ' || o.item_id) as name,
                o.item_count,
                o.outcome,
                o.money,
                o.raw_json
            from player_auction_outcomes o
            left join item_metadata m on m.item_id = o.item_id
            left join tracked_items t on t.item_id = o.item_id
            order by o.observed_at desc, o.id desc
            limit 12
            """
        ).fetchall()
        purchases = connection.execute(
            """
            select
                p.id,
                p.observed_at,
                p.event_type,
                p.character,
                p.realm,
                p.market,
                p.auction_id,
                p.item_id,
                coalesce(m.name, t.name, 'Item ' || p.item_id) as name,
                p.quantity,
                p.unit_price,
                p.total_price
            from player_auction_purchases p
            left join item_metadata m on m.item_id = p.item_id
            left join tracked_items t on t.item_id = p.item_id
            order by p.observed_at desc, p.id desc
            limit 12
            """
        ).fetchall()
        buy_opportunities = connection.execute(
            """
            select
                b.id,
                b.observed_at,
                b.fetch_run_id,
                b.item_id,
                coalesce(m.name, t.name, 'Item ' || b.item_id) as name,
                b.market,
                b.auction_id,
                b.unit_price,
                b.quantity,
                b.buy_target_unit_price,
                b.sell_target_unit_price,
                b.potential_profit,
                b.available_quantity_at_or_below_buy_target,
                b.recommendation_score,
                b.recommendation_confidence,
                b.listing_status
            from buy_opportunity_observations b
            left join item_metadata m on m.item_id = b.item_id
            left join tracked_items t on t.item_id = b.item_id
            order by b.observed_at desc, b.id desc
            limit 12
            """
        ).fetchall()
        summary = connection.execute(
            """
            select
                (select count(distinct coalesce(cast(auction_id as text), 'row-' || cast(id as text)))
                    from player_auction_posts) as listing_count,
                (select count(*) from player_auction_outcomes where outcome = 'sold') as sold_count,
                (select coalesce(sum(money), 0) from player_auction_outcomes where outcome = 'sold') as sold_money,
                (select count(*) from player_auction_purchases) as purchase_count,
                (select coalesce(sum(total_price), 0) from player_auction_purchases
                    where event_type in ('auction_purchase_completed', 'commodity_purchase_succeeded')
                ) as purchase_money,
                (select count(*) from buy_opportunity_observations) as buy_opportunity_count
            """
        ).fetchone()
        return {
            "latest_import": dict(latest_import) if latest_import else None,
            "summary": dict(summary) if summary else {},
            "listings": [dict(row) for row in listings],
            "outcomes": _enriched_player_outcomes(connection, outcomes),
            "purchases": [dict(row) for row in purchases],
            "buy_opportunities": [dict(row) for row in buy_opportunities],
        }


def create_dashboard_app(config: DashboardConfig) -> Flask:
    store = DashboardDataStore(config.database_url)
    app = Flask(__name__)

    @app.after_request
    def _disable_cache(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    @app.get("/my-auctions")
    @app.get("/market")
    def _index() -> Response:
        return Response(DASHBOARD_HTML, mimetype="text/html")

    @app.get("/api/overview")
    def _overview() -> Response:
        display_timezone = _dashboard_timezone(request.args.get("timezone", DEFAULT_DISPLAY_TIMEZONE))
        return jsonify(store.overview(display_timezone=display_timezone, dev_mode=config.dev_mode))

    @app.get("/api/history")
    def _history() -> Response | tuple[Response, int]:
        item_value = request.args.get("item_id")
        if not item_value:
            return jsonify({"error": "item_id is required"}), 400
        try:
            item_id = int(item_value)
        except ValueError:
            return jsonify({"error": "item_id must be an integer"}), 400
        return jsonify(store.item_history(item_id))

    @app.post("/api/import-addon")
    def _import_addon() -> Response | tuple[Response, int]:
        try:
            result = store.import_addon_data(config.addon_saved_variables_path)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    return app


def serve_dashboard(config: DashboardConfig) -> None:
    app = create_dashboard_app(config)
    print(f"Dashboard running at http://{config.host}:{config.port}")
    if config.reload:
        print("Dashboard reload mode enabled")
    app.run(
        host=config.host,
        port=config.port,
        debug=config.reload,
        use_reloader=config.reload,
        threaded=True,
    )


def format_file_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def _sqlite_database_path(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("dashboard currently requires a file-backed sqlite database")
    return url.database


def _dashboard_timezone(value: str) -> str:
    return value if value in DASHBOARD_TIMEZONES else DEFAULT_DISPLAY_TIMEZONE


def _enriched_player_outcomes(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> list[dict[str, Any]]:
    name_to_item_id = _item_ids_by_name(connection)
    sold_quantity_to_item_id = _item_ids_by_name_and_sold_quantity(connection)
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        outcome = dict(row)
        raw_json = str(outcome.pop("raw_json") or "{}")
        subject_name, subject_count = _auction_mail_subject_item_from_raw_json(raw_json)
        if outcome.get("name") is None and subject_name:
            outcome["name"] = subject_name
        if outcome.get("item_count") is None and subject_count is not None:
            outcome["item_count"] = subject_count
        if outcome.get("item_id") is None and subject_name:
            subject_key = _item_name_key(subject_name)
            quantity = outcome.get("item_count")
            if quantity is not None:
                outcome["item_id"] = sold_quantity_to_item_id.get((subject_key, int(quantity)))
            if outcome.get("item_id") is None:
                outcome["item_id"] = name_to_item_id.get(subject_key)
        outcomes.append(outcome)
    return outcomes


def _item_ids_by_name_and_sold_quantity(connection: sqlite3.Connection) -> dict[tuple[str, int], int]:
    rows = connection.execute(
        """
        select
            p.auction_id,
            p.observed_at,
            p.id,
            p.item_id,
            coalesce(m.name, t.name) as name,
            p.quantity
        from player_auction_posts p
        left join item_metadata m on m.item_id = p.item_id
        left join tracked_items t on t.item_id = p.item_id
        where p.auction_id is not null
            and p.item_id is not null
            and p.quantity is not null
            and coalesce(m.name, t.name) is not null
        order by p.auction_id, p.observed_at, p.id
        """
    ).fetchall()
    previous_by_auction: dict[int, tuple[str, int, int]] = {}
    mapping: dict[tuple[str, int], int] = {}
    ambiguous: set[tuple[str, int]] = set()
    seen_rows: set[tuple[int, str, int]] = set()
    for row in rows:
        auction_id = int(row["auction_id"])
        quantity = int(row["quantity"])
        observed_at = str(row["observed_at"])
        row_key = (auction_id, observed_at, quantity)
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)

        name = _item_name_key(str(row["name"]))
        item_id = int(row["item_id"])
        previous = previous_by_auction.get(auction_id)
        if previous is not None:
            previous_name, previous_item_id, previous_quantity = previous
            sold_quantity = previous_quantity - quantity
            if sold_quantity > 0 and previous_name == name and previous_item_id == item_id:
                key = (name, sold_quantity)
                if key in mapping and mapping[key] != item_id:
                    ambiguous.add(key)
                else:
                    mapping[key] = item_id
        previous_by_auction[auction_id] = (name, item_id, quantity)

    for key in ambiguous:
        mapping.pop(key, None)
    return mapping


def _item_ids_by_name(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """
        select coalesce(m.name, t.name) as name, t.item_id
        from tracked_items t
        left join item_metadata m on m.item_id = t.item_id
        where coalesce(m.name, t.name) is not null
        order by t.item_id
        """
    ).fetchall()
    mapping: dict[str, int] = {}
    ambiguous: set[str] = set()
    for row in rows:
        key = _item_name_key(str(row["name"]))
        item_id = int(row["item_id"])
        if key in mapping and mapping[key] != item_id:
            ambiguous.add(key)
            continue
        mapping[key] = item_id
    for key in ambiguous:
        mapping.pop(key, None)
    return mapping


def _auction_mail_subject_item_from_raw_json(raw_json: str) -> tuple[str | None, int | None]:
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        return (None, None)
    subject = raw.get("subject")
    if not isinstance(subject, str):
        return (None, None)
    return parse_auction_mail_subject(subject)


def _item_name_key(name: str) -> str:
    return " ".join(name.casefold().split())


def _has_buy_opportunity(min_unit_price: object, recommended_buy_price: object) -> bool:
    if min_unit_price is None or recommended_buy_price is None:
        return False
    return int(min_unit_price) < int(recommended_buy_price)


def _apply_dev_buy_opportunities(items: list[dict[str, Any]], *, limit: int = 3) -> None:
    decorated = 0
    for item in items:
        min_price = item.get("min_unit_price")
        if min_price is None:
            continue

        min_price_int = int(min_price)
        buy_price = item.get("recommended_buy_price")
        if buy_price is None or int(buy_price) <= min_price_int:
            item["recommended_buy_price"] = max(min_price_int + 1, round(min_price_int * 1.12))

        item["has_buy_opportunity"] = True
        item["dev_buy_opportunity"] = True
        decorated += 1
        if decorated >= limit:
            return


def _latest_crafting_quality(
    connection: sqlite3.Connection,
    fetch_run_id: int,
    item_id: int,
) -> str | None:
    rows = connection.execute(
        """
        select raw_json
        from auction_listings
        where fetch_run_id = ? and item_id = ?
        limit 250
        """,
        (fetch_run_id, item_id),
    ).fetchall()
    qualities = {
        quality
        for row in rows
        if (quality := _crafting_quality_from_raw_json(str(row["raw_json"]))) is not None
    }
    if not qualities:
        return None
    if len(qualities) == 1:
        return next(iter(qualities))
    return "mixed"


def _crafting_quality_from_raw_json(raw_json: str) -> str | None:
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    item = raw.get("item")
    if not isinstance(item, dict):
        return None

    for key in ("crafting_quality", "crafted_quality"):
        quality = _quality_value(item.get(key))
        if quality is not None:
            return quality

    modifiers = item.get("modifiers")
    if isinstance(modifiers, list):
        for modifier in modifiers:
            if not isinstance(modifier, dict):
                continue
            modifier_type = str(modifier.get("type", "")).lower()
            if "craft" in modifier_type and "quality" in modifier_type:
                quality = _quality_value(modifier.get("value"))
                if quality is not None:
                    return quality

    return None


def _crafting_quality_from_item_rank(connection: sqlite3.Connection, item_id: int) -> str | None:
    item = connection.execute(
        """
        select coalesce(m.name, t.name) as name
        from tracked_items t
        left join item_metadata m on m.item_id = t.item_id
        where t.item_id = ?
        """,
        (item_id,),
    ).fetchone()
    if item is None or item["name"] is None:
        return None

    sibling_rows = connection.execute(
        """
        select t.item_id
        from tracked_items t
        left join item_metadata m on m.item_id = t.item_id
        where coalesce(m.name, t.name) = ?
        order by t.item_id
        """,
        (item["name"],),
    ).fetchall()
    sibling_ids = [int(row["item_id"]) for row in sibling_rows]
    if len(sibling_ids) < 2 or item_id not in sibling_ids:
        return None

    rank = sibling_ids.index(item_id) + 1
    return str(rank) if 1 <= rank <= 5 else None


def _quality_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return _quality_value(value.get("value") or value.get("id") or value.get("name"))
    text = str(value).strip()
    if not text:
        return None
    if text.lower().startswith("quality "):
        text = text.split(" ", 1)[1]
    return text


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WoW Auction Tracker</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --surface: #ffffff;
      --surface-2: #edf1f5;
      --text: #18202a;
      --muted: #5e6b78;
      --line: #d7dee7;
      --accent: #176b87;
      --accent-2: #8a5a1f;
      --good: #237a57;
      --bad: #b2413a;
      --shadow: 0 1px 2px rgba(18, 28, 38, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0;
    }
    button {
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      height: 36px;
      padding: 0 12px;
      border-radius: 6px;
      cursor: pointer;
    }
    button:hover { border-color: var(--accent); }
    main {
      padding: 18px 24px 28px;
    }
    .tab-list {
      display: flex;
      gap: 8px;
      margin-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }
    .tab-button {
      border: 0;
      border-bottom: 3px solid transparent;
      border-radius: 0;
      background: transparent;
      color: var(--muted);
      font-weight: 700;
    }
    .tab-button.active {
      color: var(--text);
      border-bottom-color: var(--accent);
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .market-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 18px;
    }
    .player-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }
    .player-grid section:first-child {
      grid-column: 1 / -1;
    }
    section {
      min-width: 0;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 52px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    h2 {
      margin: 0;
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .metric {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      box-shadow: var(--shadow);
      min-height: 86px;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .metric strong {
      display: block;
      margin-top: 8px;
      font-size: 22px;
      line-height: 1.1;
    }
    .table-wrap { overflow: auto; max-height: calc(100vh - 260px); }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 880px;
    }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: right;
      white-space: nowrap;
    }
    th {
      position: sticky;
      top: 0;
      background: var(--surface-2);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .03em;
      z-index: 2;
    }
    .column-help {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      margin-left: 4px;
      border: 1px solid var(--line);
      border-radius: 50%;
      color: var(--muted);
      background: var(--surface);
      font-size: 11px;
      font-weight: 700;
      line-height: 1;
      text-transform: none;
      cursor: help;
      vertical-align: middle;
    }
    .column-help:focus {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    td:first-child, th:first-child { text-align: left; }
    .item-cell {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .item-icon {
      width: 24px;
      height: 24px;
      border-radius: 4px;
      border: 1px solid var(--line);
      flex: 0 0 auto;
    }
    .item-meta {
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .item-meta small {
      color: var(--muted);
      font-size: 11px;
    }
    .quality-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 74px;
      height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface-2);
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .quality-1 { color: #4f5965; background: #f1f3f5; }
    .quality-2 { color: #237a57; background: #e6f2ed; }
    .quality-3 { color: #176b87; background: #e7f2f7; }
    .quality-4 { color: #7b4bb3; background: #f0eafa; }
    .quality-5 { color: #9a5a14; background: #f7ede0; }
    tr { cursor: pointer; }
    tbody tr:hover { background: #f8fafc; }
    tr.buy-opportunity { background: #e8f6ee; }
    tr.buy-opportunity:hover { background: #dcefe5; }
    tr.buy-opportunity td:first-child { border-left: 4px solid var(--good); }
    tr.selected { background: #e8f3f6; }
    tr.buy-opportunity.selected { background: #d8eee6; }
    .stack {
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .side-body { padding: 14px; }
    canvas {
      width: 100%;
      height: 260px;
      display: block;
    }
    .runs {
      display: grid;
      gap: 8px;
      padding: 14px;
      max-height: 310px;
      overflow: auto;
    }
    .run-row {
      display: grid;
      grid-template-columns: 52px 1fr auto;
      gap: 8px;
      align-items: center;
      padding: 8px 0;
      border-bottom: 1px solid var(--line);
    }
    .recommendations {
      display: grid;
      gap: 10px;
      padding: 14px;
      max-height: 360px;
      overflow: auto;
    }
    .mini-table-wrap {
      overflow: auto;
      max-height: 420px;
    }
    .mini-table {
      min-width: 760px;
    }
    .mini-table th,
    .mini-table td {
      padding: 8px 10px;
      font-size: 12px;
    }
    .recommendation {
      border-bottom: 1px solid var(--line);
      padding-bottom: 10px;
      cursor: pointer;
    }
    .recommendation:hover { background: #f8fafc; }
    .recommendation.selected { background: #e8f3f6; }
    .recommendation:last-child { border-bottom: 0; padding-bottom: 0; }
    .recommendation-head {
      display: grid;
      grid-template-columns: 56px 1fr auto;
      gap: 8px;
      align-items: center;
    }
    .recommendation strong {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .score {
      font-weight: 700;
      color: var(--accent);
    }
    .reasons {
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 64px;
      height: 24px;
      border-radius: 999px;
      background: #e6f2ed;
      color: var(--good);
      font-size: 12px;
      font-weight: 700;
    }
    .muted { color: var(--muted); }
    .delta-up { color: var(--bad); }
    .delta-down { color: var(--good); }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .refresh-status {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .auto-refresh {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .auto-refresh input {
      width: 16px;
      min-width: 16px;
      height: 16px;
      margin: 0;
    }
    .dev-mode {
      display: none;
      align-items: center;
      height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      background: #e6f2ed;
      color: var(--good);
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    body.dev-mode-active .dev-mode { display: inline-flex; }
    .dev-marker {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 18px;
      margin-left: 6px;
      padding: 0 6px;
      border-radius: 999px;
      background: #dcefe5;
      color: var(--good);
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      vertical-align: middle;
    }
    .source-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 18px;
      margin-left: 6px;
      padding: 0 6px;
      border-radius: 999px;
      background: #edf1f5;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      vertical-align: middle;
    }
    .source-probable-sold {
      background: #dcefe5;
      color: var(--good);
    }
    input, select {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      background: var(--surface);
    }
    input {
      min-width: 220px;
    }
    select {
      min-width: 132px;
    }
    @media (max-width: 980px) {
      main { padding: 14px; }
      .market-layout, .player-grid { grid-template-columns: 1fr; }
      .player-grid section:first-child { grid-column: auto; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      header { padding: 14px; align-items: flex-start; flex-direction: column; }
      .toolbar { width: 100%; }
      input { flex: 1; min-width: 0; }
    }
  </style>
</head>
<body>
  <header>
    <h1>WoW Auction Tracker</h1>
    <div class="toolbar">
      <input id="filter" type="search" placeholder="Filter items">
      <select id="timezone" aria-label="Recommendation timing timezone">
        <option value="America/New_York" selected>Eastern</option>
        <option value="UTC">UTC</option>
        <option value="America/Chicago">Central</option>
        <option value="America/Denver">Mountain</option>
        <option value="America/Los_Angeles">Pacific</option>
        <option value="America/Phoenix">Arizona</option>
      </select>
      <label class="auto-refresh"><input id="auto-refresh" type="checkbox" checked>Auto 30s</label>
      <span class="dev-mode" id="dev-mode-status">Dev Mode</span>
      <span class="refresh-status" id="refresh-status">Not refreshed</span>
      <button id="import-addon" type="button">Import Addon</button>
      <button id="refresh" type="button">Refresh</button>
    </div>
  </header>
  <main>
    <div class="grid">
      <div class="metric"><span>Database</span><strong id="db-size">-</strong></div>
      <div class="metric"><span>Fetch Runs</span><strong id="fetch-runs">-</strong></div>
      <div class="metric"><span>Listings</span><strong id="listings">-</strong></div>
      <div class="metric"><span>Latest Run</span><strong id="latest-run">-</strong></div>
      <div class="metric"><span>New Listings</span><strong id="new-listings">-</strong></div>
      <div class="metric"><span>Missing Listings</span><strong id="missing-listings">-</strong></div>
      <div class="metric"><span>My Listings</span><strong id="my-listings">-</strong></div>
      <div class="metric"><span>My Buys</span><strong id="my-buys">-</strong></div>
      <div class="metric"><span>Buy Signals</span><strong id="buy-signals">-</strong></div>
    </div>

    <div class="tab-list" role="tablist" aria-label="Dashboard views">
      <button class="tab-button active" id="market-tab" type="button" role="tab" aria-selected="true" aria-controls="market-panel" data-tab="market">Market</button>
      <button class="tab-button" id="player-tab" type="button" role="tab" aria-selected="false" aria-controls="player-panel" data-tab="player">My Auctions</button>
    </div>

    <div class="tab-panel active" id="market-panel" role="tabpanel" aria-labelledby="market-tab" data-panel="market">
      <div class="market-layout">
        <div>
          <section>
            <div class="section-head">
              <h2>Latest Item Summaries</h2>
              <span class="muted" id="latest-time">-</span>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Item<span class="column-help" tabindex="0" title="Tracked auction item name and Blizzard item class." aria-label="Tracked auction item name and Blizzard item class.">?</span></th>
                    <th>Crafting Quality<span class="column-help" tabindex="0" title="Crafting quality from auction listing modifiers when Blizzard includes it. Shows a dash when the auction payload does not include crafting quality." aria-label="Crafting quality from auction listing modifiers when Blizzard includes it. Shows a dash when the auction payload does not include crafting quality.">?</span></th>
                    <th>ID<span class="column-help" tabindex="0" title="Blizzard item ID from the configured tracked item list." aria-label="Blizzard item ID from the configured tracked item list.">?</span></th>
                    <th>Min / Unit<span class="column-help" tabindex="0" title="Lowest per-unit price currently listed in the latest snapshot." aria-label="Lowest per-unit price currently listed in the latest snapshot.">?</span></th>
                    <th>Buy / Unit<span class="column-help" tabindex="0" title="Recommended maximum per-unit buy price, targeting margin against the conservative per-unit sell price." aria-label="Recommended maximum per-unit buy price, targeting margin against the conservative per-unit sell price.">?</span></th>
                    <th>Sell / Unit<span class="column-help" tabindex="0" title="Recommended per-unit sell price from inferred sold listings, including disappeared listings classified as probable sales and listings whose quantity decreased." aria-label="Recommended per-unit sell price from inferred sold listings, including disappeared listings classified as probable sales and listings whose quantity decreased.">?</span></th>
                    <th>Deposit / Unit<span class="column-help" tabindex="0" title="Estimated 48-hour auction deposit per unit from Blizzard vendor sell price metadata." aria-label="Estimated 48-hour auction deposit per unit from Blizzard vendor sell price metadata.">?</span></th>
                    <th>Profit / Unit<span class="column-help" tabindex="0" title="Potential per-unit profit after subtracting estimated 48-hour auction deposit from recommended sell price minus recommended buy price." aria-label="Potential per-unit profit after subtracting estimated 48-hour auction deposit from recommended sell price minus recommended buy price.">?</span></th>
                    <th>Trend<span class="column-help" tabindex="0" title="Recent price trend score from historical per-unit prices. 50 is flat, above 50 is rising, below 50 is falling." aria-label="Recent price trend score from historical per-unit prices. 50 is flat, above 50 is rising, below 50 is falling.">?</span></th>
                    <th>Sell-through<span class="column-help" tabindex="0" title="Estimated demand signal from recent disappeared listings; this is inferred and not confirmed sales." aria-label="Estimated demand signal from recent disappeared listings; this is inferred and not confirmed sales.">?</span></th>
                    <th>Min Change<span class="column-help" tabindex="0" title="Change from the latest minimum price to the last different previous minimum price." aria-label="Change from the latest minimum price to the last different previous minimum price.">?</span></th>
                    <th>Listings<span class="column-help" tabindex="0" title="Number of active auction listings found for this item in the latest snapshot." aria-label="Number of active auction listings found for this item in the latest snapshot.">?</span></th>
                    <th>Quantity<span class="column-help" tabindex="0" title="Total quantity available across latest active listings for this item." aria-label="Total quantity available across latest active listings for this item.">?</span></th>
                  </tr>
                </thead>
                <tbody id="items"></tbody>
              </table>
            </div>
          </section>
        </div>
        <div class="stack">
          <section>
            <div class="section-head"><h2 id="chart-title">Price History</h2></div>
            <div class="side-body">
              <canvas id="history" width="640" height="320"></canvas>
              <div class="muted" id="chart-note">Select an item to inspect snapshot history.</div>
            </div>
          </section>
          <section>
            <div class="section-head"><h2>Recommendations</h2></div>
            <div class="recommendations" id="recommendations"></div>
          </section>
          <section>
            <div class="section-head"><h2>Recent Runs</h2></div>
            <div class="runs" id="runs"></div>
          </section>
        </div>
      </div>
    </div>

    <div class="tab-panel" id="player-panel" role="tabpanel" aria-labelledby="player-tab" data-panel="player">
      <div class="player-grid">
        <section>
          <div class="section-head">
            <h2>My Listings</h2>
            <span class="muted" id="my-listings-note">-</span>
          </div>
          <div class="mini-table-wrap">
            <table class="mini-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Qty</th>
                  <th>Unit</th>
                  <th>Buyout</th>
                  <th>Seen</th>
                </tr>
              </thead>
              <tbody id="my-listings-table"></tbody>
            </table>
          </div>
        </section>
        <section>
          <div class="section-head"><h2>My Buys</h2></div>
          <div class="mini-table-wrap">
            <table class="mini-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Qty</th>
                  <th>Unit</th>
                  <th>Total</th>
                  <th>Event</th>
                  <th>Seen</th>
                </tr>
              </thead>
              <tbody id="my-buys-table"></tbody>
            </table>
          </div>
        </section>
        <section>
          <div class="section-head"><h2>Buy Signals</h2></div>
          <div class="mini-table-wrap">
            <table class="mini-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Qty</th>
                  <th>Unit</th>
                  <th>Target</th>
                  <th>Profit</th>
                </tr>
              </thead>
              <tbody id="buy-signals-table"></tbody>
            </table>
          </div>
        </section>
        <section>
          <div class="section-head"><h2>Auction Outcomes</h2></div>
          <div class="mini-table-wrap">
            <table class="mini-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Qty</th>
                  <th>Outcome</th>
                  <th>Money</th>
                  <th>Seen</th>
                </tr>
              </thead>
              <tbody id="auction-outcomes-table"></tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  </main>
  <script>
    const AUTO_REFRESH_MS = 30000;
    let overview = null;
    let selectedItemId = null;
    let isLoadingOverview = false;
    let autoRefreshTimer = null;

    const els = {
      dbSize: document.getElementById('db-size'),
      fetchRuns: document.getElementById('fetch-runs'),
      listings: document.getElementById('listings'),
      latestRun: document.getElementById('latest-run'),
      newListings: document.getElementById('new-listings'),
      missingListings: document.getElementById('missing-listings'),
      myListings: document.getElementById('my-listings'),
      myBuys: document.getElementById('my-buys'),
      buySignals: document.getElementById('buy-signals'),
      latestTime: document.getElementById('latest-time'),
      items: document.getElementById('items'),
      recommendations: document.getElementById('recommendations'),
      myListingsNote: document.getElementById('my-listings-note'),
      myListingsTable: document.getElementById('my-listings-table'),
      myBuysTable: document.getElementById('my-buys-table'),
      buySignalsTable: document.getElementById('buy-signals-table'),
      auctionOutcomesTable: document.getElementById('auction-outcomes-table'),
      runs: document.getElementById('runs'),
      filter: document.getElementById('filter'),
      timezone: document.getElementById('timezone'),
      autoRefresh: document.getElementById('auto-refresh'),
      devModeStatus: document.getElementById('dev-mode-status'),
      importAddon: document.getElementById('import-addon'),
      refresh: document.getElementById('refresh'),
      tabButtons: Array.from(document.querySelectorAll('[data-tab]')),
      tabPanels: Array.from(document.querySelectorAll('[data-panel]')),
      chart: document.getElementById('history'),
      chartTitle: document.getElementById('chart-title'),
      chartNote: document.getElementById('chart-note'),
      refreshStatus: document.getElementById('refresh-status')
    };

    function gold(copper) {
      if (copper === null || copper === undefined) return '-';
      return `${(copper / 10000).toLocaleString(undefined, { maximumFractionDigits: 2 })}g`;
    }

    function integer(value) {
      return Number(value || 0).toLocaleString();
    }

    function itemName(value) {
      return value || 'Unknown';
    }

    function bps(value) {
      if (value === null || value === undefined) return '-';
      return `${(value / 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
    }

    function qualityLabel(value) {
      if (!value) return '-';
      if (String(value).toLowerCase() === 'mixed') return 'mixed';
      return String(value).replaceAll('_', ' ').toLowerCase();
    }

    function qualityClass(value) {
      return `quality-${String(value || 'unknown').toLowerCase().replaceAll('_', '-')}`;
    }

    function shortTime(value) {
      if (!value) return '-';
      return new Date(value.replace(' ', 'T') + 'Z').toLocaleString();
    }

    function delta(current, previous) {
      if (current === null || current === undefined || previous === null || previous === undefined) {
        return { text: '-', cls: 'muted' };
      }
      const diff = current - previous;
      if (diff === 0) return { text: '0g', cls: 'muted' };
      return { text: `${diff > 0 ? '+' : ''}${gold(diff)}`, cls: diff > 0 ? 'delta-up' : 'delta-down' };
    }

    function profit(value) {
      if (value === null || value === undefined) {
        return { text: '-', cls: 'muted' };
      }
      return { text: gold(value), cls: value > 0 ? 'delta-down' : value < 0 ? 'delta-up' : 'muted' };
    }

    function trend(value) {
      if (value === null || value === undefined) {
        return { text: '-', cls: 'muted' };
      }
      const cls = value > 55 ? 'delta-down' : value < 45 ? 'delta-up' : 'muted';
      return { text: integer(value), cls };
    }

    function sellSourceLabel(source) {
      const labels = {
        probable_sold: 'sold'
      };
      return labels[source] || source || '';
    }

    function sellSourceBadge(source) {
      const label = sellSourceLabel(source);
      if (!label) return '';
      const cls = source === 'probable_sold' ? ' source-probable-sold' : '';
      return `<span class="source-badge${cls}" title="Sell At source: ${label}">${label}</span>`;
    }

    async function loadOverview({ refreshSelected = true } = {}) {
      if (isLoadingOverview) return;
      isLoadingOverview = true;
      els.refresh.disabled = true;
      try {
        const timezone = encodeURIComponent(els.timezone.value);
        const response = await fetch(`/api/overview?timezone=${timezone}&t=${Date.now()}`, { cache: 'no-store' });
        overview = await response.json();
        renderOverview();
        els.refreshStatus.textContent = `Refreshed ${new Date().toLocaleTimeString()}`;
        const first = overview.items[0];
        if (!selectedItemId && first) {
          await selectItem(first.item_id);
        } else if (refreshSelected && selectedItemId) {
          await loadItemHistory(selectedItemId);
        }
      } catch (error) {
        els.refreshStatus.textContent = 'Refresh failed';
      } finally {
        els.refresh.disabled = false;
        isLoadingOverview = false;
      }
    }

    async function importAddonData() {
      els.importAddon.disabled = true;
      els.refreshStatus.textContent = 'Importing addon...';
      try {
        const response = await fetch(`/api/import-addon?t=${Date.now()}`, {
          method: 'POST',
          cache: 'no-store'
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || 'Import failed');
        }
        els.refreshStatus.textContent = `Imported ${integer(payload.owned_snapshot_count)} listings, ${integer(payload.mail_event_count)} mail rows, ${integer(payload.purchase_event_count)} purchase rows`;
        await loadOverview();
      } catch (error) {
        els.refreshStatus.textContent = error.message || 'Import failed';
      } finally {
        els.importAddon.disabled = false;
      }
    }

    function renderOverview() {
      els.dbSize.textContent = overview.database.size_label;
      els.fetchRuns.textContent = integer(overview.counts.fetch_runs);
      els.listings.textContent = integer(overview.counts.auction_listings);
      els.latestRun.textContent = overview.latest_run ? `#${overview.latest_run.id}` : '-';
      els.newListings.textContent = integer(overview.latest_lifecycle.new);
      els.missingListings.textContent = integer(overview.latest_lifecycle.missing);
      els.myListings.textContent = integer(overview.player_activity?.summary?.listing_count);
      els.myBuys.textContent = integer(overview.player_activity?.summary?.purchase_count);
      els.buySignals.textContent = integer(overview.player_activity?.summary?.buy_opportunity_count);
      els.latestTime.textContent = overview.latest_run ? shortTime(overview.latest_run.finished_at) : '-';
      document.body.classList.toggle('dev-mode-active', Boolean(overview.dev_mode));
      renderItems();
      renderRecommendations();
      renderPlayerActivity();
      renderRuns();
    }

    function renderItems() {
      const query = els.filter.value.trim().toLowerCase();
      const rows = overview.items.filter((item) => {
        return !query || item.name.toLowerCase().includes(query) || String(item.item_id).includes(query);
      });
      els.items.innerHTML = rows.map((item) => {
        const change = delta(item.min_unit_price, item.previous_min_unit_price);
        const potentialProfit = profit(item.estimated_profit_unit_price);
        const priceTrend = trend(item.price_trend_score);
        const sellThroughBps = item.average_sell_through_ratio_bps ?? item.sell_through_ratio_bps;
        const rowClasses = [
          item.item_id === selectedItemId ? 'selected' : '',
          item.has_buy_opportunity ? 'buy-opportunity' : ''
        ].filter(Boolean).join(' ');
        const classAttribute = rowClasses ? ` class="${rowClasses}"` : '';
        const icon = item.icon_url ? `<img class="item-icon" src="${item.icon_url}" alt="">` : '';
        const subtitle = [item.item_class, item.item_subclass].filter(Boolean).join(' / ');
        const devMarker = item.dev_buy_opportunity ? '<span class="dev-marker">Dev</span>' : '';
        return `<tr${classAttribute} data-item-id="${item.item_id}">
          <td><span class="item-cell">${icon}<span class="item-meta"><span>${item.name}${devMarker}</span><small>${subtitle}</small></span></span></td>
          <td><span class="quality-badge ${qualityClass(item.crafting_quality)}">${qualityLabel(item.crafting_quality)}</span></td>
          <td>${item.item_id}</td>
          <td>${gold(item.min_unit_price)}</td>
          <td>${gold(item.recommended_buy_price)}</td>
          <td>${gold(item.recommended_sell_price)}${sellSourceBadge(item.recommended_sell_price_source)}</td>
          <td>${gold(item.auction_deposit_unit_price)}</td>
          <td class="${potentialProfit.cls}">${potentialProfit.text}</td>
          <td class="${priceTrend.cls}">${priceTrend.text}</td>
          <td>${bps(sellThroughBps)}</td>
          <td class="${change.cls}">${change.text}</td>
          <td>${integer(item.listing_count)}</td>
          <td>${integer(item.total_quantity)}</td>
        </tr>`;
      }).join('');
      els.items.querySelectorAll('tr').forEach((row) => {
        row.addEventListener('click', () => selectItem(Number(row.dataset.itemId)));
      });
    }

    function renderRuns() {
      els.runs.innerHTML = overview.recent_runs.map((run) => {
        return `<div class="run-row">
          <strong>#${run.id}</strong>
          <span><span class="pill">${run.status}</span> <span class="muted">${shortTime(run.finished_at)}</span></span>
          <span class="muted">${integer(run.listing_count)} listings</span>
        </div>`;
      }).join('');
    }

    function renderRecommendations() {
      const rows = overview.recommendations || [];
      if (!rows.length) {
        els.recommendations.innerHTML = '<div class="muted">No recommendations available yet.</div>';
        return;
      }
      els.recommendations.innerHTML = rows.map((item) => {
        const selected = item.item_id === selectedItemId ? ' selected' : '';
        const timing = item.best_buy_time && item.best_sell_time
          ? `<div class="reasons">Best buy: ${item.best_buy_time} near ${gold(item.historical_buy_price)}; best sell: ${item.best_sell_time} near ${gold(item.historical_sell_price)} (${item.historical_timing_confidence}% timing confidence)</div>`
          : '';
        return `<div class="recommendation${selected}" data-item-id="${item.item_id}">
          <div class="recommendation-head">
            <span class="pill">${item.action}</span>
            <strong title="${item.name}">${item.name}</strong>
            <span class="score">${item.score}</span>
          </div>
          <div class="reasons">${gold(item.recommended_buy_price)} buy, ${gold(item.recommended_sell_price)} sell (${sellSourceLabel(item.recommended_sell_price_source) || 'unknown'}), ${gold(item.auction_deposit_unit_price)} deposit, ${gold(item.estimated_profit_unit_price)} net, ${item.price_trend_score} trend, ${item.confidence}% confidence</div>
          ${timing}
          <div class="reasons">${item.reasons.join('; ')}</div>
        </div>`;
      }).join('');
      els.recommendations.querySelectorAll('.recommendation').forEach((row) => {
        row.addEventListener('click', () => selectItem(Number(row.dataset.itemId)));
      });
    }

    function renderPlayerActivity() {
      const activity = overview.player_activity || {};
      const latestImport = activity.latest_import;
      els.myListingsNote.textContent = latestImport ? shortTime(latestImport.imported_at) : 'No imports';

      const listings = activity.listings || [];
      els.myListingsTable.innerHTML = listings.length ? listings.map((row) => {
        return `<tr>
          <td>${itemName(row.name)}<br><span class="muted">#${row.item_id || '-'}</span></td>
          <td>${integer(row.quantity)}</td>
          <td>${gold(row.unit_price)}</td>
          <td>${gold(row.buyout)}</td>
          <td>${shortTime(row.observed_at)}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="5" class="muted">No addon listings imported.</td></tr>';

      const purchases = activity.purchases || [];
      els.myBuysTable.innerHTML = purchases.length ? purchases.map((row) => {
        return `<tr>
          <td>${itemName(row.name)}<br><span class="muted">#${row.item_id || row.auction_id || '-'}</span></td>
          <td>${integer(row.quantity)}</td>
          <td>${gold(row.unit_price)}</td>
          <td>${gold(row.total_price)}</td>
          <td>${row.event_type || '-'}</td>
          <td>${shortTime(row.observed_at)}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="6" class="muted">No purchase events imported yet.</td></tr>';

      const opportunities = activity.buy_opportunities || [];
      els.buySignalsTable.innerHTML = opportunities.length ? opportunities.map((row) => {
        const gain = profit(row.potential_profit);
        return `<tr>
          <td>${itemName(row.name)}<br><span class="muted">#${row.item_id}</span></td>
          <td>${integer(row.quantity)}</td>
          <td>${gold(row.unit_price)}</td>
          <td>${gold(row.buy_target_unit_price)}</td>
          <td class="${gain.cls}">${gain.text}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="5" class="muted">No buy signals recorded yet.</td></tr>';

      const outcomes = activity.outcomes || [];
      els.auctionOutcomesTable.innerHTML = outcomes.length ? outcomes.map((row) => {
        return `<tr>
          <td>${itemName(row.name)}<br><span class="muted">#${row.item_id || '-'}</span></td>
          <td>${integer(row.item_count)}</td>
          <td>${row.outcome || '-'}</td>
          <td>${gold(row.money)}</td>
          <td>${shortTime(row.observed_at)}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="5" class="muted">No auction outcomes imported.</td></tr>';
    }

    function setActiveTab(tabName) {
      els.tabButtons.forEach((button) => {
        const active = button.dataset.tab === tabName;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      els.tabPanels.forEach((panel) => {
        panel.classList.toggle('active', panel.dataset.panel === tabName);
      });
    }

    function tabFromPath() {
      if (window.location.pathname === '/my-auctions') return 'player';
      return 'market';
    }

    function pathForTab(tabName) {
      return tabName === 'player' ? '/my-auctions' : '/market';
    }

    function navigateTab(tabName) {
      setActiveTab(tabName);
      const path = pathForTab(tabName);
      if (window.location.pathname !== path) {
        window.history.pushState({ tab: tabName }, '', path);
      }
    }

    async function selectItem(itemId) {
      selectedItemId = itemId;
      renderItems();
      renderRecommendations();
      await loadItemHistory(itemId);
    }

    async function loadItemHistory(itemId) {
      const response = await fetch(`/api/history?item_id=${itemId}&t=${Date.now()}`, { cache: 'no-store' });
      const payload = await response.json();
      els.chartTitle.textContent = `${payload.item.name || 'Item ' + itemId} Price History`;
      els.chartNote.textContent = `${payload.history.length} snapshots`;
      drawChart(payload.history);
    }

    function drawChart(rows) {
      const canvas = els.chart;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      const points = rows.filter((row) => row.min_unit_price !== null);
      if (points.length < 2) {
        ctx.fillStyle = '#5e6b78';
        ctx.fillText('Not enough history yet', 24, 40);
        return;
      }
      const pad = { left: 64, right: 18, top: 22, bottom: 42 };
      const width = canvas.width - pad.left - pad.right;
      const height = canvas.height - pad.top - pad.bottom;
      const values = points.map((row) => row.min_unit_price).filter(Boolean);
      const min = Math.min(...values);
      const max = Math.max(...values);
      const spread = Math.max(max - min, 1);
      const x = (index) => pad.left + (index / Math.max(points.length - 1, 1)) * width;
      const y = (value) => pad.top + height - ((value - min) / spread) * height;

      ctx.strokeStyle = '#d7dee7';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const gy = pad.top + (i / 4) * height;
        ctx.beginPath();
        ctx.moveTo(pad.left, gy);
        ctx.lineTo(canvas.width - pad.right, gy);
        ctx.stroke();
      }
      ctx.fillStyle = '#5e6b78';
      ctx.font = '12px system-ui';
      ctx.fillText(gold(max), 8, pad.top + 4);
      ctx.fillText(gold(min), 8, pad.top + height);

      drawLine(ctx, points, x, y, 'min_unit_price', '#176b87');
      ctx.fillStyle = '#176b87';
      ctx.fillText('Min / Unit', pad.left, canvas.height - 14);
    }

    function drawLine(ctx, points, x, y, key, color) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 3;
      ctx.beginPath();
      let started = false;
      points.forEach((point, index) => {
        if (point[key] === null || point[key] === undefined) return;
        const px = x(index);
        const py = y(point[key]);
        if (!started) {
          ctx.moveTo(px, py);
          started = true;
        }
        else ctx.lineTo(px, py);
      });
      if (started) ctx.stroke();
    }

    function startAutoRefresh() {
      stopAutoRefresh();
      if (!els.autoRefresh.checked) return;
      autoRefreshTimer = setInterval(() => {
        if (document.visibilityState === 'visible') loadOverview();
      }, AUTO_REFRESH_MS);
    }

    function stopAutoRefresh() {
      if (autoRefreshTimer !== null) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
      }
    }

    els.refresh.addEventListener('click', () => loadOverview());
    els.importAddon.addEventListener('click', () => importAddonData());
    els.timezone.addEventListener('change', () => loadOverview());
    els.autoRefresh.addEventListener('change', startAutoRefresh);
    els.filter.addEventListener('input', renderItems);
    els.tabButtons.forEach((button) => {
      button.addEventListener('click', () => navigateTab(button.dataset.tab));
    });
    window.addEventListener('popstate', () => setActiveTab(tabFromPath()));
    setActiveTab(tabFromPath());
    loadOverview({ refreshSelected: false });
    startAutoRefresh();
  </script>
</body>
</html>
"""
