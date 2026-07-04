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
    assert metrics[0].probable_sold_listing_count == 0
    assert metrics[0].probable_sold_average_unit_price is None
    assert metrics[0].observed_quantity == 8
    assert metrics[0].sell_through_ratio == 0.375
    assert metrics[0].confidence > 0


def test_build_sell_through_metrics_aggregates_probable_sales() -> None:
    observations = [
        ListingObservation(
            "auction:1",
            1,
            210930,
            "commodity",
            "missing",
            None,
            3,
            None,
            200,
            None,
            None,
            None,
            None,
            None,
            "LONG",
            "probable_sold",
        ),
        ListingObservation(
            "auction:2",
            2,
            210930,
            "commodity",
            "missing",
            None,
            2,
            None,
            500,
            None,
            None,
            None,
            None,
            None,
            "SHORT",
            "probable_expired",
        ),
    ]

    metrics = build_sell_through_metrics(observations)

    assert metrics[0].probable_sold_listing_count == 1
    assert metrics[0].probable_sold_quantity == 3
    assert metrics[0].probable_sold_value == 600
    assert metrics[0].probable_sold_average_unit_price == 200


def test_build_sell_through_metrics_treats_quantity_drop_as_probable_sale() -> None:
    observations = [
        ListingObservation(
            "auction:1",
            1,
            210930,
            "commodity",
            "changed",
            6,
            10,
            250,
            250,
            None,
            None,
            None,
            None,
            "LONG",
            "LONG",
            "probable_sold",
        )
    ]

    metrics = build_sell_through_metrics(observations)

    assert metrics[0].disappeared_quantity == 0
    assert metrics[0].probable_sold_listing_count == 1
    assert metrics[0].probable_sold_quantity == 4
    assert metrics[0].probable_sold_value == 1000
    assert metrics[0].probable_sold_average_unit_price == 250


def test_build_sell_through_metrics_downweights_irregular_snapshot_cadence() -> None:
    observations = [
        ListingObservation(
            f"auction:{index}",
            index,
            210930,
            "commodity",
            "active",
            1,
            1,
            100,
            100,
            None,
            None,
            None,
            None,
            "LONG",
            "LONG",
        )
        for index in range(1, 21)
    ]

    regular = build_sell_through_metrics(
        observations,
        elapsed_seconds=1800,
        expected_interval_seconds=1800,
    )
    irregular = build_sell_through_metrics(
        observations,
        elapsed_seconds=6 * 60 * 60,
        expected_interval_seconds=1800,
    )

    assert regular[0].confidence > irregular[0].confidence
