from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable

from wow_auction_tracker.config import Market


@dataclass(frozen=True)
class AuctionListing:
    auction_id: int | None
    item_id: int
    market: Market
    quantity: int
    unit_price: int | None
    buyout: int | None
    bid: int | None
    time_left: str | None
    raw: dict[str, Any]

    @property
    def effective_unit_price(self) -> int | None:
        if self.unit_price is not None:
            return self.unit_price
        if self.buyout is not None and self.quantity > 0:
            return self.buyout // self.quantity
        return None


@dataclass(frozen=True)
class ItemSummary:
    item_id: int
    market: Market
    listing_count: int
    total_quantity: int
    min_unit_price: int | None
    median_unit_price: int | None


def filter_auctions(payload: dict[str, Any], item_ids: set[int], market: Market) -> list[AuctionListing]:
    listings = payload.get("auctions", [])
    if not isinstance(listings, list):
        raise ValueError("auction payload does not include an auctions list")

    filtered: list[AuctionListing] = []
    for listing in listings:
        if not isinstance(listing, dict):
            continue

        item = listing.get("item", {})
        if not isinstance(item, dict):
            continue

        item_id = item.get("id")
        if item_id not in item_ids:
            continue

        filtered.append(
            AuctionListing(
                auction_id=_optional_int(listing.get("id")),
                item_id=int(item_id),
                market=market,
                quantity=_positive_int_or_one(listing.get("quantity")),
                unit_price=_optional_int(listing.get("unit_price")),
                buyout=_optional_int(listing.get("buyout")),
                bid=_optional_int(listing.get("bid")),
                time_left=_optional_str(listing.get("time_left")),
                raw=listing,
            )
        )

    return filtered


def summarize_listings(listings: Iterable[AuctionListing]) -> list[ItemSummary]:
    grouped: dict[tuple[int, Market], list[AuctionListing]] = {}
    for listing in listings:
        grouped.setdefault((listing.item_id, listing.market), []).append(listing)

    summaries: list[ItemSummary] = []
    for (item_id, market), item_list in grouped.items():
        prices = [
            listing.effective_unit_price
            for listing in item_list
            if listing.effective_unit_price is not None
        ]
        summaries.append(
            ItemSummary(
                item_id=item_id,
                market=market,
                listing_count=len(item_list),
                total_quantity=sum(listing.quantity for listing in item_list),
                min_unit_price=min(prices) if prices else None,
                median_unit_price=int(median(prices)) if prices else None,
            )
        )

    return sorted(summaries, key=lambda summary: (summary.market.value, summary.item_id))


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _positive_int_or_one(value: object) -> int:
    if value is None:
        return 1
    parsed = int(value)
    return parsed if parsed > 0 else 1


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
