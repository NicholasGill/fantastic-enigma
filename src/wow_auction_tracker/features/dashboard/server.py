from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, request
from sqlalchemy.engine import make_url

from wow_auction_tracker.features.dashboard.item_page import ITEM_DETAIL_HTML
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
    tracked_item_ids: frozenset[int] | None = None


class DashboardDataStore:
    def __init__(self, database_url: str, *, tracked_item_ids: frozenset[int] | None = None) -> None:
        self.database_url = database_url
        self.database_path = _sqlite_database_path(database_url)
        self.tracked_item_ids = tracked_item_ids

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
            buy_recommendations = sorted(
                [
                    item for item in all_recommendations
                    if int(item.get("buy_score") or 0) >= int(item.get("sell_score") or 0)
                    and int(item.get("buy_score") or 0) > 0
                ],
                key=lambda item: (
                    int(item.get("buy_score") or 0),
                    int(item.get("confidence") or 0),
                    int(item.get("estimated_demand_score") or 0),
                ),
                reverse=True,
            )[:12]
            sell_recommendations = sorted(
                [
                    item for item in all_recommendations
                    if int(item.get("sell_score") or 0) > int(item.get("buy_score") or 0)
                    and int(item.get("sell_score") or 0) > 0
                ],
                key=lambda item: (
                    int(item.get("sell_score") or 0),
                    int(item.get("confidence") or 0),
                    int(item.get("estimated_demand_score") or 0),
                ),
                reverse=True,
            )[:12]
            recommendations = buy_recommendations[:8]
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
                item["latest_shifted_unit_price"] = recommendation.get("latest_shifted_unit_price")
                item["recommendation_action"] = recommendation.get("action")
                item["recommendation_score"] = recommendation.get("score")
                item["buy_score"] = recommendation.get("buy_score")
                item["sell_score"] = recommendation.get("sell_score")
                item["recommendation_confidence"] = recommendation.get("confidence")
                item["average_sell_through_ratio"] = recommendation.get("average_sell_through_ratio")
                item["average_sell_through_ratio_bps"] = round(
                    float(recommendation.get("average_sell_through_ratio") or 0) * 10000
                )
                item["average_probable_sold_unit_price"] = recommendation.get("average_probable_sold_unit_price")
                item["vendor_sell_unit_price"] = recommendation.get("vendor_sell_unit_price")
                item["auction_deposit_unit_price"] = recommendation.get("auction_deposit_unit_price")
                item["estimated_profit_unit_price"] = recommendation.get("estimated_profit_unit_price")
                item["sell_profit_unit_price"] = recommendation.get("sell_profit_unit_price")
                item["price_trend_score"] = recommendation.get("price_trend_score")
                item["price_trend_ratio"] = recommendation.get("price_trend_ratio")
                item["recent_min_change_ratio"] = recommendation.get("recent_min_change_ratio")
                item["best_buy_time"] = recommendation.get("best_buy_time")
                item["best_sell_time"] = recommendation.get("best_sell_time")
                item["historical_buy_price"] = recommendation.get("historical_buy_price")
                item["historical_sell_price"] = recommendation.get("historical_sell_price")
                item["historical_timing_confidence"] = recommendation.get("historical_timing_confidence")
                item["has_buy_opportunity"] = _has_buy_opportunity(
                    recommendation.get("latest_shifted_unit_price") or item.get("min_unit_price"),
                    recommendation.get("recommended_buy_price"),
                )
            if dev_mode:
                _apply_dev_buy_opportunities(items)
            items.sort(
                key=lambda item: (
                    int(item.get("buy_score") or 0),
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
                "snapshots": self._snapshot_overview(connection, latest_run["id"] if latest_run else None),
                "items": items,
                "latest_lifecycle": self._latest_lifecycle(connection, latest_run["id"] if latest_run else None),
                "recommendations": recommendations,
                "buy_recommendations": buy_recommendations,
                "sell_recommendations": sell_recommendations,
                "craft_opportunities": self._craft_opportunities(connection, latest_run["id"] if latest_run else None),
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

    def item_exists(self, item_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "select 1 from tracked_items where item_id = ? limit 1",
                (item_id,),
            ).fetchone()
        return row is not None

    def item_detail(
        self,
        item_id: int,
        *,
        display_timezone: str = DEFAULT_DISPLAY_TIMEZONE,
    ) -> dict[str, Any] | None:
        timezone_name = _dashboard_timezone(display_timezone)
        with self._connect() as connection:
            item = connection.execute(
                """
                select
                    t.item_id,
                    coalesce(m.name, t.name, 'Item ' || t.item_id) as name,
                    t.market,
                    m.quality,
                    m.item_class,
                    m.item_subclass,
                    m.item_level,
                    m.is_stackable,
                    m.sell_price as vendor_sell_price,
                    m.icon_url
                from tracked_items t
                left join item_metadata m on m.item_id = t.item_id
                where t.item_id = ?
                """,
                (item_id,),
            ).fetchone()
            if item is None:
                return None

            rows = connection.execute(
                """
                select
                    r.id as fetch_run_id,
                    r.started_at,
                    s.listing_count,
                    s.total_quantity,
                    s.min_unit_price,
                    s.first_quartile_unit_price,
                    s.median_unit_price,
                    s.third_quartile_unit_price,
                    h.weighted_average_unit_price,
                    h.lowest_price_quantity,
                    h.price_change_1h_bps,
                    h.price_change_24h_bps,
                    h.price_change_7d_bps,
                    h.historical_volatility_bps,
                    h.percentile_rank_bps,
                    h.market_depth_score,
                    h.liquidity_score,
                    st.disappeared_listing_count,
                    st.disappeared_quantity,
                    st.probable_sold_quantity,
                    st.probable_sold_average_unit_price,
                    st.sell_through_ratio_bps,
                    st.confidence as sell_through_confidence
                from item_summaries s
                join fetch_runs r on r.id = s.fetch_run_id
                left join item_history_metrics h
                    on h.fetch_run_id = s.fetch_run_id
                    and h.item_id = s.item_id
                    and h.market = s.market
                left join sell_through_metrics st
                    on st.fetch_run_id = s.fetch_run_id
                    and st.item_id = s.item_id
                    and st.market = s.market
                where s.item_id = ? and r.status = 'success'
                order by r.started_at, r.id
                """,
                (item_id,),
            ).fetchall()
            anomalies = connection.execute(
                """
                select detected_at, anomaly_type, severity, baseline_value, observed_value, explanation
                from item_anomalies
                where item_id = ?
                order by detected_at desc, id desc
                limit 12
                """,
                (item_id,),
            ).fetchall()
            player_outcomes = connection.execute(
                """
                select
                    o.observed_at,
                    o.outcome,
                    o.item_count,
                    o.money,
                    o.character,
                    o.realm,
                    pm.confidence as match_confidence,
                    pm.elapsed_seconds
                from player_auction_outcomes o
                left join player_auction_matches pm on pm.outcome_id = o.id
                where coalesce(o.item_id, pm.item_id) = ?
                order by o.observed_at desc, o.id desc
                limit 20
                """,
                (item_id,),
            ).fetchall()

        history = [_item_detail_history_row(dict(row), timezone_name) for row in rows]
        recommendation = _item_recommendation(self.database_url, item_id, timezone_name)
        return {
            "item": dict(item),
            "summary": _item_detail_summary(history),
            "history": history,
            "smoothed_price_history": _smoothed_price_histories(history),
            "time_of_day": _item_history_by_hour(history),
            "day_of_week": _item_history_by_weekday(history),
            "recommendation": recommendation,
            "anomalies": [dict(row) for row in anomalies],
            "player_outcomes": [dict(row) for row in player_outcomes],
            "display_timezone": timezone_name,
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
        with self._connect() as connection:
            import_row = connection.execute(
                """
                select inserted_row_count, skipped_duplicate_count, malformed_row_count
                from addon_imports
                where id = ?
                """,
                (import_id,),
            ).fetchone()
        return {
            "import_id": import_id,
            "source_path": str(path),
            "owned_snapshot_count": len(result.posts),
            "mail_event_count": len(result.outcomes),
            "purchase_event_count": len(result.purchases),
            "gold_snapshot_count": len(result.gold_snapshots or []),
            "inserted_row_count": int(import_row["inserted_row_count"]) if import_row else 0,
            "skipped_duplicate_count": int(import_row["skipped_duplicate_count"]) if import_row else 0,
            "malformed_row_count": int(import_row["malformed_row_count"]) if import_row else result.malformed_row_count,
        }

    def player_performance(self, *, window_days: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return _player_performance(connection, window_days=window_days)

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
            "raw_auction_snapshots",
            "auction_listings",
            "item_summaries",
            "item_history_metrics",
            "listing_observations",
            "sell_through_metrics",
            "market_quality_events",
            "buy_opportunity_observations",
            "craft_opportunity_observations",
            "addon_imports",
            "player_auction_posts",
            "player_auction_outcomes",
            "player_auction_purchases",
            "player_gold_snapshots",
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
                r.region,
                r.locale,
                r.connected_realm_id,
                r.expected_interval_seconds,
                r.error,
                (select count(*) from auction_listings l where l.fetch_run_id = r.id) as listing_count,
                (select count(*) from item_summaries s where s.fetch_run_id = r.id) as summary_count,
                (
                    select coalesce(sum(s.total_quantity), 0)
                    from item_summaries s
                    where s.fetch_run_id = r.id
                ) as total_quantity
            from fetch_runs r
            order by r.id desc
            limit 12
            """
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _snapshot_overview(connection: sqlite3.Connection, latest_run_id: int | None) -> dict[str, Any]:
        if latest_run_id is None:
            return {
                "latest": None,
                "lifecycle": {},
                "sell_through": {
                    "item_count": 0,
                    "disappeared_quantity": 0,
                    "probable_sold_quantity": 0,
                    "average_sell_through_ratio_bps": None,
                },
            }

        latest = connection.execute(
            """
            select
                count(*) as item_count,
                coalesce(sum(listing_count), 0) as listing_count,
                coalesce(sum(total_quantity), 0) as total_quantity,
                min(min_unit_price) as lowest_unit_price,
                avg(median_unit_price) as average_median_unit_price
            from item_summaries
            where fetch_run_id = ?
            """,
            (latest_run_id,),
        ).fetchone()
        sell_through = connection.execute(
            """
            select
                count(*) as item_count,
                coalesce(sum(disappeared_quantity), 0) as disappeared_quantity,
                coalesce(sum(probable_sold_quantity), 0) as probable_sold_quantity,
                avg(sell_through_ratio_bps) as average_sell_through_ratio_bps
            from sell_through_metrics
            where fetch_run_id = ?
            """,
            (latest_run_id,),
        ).fetchone()
        return {
            "latest": dict(latest) if latest else None,
            "lifecycle": DashboardDataStore._latest_lifecycle(connection, latest_run_id),
            "sell_through": dict(sell_through) if sell_through else {},
        }

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
    def _craft_opportunities(
        connection: sqlite3.Connection,
        latest_run_id: int | None,
    ) -> list[dict[str, Any]]:
        if latest_run_id is None:
            return []

        rows = connection.execute(
            """
            select
                c.id,
                c.observed_at,
                c.recipe_id,
                c.recipe_name,
                c.output_item_id,
                coalesce(m.name, t.name, 'Item ' || c.output_item_id) as output_name,
                c.output_market,
                c.output_quantity,
                c.craft_cost,
                c.craft_cost_unit_price,
                c.output_min_unit_price,
                c.sell_target_unit_price,
                c.auction_deposit_unit_price,
                c.ah_savings,
                c.expected_profit,
                c.max_craft_quantity,
                c.confidence,
                c.reasons_json
            from craft_opportunity_observations c
            left join item_metadata m on m.item_id = c.output_item_id
            left join tracked_items t on t.item_id = c.output_item_id
            where c.fetch_run_id = ?
            order by c.expected_profit desc, c.ah_savings desc, c.recipe_id
            limit 20
            """,
            (latest_run_id,),
        ).fetchall()

        opportunities: list[dict[str, Any]] = []
        for row in rows:
            opportunity = dict(row)
            reasons_json = str(opportunity.pop("reasons_json") or "[]")
            try:
                reasons = json.loads(reasons_json)
            except json.JSONDecodeError:
                reasons = []
            opportunity["reasons"] = reasons if isinstance(reasons, list) else []
            opportunities.append(opportunity)
        return opportunities

    def _player_activity(self, connection: sqlite3.Connection) -> dict[str, Any]:
        latest_import = connection.execute(
            """
            select
                id,
                imported_at,
                source_path,
                owned_snapshot_count,
                mail_event_count,
                purchase_event_count,
                gold_snapshot_count,
                inserted_row_count,
                skipped_duplicate_count,
                malformed_row_count
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
                (select count(*) from player_auction_matches) as matched_outcome_count,
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
            "performance": _player_performance(connection),
            "profit_loss": _player_profit_loss(connection, tracked_item_ids=self.tracked_item_ids),
            "gold": _player_gold(connection),
        }


def create_dashboard_app(config: DashboardConfig) -> Flask:
    store = DashboardDataStore(config.database_url, tracked_item_ids=config.tracked_item_ids)
    app = Flask(__name__)

    @app.after_request
    def _disable_cache(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    @app.get("/my-auctions")
    @app.get("/profit-loss")
    @app.get("/market")
    @app.get("/buy")
    @app.get("/sell")
    @app.get("/stats")
    @app.get("/snapshots")
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

    @app.get("/items/<int:item_id>")
    def _item_page(item_id: int) -> Response:
        if not store.item_exists(item_id):
            return Response("Item not found", status=404, mimetype="text/plain")
        return Response(ITEM_DETAIL_HTML, mimetype="text/html")

    @app.get("/api/items/<int:item_id>")
    def _item_detail(item_id: int) -> Response | tuple[Response, int]:
        display_timezone = _dashboard_timezone(request.args.get("timezone", DEFAULT_DISPLAY_TIMEZONE))
        detail = store.item_detail(item_id, display_timezone=display_timezone)
        if detail is None:
            return jsonify({"error": "item not found"}), 404
        return jsonify(detail)

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


def _item_recommendation(database_url: str, item_id: int, display_timezone: str) -> dict[str, Any] | None:
    try:
        recommendations = RecommendationEngine(
            database_url,
            display_timezone=display_timezone,
        ).recommend(item_id=item_id)
    except ValueError:
        return None
    for recommendation in recommendations:
        if recommendation.item_id == item_id:
            return recommendation_to_dict(recommendation)
    return None


def _item_detail_history_row(row: dict[str, Any], display_timezone: str) -> dict[str, Any]:
    started_at = _parse_dashboard_datetime(row["started_at"])
    local_started_at = started_at.astimezone(ZoneInfo(display_timezone))
    row["started_at_epoch"] = round(started_at.timestamp())
    row["display_time"] = local_started_at.strftime("%b %d, %Y %I:%M %p %Z").replace(" 0", " ")
    row["local_hour"] = local_started_at.hour
    row["local_weekday"] = local_started_at.weekday()
    return row


def _parse_dashboard_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _smooth_item_history(
    history: list[dict[str, Any]],
    *,
    window_size: int = 5,
) -> list[dict[str, Any]]:
    if window_size < 1 or window_size % 2 == 0:
        raise ValueError("window_size must be a positive odd number")

    smoothed = [dict(row) for row in history]
    radius = window_size // 2
    price_keys = (
        "first_quartile_unit_price",
        "median_unit_price",
        "third_quartile_unit_price",
    )
    for index, row in enumerate(smoothed):
        start = max(0, index - radius)
        end = min(len(history), index + radius + 1)
        neighbors = history[start:end]
        for key in price_keys:
            values = [int(neighbor[key]) for neighbor in neighbors if neighbor.get(key) is not None]
            row[key] = round(median(values)) if values else None
    return smoothed


def _smoothed_price_histories(history: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    range_windows: tuple[tuple[str, int | None, int], ...] = (
        ("24", 24, 5),
        ("168", 168, 49),
        ("720", 720, 73),
        ("all", None, 97),
    )
    return {
        key: _downsample_price_history(
            _smooth_item_history(_price_history_for_hours(history, hours), window_size=window_size)
        )
        for key, hours, window_size in range_windows
    }


def _price_history_for_hours(
    history: list[dict[str, Any]],
    hours: int | None,
) -> list[dict[str, Any]]:
    if not history:
        return []
    cutoff = None if hours is None else int(history[-1]["started_at_epoch"]) - (hours * 60 * 60)
    return [
        {
            "started_at_epoch": row["started_at_epoch"],
            "display_time": row["display_time"],
            "first_quartile_unit_price": row.get("first_quartile_unit_price"),
            "median_unit_price": row.get("median_unit_price"),
            "third_quartile_unit_price": row.get("third_quartile_unit_price"),
        }
        for row in history
        if cutoff is None or int(row["started_at_epoch"]) >= cutoff
    ]


def _downsample_price_history(
    history: list[dict[str, Any]],
    *,
    maximum_points: int = 600,
) -> list[dict[str, Any]]:
    if len(history) <= maximum_points:
        return history
    bucket_size = (len(history) + maximum_points - 1) // maximum_points
    price_keys = (
        "first_quartile_unit_price",
        "median_unit_price",
        "third_quartile_unit_price",
    )
    sampled: list[dict[str, Any]] = []
    for start in range(0, len(history), bucket_size):
        bucket = history[start : start + bucket_size]
        representative = dict(bucket[len(bucket) // 2])
        for key in price_keys:
            values = [int(row[key]) for row in bucket if row.get(key) is not None]
            representative[key] = round(median(values)) if values else None
        sampled.append(representative)
    return sampled


def _item_detail_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {
            "snapshot_count": 0,
            "latest": None,
            "first_seen": None,
            "last_seen": None,
            "all_time_low_unit_price": None,
            "average_first_quartile_unit_price": None,
            "average_median_unit_price": None,
            "seven_day_low_unit_price": None,
            "seven_day_average_first_quartile_unit_price": None,
            "seven_day_average_median_unit_price": None,
        }

    latest = history[-1]
    seven_day_cutoff = int(latest["started_at_epoch"]) - (7 * 24 * 60 * 60)
    recent = [row for row in history if int(row["started_at_epoch"]) >= seven_day_cutoff]
    return {
        "snapshot_count": len(history),
        "latest": latest,
        "first_seen": history[0]["started_at"],
        "last_seen": latest["started_at"],
        "all_time_low_unit_price": _minimum_item_value(history, "min_unit_price"),
        "average_first_quartile_unit_price": _average_item_value(history, "first_quartile_unit_price"),
        "average_median_unit_price": _average_item_value(history, "median_unit_price"),
        "seven_day_low_unit_price": _minimum_item_value(recent, "min_unit_price"),
        "seven_day_average_first_quartile_unit_price": _average_item_value(
            recent,
            "first_quartile_unit_price",
        ),
        "seven_day_average_median_unit_price": _average_item_value(recent, "median_unit_price"),
    }


def _item_history_by_hour(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _item_history_bucket(
            hour,
            _hour_label(hour),
            [row for row in history if int(row["local_hour"]) == hour],
        )
        for hour in range(24)
    ]


def _item_history_by_weekday(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weekday_labels = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    return [
        _item_history_bucket(
            weekday,
            label,
            [row for row in history if int(row["local_weekday"]) == weekday],
        )
        for weekday, label in enumerate(weekday_labels)
    ]


def _item_history_bucket(key: int, label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "snapshot_count": len(rows),
        "low_unit_price": _minimum_item_value(rows, "min_unit_price"),
        "average_first_quartile_unit_price": _average_item_value(rows, "first_quartile_unit_price"),
        "average_median_unit_price": _average_item_value(rows, "median_unit_price"),
        "average_third_quartile_unit_price": _average_item_value(rows, "third_quartile_unit_price"),
        "average_total_quantity": _average_item_value(rows, "total_quantity"),
        "average_listing_count": _average_item_value(rows, "listing_count"),
        "average_sell_through_ratio_bps": _average_item_value(rows, "sell_through_ratio_bps"),
    }


def _average_item_value(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [int(row[key]) for row in rows if row.get(key) is not None]
    if not values:
        return None
    return round(sum(values) / len(values))


def _minimum_item_value(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [int(row[key]) for row in rows if row.get(key) is not None]
    return min(values) if values else None


def _hour_label(hour: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour} {suffix}"


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


def _player_performance(connection: sqlite3.Connection, *, window_days: int | None = None) -> list[dict[str, Any]]:
    where_clause = ""
    params: tuple[str, ...] = ()
    if window_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        where_clause = "where o.observed_at >= ?"
        params = (cutoff.isoformat(sep=" "),)
    rows = connection.execute(
        f"""
        select
            coalesce(o.item_id, m.item_id) as item_id,
            coalesce(md.name, o.item_name, t.name, 'Item ' || coalesce(o.item_id, m.item_id)) as name,
            o.character,
            o.realm,
            count(*) as outcome_count,
            sum(case when o.outcome = 'sold' then 1 else 0 end) as sold_count,
            sum(case when o.outcome = 'expired' then 1 else 0 end) as expired_count,
            sum(case when o.outcome = 'cancelled' then 1 else 0 end) as cancelled_count,
            coalesce(sum(case when o.outcome = 'sold' then o.item_count else 0 end), 0) as sold_quantity,
            coalesce(sum(case when o.outcome = 'expired' then o.item_count else 0 end), 0) as expired_quantity,
            coalesce(sum(case when o.outcome = 'cancelled' then o.item_count else 0 end), 0) as cancelled_quantity,
            coalesce(sum(case when o.outcome = 'sold' then o.money else 0 end), 0) as proceeds,
            avg(case when o.outcome = 'sold' then o.money end) as average_proceeds,
            avg(case when m.outcome = 'sold' then m.elapsed_seconds end) as average_time_to_sale_seconds,
            avg(case when m.outcome = 'expired' then m.elapsed_seconds end) as average_time_to_expiry_seconds,
            avg(m.confidence) as average_match_confidence
        from player_auction_outcomes o
        left join player_auction_matches m on m.outcome_id = o.id
        left join item_metadata md on md.item_id = coalesce(o.item_id, m.item_id)
        left join tracked_items t on t.item_id = coalesce(o.item_id, m.item_id)
        {where_clause}
        group by coalesce(o.item_id, m.item_id), o.character, o.realm
        order by proceeds desc, sold_count desc, outcome_count desc
        limit 50
        """,
        params,
    ).fetchall()
    performance = [dict(row) for row in rows]
    for row in performance:
        outcome_count = int(row.get("outcome_count") or 0)
        row["sale_rate_bps"] = round((int(row.get("sold_count") or 0) / outcome_count) * 10000) if outcome_count else 0
        for key in ("average_proceeds", "average_time_to_sale_seconds", "average_time_to_expiry_seconds", "average_match_confidence"):
            if row.get(key) is not None:
                row[key] = round(float(row[key]))
    return performance


def _player_gold(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        """
        select
            observed_at,
            reason,
            character,
            realm,
            money
        from player_gold_snapshots
        where money is not null
        order by observed_at desc, id desc
        limit 100
        """
    ).fetchall()
    history = [dict(row) for row in reversed(rows)]
    if not history:
        return {
            "latest": None,
            "first": None,
            "delta": None,
            "history": [],
        }

    first = history[0]
    latest = history[-1]
    return {
        "latest": latest,
        "first": first,
        "delta": int(latest["money"]) - int(first["money"]),
        "history": history,
    }


def _player_profit_loss(
    connection: sqlite3.Connection,
    *,
    tracked_item_ids: frozenset[int] | None = None,
) -> dict[str, Any]:
    item_names = _profit_loss_item_names(connection, tracked_item_ids=tracked_item_ids)
    sale_rows = connection.execute(
        """
        with canonical_sales as (
            select
                min(o.id) as id,
                o.observed_at,
                o.character,
                o.realm,
                coalesce(o.item_id, m.item_id) as item_id,
                coalesce(md.name, t.name, o.item_name, 'Item ' || coalesce(o.item_id, m.item_id)) as name,
                o.item_count,
                o.money
            from player_auction_outcomes o
            left join player_auction_matches m on m.outcome_id = o.id
            left join item_metadata md on md.item_id = coalesce(o.item_id, m.item_id)
            left join tracked_items t on t.item_id = coalesce(o.item_id, m.item_id)
            where o.outcome = 'sold'
            group by
                o.observed_at,
                o.character,
                o.realm,
                coalesce(o.item_id, m.item_id),
                coalesce(md.name, t.name, o.item_name),
                o.item_count,
                o.money
        )
        select
            item_id,
            name,
            count(*) as sale_count,
            coalesce(sum(item_count), 0) as sold_quantity,
            coalesce(sum(money), 0) as revenue
        from canonical_sales
        group by item_id, name
        """
    ).fetchall()
    purchase_rows = connection.execute(
        """
        select
            p.item_id,
            coalesce(md.name, t.name, 'Item ' || p.item_id) as name,
            count(*) as purchase_count,
            coalesce(sum(p.quantity), 0) as purchased_quantity,
            coalesce(sum(p.total_price), 0) as cost
        from player_auction_purchases p
        left join item_metadata md on md.item_id = p.item_id
        left join tracked_items t on t.item_id = p.item_id
        where p.event_type in ('auction_purchase_completed', 'commodity_purchase_succeeded')
        group by p.item_id, coalesce(md.name, t.name)
        """
    ).fetchall()

    rows_by_key: dict[str, dict[str, Any]] = {}
    for row in sale_rows:
        item = dict(row)
        identity = _profit_loss_identity(item.get("item_id"), item.get("name"), item_names)
        if identity is None:
            continue
        key, item_id, name = identity
        rows_by_key[key] = {
            "item_id": item_id,
            "name": name,
            "sale_count": int(item.get("sale_count") or 0),
            "sold_quantity": int(item.get("sold_quantity") or 0),
            "revenue": int(item.get("revenue") or 0),
            "purchase_count": 0,
            "purchased_quantity": 0,
            "cost": 0,
        }

    for row in purchase_rows:
        item = dict(row)
        identity = _profit_loss_identity(item.get("item_id"), item.get("name"), item_names)
        if identity is None:
            continue
        key, item_id, name = identity
        target = rows_by_key.setdefault(
            key,
            {
                "item_id": item_id,
                "name": name,
                "sale_count": 0,
                "sold_quantity": 0,
                "revenue": 0,
                "purchase_count": 0,
                "purchased_quantity": 0,
                "cost": 0,
            },
        )
        target["purchase_count"] = int(item.get("purchase_count") or 0)
        target["purchased_quantity"] = int(item.get("purchased_quantity") or 0)
        target["cost"] = int(item.get("cost") or 0)

    rows = []
    for row in rows_by_key.values():
        revenue = int(row["revenue"])
        cost = int(row["cost"])
        if revenue > 0 and cost > 0:
            row["cost_basis_status"] = "complete"
            row["net_profit"] = revenue - cost
            row["margin_bps"] = round((row["net_profit"] / revenue) * 10000)
        elif revenue > 0:
            row["cost_basis_status"] = "missing_cost"
            row["net_profit"] = None
            row["margin_bps"] = None
        else:
            row["cost_basis_status"] = "open_purchase"
            row["net_profit"] = -cost if cost > 0 else None
            row["margin_bps"] = None
        rows.append(row)

    rows.sort(
        key=lambda row: (
            row["net_profit"] is not None,
            int(row["net_profit"] or 0),
            int(row["revenue"]),
        ),
        reverse=True,
    )
    revenue = sum(int(row["revenue"]) for row in rows)
    cost = sum(int(row["cost"]) for row in rows)
    known_rows = [row for row in rows if row["net_profit"] is not None]
    known_revenue = sum(int(row["revenue"]) for row in known_rows)
    known_cost = sum(int(row["cost"]) for row in known_rows)
    known_net_profit = known_revenue - known_cost
    return {
        "summary": {
            "revenue": revenue,
            "cost": cost,
            "known_revenue": known_revenue,
            "known_cost": known_cost,
            "net_profit": known_net_profit,
            "unmatched_revenue": sum(
                int(row["revenue"]) for row in rows if row["cost_basis_status"] == "missing_cost"
            ),
            "open_purchase_cost": sum(
                int(row["cost"]) for row in rows if row["cost_basis_status"] == "open_purchase"
            ),
            "sale_count": sum(int(row["sale_count"]) for row in rows),
            "purchase_count": sum(int(row["purchase_count"]) for row in rows),
            "sold_quantity": sum(int(row["sold_quantity"]) for row in rows),
            "purchased_quantity": sum(int(row["purchased_quantity"]) for row in rows),
            "margin_bps": round((known_net_profit / known_revenue) * 10000) if known_revenue else None,
        },
        "items": rows[:50],
    }


def _is_configured_profit_loss_item(item_id: object, tracked_item_ids: frozenset[int] | None) -> bool:
    if tracked_item_ids is None:
        return True
    try:
        parsed_item_id = int(item_id)
    except (TypeError, ValueError):
        return False
    return parsed_item_id in tracked_item_ids


def _profit_loss_item_names(
    connection: sqlite3.Connection,
    *,
    tracked_item_ids: frozenset[int] | None,
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        select t.item_id, coalesce(m.name, t.name) as name
        from tracked_items t
        left join item_metadata m on m.item_id = t.item_id
        where coalesce(m.name, t.name) is not null
        order by t.item_id
        """
    ).fetchall()
    names: dict[str, dict[str, Any]] = {}
    for row in rows:
        item_id = int(row["item_id"])
        if not _is_configured_profit_loss_item(item_id, tracked_item_ids):
            continue
        name = str(row["name"])
        key = _item_name_key(name)
        entry = names.setdefault(key, {"name": name, "item_ids": set()})
        entry["item_ids"].add(item_id)
    return names


def _profit_loss_identity(
    item_id: object,
    name: object,
    item_names: dict[str, dict[str, Any]],
) -> tuple[str, int | None, str] | None:
    parsed_item_id: int | None
    try:
        parsed_item_id = int(item_id) if item_id is not None else None
    except (TypeError, ValueError):
        parsed_item_id = None

    name_value = str(name).strip() if name is not None else ""
    name_key = _item_name_key(name_value) if name_value else ""
    name_entry = item_names.get(name_key) if name_key else None
    if name_entry is not None:
        item_ids = set(name_entry["item_ids"])
        if parsed_item_id is not None and parsed_item_id not in item_ids:
            return None
        display_name = str(name_entry["name"])
        if len(item_ids) == 1:
            resolved_item_id = next(iter(item_ids))
            return (f"id:{resolved_item_id}", resolved_item_id, display_name)
        return (f"name:{name_key}", None, display_name)

    if parsed_item_id is None:
        return None
    for entry in item_names.values():
        if parsed_item_id in entry["item_ids"]:
            return (f"id:{parsed_item_id}", parsed_item_id, name_value or str(entry["name"]))
    return None


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
    .top-tabs {
      position: sticky;
      top: 73px;
      z-index: 4;
      padding: 10px 24px 0;
      background: var(--bg);
    }
    main { padding: 18px 24px 28px; }
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
    .profit-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
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
    .item-link {
      color: inherit;
      font-weight: 700;
      text-decoration: none;
    }
    .item-link:hover { color: var(--accent); text-decoration: underline; }
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
      .profit-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      header { padding: 14px; align-items: flex-start; flex-direction: column; }
      .top-tabs { top: 132px; padding: 10px 14px 0; }
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
  <nav class="top-tabs">
    <div class="tab-list" role="tablist" aria-label="Dashboard views">
      <button class="tab-button active" id="market-tab" type="button" role="tab" aria-selected="true" aria-controls="market-panel" data-tab="market">Market</button>
      <button class="tab-button" id="buy-tab" type="button" role="tab" aria-selected="false" aria-controls="buy-panel" data-tab="buy">Buy</button>
      <button class="tab-button" id="sell-tab" type="button" role="tab" aria-selected="false" aria-controls="sell-panel" data-tab="sell">Sell</button>
      <button class="tab-button" id="snapshots-tab" type="button" role="tab" aria-selected="false" aria-controls="snapshots-panel" data-tab="snapshots">Snapshots</button>
      <button class="tab-button" id="stats-tab" type="button" role="tab" aria-selected="false" aria-controls="stats-panel" data-tab="stats">Fetch Stats</button>
      <button class="tab-button" id="player-tab" type="button" role="tab" aria-selected="false" aria-controls="player-panel" data-tab="player">My Auctions</button>
      <button class="tab-button" id="profit-tab" type="button" role="tab" aria-selected="false" aria-controls="profit-panel" data-tab="profit">Profit / Loss</button>
    </div>
  </nav>
  <main>

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
            <div class="section-head"><h2>Craft Signals</h2></div>
            <div class="mini-table-wrap">
              <table class="mini-table">
                <thead>
                  <tr>
                    <th>Output</th>
                    <th>Craft</th>
                    <th>AH</th>
                    <th>Sell</th>
                    <th>Profit</th>
                    <th>Max</th>
                  </tr>
                </thead>
                <tbody id="craft-signals-table"></tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </div>

    <div class="tab-panel" id="buy-panel" role="tabpanel" aria-labelledby="buy-tab" data-panel="buy">
      <section>
        <div class="section-head">
          <h2>Buy Opportunities</h2>
          <span class="muted">Entries where current price is still below the buy target.</span>
        </div>
        <div class="mini-table-wrap">
          <table class="mini-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Buy Score</th>
                <th>Current</th>
                <th>Buy Target</th>
                <th>Sell Target</th>
                <th>Profit / Unit</th>
                <th>Trend</th>
                <th>Reasons</th>
              </tr>
            </thead>
            <tbody id="buy-recommendations-table"></tbody>
          </table>
        </div>
      </section>
    </div>

    <div class="tab-panel" id="sell-panel" role="tabpanel" aria-labelledby="sell-tab" data-panel="sell">
      <section>
        <div class="section-head">
          <h2>Sell Opportunities</h2>
          <span class="muted">Exit signals from price spikes and current prices near sell targets.</span>
        </div>
        <div class="mini-table-wrap">
          <table class="mini-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Sell Score</th>
                <th>Current</th>
                <th>Sell Target</th>
                <th>Vs Buy Target</th>
                <th>Min Change</th>
                <th>Confidence</th>
                <th>Reasons</th>
              </tr>
            </thead>
            <tbody id="sell-recommendations-table"></tbody>
          </table>
        </div>
      </section>
    </div>

    <div class="tab-panel" id="snapshots-panel" role="tabpanel" aria-labelledby="snapshots-tab" data-panel="snapshots">
      <div class="grid">
        <div class="metric"><span>Latest Snapshot</span><strong id="snapshot-latest">-</strong></div>
        <div class="metric"><span>Snapshot Items</span><strong id="snapshot-items">-</strong></div>
        <div class="metric"><span>Snapshot Listings</span><strong id="snapshot-listings">-</strong></div>
        <div class="metric"><span>Snapshot Quantity</span><strong id="snapshot-quantity">-</strong></div>
        <div class="metric"><span>Lowest Unit</span><strong id="snapshot-lowest">-</strong></div>
        <div class="metric"><span>Disappeared Qty</span><strong id="snapshot-disappeared">-</strong></div>
        <div class="metric"><span>Probable Sold Qty</span><strong id="snapshot-probable-sold">-</strong></div>
        <div class="metric"><span>Avg Sell-through</span><strong id="snapshot-sell-through">-</strong></div>
      </div>
      <section>
        <div class="section-head">
          <h2>Snapshot Runs</h2>
          <span class="muted" id="snapshot-note">-</span>
        </div>
        <div class="mini-table-wrap">
          <table class="mini-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th>Started</th>
                <th>Finished</th>
                <th>Realm</th>
                <th>Items</th>
                <th>Listings</th>
                <th>Quantity</th>
                <th>Interval</th>
              </tr>
            </thead>
            <tbody id="snapshot-runs-table"></tbody>
          </table>
        </div>
      </section>
    </div>

    <div class="tab-panel" id="stats-panel" role="tabpanel" aria-labelledby="stats-tab" data-panel="stats">
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
        <div class="metric"><span>Craft Signals</span><strong id="craft-signals">-</strong></div>
      </div>
      <section>
        <div class="section-head"><h2>Recent Runs</h2></div>
        <div class="runs" id="runs"></div>
      </section>
    </div>

    <div class="tab-panel" id="profit-panel" role="tabpanel" aria-labelledby="profit-tab" data-panel="profit">
      <div class="profit-grid">
        <div class="metric"><span>Known P/L</span><strong id="pl-net">-</strong></div>
        <div class="metric"><span>Gross Sales</span><strong id="pl-revenue">-</strong></div>
        <div class="metric"><span>Purchase Spend</span><strong id="pl-cost">-</strong></div>
        <div class="metric"><span>Margin</span><strong id="pl-margin">-</strong></div>
        <div class="metric"><span>Wallet Change</span><strong id="gold-delta">-</strong></div>
        <div class="metric"><span>Current Gold</span><strong id="gold-latest">-</strong></div>
      </div>
      <section>
        <div class="section-head">
          <h2>Profit / Loss by Item</h2>
          <span class="muted" id="pl-note">-</span>
        </div>
        <div class="mini-table-wrap">
          <table class="mini-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Sold Qty</th>
                <th>Bought Qty</th>
                <th>Revenue</th>
                <th>Cost</th>
                <th>Net</th>
                <th>Margin</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody id="profit-loss-table"></tbody>
          </table>
        </div>
      </section>
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
      craftSignals: document.getElementById('craft-signals'),
      snapshotLatest: document.getElementById('snapshot-latest'),
      snapshotItems: document.getElementById('snapshot-items'),
      snapshotListings: document.getElementById('snapshot-listings'),
      snapshotQuantity: document.getElementById('snapshot-quantity'),
      snapshotLowest: document.getElementById('snapshot-lowest'),
      snapshotDisappeared: document.getElementById('snapshot-disappeared'),
      snapshotProbableSold: document.getElementById('snapshot-probable-sold'),
      snapshotSellThrough: document.getElementById('snapshot-sell-through'),
      snapshotNote: document.getElementById('snapshot-note'),
      snapshotRunsTable: document.getElementById('snapshot-runs-table'),
      plNet: document.getElementById('pl-net'),
      plRevenue: document.getElementById('pl-revenue'),
      plCost: document.getElementById('pl-cost'),
      plMargin: document.getElementById('pl-margin'),
      goldDelta: document.getElementById('gold-delta'),
      goldLatest: document.getElementById('gold-latest'),
      plNote: document.getElementById('pl-note'),
      profitLossTable: document.getElementById('profit-loss-table'),
      latestTime: document.getElementById('latest-time'),
      items: document.getElementById('items'),
      recommendations: document.getElementById('recommendations'),
      buyRecommendationsTable: document.getElementById('buy-recommendations-table'),
      sellRecommendationsTable: document.getElementById('sell-recommendations-table'),
      craftSignalsTable: document.getElementById('craft-signals-table'),
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

    function ratioPercent(value) {
      if (value === null || value === undefined) return '-';
      return `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
    }

    function margin(value) {
      if (value === null || value === undefined) return '-';
      return bps(value);
    }

    function intervalLabel(seconds) {
      if (seconds === null || seconds === undefined) return '-';
      const minutes = Math.round(Number(seconds) / 60);
      if (!minutes) return `${integer(seconds)}s`;
      return `${integer(minutes)}m`;
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
        els.refreshStatus.textContent = `Imported ${integer(payload.inserted_row_count)} new rows, skipped ${integer(payload.skipped_duplicate_count)} duplicates, ${integer(payload.malformed_row_count)} malformed`;
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
      els.craftSignals.textContent = integer(overview.craft_opportunities?.length);
      els.latestTime.textContent = overview.latest_run ? shortTime(overview.latest_run.finished_at) : '-';
      document.body.classList.toggle('dev-mode-active', Boolean(overview.dev_mode));
      renderItems();
      renderRecommendations();
      renderOpportunityTabs();
      renderCraftSignals();
      renderSnapshots();
      renderPlayerActivity();
      renderProfitLoss();
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
          <td><span class="item-cell">${icon}<span class="item-meta"><span><a class="item-link" href="/items/${item.item_id}">${item.name}</a>${devMarker}</span><small>${subtitle}</small></span></span></td>
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
        row.addEventListener('click', () => openItem(Number(row.dataset.itemId)));
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

    function renderSnapshots() {
      const snapshots = overview.snapshots || {};
      const latest = snapshots.latest || {};
      const sellThrough = snapshots.sell_through || {};
      const latestRun = overview.latest_run;
      els.snapshotLatest.textContent = latestRun ? `#${latestRun.id}` : '-';
      els.snapshotItems.textContent = integer(latest.item_count);
      els.snapshotListings.textContent = integer(latest.listing_count);
      els.snapshotQuantity.textContent = integer(latest.total_quantity);
      els.snapshotLowest.textContent = gold(latest.lowest_unit_price);
      els.snapshotDisappeared.textContent = integer(sellThrough.disappeared_quantity);
      els.snapshotProbableSold.textContent = integer(sellThrough.probable_sold_quantity);
      els.snapshotSellThrough.textContent = bps(sellThrough.average_sell_through_ratio_bps);
      els.snapshotNote.textContent = latestRun
        ? `${latestRun.region || '-'} ${latestRun.locale || '-'} realm ${latestRun.connected_realm_id || '-'}`
        : 'No snapshots yet';

      const rows = overview.recent_runs || [];
      els.snapshotRunsTable.innerHTML = rows.length ? rows.map((run) => {
        return `<tr>
          <td>#${run.id}</td>
          <td><span class="pill">${run.status}</span></td>
          <td>${shortTime(run.started_at)}</td>
          <td>${shortTime(run.finished_at)}</td>
          <td>${run.connected_realm_id || '-'}</td>
          <td>${integer(run.summary_count)}</td>
          <td>${integer(run.listing_count)}</td>
          <td>${integer(run.total_quantity)}</td>
          <td>${intervalLabel(run.expected_interval_seconds)}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="9" class="muted">No snapshot runs yet.</td></tr>';
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
            <strong title="${item.name}"><a class="item-link" href="/items/${item.item_id}">${item.name}</a></strong>
            <span class="score">${item.score}</span>
          </div>
          <div class="reasons">${gold(item.recommended_buy_price)} buy, ${gold(item.recommended_sell_price)} sell (${sellSourceLabel(item.recommended_sell_price_source) || 'unknown'}), ${gold(item.auction_deposit_unit_price)} deposit, ${gold(item.estimated_profit_unit_price)} net, ${item.price_trend_score} trend, ${item.confidence}% confidence</div>
          ${timing}
          <div class="reasons">${item.reasons.join('; ')}</div>
        </div>`;
      }).join('');
      els.recommendations.querySelectorAll('.recommendation').forEach((row) => {
        row.addEventListener('click', () => openItem(Number(row.dataset.itemId)));
      });
    }

    function renderOpportunityTabs() {
      const buyRows = (overview.buy_recommendations || []).filter((item) => {
        return Number(item.buy_score || 0) > 0 && Number(item.buy_score || 0) >= Number(item.sell_score || 0);
      });
      els.buyRecommendationsTable.innerHTML = buyRows.length ? buyRows.map((item) => {
        const gain = profit(item.estimated_profit_unit_price);
        const priceTrend = trend(item.price_trend_score);
        return `<tr data-item-id="${item.item_id}">
          <td><a class="item-link" href="/items/${item.item_id}">${itemName(item.name)}</a><br><span class="muted">#${item.item_id}</span></td>
          <td><span class="score">${integer(item.buy_score)}</span></td>
          <td>${gold(item.latest_shifted_unit_price || item.latest_min_unit_price)}</td>
          <td>${gold(item.recommended_buy_price)}</td>
          <td>${gold(item.recommended_sell_price)}${sellSourceBadge(item.recommended_sell_price_source)}</td>
          <td class="${gain.cls}">${gain.text}</td>
          <td class="${priceTrend.cls}">${integer(item.price_trend_score)}</td>
          <td>${(item.reasons || []).join('; ')}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="8" class="muted">No current buy opportunities.</td></tr>';
      els.buyRecommendationsTable.querySelectorAll('tr[data-item-id]').forEach((row) => {
        row.addEventListener('click', () => openItem(Number(row.dataset.itemId)));
      });

      const sellRows = (overview.sell_recommendations || []).filter((item) => {
        return Number(item.sell_score || 0) > 0 && Number(item.sell_score || 0) > Number(item.buy_score || 0);
      });
      els.sellRecommendationsTable.innerHTML = sellRows.length ? sellRows.map((item) => {
        const gain = profit(item.sell_profit_unit_price);
        return `<tr data-item-id="${item.item_id}">
          <td><a class="item-link" href="/items/${item.item_id}">${itemName(item.name)}</a><br><span class="muted">#${item.item_id}</span></td>
          <td><span class="score">${integer(item.sell_score)}</span></td>
          <td>${gold(item.latest_min_unit_price)}</td>
          <td>${gold(item.recommended_sell_price)}${sellSourceBadge(item.recommended_sell_price_source)}</td>
          <td class="${gain.cls}">${gain.text}</td>
          <td>${ratioPercent(item.recent_min_change_ratio)}</td>
          <td>${integer(item.confidence)}%</td>
          <td>${(item.reasons || []).join('; ')}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="8" class="muted">No current sell opportunities.</td></tr>';
      els.sellRecommendationsTable.querySelectorAll('tr[data-item-id]').forEach((row) => {
        row.addEventListener('click', () => openItem(Number(row.dataset.itemId)));
      });
    }

    function renderCraftSignals() {
      const rows = overview.craft_opportunities || [];
      els.craftSignalsTable.innerHTML = rows.length ? rows.map((row) => {
        const gain = profit(row.expected_profit);
        return `<tr>
          <td>${itemName(row.output_name)}<br><span class="muted">${row.recipe_name || row.recipe_id}</span></td>
          <td>${gold(row.craft_cost_unit_price)}</td>
          <td>${gold(row.output_min_unit_price)}</td>
          <td>${gold(row.sell_target_unit_price)}</td>
          <td class="${gain.cls}">${gain.text}</td>
          <td>${integer(row.max_craft_quantity)}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="6" class="muted">No profitable craft signals yet.</td></tr>';
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

    function renderProfitLoss() {
      const profitLoss = overview.player_activity?.profit_loss || {};
      const summary = profitLoss.summary || {};
      const net = profit(summary.net_profit);
      els.plNet.textContent = net.text;
      els.plNet.className = net.cls;
      els.plRevenue.textContent = gold(summary.revenue);
      els.plCost.textContent = gold(summary.cost);
      els.plMargin.textContent = margin(summary.margin_bps);
      const goldState = overview.player_activity?.gold || {};
      const goldDelta = profit(goldState.delta);
      els.goldDelta.textContent = goldDelta.text;
      els.goldDelta.className = goldDelta.cls;
      els.goldLatest.textContent = gold(goldState.latest?.money);
      els.plNote.textContent = `${integer(summary.sale_count)} sales, ${integer(summary.purchase_count)} purchases, ${gold(summary.unmatched_revenue)} sales missing cost basis`;

      const rows = profitLoss.items || [];
      els.profitLossTable.innerHTML = rows.length ? rows.map((row) => {
        const itemNet = profit(row.net_profit);
        return `<tr>
          <td>${itemName(row.name)}<br><span class="muted">#${row.item_id || '-'}</span></td>
          <td>${integer(row.sold_quantity)}</td>
          <td>${integer(row.purchased_quantity)}</td>
          <td>${gold(row.revenue)}</td>
          <td>${gold(row.cost)}</td>
          <td class="${itemNet.cls}">${itemNet.text}</td>
          <td>${margin(row.margin_bps)}</td>
          <td>${costBasisLabel(row.cost_basis_status)}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="8" class="muted">No sale or purchase data imported yet.</td></tr>';
    }

    function costBasisLabel(value) {
      const labels = {
        complete: 'cost matched',
        missing_cost: 'missing cost',
        open_purchase: 'open purchase'
      };
      return labels[value] || '-';
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
      if (window.location.pathname === '/buy') return 'buy';
      if (window.location.pathname === '/sell') return 'sell';
      if (window.location.pathname === '/my-auctions') return 'player';
      if (window.location.pathname === '/profit-loss') return 'profit';
      if (window.location.pathname === '/stats') return 'stats';
      if (window.location.pathname === '/snapshots') return 'snapshots';
      return 'market';
    }

    function pathForTab(tabName) {
      if (tabName === 'buy') return '/buy';
      if (tabName === 'sell') return '/sell';
      if (tabName === 'snapshots') return '/snapshots';
      if (tabName === 'stats') return '/stats';
      if (tabName === 'profit') return '/profit-loss';
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

    function openItem(itemId) {
      window.location.assign(`/items/${itemId}`);
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
