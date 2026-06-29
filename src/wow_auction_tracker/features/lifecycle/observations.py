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


def build_listing_observations(
    current_listings: list[AuctionListing],
    previous_listings: list[ListingSnapshot],
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
        observations.append(_missing_observation(previous))

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
    )


def _missing_observation(previous: ListingSnapshot) -> ListingObservation:
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
    )


def _listing_changed(current: ListingSnapshot, previous: ListingSnapshot) -> bool:
    return (
        current.quantity != previous.quantity
        or current.unit_price != previous.unit_price
        or current.buyout != previous.buyout
        or current.bid != previous.bid
        or current.time_left != previous.time_left
    )
