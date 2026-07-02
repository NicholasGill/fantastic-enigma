from __future__ import annotations

from dataclasses import dataclass

from wow_auction_tracker.auction import AuctionListing


@dataclass(frozen=True)
class ListingSnapshot:
    observation_key: str
    auction_id: int | None
    item_id: int
    market: str
    quantity: int
    unit_price: int | None
    buyout: int | None
    bid: int | None
    time_left: str | None


@dataclass(frozen=True)
class ListingObservation:
    observation_key: str
    auction_id: int | None
    item_id: int
    market: str
    status: str
    quantity: int | None
    previous_quantity: int | None
    unit_price: int | None
    previous_unit_price: int | None
    buyout: int | None
    previous_buyout: int | None
    bid: int | None
    previous_bid: int | None
    time_left: str | None
    previous_time_left: str | None
    inferred_outcome: str | None = None


def build_listing_observations(
    current_listings: list[AuctionListing],
    previous_listings: list[ListingSnapshot],
    *,
    elapsed_seconds: int | None = None,
) -> list[ListingObservation]:
    current_by_key = {
        _listing_key(listing): _snapshot_from_listing(listing)
        for listing in current_listings
    }
    previous_by_key = {listing.observation_key: listing for listing in previous_listings}

    observations: list[ListingObservation] = []
    for key, current in current_by_key.items():
        previous = previous_by_key.get(key)
        observations.append(_current_observation(current, previous))

    for key, previous in previous_by_key.items():
        if key in current_by_key:
            continue
        observations.append(_missing_observation(previous, elapsed_seconds))

    return sorted(observations, key=lambda item: (item.market, item.item_id, item.observation_key, item.status))


def listing_key_from_parts(
    *,
    auction_id: int | None,
    item_id: int,
    market: str,
    quantity: int,
    unit_price: int | None,
    buyout: int | None,
    bid: int | None,
) -> str:
    if auction_id is not None:
        return f"auction:{auction_id}"
    return f"synthetic:{market}:{item_id}:{quantity}:{unit_price}:{buyout}:{bid}"


def _listing_key(listing: AuctionListing) -> str:
    return listing_key_from_parts(
        auction_id=listing.auction_id,
        item_id=listing.item_id,
        market=listing.market.value,
        quantity=listing.quantity,
        unit_price=listing.unit_price,
        buyout=listing.buyout,
        bid=listing.bid,
    )


def _snapshot_from_listing(listing: AuctionListing) -> ListingSnapshot:
    return ListingSnapshot(
        observation_key=_listing_key(listing),
        auction_id=listing.auction_id,
        item_id=listing.item_id,
        market=listing.market.value,
        quantity=listing.quantity,
        unit_price=listing.unit_price,
        buyout=listing.buyout,
        bid=listing.bid,
        time_left=listing.time_left,
    )


def _current_observation(current: ListingSnapshot, previous: ListingSnapshot | None) -> ListingObservation:
    if previous is None:
        status = "new"
    elif _listing_changed(current, previous):
        status = "changed"
    else:
        status = "active"

    return ListingObservation(
        observation_key=current.observation_key,
        auction_id=current.auction_id,
        item_id=current.item_id,
        market=current.market,
        status=status,
        quantity=current.quantity,
        previous_quantity=previous.quantity if previous else None,
        unit_price=current.unit_price,
        previous_unit_price=previous.unit_price if previous else None,
        buyout=current.buyout,
        previous_buyout=previous.buyout if previous else None,
        bid=current.bid,
        previous_bid=previous.bid if previous else None,
        time_left=current.time_left,
        previous_time_left=previous.time_left if previous else None,
        inferred_outcome=_inferred_current_outcome(current, previous),
    )


def _missing_observation(previous: ListingSnapshot, elapsed_seconds: int | None) -> ListingObservation:
    return ListingObservation(
        observation_key=previous.observation_key,
        auction_id=previous.auction_id,
        item_id=previous.item_id,
        market=previous.market,
        status="missing",
        quantity=None,
        previous_quantity=previous.quantity,
        unit_price=None,
        previous_unit_price=previous.unit_price,
        buyout=None,
        previous_buyout=previous.buyout,
        bid=None,
        previous_bid=previous.bid,
        time_left=None,
        previous_time_left=previous.time_left,
        inferred_outcome=_inferred_missing_outcome(previous.time_left, elapsed_seconds),
    )


def _listing_changed(current: ListingSnapshot, previous: ListingSnapshot) -> bool:
    return (
        current.quantity != previous.quantity
        or current.unit_price != previous.unit_price
        or current.buyout != previous.buyout
        or current.bid != previous.bid
        or current.time_left != previous.time_left
    )


def _inferred_current_outcome(current: ListingSnapshot, previous: ListingSnapshot | None) -> str | None:
    if previous is None:
        return None
    if current.quantity < previous.quantity:
        return "probable_sold"
    return None


def _inferred_missing_outcome(previous_time_left: str | None, elapsed_seconds: int | None) -> str:
    if previous_time_left is None or elapsed_seconds is None:
        return "removed_unknown"

    remaining_range = _remaining_time_range_seconds(previous_time_left)
    if remaining_range is None:
        return "removed_unknown"

    minimum_remaining, maximum_remaining = remaining_range
    if elapsed_seconds < minimum_remaining:
        return "probable_sold"
    if maximum_remaining is not None and elapsed_seconds >= maximum_remaining:
        return "probable_expired"
    return "removed_unknown"


def _remaining_time_range_seconds(time_left: str) -> tuple[int, int | None] | None:
    match time_left.upper():
        case "SHORT":
            return (0, 30 * 60)
        case "MEDIUM":
            return (30 * 60, 2 * 60 * 60)
        case "LONG":
            return (2 * 60 * 60, 12 * 60 * 60)
        case "VERY_LONG":
            return (12 * 60 * 60, None)
        case _:
            return None
