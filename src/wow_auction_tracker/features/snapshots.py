from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from wow_auction_tracker.auction import (
    AuctionListing,
    calculate_item_history_metrics,
    filter_auctions,
    summarize_listings,
)
from wow_auction_tracker.clients.blizzard import BlizzardClient
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.features.crafting import build_craft_opportunity_observations
from wow_auction_tracker.features.lifecycle import build_listing_observations
from wow_auction_tracker.features.market_data import (
    RawAuctionSnapshot,
    api_path_for_market,
    build_market_quality_events,
    read_raw_auction_payload,
    write_raw_auction_snapshot,
)
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
    raw_snapshot_dir: Path | None = None,
) -> FetchResult:
    fetch_run_id = repository.start_fetch_run(config, expected_interval_seconds=expected_interval_seconds)

    try:
        metadata_items = _fetch_missing_item_metadata(config, client, repository)
        repository.upsert_item_metadata(metadata_items)

        raw_snapshots: list[RawAuctionSnapshot] = []
        listings: list[AuctionListing] = []
        if config.realm_item_ids:
            if config.connected_realm_id is None:
                raise ValueError("connected_realm_id is required for realm items")
            payload = client.fetch_connected_realm_auctions(config.connected_realm_id)
            snapshot, payload = _store_raw_market_payload(
                payload,
                config=config,
                repository=repository,
                fetch_run_id=fetch_run_id,
                market=Market.REALM,
                raw_snapshot_dir=raw_snapshot_dir,
            )
            raw_snapshots.append(snapshot)
            listings.extend(filter_auctions(payload, config.realm_item_ids, Market.REALM))

        if config.commodity_item_ids:
            payload = client.fetch_commodity_auctions()
            snapshot, payload = _store_raw_market_payload(
                payload,
                config=config,
                repository=repository,
                fetch_run_id=fetch_run_id,
                market=Market.COMMODITY,
                raw_snapshot_dir=raw_snapshot_dir,
            )
            raw_snapshots.append(snapshot)
            listings.extend(filter_auctions(payload, config.commodity_item_ids, Market.COMMODITY))

        summaries = summarize_listings(listings)
        history_metrics = calculate_item_history_metrics(listings)
        previous_fetch_run_id = repository.previous_successful_fetch_run_id(fetch_run_id)
        previous_listings = (
            repository.list_listing_snapshots(previous_fetch_run_id)
            if previous_fetch_run_id is not None
            else []
        )
        elapsed_seconds = _elapsed_seconds_between_runs(repository, previous_fetch_run_id, fetch_run_id)
        listing_observations = build_listing_observations(
            listings,
            previous_listings,
            elapsed_seconds=elapsed_seconds,
        )
        sell_through_metrics = build_sell_through_metrics(
            listing_observations,
            elapsed_seconds=elapsed_seconds,
            expected_interval_seconds=expected_interval_seconds,
        )
        recommendations = _load_prior_recommendations(repository)
        buy_opportunity_observations = build_buy_opportunity_observations(
            listings,
            listing_observations,
            recommendations,
        )
        craft_opportunity_observations = build_craft_opportunity_observations(
            config.recipes,
            listings,
            recommendations,
        )
        quality_events = build_market_quality_events(
            fetch_run_id=fetch_run_id,
            detected_at=repository.fetch_run_started_at(fetch_run_id) or raw_snapshots[-1].fetched_at,
            raw_snapshots=raw_snapshots,
            previous_raw_snapshots=repository.previous_raw_auction_snapshots(fetch_run_id),
            configured_item_ids_by_market={
                Market.REALM.value: config.realm_item_ids,
                Market.COMMODITY.value: config.commodity_item_ids,
            },
            observed_item_ids_by_market=_observed_item_ids_by_market(listings),
            expected_interval_seconds=expected_interval_seconds,
            elapsed_seconds=elapsed_seconds,
        )
        repository.complete_fetch_run(
            fetch_run_id,
            listings,
            summaries,
            history_metrics,
            listing_observations,
            sell_through_metrics,
            buy_opportunity_observations,
            craft_opportunity_observations,
            quality_events,
        )
        return FetchResult(
            fetch_run_id=fetch_run_id,
            listing_count=len(listings),
            summary_count=len(summaries),
        )
    except Exception as exc:
        repository.fail_fetch_run(fetch_run_id, str(exc))
        raise


