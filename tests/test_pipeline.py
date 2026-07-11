from typing import Any
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from wow_auction_tracker.config import TrackerConfig
from wow_auction_tracker.features.snapshots import fetch_and_store
from wow_auction_tracker.storage import (
    AuctionRepository,
    BuyOpportunityObservationRecord,
    CraftOpportunityObservationRecord,
    FetchRun,
    ItemHistoryMetricRecord,
    MarketQualityEventRecord,
    RawAuctionSnapshotRecord,
    create_db_engine,
    init_db,
)


class FakeClient:
    def __init__(self, realm_payload: dict[str, Any] | None = None, commodity_payload: dict[str, Any] | None = None):
        self.realm_payload = realm_payload or {"auctions": []}
        self.commodity_payload = commodity_payload or {"auctions": []}
        self.fetched_item_ids: list[int] = []

    def fetch_connected_realm_auctions(self, connected_realm_id: int) -> dict[str, Any]:
        assert connected_realm_id == 3678
        return self.realm_payload

    def fetch_commodity_auctions(self) -> dict[str, Any]:
        return self.commodity_payload

    def fetch_item(self, item_id: int) -> dict[str, Any]:
        self.fetched_item_ids.append(item_id)
        return {
            "id": item_id,
            "name": f"Item {item_id}",
            "quality": {"type": "COMMON"},
            "item_class": {"name": "Tradeskill"},
            "item_subclass": {"name": "Metal & Stone"},
            "inventory_type": {"type": "NON_EQUIP"},
            "is_stackable": True,
            "is_equippable": False,
        }

    def fetch_item_media(self, item_id: int) -> dict[str, Any]:
        return {"id": item_id, "assets": [{"key": "icon", "value": f"https://example.test/{item_id}.jpg"}]}


def test_fetch_and_store_combines_realm_and_commodity_sources() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {
            "connected_realm_id": 3678,
            "items": [
                {"id": 19019, "market": "realm"},
                {"id": 124105, "market": "commodity"},
            ],
        }
    )
    client = FakeClient(
        realm_payload={"auctions": [{"id": 1, "item": {"id": 19019}, "quantity": 1, "buyout": 1000}]},
        commodity_payload={"auctions": [{"id": 2, "item": {"id": 124105}, "quantity": 4, "unit_price": 200}]},
    )

    result = fetch_and_store(config, client, repository)  # type: ignore[arg-type]

    assert result.listing_count == 2
    assert result.summary_count == 2
    assert client.fetched_item_ids == [19019, 124105]


def test_fetch_and_store_preserves_raw_payload_metadata(tmp_path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    raw_dir = tmp_path / "raw"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 124105, "market": "commodity"}]})

    result = fetch_and_store(
        config,
        FakeClient(commodity_payload={"auctions": [{"id": 2, "item": {"id": 124105}, "quantity": 4, "unit_price": 200}]}),
        repository,
        raw_snapshot_dir=raw_dir,
    )  # type: ignore[arg-type]

    with Session(engine) as session:
        snapshot = session.scalars(select(RawAuctionSnapshotRecord)).one()

    assert snapshot.fetch_run_id == result.fetch_run_id
    assert snapshot.market == "commodity"
    assert snapshot.auction_count == 1
    assert snapshot.item_count == 1
    assert snapshot.payload_sha256
    assert Path(snapshot.storage_path).exists()
    assert Path(snapshot.storage_path).is_relative_to(raw_dir)


def test_fetch_and_store_records_quality_events_for_missing_items(tmp_path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 124105, "market": "commodity"}]})

    fetch_and_store(
        config,
        FakeClient(commodity_payload={"auctions": [{"id": 2, "item": {"id": 999999}, "quantity": 4, "unit_price": 200}]}),
        repository,
        raw_snapshot_dir=tmp_path / "raw",
    )  # type: ignore[arg-type]

    with Session(engine) as session:
        events = session.scalars(select(MarketQualityEventRecord)).all()

    assert {event.event_type for event in events} == {"missing_configured_item"}
    assert events[0].item_id == 124105


def test_fetch_and_store_enriches_history_metrics_with_risk_fields(tmp_path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 124105, "market": "commodity"}]})

    for price in (100, 120, 90, 150):
        fetch_and_store(
            config,
            FakeClient(
                commodity_payload={
                    "auctions": [{"id": price, "item": {"id": 124105}, "quantity": 10, "unit_price": price}]
                }
            ),
            repository,
            raw_snapshot_dir=tmp_path / "raw",
        )  # type: ignore[arg-type]

    with Session(engine) as session:
        latest = session.scalars(select(ItemHistoryMetricRecord).order_by(ItemHistoryMetricRecord.fetch_run_id.desc())).first()

    assert latest is not None
    assert latest.price_change_1h_bps == 5000
    assert latest.percentile_rank_bps == 10000
    assert latest.historical_volatility_bps is not None
    assert latest.market_depth_score is not None
    assert latest.liquidity_score is not None


