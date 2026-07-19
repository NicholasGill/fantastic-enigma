from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.engine import make_url

AUCTION_DEPOSIT_RATE_BPS_BY_DURATION_HOURS = {
    12: 1500,
    24: 3000,
    48: 6000,
}
DEFAULT_AUCTION_DURATION_HOURS = 48
CURRENT_PRICE_QUANTITY_SHIFT = 5


@dataclass(frozen=True)
class RecommendationInputs:
    item_id: int
    name: str
    market: str
    snapshots: int
    latest_min_unit_price: int | None
    latest_shifted_unit_price: int | None
    latest_median_unit_price: int | None
    latest_listing_count: int
    latest_total_quantity: int
    average_first_quartile_unit_price: int | None
    average_median_unit_price: int | None
    average_third_quartile_unit_price: int | None
    average_weighted_unit_price: int | None
    average_listing_count: float
    average_total_quantity: float
    price_trend_score: int
    price_trend_ratio: float
    recent_min_change_ratio: float
    recent_quantity_drop_ratio: float
    average_sell_through_ratio: float
    average_sell_through_confidence: int
    average_probable_sold_unit_price: int | None
    vendor_sell_unit_price: int | None
    auction_deposit_unit_price: int | None
    player_post_count: int
    player_sold_count: int
    player_expired_count: int
    player_cancelled_count: int
    player_sale_rate: float
    average_player_net_proceeds: int | None
    best_buy_time: str | None
    best_sell_time: str | None
    historical_buy_price: int | None
    historical_sell_price: int | None
    historical_timing_confidence: int


@dataclass(frozen=True)
class Recommendation:
    item_id: int
    name: str
    market: str
    action: str
    score: int
    buy_score: int
    sell_score: int
    confidence: int
    latest_min_unit_price: int | None
    latest_shifted_unit_price: int | None
    latest_median_unit_price: int | None
    recommended_buy_price: int | None
    recommended_sell_price: int | None
    recommended_sell_price_source: str | None
    average_first_quartile_unit_price: int | None
    average_median_unit_price: int | None
    average_third_quartile_unit_price: int | None
    average_weighted_unit_price: int | None
    price_trend_score: int
    price_trend_ratio: float
    recent_min_change_ratio: float
    estimated_demand_score: int
    average_sell_through_ratio: float
    average_sell_through_confidence: int
    average_probable_sold_unit_price: int | None
    vendor_sell_unit_price: int | None
    auction_deposit_unit_price: int | None
    estimated_profit_unit_price: int | None
    sell_profit_unit_price: int | None
    player_post_count: int
    player_sold_count: int
    player_expired_count: int
    player_cancelled_count: int
    player_sale_rate: float
    average_player_net_proceeds: int | None
    best_buy_time: str | None
    best_sell_time: str | None
    historical_buy_price: int | None
    historical_sell_price: int | None
    historical_timing_confidence: int
    reasons: list[str]


DEFAULT_DISPLAY_TIMEZONE = "America/New_York"


