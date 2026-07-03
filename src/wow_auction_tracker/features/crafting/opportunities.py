from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from wow_auction_tracker.auction import AuctionListing
from wow_auction_tracker.config import RecipeConfig
from wow_auction_tracker.features.recommendations import Recommendation


@dataclass(frozen=True)
class CraftOpportunityObservation:
    recipe_id: str
    recipe_name: str | None
    output_item_id: int
    output_market: str
    output_quantity: int
    craft_cost: int
    craft_cost_unit_price: int
    output_min_unit_price: int
    sell_target_unit_price: int
    auction_deposit_unit_price: int
    ah_savings: int
    expected_profit: int
    max_craft_quantity: int
    confidence: int
    reasons: list[str]


def build_craft_opportunity_observations(
    recipes: Iterable[RecipeConfig],
    listings: Iterable[AuctionListing],
    recommendations: Iterable[Recommendation],
) -> list[CraftOpportunityObservation]:
    listing_list = list(listings)
    listings_by_item = _listings_by_item(listing_list)
    recommendation_by_item = {
        (recommendation.item_id, recommendation.market): recommendation
        for recommendation in recommendations
    }

    opportunities: list[CraftOpportunityObservation] = []
    for recipe in recipes:
        recommendation = recommendation_by_item.get((recipe.output.item_id, recipe.output.market.value))
        if recommendation is None or recommendation.recommended_sell_price is None:
            continue

        output_min = _min_unit_price(listings_by_item.get((recipe.output.item_id, recipe.output.market.value), []))
        if output_min is None:
            continue

        ingredient_costs: list[int] = []
        max_crafts: list[int] = []
        missing_ingredient = False
        for ingredient in recipe.ingredients:
            item_list = listings_by_item.get((ingredient.item_id, ingredient.market.value), [])
            ingredient_cost = _cost_for_quantity(item_list, ingredient.quantity)
            if ingredient_cost is None:
                missing_ingredient = True
                break
            ingredient_costs.append(ingredient_cost)
            max_crafts.append(_available_quantity(item_list) // ingredient.quantity)
        if missing_ingredient or not ingredient_costs or not max_crafts:
            continue

        craft_cost = sum(ingredient_costs)
        craft_cost_unit = _ceil_div(craft_cost, recipe.output.quantity)
        if craft_cost_unit >= output_min:
            continue

        deposit = recommendation.auction_deposit_unit_price or 0
        expected_profit = (recommendation.recommended_sell_price - craft_cost_unit - deposit) * recipe.output.quantity
        if expected_profit <= 0:
            continue

        ah_savings = (output_min - craft_cost_unit) * recipe.output.quantity
        opportunities.append(
            CraftOpportunityObservation(
                recipe_id=recipe.id,
                recipe_name=recipe.name,
                output_item_id=recipe.output.item_id,
                output_market=recipe.output.market.value,
                output_quantity=recipe.output.quantity,
                craft_cost=craft_cost,
                craft_cost_unit_price=craft_cost_unit,
                output_min_unit_price=output_min,
                sell_target_unit_price=recommendation.recommended_sell_price,
                auction_deposit_unit_price=deposit,
                ah_savings=ah_savings,
                expected_profit=expected_profit,
                max_craft_quantity=min(max_crafts),
                confidence=recommendation.confidence,
                reasons=[
                    f"craft cost is {ah_savings} copper below current output auction price",
                    "output has conservative sell target from sale evidence",
                ],
            )
        )

    return sorted(
        opportunities,
        key=lambda item: (-item.expected_profit, item.output_market, item.output_item_id, item.recipe_id),
    )


def _listings_by_item(listings: list[AuctionListing]) -> dict[tuple[int, str], list[AuctionListing]]:
    grouped: dict[tuple[int, str], list[AuctionListing]] = {}
    for listing in listings:
        if listing.effective_unit_price is None:
            continue
        grouped.setdefault((listing.item_id, listing.market.value), []).append(listing)
    for item_list in grouped.values():
        item_list.sort(key=lambda item: (item.effective_unit_price or 0, item.auction_id or 0))
    return grouped


def _min_unit_price(listings: list[AuctionListing]) -> int | None:
    prices = [listing.effective_unit_price for listing in listings if listing.effective_unit_price is not None]
    return min(prices) if prices else None


def _cost_for_quantity(listings: list[AuctionListing], quantity: int) -> int | None:
    remaining = quantity
    total = 0
    for listing in listings:
        unit_price = listing.effective_unit_price
        if unit_price is None:
            continue
        purchased = min(remaining, listing.quantity)
        total += purchased * unit_price
        remaining -= purchased
        if remaining == 0:
            return total
    return None


def _available_quantity(listings: list[AuctionListing]) -> int:
    return sum(listing.quantity for listing in listings if listing.effective_unit_price is not None)


def _ceil_div(value: int, divisor: int) -> int:
    return -(-value // divisor)
