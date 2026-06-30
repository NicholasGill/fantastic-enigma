from wow_auction_tracker.features.lifecycle import ListingObservation
from wow_auction_tracker.features.sellthrough import build_sell_through_metrics


def test_build_sell_through_metrics_aggregates_missing_listings() -> None:
    observations = [
        ListingObservation("auction:1", 1, 210930, "commodity", "active", 5, 5, 100, 100, None, None, None, None, "LONG", "LONG"),
        ListingObservation("auction:2", 2, 210930, "commodity", "missing", None, 3, None, 200, None, None, None, None, None, "SHORT"),
        ListingObservation("auction:3", 3, 210930, "commodity", "new", 4, None, 150, None, None, None, None, None, "LONG", None),
    ]

    metrics = build_sell_through_metrics(observations)

    assert len(metrics) == 1
    assert metrics[0].item_id == 210930
    assert metrics[0].disappeared_listing_count == 1
    assert metrics[0].disappeared_quantity == 3
    assert metrics[0].disappeared_value == 600
    assert metrics[0].observed_quantity == 8
    assert metrics[0].sell_through_ratio == 0.375
    assert metrics[0].confidence > 0
