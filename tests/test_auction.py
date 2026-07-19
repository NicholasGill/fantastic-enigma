from wow_auction_tracker.auction import calculate_item_history_metrics, filter_auctions, summarize_listings
from wow_auction_tracker.config import Market


def test_filter_auctions_keeps_configured_item_ids() -> None:
    payload = {
        "auctions": [
            {
                "id": 100,
                "item": {"id": 124105},
                "quantity": 5,
                "unit_price": 1200,
                "time_left": "LONG",
            },
            {
                "id": 101,
                "item": {"id": 999999},
                "quantity": 3,
                "unit_price": 100,
            },
        ]
    }

    listings = filter_auctions(payload, {124105}, Market.COMMODITY)

    assert len(listings) == 1
    assert listings[0].auction_id == 100
    assert listings[0].item_id == 124105
    assert listings[0].quantity == 5
    assert listings[0].effective_unit_price == 1200


def test_summarize_listings_uses_unit_price_or_buyout_per_unit() -> None:
    commodity_payload = {
        "auctions": [
            {"id": 1, "item": {"id": 1}, "quantity": 2, "unit_price": 100},
            {"id": 2, "item": {"id": 1}, "quantity": 4, "unit_price": 300},
        ]
    }
    realm_payload = {
        "auctions": [
            {"id": 3, "item": {"id": 2}, "quantity": 5, "buyout": 1000},
        ]
    }

    listings = [
        *filter_auctions(commodity_payload, {1}, Market.COMMODITY),
        *filter_auctions(realm_payload, {2}, Market.REALM),
    ]
    summaries = summarize_listings(listings)

    assert summaries[0].item_id == 1
    assert summaries[0].listing_count == 2
    assert summaries[0].total_quantity == 6
    assert summaries[0].min_unit_price == 100
    assert summaries[0].first_quartile_unit_price == 100
    assert summaries[0].median_unit_price == 200
    assert summaries[0].third_quartile_unit_price == 300
    assert summaries[1].item_id == 2
    assert summaries[1].min_unit_price == 200


def test_summarize_listings_calculates_quartiles_for_middle_price_band() -> None:
    payload = {
        "auctions": [
            {"id": 1, "item": {"id": 1}, "quantity": 1, "unit_price": 100},
            {"id": 2, "item": {"id": 1}, "quantity": 1, "unit_price": 200},
            {"id": 3, "item": {"id": 1}, "quantity": 1, "unit_price": 300},
            {"id": 4, "item": {"id": 1}, "quantity": 1, "unit_price": 100000},
        ]
    }

    summary = summarize_listings(filter_auctions(payload, {1}, Market.COMMODITY))[0]

    assert summary.min_unit_price == 100
    assert summary.first_quartile_unit_price == 100
    assert summary.median_unit_price == 200
    assert summary.third_quartile_unit_price == 300


def test_summarize_listings_uses_the_densest_contiguous_price_band() -> None:
    prices_and_quantities = (
        (74000, 23),
        (75000, 355),
        (78000, 28),
        (96000, 18),
        (100000, 108),
        (250000, 4000),
        (1970000, 400),
        (409990000, 1),
        (488880018, 5),
        (488880019, 5),
    )
    payload = {
        "auctions": [
            {
                "id": index,
                "item": {"id": 1},
                "quantity": quantity,
                "unit_price": price,
            }
            for index, (price, quantity) in enumerate(prices_and_quantities, start=1)
        ]
    }

    summary = summarize_listings(filter_auctions(payload, {1}, Market.COMMODITY))[0]

    assert summary.min_unit_price == 74000
    assert summary.first_quartile_unit_price == 74500
    assert summary.median_unit_price == 78000
    assert summary.third_quartile_unit_price == 98000


def test_summarize_listings_ignores_an_isolated_low_price_bait_listing() -> None:
    payload = {
        "auctions": [
            {"id": 1, "item": {"id": 1}, "quantity": 1, "unit_price": 200},
            {"id": 2, "item": {"id": 1}, "quantity": 5, "unit_price": 10000},
            {"id": 3, "item": {"id": 1}, "quantity": 5, "unit_price": 10500},
            {"id": 4, "item": {"id": 1}, "quantity": 5, "unit_price": 11000},
            {"id": 5, "item": {"id": 1}, "quantity": 5, "unit_price": 11500},
        ]
    }

    summary = summarize_listings(filter_auctions(payload, {1}, Market.COMMODITY))[0]

    assert summary.min_unit_price == 200
    assert summary.first_quartile_unit_price == 10250
    assert summary.median_unit_price == 10750
    assert summary.third_quartile_unit_price == 11250


def test_summarize_listings_keeps_gradually_increasing_prices() -> None:
    payload = {
        "auctions": [
            {"id": 1, "item": {"id": 1}, "quantity": 1, "unit_price": 100},
            {"id": 2, "item": {"id": 1}, "quantity": 1, "unit_price": 180},
            {"id": 3, "item": {"id": 1}, "quantity": 1, "unit_price": 300},
            {"id": 4, "item": {"id": 1}, "quantity": 1, "unit_price": 500},
        ]
    }

    summary = summarize_listings(filter_auctions(payload, {1}, Market.COMMODITY))[0]

    assert summary.first_quartile_unit_price == 140
    assert summary.median_unit_price == 240
    assert summary.third_quartile_unit_price == 400


def test_calculate_item_history_metrics_tracks_weighted_average_and_lowest_quantity() -> None:
    payload = {
        "auctions": [
            {"id": 1, "item": {"id": 1}, "quantity": 2, "unit_price": 100},
            {"id": 2, "item": {"id": 1}, "quantity": 8, "unit_price": 180},
            {"id": 3, "item": {"id": 1}, "quantity": 5, "unit_price": 100},
        ]
    }

    metrics = calculate_item_history_metrics(filter_auctions(payload, {1}, Market.COMMODITY))

    assert metrics[0].first_quartile_unit_price == 100
    assert metrics[0].third_quartile_unit_price == 180
    assert metrics[0].weighted_average_unit_price == 142
    assert metrics[0].lowest_price_quantity == 7


def test_calculate_item_history_metrics_weights_only_the_representative_band() -> None:
    payload = {
        "auctions": [
            {"id": 1, "item": {"id": 1}, "quantity": 10, "unit_price": 100},
            {"id": 2, "item": {"id": 1}, "quantity": 10, "unit_price": 110},
            {"id": 3, "item": {"id": 1}, "quantity": 10, "unit_price": 120},
            {"id": 4, "item": {"id": 1}, "quantity": 10000, "unit_price": 100000},
        ]
    }

    metrics = calculate_item_history_metrics(
        filter_auctions(payload, {1}, Market.COMMODITY)
    )[0]

    assert metrics.total_quantity == 10030
    assert metrics.min_unit_price == 100
    assert metrics.median_unit_price == 110
    assert metrics.weighted_average_unit_price == 110
