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
    average_first_quartile_unit_price: int | None
    average_median_unit_price: int | None
    average_third_quartile_unit_price: int | None
    average_weighted_unit_price: int | None
    average_listing_count: float
    average_total_quantity: float
    recent_quantity_drop_ratio: float
    average_sell_through_ratio: float
    average_sell_through_confidence: int
    player_post_count: int
    player_sold_count: int
    player_expired_count: int
    player_cancelled_count: int
    player_sale_rate: float
    average_player_net_proceeds: int | None


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
    recommended_sell_price: int | None
    average_first_quartile_unit_price: int | None
    average_median_unit_price: int | None
    average_third_quartile_unit_price: int | None
    average_weighted_unit_price: int | None
    estimated_demand_score: int
    average_sell_through_ratio: float
    average_sell_through_confidence: int
    player_post_count: int
    player_sold_count: int
    player_expired_count: int
    player_cancelled_count: int
    player_sale_rate: float
    average_player_net_proceeds: int | None
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
                s.first_quartile_unit_price,
                s.median_unit_price,
                s.third_quartile_unit_price,
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
        first_quartiles = [
            int(row["first_quartile_unit_price"])
            for row in rows
            if "first_quartile_unit_price" in row.keys() and row["first_quartile_unit_price"] is not None
        ]
        medians = [int(row["median_unit_price"]) for row in rows if row["median_unit_price"] is not None]
        third_quartiles = [
            int(row["third_quartile_unit_price"])
            for row in rows
            if "third_quartile_unit_price" in row.keys() and row["third_quartile_unit_price"] is not None
        ]
        weighted_averages = [
            int(row["weighted_average_unit_price"])
            for row in rows
            if row["weighted_average_unit_price"] is not None
        ]
        listing_counts = [int(row["listing_count"]) for row in rows]
        quantities = [int(row["total_quantity"]) for row in rows]
        sell_through = _load_sell_through_inputs(
            connection,
            int(item_row["item_id"]),
            self.lookback_runs,
        )
        player_outcomes = _load_player_outcome_inputs(connection, int(item_row["item_id"]))

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
            average_first_quartile_unit_price=int(mean(first_quartiles)) if first_quartiles else None,
            average_median_unit_price=int(mean(medians)) if medians else None,
            average_third_quartile_unit_price=int(mean(third_quartiles)) if third_quartiles else None,
            average_weighted_unit_price=int(mean(weighted_averages)) if weighted_averages else None,
            average_listing_count=mean(listing_counts) if listing_counts else 0.0,
            average_total_quantity=mean(quantities) if quantities else 0.0,
            recent_quantity_drop_ratio=_recent_drop_ratio(quantities),
            average_sell_through_ratio=sell_through[0],
            average_sell_through_confidence=sell_through[1],
            player_post_count=player_outcomes["post_count"],
            player_sold_count=player_outcomes["sold_count"],
            player_expired_count=player_outcomes["expired_count"],
            player_cancelled_count=player_outcomes["cancelled_count"],
            player_sale_rate=player_outcomes["sale_rate"],
            average_player_net_proceeds=player_outcomes["average_net_proceeds"],
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
                recommended_sell_price=_recommended_sell_price(inputs),
                average_first_quartile_unit_price=inputs.average_first_quartile_unit_price,
                average_median_unit_price=inputs.average_median_unit_price,
                average_third_quartile_unit_price=inputs.average_third_quartile_unit_price,
                average_weighted_unit_price=inputs.average_weighted_unit_price,
                estimated_demand_score=0,
                average_sell_through_ratio=inputs.average_sell_through_ratio,
                average_sell_through_confidence=inputs.average_sell_through_confidence,
                player_post_count=inputs.player_post_count,
                player_sold_count=inputs.player_sold_count,
                player_expired_count=inputs.player_expired_count,
                player_cancelled_count=inputs.player_cancelled_count,
                player_sale_rate=inputs.player_sale_rate,
                average_player_net_proceeds=inputs.average_player_net_proceeds,
                reasons=[f"needs at least {self.min_snapshots} snapshots"],
            )

        average_price = inputs.average_weighted_unit_price or inputs.average_median_unit_price
        price_score = _price_discount_score(inputs.latest_min_unit_price, average_price)
        scarcity_score = _scarcity_score(inputs.latest_listing_count, inputs.average_listing_count)
        demand_score = _demand_score(inputs.recent_quantity_drop_ratio, inputs.average_sell_through_ratio)
        confidence = _confidence(inputs.snapshots, self.lookback_runs)
        if inputs.average_sell_through_confidence > 0:
            confidence = round((confidence * 0.75) + (inputs.average_sell_through_confidence * 0.25))
        player_confidence = _player_outcome_confidence(inputs.player_post_count)
        if player_confidence > 0:
            demand_score = _player_blended_demand_score(demand_score, inputs.player_sale_rate, player_confidence)
            confidence = max(confidence, player_confidence)
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
            recommended_sell_price=_recommended_sell_price(inputs),
            average_first_quartile_unit_price=inputs.average_first_quartile_unit_price,
            average_median_unit_price=inputs.average_median_unit_price,
            average_third_quartile_unit_price=inputs.average_third_quartile_unit_price,
            average_weighted_unit_price=inputs.average_weighted_unit_price,
            estimated_demand_score=max(0, min(demand_score, 100)),
            average_sell_through_ratio=inputs.average_sell_through_ratio,
            average_sell_through_confidence=inputs.average_sell_through_confidence,
            player_post_count=inputs.player_post_count,
            player_sold_count=inputs.player_sold_count,
            player_expired_count=inputs.player_expired_count,
            player_cancelled_count=inputs.player_cancelled_count,
            player_sale_rate=inputs.player_sale_rate,
            average_player_net_proceeds=inputs.average_player_net_proceeds,
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
        "recommended_sell_price": recommendation.recommended_sell_price,
        "average_first_quartile_unit_price": recommendation.average_first_quartile_unit_price,
        "average_median_unit_price": recommendation.average_median_unit_price,
        "average_third_quartile_unit_price": recommendation.average_third_quartile_unit_price,
        "average_weighted_unit_price": recommendation.average_weighted_unit_price,
        "estimated_demand_score": recommendation.estimated_demand_score,
        "average_sell_through_ratio": recommendation.average_sell_through_ratio,
        "average_sell_through_confidence": recommendation.average_sell_through_confidence,
        "player_post_count": recommendation.player_post_count,
        "player_sold_count": recommendation.player_sold_count,
        "player_expired_count": recommendation.player_expired_count,
        "player_cancelled_count": recommendation.player_cancelled_count,
        "player_sale_rate": recommendation.player_sale_rate,
        "average_player_net_proceeds": recommendation.average_player_net_proceeds,
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


