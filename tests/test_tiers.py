from wow_auction_tracker.features.tiers import TierMarketItem, analyze_tier_market


def test_tier_market_marks_a_more_expensive_middle_tier_as_dominated() -> None:
    analysis = analyze_tier_market(
        [
            TierMarketItem(210930, "Bismuth", "commodity", 297300),
            TierMarketItem(210931, "Bismuth", "commodity", 721000),
            TierMarketItem(210932, "Bismuth", "commodity", 329800),
        ]
    )

    assert analysis[210930].quality == 1
    assert analysis[210930].is_best_value is True
    assert analysis[210930].is_dominated is False
    assert analysis[210931].quality == 2
    assert analysis[210931].is_dominated is True
    assert analysis[210931].dominated_by_item_id == 210932
    assert analysis[210931].dominated_by_quality == 3
    assert analysis[210931].dominated_by_unit_price == 329800
    assert analysis[210931].dominance_savings_bps == 5426
    assert analysis[210932].is_dominated is False


def test_tier_market_uses_explicit_quality_and_keeps_missing_prices() -> None:
    analysis = analyze_tier_market(
        [
            TierMarketItem(30, "Cloth", "commodity", 300, quality=3),
            TierMarketItem(10, "Cloth", "commodity", None, quality=1),
            TierMarketItem(20, "Cloth", "commodity", 300, quality=2),
        ]
    )

    assert analysis[10].quality == 1
    assert analysis[10].typical_unit_price is None
    assert analysis[20].dominated_by_item_id == 30
    assert analysis[30].is_best_value is True


def test_tier_market_does_not_group_unique_item_names() -> None:
    analysis = analyze_tier_market(
        [
            TierMarketItem(1, "Bismuth", "commodity", 100),
            TierMarketItem(2, "Aqirite", "commodity", 100),
        ]
    )

    assert analysis == {}
