from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from wow_auction_tracker.auction import AuctionListing
from wow_auction_tracker.features.lifecycle import ListingObservation, listing_key_from_parts
from wow_auction_tracker.features.recommendations import CURRENT_PRICE_QUANTITY_SHIFT, Recommendation


@dataclass(frozen=True)
class BuyOpportunityObservation:
    item_id: int
    market: str
    auction_id: int | None
    unit_price: int
    quantity: int
    buy_target_unit_price: int
    sell_target_unit_price: int | None
    potential_profit: int | None
    available_quantity_at_or_below_buy_target: int
    recommendation_score: int
    recommendation_confidence: int
    listing_status: str


def build_buy_opportunity_observations(
    listings: Iterable[AuctionListing],
    listing_observations: Iterable[ListingObservation],
    recommendations: Iterable[Recommendation],
) -> list[BuyOpportunityObservation]:
    listing_list = list(listings)
    recommendation_by_item = {
        (recommendation.item_id, recommendation.market): recommendation
        for recommendation in recommendations
        if recommendation.recommended_buy_price is not None
    }
    observation_by_key = {
        observation.observation_key: observation
        for observation in listing_observations
    }
    available_quantity_by_item = _available_quantity_by_item(listing_list, recommendation_by_item)

    opportunities: list[BuyOpportunityObservation] = []
    for listing in listing_list:
        recommendation = recommendation_by_item.get((listing.item_id, listing.market.value))
        if recommendation is None or recommendation.recommended_buy_price is None:
            continue

        unit_price = listing.effective_unit_price
        if unit_price is None or unit_price >= recommendation.recommended_buy_price:
            continue

        available_quantity = available_quantity_by_item[(listing.item_id, listing.market.value)]
        if available_quantity < CURRENT_PRICE_QUANTITY_SHIFT:
            continue

        observation = observation_by_key.get(_listing_key(listing))
        if observation is None or not _is_new_below_target_listing(observation, recommendation.recommended_buy_price):
            continue

        sell_target = recommendation.recommended_sell_price
        deposit = recommendation.auction_deposit_unit_price or 0
        opportunities.append(
            BuyOpportunityObservation(
                item_id=listing.item_id,
                market=listing.market.value,
                auction_id=listing.auction_id,
                unit_price=unit_price,
                quantity=listing.quantity,
                buy_target_unit_price=recommendation.recommended_buy_price,
                sell_target_unit_price=sell_target,
                potential_profit=(
                    (sell_target - unit_price - deposit) * listing.quantity
                    if sell_target is not None
                    else None
                ),
                available_quantity_at_or_below_buy_target=available_quantity,
                recommendation_score=recommendation.score,
                recommendation_confidence=recommendation.confidence,
                listing_status=observation.status,
            )
        )

    return sorted(opportunities, key=lambda item: (item.market, item.item_id, item.unit_price, item.auction_id or 0))


def _available_quantity_by_item(
    listings: list[AuctionListing],
    recommendation_by_item: dict[tuple[int, str], Recommendation],
) -> dict[tuple[int, str], int]:
    quantities: dict[tuple[int, str], int] = {
        key: 0
        for key in recommendation_by_item
    }
    for listing in listings:
        key = (listing.item_id, listing.market.value)
        recommendation = recommendation_by_item.get(key)
        unit_price = listing.effective_unit_price
        if recommendation is None or recommendation.recommended_buy_price is None or unit_price is None:
            continue
        if unit_price <= recommendation.recommended_buy_price:
            quantities[key] = quantities.get(key, 0) + listing.quantity
    return quantities


def _is_new_below_target_listing(observation: ListingObservation, buy_target_unit_price: int) -> bool:
    if observation.status == "new":
        return True
    if observation.status != "changed":
        return False
    if observation.previous_unit_price is None:
        return True
    return observation.previous_unit_price >= buy_target_unit_price


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