def _recommended_sell_price(inputs: RecommendationInputs) -> int | None:
    return (
        inputs.average_first_quartile_unit_price
        or inputs.average_median_unit_price
        or inputs.latest_median_unit_price
    )


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


def _load_sell_through_inputs(
    connection: sqlite3.Connection,
    item_id: int,
    lookback_runs: int,
) -> tuple[float, int]:
    if not _table_exists(connection, "sell_through_metrics"):
        return (0.0, 0)

    rows = connection.execute(
        """
        select sell_through_ratio_bps, confidence
        from sell_through_metrics s
        join fetch_runs r on r.id = s.fetch_run_id
        where s.item_id = ? and r.status = 'success'
        order by s.fetch_run_id desc
        limit ?
        """,
        (item_id, lookback_runs),
    ).fetchall()
    if not rows:
        return (0.0, 0)

    return (
        mean(int(row["sell_through_ratio_bps"]) / 10000 for row in rows),
        round(mean(int(row["confidence"]) for row in rows)),
    )


def _load_player_outcome_inputs(connection: sqlite3.Connection, item_id: int) -> dict[str, Any]:
    if not _table_exists(connection, "player_auction_outcomes"):
        return _empty_player_outcomes()

    outcome_rows = connection.execute(
        """
        select outcome, money
        from player_auction_outcomes
        where item_id = ?
        """,
        (item_id,),
    ).fetchall()
    post_count = 0
    if _table_exists(connection, "player_auction_posts"):
        post_count = int(connection.execute(
            "select count(*) from player_auction_posts where item_id = ?",
            (item_id,),
        ).fetchone()[0])

    sold_count = sum(1 for row in outcome_rows if row["outcome"] == "sold")
    expired_count = sum(1 for row in outcome_rows if row["outcome"] == "expired")
    cancelled_count = sum(1 for row in outcome_rows if row["outcome"] == "cancelled")
    known_outcomes = sold_count + expired_count + cancelled_count
    net_proceeds = [int(row["money"]) for row in outcome_rows if row["outcome"] == "sold" and row["money"] is not None]
    denominator = post_count if post_count > 0 else known_outcomes
    sale_rate = sold_count / denominator if denominator > 0 else 0.0
    return {
        "post_count": post_count,
        "sold_count": sold_count,
        "expired_count": expired_count,
        "cancelled_count": cancelled_count,
        "sale_rate": sale_rate,
        "average_net_proceeds": int(mean(net_proceeds)) if net_proceeds else None,
    }


def _empty_player_outcomes() -> dict[str, Any]:
    return {
        "post_count": 0,
        "sold_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "sale_rate": 0.0,
        "average_net_proceeds": None,
    }


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


def _demand_score(recent_quantity_drop_ratio: float, average_sell_through_ratio: float) -> int:
    quantity_drop_score = recent_quantity_drop_ratio * 100
    sell_through_score = average_sell_through_ratio * 100
    return round(max(quantity_drop_score, (quantity_drop_score * 0.35) + (sell_through_score * 0.65)))


def _player_outcome_confidence(post_count: int) -> int:
    return round(max(0.0, min(post_count / 10, 1.0)) * 100)


def _player_blended_demand_score(inferred_demand_score: int, player_sale_rate: float, confidence: int) -> int:
    player_score = round(max(0.0, min(player_sale_rate, 1.0)) * 100)
    player_weight = max(0.0, min(confidence / 100, 1.0))
    return round((inferred_demand_score * (1 - player_weight)) + (player_score * player_weight))


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
        if inputs.player_post_count > 0:
            reasons.append(
                f"personal sale rate is {round(inputs.player_sale_rate * 100)}% "
                f"across {inputs.player_post_count} posts"
            )
        elif inputs.average_sell_through_ratio > 0:
            reasons.append(f"inferred sell-through is {round(inputs.average_sell_through_ratio * 100)}%")
        else:
            reasons.append(f"recent listed quantity dropped by an estimated {demand_score}%")
    if inputs.average_player_net_proceeds is not None:
        reasons.append(f"average personal sale proceeds are {inputs.average_player_net_proceeds / 10000:.2f}g")
    if scarcity_score > 0:
        reasons.append(f"listing count is {scarcity_score}% below recent average")
    if inputs.snapshots > 0:
        reasons.append(f"based on {inputs.snapshots} snapshots")
    return reasons or ["no strong pricing or demand signal"]
