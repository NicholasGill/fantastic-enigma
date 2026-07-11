from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wow_auction_tracker.config import Market


@dataclass(frozen=True)
class RawAuctionSnapshot:
    fetch_run_id: int
    fetched_at: datetime
    region: str
    locale: str
    namespace: str
    market: str
    connected_realm_id: int | None
    api_path: str
    storage_path: str
    payload_sha256: str
    payload_size_bytes: int
    compressed_size_bytes: int
    auction_count: int
    item_count: int


@dataclass(frozen=True)
class MarketQualityEvent:
    fetch_run_id: int
    detected_at: datetime
    event_type: str
    severity: int
    market: str | None
    item_id: int | None
    expected_value: str | None
    observed_value: str | None
    explanation: str


def write_raw_auction_snapshot(
    payload: dict[str, Any],
    *,
    root_dir: Path,
    fetch_run_id: int,
    region: str,
    locale: str,
    namespace: str,
    market: Market,
    connected_realm_id: int | None,
    api_path: str,
    fetched_at: datetime | None = None,
) -> tuple[RawAuctionSnapshot, dict[str, Any]]:
    fetched = fetched_at or datetime.now(UTC)
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload_bytes).hexdigest()
    relative_path = Path(region) / market.value / f"{fetched:%Y}" / f"{fetched:%m}" / f"{fetched:%d}"
    file_name = f"run-{fetch_run_id}-{market.value}-{digest[:12]}.json.gz"
    storage_path = root_dir / relative_path / file_name
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(storage_path, "wb") as file:
        file.write(payload_bytes)

    snapshot = RawAuctionSnapshot(
        fetch_run_id=fetch_run_id,
        fetched_at=fetched,
        region=region,
        locale=locale,
        namespace=namespace,
        market=market.value,
        connected_realm_id=connected_realm_id,
        api_path=api_path,
        storage_path=str(storage_path),
        payload_sha256=digest,
        payload_size_bytes=len(payload_bytes),
        compressed_size_bytes=storage_path.stat().st_size,
        auction_count=_auction_count(payload),
        item_count=_item_count(payload),
    )
    return snapshot, payload


def read_raw_auction_payload(storage_path: str | Path) -> dict[str, Any]:
    with gzip.open(storage_path, "rb") as file:
        payload = json.loads(file.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"raw auction payload at {storage_path} is not a JSON object")
    return payload


def build_market_quality_events(
    *,
    fetch_run_id: int,
    detected_at: datetime,
    raw_snapshots: list[RawAuctionSnapshot],
    previous_raw_snapshots: list[RawAuctionSnapshot],
    configured_item_ids_by_market: dict[str, set[int]],
    observed_item_ids_by_market: dict[str, set[int]],
    expected_interval_seconds: int | None,
    elapsed_seconds: int | None,
) -> list[MarketQualityEvent]:
    events: list[MarketQualityEvent] = []
    previous_by_market = {snapshot.market: snapshot for snapshot in previous_raw_snapshots}

    if expected_interval_seconds and elapsed_seconds and elapsed_seconds > expected_interval_seconds * 2:
        events.append(
            MarketQualityEvent(
                fetch_run_id=fetch_run_id,
                detected_at=detected_at,
                event_type="stale_snapshot",
                severity=70,
                market=None,
                item_id=None,
                expected_value=str(expected_interval_seconds),
                observed_value=str(elapsed_seconds),
                explanation=(
                    f"elapsed time {elapsed_seconds}s is more than twice expected cadence "
                    f"{expected_interval_seconds}s"
                ),
            )
        )

    for snapshot in raw_snapshots:
        if snapshot.auction_count == 0:
            events.append(
                MarketQualityEvent(
                    fetch_run_id=fetch_run_id,
                    detected_at=detected_at,
                    event_type="empty_payload",
                    severity=90,
                    market=snapshot.market,
                    item_id=None,
                    expected_value=">0",
                    observed_value="0",
                    explanation=f"{snapshot.market} payload contained no auctions",
                )
            )

        previous = previous_by_market.get(snapshot.market)
        if previous is not None:
            if previous.payload_sha256 == snapshot.payload_sha256:
                events.append(
                    MarketQualityEvent(
                        fetch_run_id=fetch_run_id,
                        detected_at=detected_at,
                        event_type="repeated_payload",
                        severity=60,
                        market=snapshot.market,
                        item_id=None,
                        expected_value="new payload hash",
                        observed_value=snapshot.payload_sha256[:12],
                        explanation=f"{snapshot.market} payload hash matches previous successful snapshot",
                    )
                )

            if previous.auction_count > 0:
                change_ratio = abs(snapshot.auction_count - previous.auction_count) / previous.auction_count
                if change_ratio >= 0.5:
                    events.append(
                        MarketQualityEvent(
                            fetch_run_id=fetch_run_id,
                            detected_at=detected_at,
                            event_type="record_count_change",
                            severity=round(min(100.0, change_ratio * 100)),
                            market=snapshot.market,
                            item_id=None,
                            expected_value=str(previous.auction_count),
                            observed_value=str(snapshot.auction_count),
                            explanation=(
                                f"{snapshot.market} auction count changed from "
                                f"{previous.auction_count} to {snapshot.auction_count}"
                            ),
                        )
                    )

    for market, configured_ids in configured_item_ids_by_market.items():
        observed_ids = observed_item_ids_by_market.get(market, set())
        for item_id in sorted(configured_ids - observed_ids):
            events.append(
                MarketQualityEvent(
                    fetch_run_id=fetch_run_id,
                    detected_at=detected_at,
                    event_type="missing_configured_item",
                    severity=50,
                    market=market,
                    item_id=item_id,
                    expected_value="present",
                    observed_value="missing",
                    explanation=f"configured {market} item {item_id} was absent from the filtered snapshot",
                )
            )

    return events


def api_path_for_market(market: Market, connected_realm_id: int | None) -> str:
    if market == Market.REALM:
        if connected_realm_id is None:
            raise ValueError("connected_realm_id is required for realm auction API path")
        return f"/data/wow/connected-realm/{connected_realm_id}/auctions"
    return "/data/wow/auctions/commodities"


def _auction_count(payload: dict[str, Any]) -> int:
    auctions = payload.get("auctions")
    return len(auctions) if isinstance(auctions, list) else 0


def _item_count(payload: dict[str, Any]) -> int:
    auctions = payload.get("auctions")
    if not isinstance(auctions, list):
        return 0
    item_ids = set()
    for auction in auctions:
        if not isinstance(auction, dict):
            continue
        item = auction.get("item")
        if isinstance(item, dict) and item.get("id") is not None:
            item_ids.add(int(item["id"]))
    return len(item_ids)
