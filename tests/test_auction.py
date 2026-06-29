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
    assert summaries[0].median_unit_price == 200
    assert summaries[1].item_id == 2
    assert summaries[1].min_unit_price == 200


def test_calculate_item_history_metrics_tracks_weighted_average_and_lowest_quantity() -> None:
    payload = {
        "auctions": [
            {"id": 1, "item": {"id": 1}, "quantity": 2, "unit_price": 100},
            {"id": 2, "item": {"id": 1}, "quantity": 8, "unit_price": 300},
            {"id": 3, "item": {"id": 1}, "quantity": 5, "unit_price": 100},
        ]
    }

    metrics = calculate_item_history_metrics(filter_auctions(payload, {1}, Market.COMMODITY))

    assert metrics[0].weighted_average_unit_price == 206
    assert metrics[0].lowest_price_quantity == 7