def test_fetch_and_store_marks_failed_runs() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 124105, "market": "commodity"}]})

    class FailingClient(FakeClient):
        def fetch_commodity_auctions(self) -> dict[str, Any]:
            raise RuntimeError("api unavailable")

    with pytest.raises(RuntimeError, match="api unavailable"):
        fetch_and_store(config, FailingClient(), repository)  # type: ignore[arg-type]

    with Session(engine) as session:
        runs = session.scalars(select(FetchRun)).all()

    assert len(runs) == 1
    assert runs[0].status == "failed"


def test_fetch_and_store_tracks_new_auctions_below_prior_buy_price(tmp_path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 124105, "name": "Starlight Rose", "market": "commodity"}]}
    )

    for quantity in (10, 5, 5):
        fetch_and_store(
            config,
            FakeClient(
                commodity_payload={
                    "auctions": [
                        {"id": 1, "item": {"id": 124105}, "quantity": quantity, "unit_price": 10000}
                    ]
                }
            ),
            repository,
        )  # type: ignore[arg-type]

    fetch_and_store(
        config,
        FakeClient(
            commodity_payload={
                "auctions": [
                    {"id": 4, "item": {"id": 124105}, "quantity": 3, "unit_price": 7900},
                    {"id": 5, "item": {"id": 124105}, "quantity": 2, "unit_price": 8000},
                    {"id": 6, "item": {"id": 124105}, "quantity": 4, "unit_price": 9000},
                ]
            }
        ),
        repository,
    )  # type: ignore[arg-type]

    with Session(engine) as session:
        opportunities = session.scalars(select(BuyOpportunityObservationRecord)).all()

    assert len(opportunities) == 1
    assert opportunities[0].auction_id == 4
    assert opportunities[0].unit_price == 7900
    assert opportunities[0].buy_target_unit_price == 8000
    assert opportunities[0].sell_target_unit_price == 10000
    assert opportunities[0].available_quantity_at_or_below_buy_target == 5
    assert opportunities[0].potential_profit == 6300


def test_fetch_and_store_ignores_buy_opportunities_below_shifted_quantity(tmp_path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {"items": [{"id": 124105, "name": "Starlight Rose", "market": "commodity"}]}
    )

    for quantity in (10, 5, 5):
        fetch_and_store(
            config,
            FakeClient(
                commodity_payload={
                    "auctions": [
                        {"id": 1, "item": {"id": 124105}, "quantity": quantity, "unit_price": 10000}
                    ]
                }
            ),
            repository,
        )  # type: ignore[arg-type]

    fetch_and_store(
        config,
        FakeClient(
            commodity_payload={
                "auctions": [
                    {"id": 4, "item": {"id": 124105}, "quantity": 4, "unit_price": 7900},
                    {"id": 5, "item": {"id": 124105}, "quantity": 10, "unit_price": 10000},
                ]
            }
        ),
        repository,
    )  # type: ignore[arg-type]

    with Session(engine) as session:
        opportunities = session.scalars(select(BuyOpportunityObservationRecord)).all()

    assert opportunities == []


def test_fetch_and_store_tracks_profitable_craft_opportunities(tmp_path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate(
        {
            "items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}],
            "recipes": [
                {
                    "id": "refine-bismuth",
                    "name": "Refine Bismuth",
                    "output": {"item_id": 210931, "name": "Bismuth", "market": "commodity", "quantity": 1},
                    "ingredients": [{"item_id": 210930, "name": "Bismuth", "market": "commodity", "quantity": 5}],
                }
            ],
        }
    )

    for quantity in (10, 5, 5):
        fetch_and_store(
            config,
            FakeClient(
                commodity_payload={
                    "auctions": [
                        {"id": 1, "item": {"id": 210931}, "quantity": quantity, "unit_price": 1000},
                        {"id": 2, "item": {"id": 210930}, "quantity": 5, "unit_price": 100},
                    ]
                }
            ),
            repository,
        )  # type: ignore[arg-type]

    client = FakeClient(
        commodity_payload={
            "auctions": [
                {"id": 3, "item": {"id": 210931}, "quantity": 1, "unit_price": 900},
                {"id": 4, "item": {"id": 210930}, "quantity": 5, "unit_price": 100},
            ]
        }
    )
    latest_result = fetch_and_store(config, client, repository)  # type: ignore[arg-type]

    with Session(engine) as session:
        opportunities = session.scalars(
            select(CraftOpportunityObservationRecord).where(
                CraftOpportunityObservationRecord.fetch_run_id == latest_result.fetch_run_id
            )
        ).all()

    assert client.fetched_item_ids == []
    assert len(opportunities) == 1
    assert opportunities[0].recipe_id == "refine-bismuth"
    assert opportunities[0].craft_cost_unit_price == 500
    assert opportunities[0].output_min_unit_price == 900
    assert opportunities[0].sell_target_unit_price == 1000
    assert opportunities[0].expected_profit == 500