class RecommendationEngine:
    def __init__(
        self,
        database_url: str,
        *,
        lookback_runs: int = 12,
        sell_price_lookback_runs: int = 48,
        auction_duration_hours: int = DEFAULT_AUCTION_DURATION_HOURS,
        min_snapshots: int = 3,
        display_timezone: str = DEFAULT_DISPLAY_TIMEZONE,
    ) -> None:
        if lookback_runs <= 1:
            raise ValueError("lookback_runs must be greater than 1")
        if sell_price_lookback_runs <= 1:
            raise ValueError("sell_price_lookback_runs must be greater than 1")
        if auction_duration_hours not in AUCTION_DEPOSIT_RATE_BPS_BY_DURATION_HOURS:
            durations = ", ".join(str(duration) for duration in sorted(AUCTION_DEPOSIT_RATE_BPS_BY_DURATION_HOURS))
            raise ValueError(f"auction_duration_hours must be one of: {durations}")
        if min_snapshots <= 0:
            raise ValueError("min_snapshots must be greater than 0")

        self.database_path = _sqlite_database_path(database_url)
        self.lookback_runs = lookback_runs
        self.sell_price_lookback_runs = sell_price_lookback_runs
        self.auction_duration_hours = auction_duration_hours
        self.min_snapshots = min_snapshots
        self.display_timezone = _display_timezone(display_timezone)

    def recommend(
        self,
        *,
        limit: int | None = None,
        item_id: int | None = None,
    ) -> list[Recommendation]:
        inputs = self._load_inputs(item_id=item_id)
        recommendations = [self._score(item) for item in inputs]
        recommendations.sort(key=lambda item: (item.score, item.confidence, item.estimated_demand_score), reverse=True)
        if limit is not None:
            return recommendations[:limit]
        return recommendations

    def _load_inputs(self, *, item_id: int | None = None) -> list[RecommendationInputs]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            where_clause = "where t.item_id = ?" if item_id is not None else ""
            parameters: tuple[int, ...] = (item_id,) if item_id is not None else ()
            item_rows = connection.execute(
                f"""
                select
                    t.item_id,
                    coalesce(m.name, t.name, 'Item ' || t.item_id) as name,
                    t.market,
                    m.sell_price
                from tracked_items t
                left join item_metadata m on m.item_id = t.item_id
                {where_clause}
                order by t.item_id
                """,
                parameters,
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
        latest_fetch_run_id = int(latest["fetch_run_id"]) if latest else None
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
        trend_prices = [
            int(row["weighted_average_unit_price"] or row["median_unit_price"])
            for row in rows
            if row["weighted_average_unit_price"] is not None or row["median_unit_price"] is not None
        ]
        min_prices = [int(row["min_unit_price"]) for row in rows if row["min_unit_price"] is not None]
        price_trend_ratio = _price_trend_ratio(trend_prices)
        listing_counts = [int(row["listing_count"]) for row in rows]
        quantities = [int(row["total_quantity"]) for row in rows]
        sell_through = _load_sell_through_inputs(
            connection,
            int(item_row["item_id"]),
            self.lookback_runs,
            self.sell_price_lookback_runs,
        )
        player_outcomes = _load_player_outcome_inputs(connection, int(item_row["item_id"]))
        timing = _load_historical_timing_inputs(
            connection,
            int(item_row["item_id"]),
            source_table,
            self.display_timezone,
        )

        return RecommendationInputs(
            item_id=int(item_row["item_id"]),
            name=str(item_row["name"]),
            market=str(item_row["market"]),
            snapshots=len(rows),
            latest_min_unit_price=int(latest["min_unit_price"]) if latest and latest["min_unit_price"] is not None else None,
            latest_shifted_unit_price=(
                _load_shifted_unit_price(connection, int(item_row["item_id"]), latest_fetch_run_id)
                if latest_fetch_run_id is not None
                else None
            ),
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
            price_trend_score=_price_trend_score(price_trend_ratio),
            price_trend_ratio=price_trend_ratio,
            recent_min_change_ratio=_recent_min_change_ratio(min_prices),
            recent_quantity_drop_ratio=_recent_drop_ratio(quantities),
            average_sell_through_ratio=sell_through[0],
            average_sell_through_confidence=sell_through[1],
            average_probable_sold_unit_price=sell_through[2],
            vendor_sell_unit_price=_optional_int(item_row["sell_price"]),
            auction_deposit_unit_price=_auction_deposit_unit_price(
                _optional_int(item_row["sell_price"]),
                self.auction_duration_hours,
            ),
            player_post_count=player_outcomes["post_count"],
            player_sold_count=player_outcomes["sold_count"],
            player_expired_count=player_outcomes["expired_count"],
            player_cancelled_count=player_outcomes["cancelled_count"],
            player_sale_rate=player_outcomes["sale_rate"],
            average_player_net_proceeds=player_outcomes["average_net_proceeds"],
            best_buy_time=timing["best_buy_time"],
            best_sell_time=timing["best_sell_time"],
            historical_buy_price=timing["historical_buy_price"],
            historical_sell_price=timing["historical_sell_price"],
            historical_timing_confidence=timing["historical_timing_confidence"],
        )

    def _score(self, inputs: RecommendationInputs) -> Recommendation:
        if inputs.snapshots < self.min_snapshots:
            return Recommendation(
                item_id=inputs.item_id,
                name=inputs.name,
                market=inputs.market,
                action="watch",
                score=0,
                buy_score=0,
                sell_score=0,
                confidence=_confidence(inputs.snapshots, self.lookback_runs),
                latest_min_unit_price=inputs.latest_min_unit_price,
                latest_shifted_unit_price=inputs.latest_shifted_unit_price,
                latest_median_unit_price=inputs.latest_median_unit_price,
                recommended_buy_price=_recommended_buy_price(inputs),
                recommended_sell_price=_recommended_sell_price(inputs),
                recommended_sell_price_source=_recommended_sell_price_source(inputs),
                average_first_quartile_unit_price=inputs.average_first_quartile_unit_price,
                average_median_unit_price=inputs.average_median_unit_price,
                average_third_quartile_unit_price=inputs.average_third_quartile_unit_price,
                average_weighted_unit_price=inputs.average_weighted_unit_price,
                price_trend_score=inputs.price_trend_score,
                price_trend_ratio=inputs.price_trend_ratio,
                recent_min_change_ratio=inputs.recent_min_change_ratio,
                estimated_demand_score=0,
                average_sell_through_ratio=inputs.average_sell_through_ratio,
                average_sell_through_confidence=inputs.average_sell_through_confidence,
                average_probable_sold_unit_price=inputs.average_probable_sold_unit_price,
                vendor_sell_unit_price=inputs.vendor_sell_unit_price,
                auction_deposit_unit_price=inputs.auction_deposit_unit_price,
                estimated_profit_unit_price=_estimated_profit_unit_price(inputs),
                sell_profit_unit_price=_sell_profit_unit_price(inputs),
                player_post_count=inputs.player_post_count,
                player_sold_count=inputs.player_sold_count,
                player_expired_count=inputs.player_expired_count,
                player_cancelled_count=inputs.player_cancelled_count,
                player_sale_rate=inputs.player_sale_rate,
                average_player_net_proceeds=inputs.average_player_net_proceeds,
                best_buy_time=inputs.best_buy_time,
                best_sell_time=inputs.best_sell_time,
                historical_buy_price=inputs.historical_buy_price,
                historical_sell_price=inputs.historical_sell_price,
                historical_timing_confidence=inputs.historical_timing_confidence,
                reasons=[f"needs at least {self.min_snapshots} snapshots"],
            )

        average_price = inputs.average_weighted_unit_price or inputs.average_median_unit_price
        price_score = _price_discount_score(_latest_market_unit_price(inputs), average_price)
        scarcity_score = _scarcity_score(inputs.latest_listing_count, inputs.average_listing_count)
        demand_score = _demand_score(inputs.recent_quantity_drop_ratio, inputs.average_sell_through_ratio)
        confidence = _confidence(inputs.snapshots, self.lookback_runs)
        if inputs.average_sell_through_confidence > 0:
            confidence = round((confidence * 0.75) + (inputs.average_sell_through_confidence * 0.25))
        player_confidence = _player_outcome_confidence(inputs.player_post_count)
        if player_confidence > 0:
            demand_score = _player_blended_demand_score(demand_score, inputs.player_sale_rate, player_confidence)
            confidence = max(confidence, player_confidence)
        base_buy_score = round(
            (price_score * 0.45) + (demand_score * 0.25) + (scarcity_score * 0.15) + (confidence * 0.15)
        )
        buy_score = _clamp_score(
            base_buy_score
            + _buy_price_trend_adjustment(inputs.price_trend_score)
            - _buy_spike_penalty(inputs.recent_min_change_ratio)
        )
        sell_score = _sell_score(inputs, demand_score=demand_score, confidence=confidence)
        score = max(buy_score, sell_score)
        action = _action_for_scores(buy_score, sell_score)

        return Recommendation(
            item_id=inputs.item_id,
            name=inputs.name,
            market=inputs.market,
            action=action,
            score=score,
            buy_score=buy_score,
            sell_score=sell_score,
            confidence=confidence,
            latest_min_unit_price=inputs.latest_min_unit_price,
            latest_shifted_unit_price=inputs.latest_shifted_unit_price,
            latest_median_unit_price=inputs.latest_median_unit_price,
            recommended_buy_price=_recommended_buy_price(inputs),
            recommended_sell_price=_recommended_sell_price(inputs),
            recommended_sell_price_source=_recommended_sell_price_source(inputs),
            average_first_quartile_unit_price=inputs.average_first_quartile_unit_price,
            average_median_unit_price=inputs.average_median_unit_price,
            average_third_quartile_unit_price=inputs.average_third_quartile_unit_price,
            average_weighted_unit_price=inputs.average_weighted_unit_price,
            price_trend_score=inputs.price_trend_score,
            price_trend_ratio=inputs.price_trend_ratio,
            recent_min_change_ratio=inputs.recent_min_change_ratio,
            estimated_demand_score=max(0, min(demand_score, 100)),
            average_sell_through_ratio=inputs.average_sell_through_ratio,
            average_sell_through_confidence=inputs.average_sell_through_confidence,
            average_probable_sold_unit_price=inputs.average_probable_sold_unit_price,
            vendor_sell_unit_price=inputs.vendor_sell_unit_price,
            auction_deposit_unit_price=inputs.auction_deposit_unit_price,
            estimated_profit_unit_price=_estimated_profit_unit_price(inputs),
            sell_profit_unit_price=_sell_profit_unit_price(inputs),
            player_post_count=inputs.player_post_count,
            player_sold_count=inputs.player_sold_count,
            player_expired_count=inputs.player_expired_count,
            player_cancelled_count=inputs.player_cancelled_count,
            player_sale_rate=inputs.player_sale_rate,
            average_player_net_proceeds=inputs.average_player_net_proceeds,
            best_buy_time=inputs.best_buy_time,
            best_sell_time=inputs.best_sell_time,
            historical_buy_price=inputs.historical_buy_price,
            historical_sell_price=inputs.historical_sell_price,
            historical_timing_confidence=inputs.historical_timing_confidence,
            reasons=_reasons(inputs, price_score, scarcity_score, demand_score),
        )


def recommendation_to_dict(recommendation: Recommendation) -> dict[str, Any]:
    return {
        "item_id": recommendation.item_id,
        "name": recommendation.name,
        "market": recommendation.market,
        "action": recommendation.action,
        "score": recommendation.score,
        "buy_score": recommendation.buy_score,
        "sell_score": recommendation.sell_score,
        "confidence": recommendation.confidence,
        "latest_min_unit_price": recommendation.latest_min_unit_price,
        "latest_shifted_unit_price": recommendation.latest_shifted_unit_price,
        "latest_median_unit_price": recommendation.latest_median_unit_price,
        "recommended_buy_price": recommendation.recommended_buy_price,
        "recommended_sell_price": recommendation.recommended_sell_price,
        "recommended_buy_unit_price": recommendation.recommended_buy_price,
        "recommended_sell_unit_price": recommendation.recommended_sell_price,
        "recommended_sell_price_source": recommendation.recommended_sell_price_source,
        "average_first_quartile_unit_price": recommendation.average_first_quartile_unit_price,
        "average_median_unit_price": recommendation.average_median_unit_price,
        "average_third_quartile_unit_price": recommendation.average_third_quartile_unit_price,
        "average_weighted_unit_price": recommendation.average_weighted_unit_price,
        "price_trend_score": recommendation.price_trend_score,
        "price_trend_ratio": recommendation.price_trend_ratio,
        "recent_min_change_ratio": recommendation.recent_min_change_ratio,
        "estimated_demand_score": recommendation.estimated_demand_score,
        "average_sell_through_ratio": recommendation.average_sell_through_ratio,
        "average_sell_through_confidence": recommendation.average_sell_through_confidence,
        "average_probable_sold_unit_price": recommendation.average_probable_sold_unit_price,
        "vendor_sell_unit_price": recommendation.vendor_sell_unit_price,
        "auction_deposit_unit_price": recommendation.auction_deposit_unit_price,
        "estimated_profit_unit_price": recommendation.estimated_profit_unit_price,
        "sell_profit_unit_price": recommendation.sell_profit_unit_price,
        "player_post_count": recommendation.player_post_count,
        "player_sold_count": recommendation.player_sold_count,
        "player_expired_count": recommendation.player_expired_count,
        "player_cancelled_count": recommendation.player_cancelled_count,
        "player_sale_rate": recommendation.player_sale_rate,
        "average_player_net_proceeds": recommendation.average_player_net_proceeds,
        "best_buy_time": recommendation.best_buy_time,
        "best_sell_time": recommendation.best_sell_time,
        "historical_buy_price": recommendation.historical_buy_price,
        "historical_sell_price": recommendation.historical_sell_price,
        "historical_timing_confidence": recommendation.historical_timing_confidence,
        "reasons": recommendation.reasons,
    }


def _sqlite_database_path(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("recommendations currently require a file-backed sqlite database")
    return str(Path(url.database))


def _price_discount_score(latest_price: int | None, average_median: int | None) -> int:
    if latest_price is None or average_median is None or average_median <= 0:
        return 0
    discount_ratio = (average_median - latest_price) / average_median
    return round(max(0.0, min(discount_ratio, 1.0)) * 100)


def _price_trend_ratio(prices: list[int]) -> float:
    prices = [price for price in prices if price > 0]
    if len(prices) < 3:
        return 0.0

    midpoint = len(prices) // 2
    earlier = prices[:midpoint]
    recent = prices[midpoint:]
    earlier_average = mean(earlier)
    if earlier_average <= 0:
        return 0.0
    return (mean(recent) - earlier_average) / earlier_average


def _price_trend_score(price_trend_ratio: float) -> int:
    return round(max(0.0, min(100.0, 50 + (price_trend_ratio * 100))))


def _buy_price_trend_adjustment(price_trend_score: int) -> int:
    if price_trend_score >= 50:
        return 0
    return round((price_trend_score - 50) * 0.25)


def _buy_spike_penalty(recent_min_change_ratio: float) -> int:
    if recent_min_change_ratio <= 0:
        return 0
    return round(min(recent_min_change_ratio, 1.0) * 50)


def _recent_min_change_ratio(prices: list[int]) -> float:
    prices = [price for price in prices if price > 0]
    if len(prices) < 2:
        return 0.0

    latest = prices[-1]
    previous = next((price for price in reversed(prices[:-1]) if price != latest), prices[-2])
    if previous <= 0:
        return 0.0
    return (latest - previous) / previous


def _recommended_sell_price(inputs: RecommendationInputs) -> int | None:
    return inputs.average_probable_sold_unit_price


def _recommended_sell_price_source(inputs: RecommendationInputs) -> str | None:
    if inputs.average_probable_sold_unit_price is not None:
        return "probable_sold"
    return None


def _recommended_buy_price(inputs: RecommendationInputs) -> int | None:
    sell_price = _recommended_sell_price(inputs)
    if sell_price is None:
        return None

    deposit = inputs.auction_deposit_unit_price or 0
    target_buy_price = round(max(sell_price - deposit, 0) * 0.8)
    latest_price = _latest_market_unit_price(inputs)
    if latest_price is not None and latest_price <= target_buy_price:
        return latest_price
    return target_buy_price


def _sell_score(inputs: RecommendationInputs, *, demand_score: int, confidence: int) -> int:
    current_price = inputs.latest_min_unit_price or _latest_market_unit_price(inputs)
    if current_price is None:
        return 0

    target_price = _sell_signal_target_price(inputs)
    target_score = 0
    if target_price is not None and target_price > 0:
        premium_ratio = (current_price - target_price) / target_price
        if premium_ratio >= 0:
            target_score = 60 + round(min(premium_ratio, 0.5) * 50)
        elif premium_ratio >= -0.05:
            target_score = 45

    spike_score = 0
    if inputs.recent_min_change_ratio > 0:
        spike_score = round(min(inputs.recent_min_change_ratio, 1.0) * 100)
        if target_price is not None and current_price >= round(target_price * 0.95):
            spike_score += 15

    base_score = max(target_score, spike_score)
    if base_score <= 0:
        return 0
    return _clamp_score(base_score + round(demand_score * 0.10) + round(confidence * 0.10))


def _sell_signal_target_price(inputs: RecommendationInputs) -> int | None:
    return (
        _recommended_sell_price(inputs)
        or inputs.historical_sell_price
        or inputs.average_third_quartile_unit_price
        or inputs.average_median_unit_price
    )


def _latest_market_unit_price(inputs: RecommendationInputs) -> int | None:
    return (
        inputs.latest_shifted_unit_price
        or inputs.latest_median_unit_price
        or inputs.latest_min_unit_price
    )


def _load_shifted_unit_price(
    connection: sqlite3.Connection,
    item_id: int,
    fetch_run_id: int,
    *,
    quantity_shift: int = CURRENT_PRICE_QUANTITY_SHIFT,
) -> int | None:
    rows = connection.execute(
        """
        select quantity, unit_price, buyout
        from auction_listings
        where fetch_run_id = ?
            and item_id = ?
            and (unit_price is not null or buyout is not null)
        order by
            coalesce(unit_price, buyout / nullif(quantity, 0)),
            auction_id,
            id
        """,
        (fetch_run_id, item_id),
    ).fetchall()
    if not rows:
        return None

    cumulative_quantity = 0
    fallback_price: int | None = None
    for row in rows:
        quantity = max(int(row["quantity"] or 0), 1)
        unit_price = _listing_unit_price(row)
        if unit_price is None:
            continue
        fallback_price = unit_price
        cumulative_quantity += quantity
        if cumulative_quantity >= quantity_shift:
            return unit_price
    return fallback_price


def _listing_unit_price(row: sqlite3.Row) -> int | None:
    if row["unit_price"] is not None:
        return int(row["unit_price"])
    quantity = int(row["quantity"] or 0)
    if quantity <= 0 or row["buyout"] is None:
        return None
    return int(row["buyout"]) // quantity


def _estimated_profit_unit_price(inputs: RecommendationInputs) -> int | None:
    buy_price = _recommended_buy_price(inputs)
    sell_price = _recommended_sell_price(inputs)
    if buy_price is None or sell_price is None:
        return None
    return sell_price - buy_price - (inputs.auction_deposit_unit_price or 0)


def _sell_profit_unit_price(inputs: RecommendationInputs) -> int | None:
    current_price = inputs.latest_min_unit_price or _latest_market_unit_price(inputs)
    buy_price = _recommended_buy_price(inputs)
    if current_price is None or buy_price is None:
        return None
    return current_price - buy_price - (inputs.auction_deposit_unit_price or 0)


def _auction_deposit_unit_price(vendor_sell_price: int | None, duration_hours: int) -> int | None:
    if vendor_sell_price is None:
        return None
    return vendor_sell_price * AUCTION_DEPOSIT_RATE_BPS_BY_DURATION_HOURS[duration_hours] // 10000


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


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
    sell_price_lookback_runs: int,
) -> tuple[float, int, int | None]:
    if not _table_exists(connection, "sell_through_metrics"):
        return (0.0, 0, None)

    rows = connection.execute(
        """
        select sell_through_ratio_bps, confidence, probable_sold_average_unit_price
        from sell_through_metrics s
        join fetch_runs r on r.id = s.fetch_run_id
        where s.item_id = ? and r.status = 'success'
        order by s.fetch_run_id desc
        limit ?
        """,
        (item_id, lookback_runs),
    ).fetchall()
    if not rows:
        return (0.0, 0, None)

    probable_sold_rows = connection.execute(
        """
        select probable_sold_average_unit_price
        from sell_through_metrics s
        join fetch_runs r on r.id = s.fetch_run_id
        where s.item_id = ?
            and r.status = 'success'
            and s.probable_sold_average_unit_price is not null
        order by s.fetch_run_id desc
        limit ?
        """,
        (item_id, sell_price_lookback_runs),
    ).fetchall()
    probable_sold_prices = _without_price_outliers(
        [int(row["probable_sold_average_unit_price"]) for row in probable_sold_rows]
    )

    return (
        _weighted_recent_average([int(row["sell_through_ratio_bps"]) / 10000 for row in rows]),
        round(_weighted_recent_average([int(row["confidence"]) for row in rows])),
        int(_weighted_recent_average(probable_sold_prices)) if probable_sold_prices else None,
    )


def _without_price_outliers(prices: list[int]) -> list[int]:
    if len(prices) < 3:
        return prices
    midpoint = median(prices)
    if midpoint <= 0:
        return prices
    filtered = [
        price
        for price in prices
        if midpoint * 0.5 <= price <= midpoint * 2
    ]
    return filtered or prices


def _weighted_recent_average(values: list[float | int]) -> float:
    if not values:
        return 0.0

    total_weight = 0
    weighted_total = 0.0
    count = len(values)
    for index, value in enumerate(values):
        weight = count - index
        weighted_total += float(value) * weight
        total_weight += weight
    return weighted_total / total_weight


def _load_player_outcome_inputs(connection: sqlite3.Connection, item_id: int) -> dict[str, Any]:
    if not _table_exists(connection, "player_auction_outcomes"):
        return _empty_player_outcomes()

    outcome_rows = connection.execute(
        """
        select outcome, money, observed_at, id
        from player_auction_outcomes
        where item_id = ?
        order by
            case when observed_at is null then 1 else 0 end,
            observed_at desc,
            id desc
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
    sale_rate = _weighted_player_sale_rate(outcome_rows, denominator)
    return {
        "post_count": post_count,
        "sold_count": sold_count,
        "expired_count": expired_count,
        "cancelled_count": cancelled_count,
        "sale_rate": sale_rate,
        "average_net_proceeds": int(mean(net_proceeds)) if net_proceeds else None,
    }


def _weighted_player_sale_rate(outcome_rows: list[sqlite3.Row], denominator: int) -> float:
    if denominator <= 0 or not outcome_rows:
        return 0.0

    weights = [len(outcome_rows) - index for index, _row in enumerate(outcome_rows)]
    average_weight = mean(weights)
    weighted_sold_equivalent = 0.0
    for row, weight in zip(outcome_rows, weights, strict=True):
        if row["outcome"] == "sold":
            weighted_sold_equivalent += weight / average_weight
    return max(0.0, min(weighted_sold_equivalent / denominator, 1.0))


def _empty_player_outcomes() -> dict[str, Any]:
    return {
        "post_count": 0,
        "sold_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "sale_rate": 0.0,
        "average_net_proceeds": None,
    }


def _load_historical_timing_inputs(
    connection: sqlite3.Connection,
    item_id: int,
    source_table: str,
    display_timezone: ZoneInfo,
) -> dict[str, Any]:
    rows = connection.execute(
        f"""
        select
            r.started_at,
            s.min_unit_price,
            s.first_quartile_unit_price,
            s.median_unit_price
        from {source_table} s
        join fetch_runs r on r.id = s.fetch_run_id
        where s.item_id = ? and r.status = 'success'
        order by s.fetch_run_id
        """,
        (item_id,),
    ).fetchall()
    buckets: dict[tuple[int, int], dict[str, list[int]]] = {}
    weeks: set[tuple[int, int]] = set()
    for row in rows:
        started_at = _parse_datetime(row["started_at"])
        if started_at is None:
            continue
        buy_price = _first_int(row["min_unit_price"], row["first_quartile_unit_price"], row["median_unit_price"])
        sell_price = _first_int(row["first_quartile_unit_price"], row["median_unit_price"], row["min_unit_price"])
        if buy_price is None and sell_price is None:
            continue

        local_started_at = _as_display_time(started_at, display_timezone)
        iso_year, iso_week, _ = local_started_at.isocalendar()
        weeks.add((iso_year, iso_week))
        bucket_key = (local_started_at.weekday(), local_started_at.hour)
        bucket = buckets.setdefault(bucket_key, {"buy": [], "sell": []})
        if buy_price is not None:
            bucket["buy"].append(buy_price)
        if sell_price is not None:
            bucket["sell"].append(sell_price)

    buy_buckets = [
        (key, values["buy"])
        for key, values in buckets.items()
        if values["buy"]
    ]
    sell_buckets = [
        (key, values["sell"])
        for key, values in buckets.items()
        if values["sell"]
    ]
    if len(buy_buckets) < 2 or len(sell_buckets) < 2:
        return _empty_timing_inputs()

    best_buy_key, best_buy_values = min(buy_buckets, key=lambda item: mean(item[1]))
    best_sell_key, best_sell_values = max(sell_buckets, key=lambda item: mean(item[1]))
    sample_count = sum(len(values["buy"]) for values in buckets.values())
    confidence = _historical_timing_confidence(sample_count, len(weeks), len(buckets))
    return {
        "best_buy_time": _bucket_label(best_buy_key, display_timezone),
        "best_sell_time": _bucket_label(best_sell_key, display_timezone),
        "historical_buy_price": int(mean(best_buy_values)),
        "historical_sell_price": int(mean(best_sell_values)),
        "historical_timing_confidence": confidence,
    }


def _empty_timing_inputs() -> dict[str, Any]:
    return {
        "best_buy_time": None,
        "best_sell_time": None,
        "historical_buy_price": None,
        "historical_sell_price": None,
        "historical_timing_confidence": 0,
    }


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("T", " ")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _as_display_time(value: datetime, display_timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(display_timezone)


def _display_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone {value!r}") from exc


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is not None:
            return int(value)
    return None


def _bucket_label(bucket_key: tuple[int, int], display_timezone: ZoneInfo) -> str:
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    weekday, hour = bucket_key
    sample_date = datetime(2026, 1, 5 + weekday, hour, tzinfo=display_timezone)
    abbreviation = sample_date.tzname() or display_timezone.key
    return f"{weekdays[weekday]} {hour:02d}:00 {abbreviation}"


def _historical_timing_confidence(sample_count: int, week_count: int, bucket_count: int) -> int:
    sample_score = min(sample_count / 56, 1.0)
    week_score = min(week_count / 4, 1.0)
    bucket_score = min(bucket_count / 8, 1.0)
    return round(((sample_score * 0.4) + (week_score * 0.4) + (bucket_score * 0.2)) * 100)


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


def _clamp_score(score: int) -> int:
    return max(0, min(score, 100))


def _action_for_scores(buy_score: int, sell_score: int) -> str:
    if sell_score >= 60 and sell_score >= buy_score:
        return "sell"
    if buy_score >= 60:
        return "buy"
    if max(buy_score, sell_score) >= 35:
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
        reasons.append(f"current market price is {price_score}% below recent median")
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
    if inputs.historical_timing_confidence > 0 and inputs.best_buy_time and inputs.best_sell_time:
        reasons.append(
            "historically best buy window is "
            f"{inputs.best_buy_time} near {inputs.historical_buy_price / 10000:.2f}g"
        )
        reasons.append(
            "historically best sell window is "
            f"{inputs.best_sell_time} near {inputs.historical_sell_price / 10000:.2f}g"
        )
    if scarcity_score > 0:
        reasons.append(f"listing count is {scarcity_score}% below recent average")
    if inputs.price_trend_score < 45:
        reasons.append(f"recent price trend is down {round(abs(inputs.price_trend_ratio) * 100)}%")
    elif inputs.price_trend_score > 55:
        reasons.append(f"recent price trend is up {round(inputs.price_trend_ratio * 100)}%")
    if inputs.snapshots > 0:
        reasons.append(f"based on {inputs.snapshots} snapshots")
    return reasons or ["no strong pricing or demand signal"]
