from wow_auction_tracker.auction import AuctionListing
from wow_auction_tracker.config import Market, RecipeConfig
from wow_auction_tracker.features.crafting import build_craft_opportunity_observations
from wow_auction_tracker.features.recommendations import Recommendation


def test_build_craft_opportunities_uses_cheapest_ingredient_stack() -> None:
    recipe = RecipeConfig.model_validate(
        {
            "id": "refine-bismuth",
            "name": "Refine Bismuth",
            "output": {"item_id": 210931, "name": "Bismuth", "market": "commodity", "quantity": 1},
            "ingredients": [
                {"item_id": 210930, "name": "Bismuth", "market": "commodity", "quantity": 5}
            ],
        }
    )
    listings = [
        _listing(1, 210930, 2, 100),
        _listing(2, 210930, 10, 120),
        _listing(3, 210931, 1, 900),
    ]
    recommendations = [_recommendation(210931, sell_price=1000, deposit=20)]

    opportunities = build_craft_opportunity_observations([recipe], listings, recommendations)

    assert len(opportunities) == 1
    assert opportunities[0].craft_cost == 560
    assert opportunities[0].craft_cost_unit_price == 560
    assert opportunities[0].output_min_unit_price == 900
    assert opportunities[0].expected_profit == 420
    assert opportunities[0].max_craft_quantity == 2


def test_build_craft_opportunities_require_sell_target_and_positive_profit() -> None:
    recipe = RecipeConfig.model_validate(
        {
            "id": "no-profit",
            "output": {"item_id": 2, "market": "commodity", "quantity": 1},
            "ingredients": [{"item_id": 1, "market": "commodity", "quantity": 2}],
        }
    )
    listings = [_listing(1, 1, 2, 100), _listing(2, 2, 1, 300)]

    assert build_craft_opportunity_observations([recipe], listings, []) == []
    assert build_craft_opportunity_observations([recipe], listings, [_recommendation(2, sell_price=210, deposit=20)]) == []


def _listing(auction_id: int, item_id: int, quantity: int, unit_price: int) -> AuctionListing:
    return AuctionListing(
        auction_id=auction_id,
        item_id=item_id,
        market=Market.COMMODITY,
        quantity=quantity,
        unit_price=unit_price,
        buyout=None,
        bid=None,
        time_left="LONG",
        raw={"id": auction_id, "item": {"id": item_id}},
    )


def _recommendation(item_id: int, *, sell_price: int, deposit: int) -> Recommendation:
    return Recommendation(
        item_id=item_id,
        name=f"Item {item_id}",
        market="commodity",
        action="buy",
        score=75,
        confidence=80,
        latest_min_unit_price=900,
        latest_median_unit_price=950,
        recommended_buy_price=700,
        recommended_sell_price=sell_price,
        recommended_sell_price_source="probable_sold",
        average_first_quartile_unit_price=900,
        average_median_unit_price=950,
        average_third_quartile_unit_price=1000,
        average_weighted_unit_price=950,
        price_trend_score=50,
        price_trend_ratio=0.0,
        estimated_demand_score=50,
        average_sell_through_ratio=0.1,
        average_sell_through_confidence=80,
        average_probable_sold_unit_price=sell_price,
        vendor_sell_unit_price=100,
        auction_deposit_unit_price=deposit,
        estimated_profit_unit_price=200,
        player_post_count=0,
        player_sold_count=0,
        player_expired_count=0,
        player_cancelled_count=0,
        player_sale_rate=0.0,
        average_player_net_proceeds=None,
        best_buy_time=None,
        best_sell_time=None,
        historical_buy_price=None,
        historical_sell_price=None,
        historical_timing_confidence=0,
        reasons=["test"],
    )
