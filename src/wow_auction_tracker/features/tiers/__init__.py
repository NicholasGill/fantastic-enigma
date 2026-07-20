from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class TierMarketItem:
    item_id: int
    name: str
    market: str
    typical_unit_price: int | None
    quality: int | None = None


@dataclass(frozen=True)
class TierMarketAnalysis:
    item_id: int
    family_name: str
    market: str
    quality: int
    typical_unit_price: int | None
    is_best_value: bool
    price_premium_bps: int | None
    dominated_by_item_id: int | None
    dominated_by_quality: int | None
    dominated_by_unit_price: int | None
    dominance_savings_bps: int | None

    @property
    def is_dominated(self) -> bool:
        return self.dominated_by_item_id is not None


def analyze_tier_market(
    items: Iterable[TierMarketItem],
) -> dict[int, TierMarketAnalysis]:
    """Compare interchangeable crafting qualities within same-name families."""
    grouped: dict[tuple[str, str], list[TierMarketItem]] = {}
    for item in items:
        family_key = " ".join(item.name.split()).casefold()
        grouped.setdefault((family_key, item.market), []).append(item)

    analyses: dict[int, TierMarketAnalysis] = {}
    for siblings in grouped.values():
        if not 2 <= len(siblings) <= 5:
            continue

        ranked = _rank_siblings(siblings)
        priced = [item for item, _quality in ranked if _valid_price(item.typical_unit_price)]
        best_value = min(
            priced,
            key=lambda item: (
                int(item.typical_unit_price or 0),
                -_quality_for_item(ranked, item.item_id),
            ),
        ) if priced else None
        best_price = int(best_value.typical_unit_price) if best_value is not None else None

        for item, quality in ranked:
            price = int(item.typical_unit_price) if _valid_price(item.typical_unit_price) else None
            dominating = [
                (candidate, candidate_quality)
                for candidate, candidate_quality in ranked
                if candidate_quality > quality
                and price is not None
                and _valid_price(candidate.typical_unit_price)
                and int(candidate.typical_unit_price) <= price
            ]
            dominator = min(
                dominating,
                key=lambda pair: (int(pair[0].typical_unit_price or 0), -pair[1]),
                default=None,
            )
            dominator_price = (
                int(dominator[0].typical_unit_price)
                if dominator is not None and dominator[0].typical_unit_price is not None
                else None
            )
            analyses[item.item_id] = TierMarketAnalysis(
                item_id=item.item_id,
                family_name=item.name,
                market=item.market,
                quality=quality,
                typical_unit_price=price,
                is_best_value=best_value is not None and item.item_id == best_value.item_id,
                price_premium_bps=(
                    round(((price - best_price) / best_price) * 10000)
                    if price is not None and best_price is not None and best_price > 0
                    else None
                ),
                dominated_by_item_id=dominator[0].item_id if dominator is not None else None,
                dominated_by_quality=dominator[1] if dominator is not None else None,
                dominated_by_unit_price=dominator_price,
                dominance_savings_bps=(
                    round(((price - dominator_price) / price) * 10000)
                    if price is not None and dominator_price is not None and price > 0
                    else None
                ),
            )

    return analyses


def _rank_siblings(items: list[TierMarketItem]) -> list[tuple[TierMarketItem, int]]:
    explicit_qualities = [item.quality for item in items]
    if (
        all(quality is not None and 1 <= quality <= 5 for quality in explicit_qualities)
        and len(set(explicit_qualities)) == len(items)
    ):
        return sorted(
            ((item, int(item.quality or 0)) for item in items),
            key=lambda pair: pair[1],
        )
    return [
        (item, quality)
        for quality, item in enumerate(sorted(items, key=lambda item: item.item_id), start=1)
    ]


def _quality_for_item(
    ranked: list[tuple[TierMarketItem, int]],
    item_id: int,
) -> int:
    return next(quality for item, quality in ranked if item.item_id == item_id)


def _valid_price(value: int | None) -> bool:
    return value is not None and value > 0


__all__ = ["TierMarketAnalysis", "TierMarketItem", "analyze_tier_market"]
