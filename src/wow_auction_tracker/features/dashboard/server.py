from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy.engine import make_url

from wow_auction_tracker.features.recommendations import RecommendationEngine, recommendation_to_dict

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


class DashboardDataStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.database_path = _sqlite_database_path(database_url)

    def overview(self, *, display_timezone: str = DEFAULT_DISPLAY_TIMEZONE) -> dict[str, Any]:
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
                item["recommendation_action"] = recommendation.get("action")
                item["recommendation_score"] = recommendation.get("score")
                item["recommendation_confidence"] = recommendation.get("confidence")
                item["average_sell_through_ratio"] = recommendation.get("average_sell_through_ratio")
                item["average_sell_through_ratio_bps"] = round(float(recommendation.get("average_sell_through_ratio") or 0) * 10000)
                item["best_buy_time"] = recommendation.get("best_buy_time")
                item["best_sell_time"] = recommendation.get("best_sell_time")
                item["historical_buy_price"] = recommendation.get("historical_buy_price")
                item["historical_sell_price"] = recommendation.get("historical_sell_price")
                item["historical_timing_confidence"] = recommendation.get("historical_timing_confidence")
                item["has_buy_opportunity"] = _has_buy_opportunity(
                    item.get("min_unit_price"),
                    recommendation.get("recommended_buy_price"),
                )
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
            "addon_imports",
            "player_auction_posts",
            "player_auction_outcomes",
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


