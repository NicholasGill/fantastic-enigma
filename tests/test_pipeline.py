from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from wow_auction_tracker.config import TrackerConfig
from wow_auction_tracker.db import AuctionRepository, FetchRun, create_db_engine, init_db
from wow_auction_tracker.pipeline import fetch_and_store


class FakeClient:
    def __init__(self, realm_payload: dict[str, Any] | None = None, commodity_payload: dict[str, Any] | None = None):
        self.realm_payload = realm_payload or {"auctions": []}
        self.commodity_payload = commodity_payload or {"auctions": []}

    def fetch_connected_realm_auctions(self, connected_realm_id: int) -> dict[str, Any]:
        assert connected_realm_id == 3678
        return self.realm_payload

    def fetch_commodity_auctions(self) -> dict[str, Any]:
        return self.commodity_payload


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
