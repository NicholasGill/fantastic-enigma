from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class RecommendationInputs:
    item_id: int
    name: str
    market: str
    snapshots: int
    latest_min_unit_price: int | None
    latest_median_unit_price: int | None
    latest_listing_count: int
    latest_total_quantity: int
    average_median_unit_price: int | None
    average_weighted_unit_price: int | None
    average_listing_count: float
    average_total_quantity: float
    recent_quantity_drop_ratio: float


@dataclass(frozen=True)
class Recommendation:
    item_id: int
    name: str
    market: str
    action: str
    score: int
    confidence: int
    latest_min_unit_price: int | None
    latest_median_unit_price: int | None
    average_median_unit_price: int | None
    average_weighted_unit_price: int | None
    estimated_demand_score: int
    reasons: list[str]


class RecommendationEngine:
    def __init__(self, database_url: str, *, lookback_runs: int = 12, min_snapshots: int = 3) -> None:
        if lookback_runs <= 1:
            raise ValueError("lookback_runs must be greater than 1")
        if min_snapshots <= 0:
            raise ValueError("min_snapshots must be greater than 0")

        self.database_path = _sqlite_database_path(database_url)
        self.lookback_runs = lookback_runs
        self.min_snapshots = min_snapshots

    def recommend(self, *, limit: int | None = None) -> list[Recommendation]:
        inputs = self._load_inputs()
        recommendations = [self._score(item) for item in inputs]
        recommendations.sort(key=lambda item: (item.score, item.confidence, item.estimated_demand_score), reverse=True)
        if limit is not None:
            return recommendations[:limit]
        return recommendations

    def _load_inputs(self) -> list[RecommendationInputs]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            item_rows = connection.execute(
                """
                select item_id, coalesce(name, 'Item ' || item_id) as name, market
                from tracked_items
                order by item_id
                """
            ).fetchall()
            return [self._load_item_inputs(connection, row) for row in item_rows]

    def _load_item_inputs(self, connection: sqlite3.Connection, item_row: sqlite3.Row) -> RecommendationInputs:
        source_table = (
            "item_history_metrics"
            if _history_metric_count(connection, int(item_row["item_id"])) >= self.min_snapshots
            else "item_summaries"
        )
        weighted_column = "weighted_average_unit_price" if source_table == "item_history_metrics" else "median_unit_price"
        rows = connection.execute(
            f"""
            select
                s.fetch_run_id,
                s.listing_count,
                s.total_quantity,
                s.min_unit_price,
                s.median_unit_price,
                s.{weighted_column} as weighted_average_unit_price
            from {source_table} s
            join fetch_runs r on r.id = s.fetch_run_id
            where s.item_id = ? and r.status = 'success'
            order by s.fetch_run_id desc
            limit ?
            """,
            (item_row["item_id"], self.lookback_runs),
        ).fetchall()
        rows = list(reversed(rows))
        latest = rows[-1] if rows else None
        medians = [int(row["median_unit_price"]) for row in rows if row["median_unit_price"] is not None]
        weighted_averages = [
            int(row["weighted_average_unit_price"])
            for row in rows
            if row["weighted_average_unit_price"] is not None
        ]
        listing_counts = [int(row["listing_count"]) for row in rows]
        quantities = [int(row["total_quantity"]) for row in rows]

        return RecommendationInputs(
            item_id=int(item_row["item_id"]),
            name=str(item_row["name"]),
            market=str(item_row["market"]),
            snapshots=len(rows),
            latest_min_unit_price=int(latest["min_unit_price"]) if latest and latest["min_unit_price"] is not None else None,
            latest_median_unit_price=(
                int(latest["median_unit_price"]) if latest and latest["median_unit_price"] is not None else None
            ),
            latest_listing_count=int(latest["listing_count"]) if latest else 0,
            latest_total_quantity=int(latest["total_quantity"]) if latest else 0,
            average_median_unit_price=int(mean(medians)) if medians else None,
            average_weighted_unit_price=int(mean(weighted_averages)) if weighted_averages else None,
            average_listing_count=mean(listing_counts) if listing_counts else 0.0,
            average_total_quantity=mean(quantities) if quantities else 0.0,
            recent_quantity_drop_ratio=_recent_drop_ratio(quantities),
        )

    def _score(self, inputs: RecommendationInputs) -> Recommendation:
        if inputs.snapshots < self.min_snapshots:
            return Recommendation(
                item_id=inputs.item_id,
                name=inputs.name,
                market=inputs.market,
                action="watch",
                score=0,
                confidence=_confidence(inputs.snapshots, self.lookback_runs),
                latest_min_unit_price=inputs.latest_min_unit_price,
                latest_median_unit_price=inputs.latest_median_unit_price,
                average_median_unit_price=inputs.average_median_unit_price,
                average_weighted_unit_price=inputs.average_weighted_unit_price,
                estimated_demand_score=0,
                reasons=[f"needs at least {self.min_snapshots} snapshots"],
            )

        average_price = inputs.average_weighted_unit_price or inputs.average_median_unit_price
        price_score = _price_discount_score(inputs.latest_min_unit_price, average_price)
        scarcity_score = _scarcity_score(inputs.latest_listing_count, inputs.average_listing_count)
        demand_score = round(inputs.recent_quantity_drop_ratio * 100)
        confidence = _confidence(inputs.snapshots, self.lookback_runs)
        score = round((price_score * 0.45) + (demand_score * 0.25) + (scarcity_score * 0.15) + (confidence * 0.15))
        action = _action_for_score(score)

        return Recommendation(
            item_id=inputs.item_id,
            name=inputs.name,
            market=inputs.market,
            action=action,
            score=max(0, min(score, 100)),
            confidence=confidence,
            latest_min_unit_price=inputs.latest_min_unit_price,
            latest_median_unit_price=inputs.latest_median_unit_price,
            average_median_unit_price=inputs.average_median_unit_price,
            average_weighted_unit_price=inputs.average_weighted_unit_price,
            estimated_demand_score=max(0, min(demand_score, 100)),
            reasons=_reasons(inputs, price_score, scarcity_score, demand_score),
        )


