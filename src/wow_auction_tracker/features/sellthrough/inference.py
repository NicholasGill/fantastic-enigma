from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from wow_auction_tracker.features.lifecycle import ListingObservation


@dataclass(frozen=True)
class SellThroughMetric:
    item_id: int
    market: str
    disappeared_listing_count: int
    disappeared_quantity: int
    disappeared_value: int | None
    probable_sold_listing_count: int
    probable_sold_quantity: int
    probable_sold_value: int | None
    probable_sold_average_unit_price: int | None
    observed_listing_count: int
    observed_quantity: int
    sell_through_ratio: float
    confidence: int


def build_sell_through_metrics(observations: list[ListingObservation]) -> list[SellThroughMetric]:
    grouped: dict[tuple[int, str], list[ListingObservation]] = {}
    for observation in observations:
        grouped.setdefault((observation.item_id, observation.market), []).append(observation)

    metrics: list[SellThroughMetric] = []
    for (item_id, market), rows in grouped.items():
        missing_rows = [row for row in rows if row.status == "missing"]
        probable_sold_rows = [row for row in rows if row.inferred_outcome == "probable_sold"]
        observed_rows = [row for row in rows if row.status in {"active", "changed", "missing"}]
        observed_quantity = sum(_observation_quantity(row) for row in observed_rows)
        disappeared_quantity = sum(row.previous_quantity or 0 for row in missing_rows)
        disappeared_values = [
            (row.previous_quantity or 0) * row.previous_unit_price
            for row in missing_rows
            if row.previous_unit_price is not None
        ]
        probable_sold_quantity = sum(_probable_sold_quantity(row) for row in probable_sold_rows)
        probable_sold_values = [
            _probable_sold_quantity(row) * price
            for row in probable_sold_rows
            if (price := _probable_sold_unit_price(row)) is not None
        ]
        sell_through_ratio = disappeared_quantity / observed_quantity if observed_quantity > 0 else 0.0
        metrics.append(
            SellThroughMetric(
                item_id=item_id,
                market=market,
                disappeared_listing_count=len(missing_rows),
                disappeared_quantity=disappeared_quantity,
                disappeared_value=sum(disappeared_values) if disappeared_values else None,
                probable_sold_listing_count=len(probable_sold_rows),
                probable_sold_quantity=probable_sold_quantity,
                probable_sold_value=sum(probable_sold_values) if probable_sold_values else None,
                probable_sold_average_unit_price=(
                    sum(probable_sold_values) // probable_sold_quantity
                    if probable_sold_values and probable_sold_quantity > 0
                    else None
                ),
                observed_listing_count=len(observed_rows),
                observed_quantity=observed_quantity,
                sell_through_ratio=max(0.0, min(sell_through_ratio, 1.0)),
                confidence=_confidence(observed_rows),
            )
        )

    return sorted(metrics, key=lambda item: (item.market, item.item_id))


def _observation_quantity(observation: ListingObservation) -> int:
    if observation.status == "missing":
        return observation.previous_quantity or 0
    return observation.quantity or 0


def _probable_sold_quantity(observation: ListingObservation) -> int:
    if observation.status == "missing":
        return observation.previous_quantity or 0
    if observation.status == "changed" and observation.previous_quantity is not None and observation.quantity is not None:
        return max(observation.previous_quantity - observation.quantity, 0)
    return 0


def _probable_sold_unit_price(observation: ListingObservation) -> int | None:
    return observation.previous_unit_price or observation.unit_price


def _confidence(observations: list[ListingObservation]) -> int:
    if not observations:
        return 0

    observed_count_score = min(len(observations) / 20, 1.0)
    auction_id_ratio = mean(1.0 if row.auction_id is not None else 0.0 for row in observations)
    return round(((observed_count_score * 0.6) + (auction_id_ratio * 0.4)) * 100)
