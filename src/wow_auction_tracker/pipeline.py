from __future__ import annotations

from dataclasses import dataclass

from wow_auction_tracker.auction import AuctionListing, filter_auctions, summarize_listings
from wow_auction_tracker.blizzard import BlizzardClient
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.db import AuctionRepository


@dataclass(frozen=True)
class FetchResult:
    fetch_run_id: int
    listing_count: int
    summary_count: int


def fetch_and_store(
    config: TrackerConfig,
    client: BlizzardClient,
    repository: AuctionRepository,
) -> FetchResult:
    fetch_run_id = repository.start_fetch_run(config)

    try:
        listings: list[AuctionListing] = []
        if config.realm_item_ids:
            if config.connected_realm_id is None:
                raise ValueError("connected_realm_id is required for realm items")
            payload = client.fetch_connected_realm_auctions(config.connected_realm_id)
            listings.extend(filter_auctions(payload, config.realm_item_ids, Market.REALM))

        if config.commodity_item_ids:
            payload = client.fetch_commodity_auctions()
            listings.extend(filter_auctions(payload, config.commodity_item_ids, Market.COMMODITY))

        summaries = summarize_listings(listings)
        repository.complete_fetch_run(fetch_run_id, listings, summaries)
        return FetchResult(
            fetch_run_id=fetch_run_id,
            listing_count=len(listings),
            summary_count=len(summaries),
        )
    except Exception as exc:
        repository.fail_fetch_run(fetch_run_id, str(exc))
        raise
