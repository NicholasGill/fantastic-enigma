from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from wow_auction_tracker.auction import AuctionListing, ItemSummary
from wow_auction_tracker.config import Market, TrackerConfig, TrackedItem


class Base(DeclarativeBase):
    metadata = MetaData()


class FetchRun(Base):
    __tablename__ = "fetch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    connected_realm_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    listings: Mapped[list[AuctionListingRecord]] = relationship(back_populates="fetch_run")
    summaries: Mapped[list[ItemSummaryRecord]] = relationship(back_populates="fetch_run")


class TrackedItemRecord(Base):
    __tablename__ = "tracked_items"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuctionListingRecord(Base):
    __tablename__ = "auction_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_run_id: Mapped[int] = mapped_column(ForeignKey("fetch_runs.id"), nullable=False, index=True)
    auction_id: Mapped[int | None] = mapped_column(Integer, index=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[int | None] = mapped_column(Integer)
    buyout: Mapped[int | None] = mapped_column(Integer)
    bid: Mapped[int | None] = mapped_column(Integer)
    time_left: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)

    fetch_run: Mapped[FetchRun] = relationship(back_populates="listings")


class ItemSummaryRecord(Base):
    __tablename__ = "item_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_run_id: Mapped[int] = mapped_column(ForeignKey("fetch_runs.id"), nullable=False, index=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    listing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    min_unit_price: Mapped[int | None] = mapped_column(Integer)
    median_unit_price: Mapped[int | None] = mapped_column(Integer)

    fetch_run: Mapped[FetchRun] = relationship(back_populates="summaries")


def create_db_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)

    return create_engine(database_url, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


class AuctionRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def start_fetch_run(self, config: TrackerConfig) -> int:
        with Session(self.engine) as session:
            self._upsert_tracked_items(session, config.items)
            run = FetchRun(
                started_at=datetime.now(UTC),
                region=config.region,
                locale=config.locale,
                connected_realm_id=config.connected_realm_id,
                status="running",
            )
            session.add(run)
            session.commit()
            return run.id

    def complete_fetch_run(
        self,
        fetch_run_id: int,
        listings: Iterable[AuctionListing],
        summaries: Iterable[ItemSummary],
    ) -> None:
        with Session(self.engine) as session:
            run = session.get(FetchRun, fetch_run_id)
            if run is None:
                raise ValueError(f"fetch run {fetch_run_id} does not exist")

            for listing in listings:
                session.add(
                    AuctionListingRecord(
                        fetch_run_id=fetch_run_id,
                        auction_id=listing.auction_id,
                        item_id=listing.item_id,
                        market=listing.market.value,
                        quantity=listing.quantity,
                        unit_price=listing.unit_price,
                        buyout=listing.buyout,
                        bid=listing.bid,
                        time_left=listing.time_left,
                        raw_json=json.dumps(listing.raw, sort_keys=True),
                    )
                )

            for summary in summaries:
                session.add(
                    ItemSummaryRecord(
                        fetch_run_id=fetch_run_id,
                        item_id=summary.item_id,
                        market=summary.market.value,
                        listing_count=summary.listing_count,
                        total_quantity=summary.total_quantity,
                        min_unit_price=summary.min_unit_price,
                        median_unit_price=summary.median_unit_price,
                    )
                )

            run.finished_at = datetime.now(UTC)
            run.status = "success"
            session.commit()

    def fail_fetch_run(self, fetch_run_id: int, error: str) -> None:
        with Session(self.engine) as session:
            run = session.get(FetchRun, fetch_run_id)
            if run is None:
                raise ValueError(f"fetch run {fetch_run_id} does not exist")

            run.finished_at = datetime.now(UTC)
            run.status = "failed"
            run.error = error
            session.commit()

    def list_summaries(self, fetch_run_id: int) -> list[ItemSummary]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(ItemSummaryRecord).where(ItemSummaryRecord.fetch_run_id == fetch_run_id)
            ).all()
            return [
                ItemSummary(
                    item_id=row.item_id,
                    market=Market(row.market),
                    listing_count=row.listing_count,
                    total_quantity=row.total_quantity,
                    min_unit_price=row.min_unit_price,
                    median_unit_price=row.median_unit_price,
                )
                for row in rows
            ]

    @staticmethod
    def _upsert_tracked_items(session: Session, items: Iterable[TrackedItem]) -> None:
        now = datetime.now(UTC)
        for item in items:
            record = session.get(TrackedItemRecord, item.id)
            if record is None:
                record = TrackedItemRecord(item_id=item.id, updated_at=now)
                session.add(record)

            record.name = item.name
            record.market = item.market.value
            record.updated_at = now