def recommendation_to_dict(recommendation: Recommendation) -> dict[str, Any]:
    return {
        "item_id": recommendation.item_id,
        "name": recommendation.name,
        "market": recommendation.market,
        "action": recommendation.action,
        "score": recommendation.score,
        "confidence": recommendation.confidence,
        "latest_min_unit_price": recommendation.latest_min_unit_price,
        "latest_median_unit_price": recommendation.latest_median_unit_price,
        "average_median_unit_price": recommendation.average_median_unit_price,
        "average_weighted_unit_price": recommendation.average_weighted_unit_price,
        "estimated_demand_score": recommendation.estimated_demand_score,
        "reasons": recommendation.reasons,
    }


def _sqlite_database_path(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("recommendations currently require a file-backed sqlite database")
    return str(Path(url.database))


def _price_discount_score(latest_min: int | None, average_median: int | None) -> int:
    if latest_min is None or average_median is None or average_median <= 0:
        return 0
    discount_ratio = (average_median - latest_min) / average_median
    return round(max(0.0, min(discount_ratio, 1.0)) * 100)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _history_metric_count(connection: sqlite3.Connection, item_id: int) -> int:
    if not _table_exists(connection, "item_history_metrics"):
        return 0
    return int(connection.execute(
        "select count(*) from item_history_metrics where item_id = ?",
        (item_id,),
    ).fetchone()[0])


def _scarcity_score(latest_listing_count: int, average_listing_count: float) -> int:
    if average_listing_count <= 0:
        return 0
    scarcity_ratio = (average_listing_count - latest_listing_count) / average_listing_count
    return round(max(0.0, min(scarcity_ratio, 1.0)) * 100)


def _recent_drop_ratio(quantities: list[int]) -> float:
    if len(quantities) < 2:
        return 0.0

    drops: list[float] = []
    for previous, current in zip(quantities, quantities[1:]):
        if previous <= 0:
            continue
        drops.append(max(0.0, (previous - current) / previous))
    return mean(drops) if drops else 0.0


def _confidence(snapshot_count: int, lookback_runs: int) -> int:
    return round(max(0.0, min(snapshot_count / lookback_runs, 1.0)) * 100)


def _action_for_score(score: int) -> str:
    if score >= 60:
        return "buy"
    if score >= 35:
        return "watch"
    return "avoid"


def _reasons(
    inputs: RecommendationInputs,
    price_score: int,
    scarcity_score: int,
    demand_score: int,
) -> list[str]:
    reasons: list[str] = []
    if price_score > 0:
        reasons.append(f"lowest listing is {price_score}% below recent median")
    if demand_score > 0:
        reasons.append(f"recent listed quantity dropped by an estimated {demand_score}%")
    if scarcity_score > 0:
        reasons.append(f"listing count is {scarcity_score}% below recent average")
    if inputs.snapshots > 0:
        reasons.append(f"based on {inputs.snapshots} snapshots")
    return reasons or ["no strong pricing or demand signal"]
