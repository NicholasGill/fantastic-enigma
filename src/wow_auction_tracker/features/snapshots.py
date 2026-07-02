from __future__ import annotations

from dataclasses import dataclass

from wow_auction_tracker.auction import (
    AuctionListing,
    calculate_item_history_metrics,
    filter_auctions,
    summarize_listings,
)
from wow_auction_tracker.clients.blizzard import BlizzardClient
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.lifecycle import build_listing_observations
from wow_auction_tracker.features.metadata import ItemMetadata, parse_item_metadata
from wow_auction_tracker.features.opportunities import build_buy_opportunity_observations
from wow_auction_tracker.features.recommendations import Recommendation, RecommendationEngine
from wow_auction_tracker.features.sellthrough import build_sell_through_metrics
from wow_auction_tracker.storage import AuctionRepository


@dataclass(frozen=True)
class FetchResult:
    fetch_run_id: int
    listing_count: int
    summary_count: int


def fetch_and_store(
    config: TrackerConfig,
    client: BlizzardClient,
    repository: AuctionRepository,
    *,
    expected_interval_seconds: int | None = None,
) -> FetchResult:
    fetch_run_id = repository.start_fetch_run(config, expected_interval_seconds=expected_interval_seconds)

    try:
        metadata_items = _fetch_missing_item_metadata(config, client, repository)
        repository.upsert_item_metadata(metadata_items)

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
        history_metrics = calculate_item_history_metrics(listings)
        previous_fetch_run_id = repository.previous_successful_fetch_run_id(fetch_run_id)
        previous_listings = (
            repository.list_listing_snapshots(previous_fetch_run_id)
            if previous_fetch_run_id is not None
            else []
        )
        listing_observations = build_listing_observations(
            listings,
            previous_listings,
            elapsed_seconds=_elapsed_seconds_between_runs(repository, previous_fetch_run_id, fetch_run_id),
        )
        sell_through_metrics = build_sell_through_metrics(listing_observations)
        recommendations = _load_prior_recommendations(repository)
        buy_opportunity_observations = build_buy_opportunity_observations(
            listings,
            listing_observations,
            recommendations,
        )
        repository.complete_fetch_run(
            fetch_run_id,
            listings,
            summaries,
            history_metrics,
            listing_observations,
            sell_through_metrics,
            buy_opportunity_observations,
        )
        return FetchResult(
            fetch_run_id=fetch_run_id,
            listing_count=len(listings),
            summary_count=len(summaries),
        )
    except Exception as exc:
        repository.fail_fetch_run(fetch_run_id, str(exc))
        raise


def _fetch_missing_item_metadata(
    config: TrackerConfig,
    client: BlizzardClient,
    repository: AuctionRepository,
) -> list[ItemMetadata]:
    missing_item_ids = repository.missing_metadata_item_ids(item.id for item in config.items)
    metadata_items: list[ItemMetadata] = []
    for item_id in sorted(missing_item_ids):
        item_payload = client.fetch_item(item_id)
        media_payload = client.fetch_item_media(item_id)
        metadata_items.append(parse_item_metadata(item_payload, media_payload))
    return metadata_items


def _load_prior_recommendations(repository: AuctionRepository) -> list[Recommendation]:
    try:
        return RecommendationEngine(str(repository.engine.url)).recommend()
    except ValueError:
        return []


def _elapsed_seconds_between_runs(
    repository: AuctionRepository,
    previous_fetch_run_id: int | None,
    current_fetch_run_id: int,
) -> int | None:
    if previous_fetch_run_id is None:
        return None
    previous_started_at = repository.fetch_run_started_at(previous_fetch_run_id)
    current_started_at = repository.fetch_run_started_at(current_fetch_run_id)
    if previous_started_at is None or current_started_at is None:
        return None
    return max(0, round((current_started_at - previous_started_at).total_seconds()))