def serve_dashboard(config: DashboardConfig) -> None:
    store = DashboardDataStore(config.database_url)

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed_url = urlparse(self.path)
            if parsed_url.path == "/":
                self._send_html(DASHBOARD_HTML)
                return

            if parsed_url.path == "/api/overview":
                query = parse_qs(parsed_url.query)
                display_timezone = _dashboard_timezone(query.get("timezone", [DEFAULT_DISPLAY_TIMEZONE])[0])
                self._send_json(store.overview(display_timezone=display_timezone))
                return

            if parsed_url.path == "/api/history":
                query = parse_qs(parsed_url.query)
                item_values = query.get("item_id", [])
                if not item_values:
                    self._send_error(HTTPStatus.BAD_REQUEST, "item_id is required")
                    return
                try:
                    item_id = int(item_values[0])
                except ValueError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "item_id must be an integer")
                    return
                self._send_json(store.item_history(item_id))
                return

            self._send_error(HTTPStatus.NOT_FOUND, "not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_html(self, content: str) -> None:
            encoded = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            encoded = json.dumps({"error": message}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer((config.host, config.port), DashboardHandler)
    print(f"Dashboard running at http://{config.host}:{config.port}")
    server.serve_forever()


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


def _has_buy_opportunity(min_unit_price: object, recommended_buy_price: object) -> bool:
    if min_unit_price is None or recommended_buy_price is None:
        return False
    return int(min_unit_price) < int(recommended_buy_price)


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
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 18px;
      padding: 18px 24px 28px;
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
      min-width: 1120px;
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
      main { grid-template-columns: 1fr; padding: 14px; }
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
      <span class="refresh-status" id="refresh-status">Not refreshed</span>
      <button id="refresh" type="button">Refresh</button>
    </div>
  </header>
  <main>
    <div>
      <div class="grid">
        <div class="metric"><span>Database</span><strong id="db-size">-</strong></div>
        <div class="metric"><span>Fetch Runs</span><strong id="fetch-runs">-</strong></div>
        <div class="metric"><span>Listings</span><strong id="listings">-</strong></div>
        <div class="metric"><span>Latest Run</span><strong id="latest-run">-</strong></div>
        <div class="metric"><span>New Listings</span><strong id="new-listings">-</strong></div>
        <div class="metric"><span>Missing Listings</span><strong id="missing-listings">-</strong></div>
      </div>
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
                <th>Min<span class="column-help" tabindex="0" title="Lowest unit price currently listed in the latest snapshot." aria-label="Lowest unit price currently listed in the latest snapshot.">?</span></th>
                <th>Q1<span class="column-help" tabindex="0" title="First quartile unit price from the latest snapshot; useful as a conservative market price." aria-label="First quartile unit price from the latest snapshot; useful as a conservative market price.">?</span></th>
                <th>Median<span class="column-help" tabindex="0" title="Median unit price from the latest snapshot." aria-label="Median unit price from the latest snapshot.">?</span></th>
                <th>Q3<span class="column-help" tabindex="0" title="Third quartile unit price from the latest snapshot; helps show the upper typical price band." aria-label="Third quartile unit price from the latest snapshot; helps show the upper typical price band.">?</span></th>
                <th>Buy At<span class="column-help" tabindex="0" title="Recommended maximum buy price, targeting margin against the conservative sell price." aria-label="Recommended maximum buy price, targeting margin against the conservative sell price.">?</span></th>
                <th>Sell At<span class="column-help" tabindex="0" title="Recommended conservative sell price, preferring recent first-quartile pricing and falling back to median pricing." aria-label="Recommended conservative sell price, preferring recent first-quartile pricing and falling back to median pricing.">?</span></th>
                <th>Profit<span class="column-help" tabindex="0" title="Potential per-item profit before fees, calculated as recommended sell price minus recommended buy price." aria-label="Potential per-item profit before fees, calculated as recommended sell price minus recommended buy price.">?</span></th>
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
  </main>
  <script>
    let overview = null;
    let selectedItemId = null;

    const els = {
      dbSize: document.getElementById('db-size'),
      fetchRuns: document.getElementById('fetch-runs'),
      listings: document.getElementById('listings'),
      latestRun: document.getElementById('latest-run'),
      newListings: document.getElementById('new-listings'),
      missingListings: document.getElementById('missing-listings'),
      latestTime: document.getElementById('latest-time'),
      items: document.getElementById('items'),
      recommendations: document.getElementById('recommendations'),
      runs: document.getElementById('runs'),
      filter: document.getElementById('filter'),
      timezone: document.getElementById('timezone'),
      refresh: document.getElementById('refresh'),
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

    function profit(buyPrice, sellPrice) {
      if (buyPrice === null || buyPrice === undefined || sellPrice === null || sellPrice === undefined) {
        return { text: '-', cls: 'muted' };
      }
      const value = sellPrice - buyPrice;
      return { text: gold(value), cls: value > 0 ? 'delta-down' : value < 0 ? 'delta-up' : 'muted' };
    }

    async function loadOverview() {
      els.refresh.disabled = true;
      const timezone = encodeURIComponent(els.timezone.value);
      const response = await fetch(`/api/overview?timezone=${timezone}&t=${Date.now()}`, { cache: 'no-store' });
      overview = await response.json();
      renderOverview();
      els.refreshStatus.textContent = `Refreshed ${new Date().toLocaleTimeString()}`;
      els.refresh.disabled = false;
      const first = overview.items[0];
      if (!selectedItemId && first) selectItem(first.item_id);
    }

    function renderOverview() {
      els.dbSize.textContent = overview.database.size_label;
      els.fetchRuns.textContent = integer(overview.counts.fetch_runs);
      els.listings.textContent = integer(overview.counts.auction_listings);
      els.latestRun.textContent = overview.latest_run ? `#${overview.latest_run.id}` : '-';
      els.newListings.textContent = integer(overview.latest_lifecycle.new);
      els.missingListings.textContent = integer(overview.latest_lifecycle.missing);
      els.latestTime.textContent = overview.latest_run ? shortTime(overview.latest_run.finished_at) : '-';
      renderItems();
      renderRecommendations();
      renderRuns();
    }

    function renderItems() {
      const query = els.filter.value.trim().toLowerCase();
      const rows = overview.items.filter((item) => {
        return !query || item.name.toLowerCase().includes(query) || String(item.item_id).includes(query);
      });
      els.items.innerHTML = rows.map((item) => {
        const change = delta(item.min_unit_price, item.previous_min_unit_price);
        const potentialProfit = profit(item.recommended_buy_price, item.recommended_sell_price);
        const sellThroughBps = item.average_sell_through_ratio_bps ?? item.sell_through_ratio_bps;
        const rowClasses = [
          item.item_id === selectedItemId ? 'selected' : '',
          item.has_buy_opportunity ? 'buy-opportunity' : ''
        ].filter(Boolean).join(' ');
        const classAttribute = rowClasses ? ` class="${rowClasses}"` : '';
        const icon = item.icon_url ? `<img class="item-icon" src="${item.icon_url}" alt="">` : '';
        const subtitle = [item.item_class, item.item_subclass].filter(Boolean).join(' / ');
        return `<tr${classAttribute} data-item-id="${item.item_id}">
          <td><span class="item-cell">${icon}<span class="item-meta"><span>${item.name}</span><small>${subtitle}</small></span></span></td>
          <td><span class="quality-badge ${qualityClass(item.crafting_quality)}">${qualityLabel(item.crafting_quality)}</span></td>
          <td>${item.item_id}</td>
          <td>${gold(item.min_unit_price)}</td>
          <td>${gold(item.first_quartile_unit_price)}</td>
          <td>${gold(item.median_unit_price)}</td>
          <td>${gold(item.third_quartile_unit_price)}</td>
          <td>${gold(item.recommended_buy_price)}</td>
          <td>${gold(item.recommended_sell_price)}</td>
          <td class="${potentialProfit.cls}">${potentialProfit.text}</td>
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
          <div class="reasons">${gold(item.recommended_buy_price)} buy, ${gold(item.recommended_sell_price)} sell, ${item.confidence}% confidence</div>
          ${timing}
          <div class="reasons">${item.reasons.join('; ')}</div>
        </div>`;
      }).join('');
      els.recommendations.querySelectorAll('.recommendation').forEach((row) => {
        row.addEventListener('click', () => selectItem(Number(row.dataset.itemId)));
      });
    }

    async function selectItem(itemId) {
      selectedItemId = itemId;
      renderItems();
      renderRecommendations();
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
      const values = points.flatMap((row) => [
        row.first_quartile_unit_price,
        row.median_unit_price,
        row.third_quartile_unit_price
      ].filter(Boolean));
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

      drawLine(ctx, points, x, y, 'first_quartile_unit_price', '#176b87');
      drawLine(ctx, points, x, y, 'median_unit_price', '#8a5a1f');
      drawLine(ctx, points, x, y, 'third_quartile_unit_price', '#476f3f');
      ctx.fillStyle = '#176b87';
      ctx.fillText('Q1', pad.left, canvas.height - 14);
      ctx.fillStyle = '#8a5a1f';
      ctx.fillText('Median', pad.left + 46, canvas.height - 14);
      ctx.fillStyle = '#476f3f';
      ctx.fillText('Q3', pad.left + 112, canvas.height - 14);
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

    els.refresh.addEventListener('click', loadOverview);
    els.timezone.addEventListener('change', loadOverview);
    els.filter.addEventListener('input', renderItems);
    loadOverview();
    setInterval(loadOverview, 30000);
  </script>
</body>
</html>
"""
