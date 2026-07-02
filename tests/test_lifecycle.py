from wow_auction_tracker.auction import AuctionListing
from wow_auction_tracker.config import Market
from wow_auction_tracker.features.lifecycle import ListingSnapshot, build_listing_observations


def test_build_listing_observations_marks_new_active_changed_and_missing() -> None:
    current = [
        AuctionListing(1, 100, Market.COMMODITY, 5, 100, None, None, "LONG", {}),
        AuctionListing(2, 100, Market.COMMODITY, 2, 250, None, None, "SHORT", {}),
        AuctionListing(4, 100, Market.COMMODITY, 1, 500, None, None, "VERY_LONG", {}),
    ]
    previous = [
        ListingSnapshot("auction:1", 1, 100, "commodity", 5, 100, None, None, "LONG"),
        ListingSnapshot("auction:2", 2, 100, "commodity", 3, 200, None, None, "SHORT"),
        ListingSnapshot("auction:3", 3, 100, "commodity", 8, 300, None, None, "MEDIUM"),
    ]

    observations = build_listing_observations(current, previous)
    statuses = {observation.auction_id: observation.status for observation in observations}
    outcomes = {observation.auction_id: observation.inferred_outcome for observation in observations}

    assert statuses == {1: "active", 2: "changed", 3: "missing", 4: "new"}
    assert outcomes[2] == "probable_sold"


def test_build_listing_observations_labels_missing_listing_outcomes_from_elapsed_time() -> None:
    previous = [
        ListingSnapshot("auction:1", 1, 100, "commodity", 5, 100, None, None, "LONG"),
        ListingSnapshot("auction:2", 2, 100, "commodity", 3, 200, None, None, "SHORT"),
        ListingSnapshot("auction:3", 3, 100, "commodity", 8, 300, None, None, "MEDIUM"),
    ]

    observations = build_listing_observations([], previous, elapsed_seconds=60 * 60)
    outcomes = {observation.auction_id: observation.inferred_outcome for observation in observations}

    assert outcomes == {
        1: "probable_sold",
        2: "probable_expired",
        3: "removed_unknown",
    }
