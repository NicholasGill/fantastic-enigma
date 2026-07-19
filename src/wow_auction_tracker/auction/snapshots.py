from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable

from wow_auction_tracker.config import Market

_MAX_CONTIGUOUS_PRICE_RATIO = 2
_MIN_CLUSTER_SAMPLE_SIZE = 3


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
    first_quartile_unit_price: int | None
    third_quartile_unit_price: int | None


@dataclass(frozen=True)
class ItemHistoryMetric:
    item_id: int
    market: Market
    listing_count: int
    total_quantity: int
    min_unit_price: int | None
    median_unit_price: int | None
    first_quartile_unit_price: int | None
    third_quartile_unit_price: int | None
    weighted_average_unit_price: int | None
    lowest_price_quantity: int
    price_change_1h_bps: int | None = None
    price_change_24h_bps: int | None = None
    price_change_7d_bps: int | None = None
    historical_volatility_bps: int | None = None
    percentile_rank_bps: int | None = None
    market_depth_score: int | None = None
    liquidity_score: int | None = None


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
        priced_listings = [
            listing
            for listing in item_list
            if listing.effective_unit_price is not None
        ]
        raw_prices = [_listing_price(listing) for listing in priced_listings]
        representative_prices = [
            _listing_price(listing)
            for listing in _representative_price_listings(priced_listings)
        ]
        first_quartile, third_quartile = _quartiles(representative_prices)
        summaries.append(
            ItemSummary(
                item_id=item_id,
                market=market,
                listing_count=len(item_list),
                total_quantity=sum(listing.quantity for listing in item_list),
                min_unit_price=min(raw_prices) if raw_prices else None,
                median_unit_price=(
                    int(median(representative_prices)) if representative_prices else None
                ),
                first_quartile_unit_price=first_quartile,
                third_quartile_unit_price=third_quartile,
            )
        )

    return sorted(summaries, key=lambda summary: (summary.market.value, summary.item_id))


def calculate_item_history_metrics(listings: Iterable[AuctionListing]) -> list[ItemHistoryMetric]:
    grouped: dict[tuple[int, Market], list[AuctionListing]] = {}
    for listing in listings:
        grouped.setdefault((listing.item_id, listing.market), []).append(listing)

    metrics: list[ItemHistoryMetric] = []
    for (item_id, market), item_list in grouped.items():
        priced_listings = [
            listing
            for listing in item_list
            if listing.effective_unit_price is not None
        ]
        raw_prices = [_listing_price(listing) for listing in priced_listings]
        representative_listings = _representative_price_listings(priced_listings)
        representative_prices = [
            _listing_price(listing) for listing in representative_listings
        ]
        min_price = min(raw_prices) if raw_prices else None
        first_quartile, third_quartile = _quartiles(representative_prices)
        priced_quantity = sum(listing.quantity for listing in representative_listings)
        weighted_total = sum(
            _listing_price(listing) * listing.quantity
            for listing in representative_listings
        )
        metrics.append(
            ItemHistoryMetric(
                item_id=item_id,
                market=market,
                listing_count=len(item_list),
                total_quantity=sum(listing.quantity for listing in item_list),
                min_unit_price=min_price,
                median_unit_price=(
                    int(median(representative_prices)) if representative_prices else None
                ),
                first_quartile_unit_price=first_quartile,
                third_quartile_unit_price=third_quartile,
                weighted_average_unit_price=weighted_total // priced_quantity if priced_quantity else None,
                lowest_price_quantity=sum(
                    listing.quantity
                    for listing in priced_listings
                    if listing.effective_unit_price == min_price
                ),
            )
        )

    return sorted(metrics, key=lambda item: (item.market.value, item.item_id))


def _representative_price_listings(
    priced_listings: list[AuctionListing],
) -> list[AuctionListing]:
    """Return the densest contiguous listing-price band.

    Large multiplicative gaps split listings into price bands. The band with
    the most listing records is treated as the active market; ties prefer the
    lower-priced band to keep pricing conservative. Sparse samples are left
    unchanged because there is not enough evidence to classify an outlier.
    """
    ordered = sorted(priced_listings, key=_listing_price)
    if len(ordered) < _MIN_CLUSTER_SAMPLE_SIZE:
        return ordered

    clusters: list[list[AuctionListing]] = [[]]
    previous_price: int | None = None
    for listing in ordered:
        price = _listing_price(listing)
        if previous_price is not None and (
            (previous_price <= 0 < price)
            or price > previous_price * _MAX_CONTIGUOUS_PRICE_RATIO
        ):
            clusters.append([])
        clusters[-1].append(listing)
        previous_price = price

    largest_cluster_size = max(len(cluster) for cluster in clusters)
    if largest_cluster_size < 2:
        return ordered

    largest_clusters = [
        cluster for cluster in clusters if len(cluster) == largest_cluster_size
    ]
    return min(
        largest_clusters,
        key=lambda cluster: median(_listing_price(listing) for listing in cluster),
    )


def _listing_price(listing: AuctionListing) -> int:
    price = listing.effective_unit_price
    if price is None:
        raise ValueError("a representative price listing must have a price")
    return price


def _quartiles(values: list[int | None]) -> tuple[int | None, int | None]:
    prices = sorted(value for value in values if value is not None)
    if not prices:
        return (None, None)
    if len(prices) == 1:
        return (prices[0], prices[0])

    midpoint = len(prices) // 2
    if len(prices) % 2 == 0:
        lower = prices[:midpoint]
        upper = prices[midpoint:]
    else:
        lower = prices[:midpoint]
        upper = prices[midpoint + 1 :]

    return (int(median(lower or prices)), int(median(upper or prices)))


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