def replay_raw_fetch_run(
    config: TrackerConfig,
    repository: AuctionRepository,
    fetch_run_id: int,
) -> FetchResult:
    raw_snapshots = repository.list_raw_auction_snapshots(fetch_run_id)
    if not raw_snapshots:
        raise ValueError(f"fetch run {fetch_run_id} has no raw auction snapshots")

    listings: list[AuctionListing] = []
    for snapshot in raw_snapshots:
        payload = read_raw_auction_payload(snapshot.storage_path)
        market = Market(snapshot.market)
        item_ids = config.realm_item_ids if market == Market.REALM else config.commodity_item_ids
        listings.extend(filter_auctions(payload, item_ids, market))

    summaries = summarize_listings(listings)
    history_metrics = calculate_item_history_metrics(listings)
    previous_fetch_run_id = repository.previous_successful_fetch_run_id(fetch_run_id)
    previous_listings = (
        repository.list_listing_snapshots(previous_fetch_run_id)
        if previous_fetch_run_id is not None
        else []
    )
    elapsed_seconds = _elapsed_seconds_between_runs(repository, previous_fetch_run_id, fetch_run_id)
    listing_observations = build_listing_observations(
        listings,
        previous_listings,
        elapsed_seconds=elapsed_seconds,
    )
    sell_through_metrics = build_sell_through_metrics(
        listing_observations,
        elapsed_seconds=elapsed_seconds,
        expected_interval_seconds=repository.fetch_run_expected_interval_seconds(fetch_run_id),
    )
    recommendations = _load_prior_recommendations(repository)
    buy_opportunity_observations = build_buy_opportunity_observations(listings, listing_observations, recommendations)
    craft_opportunity_observations = build_craft_opportunity_observations(config.recipes, listings, recommendations)
    detected_at = repository.fetch_run_started_at(fetch_run_id) or raw_snapshots[-1].fetched_at
    quality_events = build_market_quality_events(
        fetch_run_id=fetch_run_id,
        detected_at=detected_at,
        raw_snapshots=raw_snapshots,
        previous_raw_snapshots=repository.previous_raw_auction_snapshots(fetch_run_id),
        configured_item_ids_by_market={
            Market.REALM.value: config.realm_item_ids,
            Market.COMMODITY.value: config.commodity_item_ids,
        },
        observed_item_ids_by_market=_observed_item_ids_by_market(listings),
        expected_interval_seconds=repository.fetch_run_expected_interval_seconds(fetch_run_id),
        elapsed_seconds=elapsed_seconds,
    )
    repository.replace_snapshot_derivatives(
        fetch_run_id,
        listings,
        summaries,
        history_metrics,
        listing_observations,
        sell_through_metrics,
        buy_opportunity_observations,
        craft_opportunity_observations,
        quality_events,
    )
    return FetchResult(fetch_run_id=fetch_run_id, listing_count=len(listings), summary_count=len(summaries))


def _fetch_missing_item_metadata(
    config: TrackerConfig,
    client: BlizzardClient,
    repository: AuctionRepository,
) -> list[ItemMetadata]:
    missing_item_ids = repository.missing_metadata_item_ids(item.id for item in config.all_tracked_items)
    metadata_items: list[ItemMetadata] = []
    for item_id in sorted(missing_item_ids):
        item_payload = client.fetch_item(item_id)
        media_payload = client.fetch_item_media(item_id)
        metadata_items.append(parse_item_metadata(item_payload, media_payload))
    return metadata_items


def _store_raw_market_payload(
    payload: dict[str, object],
    *,
    config: TrackerConfig,
    repository: AuctionRepository,
    fetch_run_id: int,
    market: Market,
    raw_snapshot_dir: Path | None,
) -> tuple[RawAuctionSnapshot, dict[str, object]]:
    root_dir = raw_snapshot_dir or Path(os.getenv("WAT_RAW_SNAPSHOT_DIR", "data/raw_snapshots"))
    snapshot, payload = write_raw_auction_snapshot(
        payload,
        root_dir=root_dir,
        fetch_run_id=fetch_run_id,
        region=config.region,
        locale=config.locale,
        namespace=f"dynamic-{config.region}",
        market=market,
        connected_realm_id=config.connected_realm_id if market == Market.REALM else None,
        api_path=api_path_for_market(market, config.connected_realm_id),
    )
    repository.store_raw_auction_snapshot(snapshot)
    return snapshot, payload


def _observed_item_ids_by_market(listings: list[AuctionListing]) -> dict[str, set[int]]:
    observed: dict[str, set[int]] = {}
    for listing in listings:
        observed.setdefault(listing.market.value, set()).add(listing.item_id)
    return observed


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
