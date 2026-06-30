from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from wow_auction_tracker.auction import AuctionListing, ItemHistoryMetric, ItemSummary
from wow_auction_tracker.config import Market, TrackerConfig, TrackedItem
from wow_auction_tracker.features.lifecycle import ListingObservation, ListingSnapshot, listing_key_from_parts
from wow_auction_tracker.features.metadata import ItemMetadata
from wow_auction_tracker.features.sellthrough import SellThroughMetric


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
    history_metrics: Mapped[list[ItemHistoryMetricRecord]] = relationship(back_populates="fetch_run")
    listing_observations: Mapped[list[ListingObservationRecord]] = relationship(back_populates="fetch_run")
    sell_through_metrics: Mapped[list[SellThroughMetricRecord]] = relationship(back_populates="fetch_run")


class TrackedItemRecord(Base):
    __tablename__ = "tracked_items"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemMetadataRecord(Base):
    __tablename__ = "item_metadata"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quality: Mapped[str | None] = mapped_column(String(64))
    item_class: Mapped[str | None] = mapped_column(String(128))
    item_subclass: Mapped[str | None] = mapped_column(String(128))
    inventory_type: Mapped[str | None] = mapped_column(String(64))
    item_level: Mapped[int | None] = mapped_column(Integer)
    required_level: Mapped[int | None] = mapped_column(Integer)
    purchase_price: Mapped[int | None] = mapped_column(Integer)
    sell_price: Mapped[int | None] = mapped_column(Integer)
    max_count: Mapped[int | None] = mapped_column(Integer)
    is_equippable: Mapped[bool | None] = mapped_column(Boolean)
    is_stackable: Mapped[bool | None] = mapped_column(Boolean)
    icon_url: Mapped[str | None] = mapped_column(Text)
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
    first_quartile_unit_price: Mapped[int | None] = mapped_column(Integer)
    third_quartile_unit_price: Mapped[int | None] = mapped_column(Integer)

    fetch_run: Mapped[FetchRun] = relationship(back_populates="summaries")


class ItemHistoryMetricRecord(Base):
    __tablename__ = "item_history_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_run_id: Mapped[int] = mapped_column(ForeignKey("fetch_runs.id"), nullable=False, index=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    listing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    min_unit_price: Mapped[int | None] = mapped_column(Integer)
    median_unit_price: Mapped[int | None] = mapped_column(Integer)
    first_quartile_unit_price: Mapped[int | None] = mapped_column(Integer)
    third_quartile_unit_price: Mapped[int | None] = mapped_column(Integer)
    weighted_average_unit_price: Mapped[int | None] = mapped_column(Integer)
    lowest_price_quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    fetch_run: Mapped[FetchRun] = relationship(back_populates="history_metrics")


class ListingObservationRecord(Base):
    __tablename__ = "listing_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_run_id: Mapped[int] = mapped_column(ForeignKey("fetch_runs.id"), nullable=False, index=True)
    observation_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    auction_id: Mapped[int | None] = mapped_column(Integer, index=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quantity: Mapped[int | None] = mapped_column(Integer)
    previous_quantity: Mapped[int | None] = mapped_column(Integer)
    unit_price: Mapped[int | None] = mapped_column(Integer)
    previous_unit_price: Mapped[int | None] = mapped_column(Integer)
    buyout: Mapped[int | None] = mapped_column(Integer)
    previous_buyout: Mapped[int | None] = mapped_column(Integer)
    bid: Mapped[int | None] = mapped_column(Integer)
    previous_bid: Mapped[int | None] = mapped_column(Integer)
    time_left: Mapped[str | None] = mapped_column(String(32))
    previous_time_left: Mapped[str | None] = mapped_column(String(32))

    fetch_run: Mapped[FetchRun] = relationship(back_populates="listing_observations")


class SellThroughMetricRecord(Base):
    __tablename__ = "sell_through_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_run_id: Mapped[int] = mapped_column(ForeignKey("fetch_runs.id"), nullable=False, index=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    disappeared_listing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    disappeared_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    disappeared_value: Mapped[int | None] = mapped_column(Integer)
    observed_listing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_through_ratio_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)

    fetch_run: Mapped[FetchRun] = relationship(back_populates="sell_through_metrics")


def create_db_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)

    return create_engine(database_url, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _ensure_sqlite_compatible_schema(engine)


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
        history_metrics: Iterable[ItemHistoryMetric] = (),
        listing_observations: Iterable[ListingObservation] = (),
        sell_through_metrics: Iterable[SellThroughMetric] = (),
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
                        first_quartile_unit_price=summary.first_quartile_unit_price,
                        third_quartile_unit_price=summary.third_quartile_unit_price,
                    )
                )

            for metric in history_metrics:
                session.add(
                    ItemHistoryMetricRecord(
                        fetch_run_id=fetch_run_id,
                        item_id=metric.item_id,
                        market=metric.market.value,
                        listing_count=metric.listing_count,
                        total_quantity=metric.total_quantity,
                        min_unit_price=metric.min_unit_price,
                        median_unit_price=metric.median_unit_price,
                        first_quartile_unit_price=metric.first_quartile_unit_price,
                        third_quartile_unit_price=metric.third_quartile_unit_price,
                        weighted_average_unit_price=metric.weighted_average_unit_price,
                        lowest_price_quantity=metric.lowest_price_quantity,
                    )
                )

            for observation in listing_observations:
                session.add(
                    ListingObservationRecord(
                        fetch_run_id=fetch_run_id,
                        observation_key=observation.observation_key,
                        auction_id=observation.auction_id,
                        item_id=observation.item_id,
                        market=observation.market,
                        status=observation.status,
                        quantity=observation.quantity,
                        previous_quantity=observation.previous_quantity,
                        unit_price=observation.unit_price,
                        previous_unit_price=observation.previous_unit_price,
                        buyout=observation.buyout,
                        previous_buyout=observation.previous_buyout,
                        bid=observation.bid,
                        previous_bid=observation.previous_bid,
                        time_left=observation.time_left,
                        previous_time_left=observation.previous_time_left,
                    )
                )

            for metric in sell_through_metrics:
                session.add(
                    SellThroughMetricRecord(
                        fetch_run_id=fetch_run_id,
                        item_id=metric.item_id,
                        market=metric.market,
                        disappeared_listing_count=metric.disappeared_listing_count,
                        disappeared_quantity=metric.disappeared_quantity,
                        disappeared_value=metric.disappeared_value,
                        observed_listing_count=metric.observed_listing_count,
                        observed_quantity=metric.observed_quantity,
                        sell_through_ratio_bps=round(metric.sell_through_ratio * 10000),
                        confidence=metric.confidence,
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
                    first_quartile_unit_price=row.first_quartile_unit_price,
                    third_quartile_unit_price=row.third_quartile_unit_price,
                )
                for row in rows
            ]

    def previous_successful_fetch_run_id(self, fetch_run_id: int) -> int | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(FetchRun.id)
                .where(FetchRun.id < fetch_run_id, FetchRun.status == "success")
                .order_by(FetchRun.id.desc())
                .limit(1)
            )

    def list_listing_snapshots(self, fetch_run_id: int) -> list[ListingSnapshot]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(AuctionListingRecord).where(AuctionListingRecord.fetch_run_id == fetch_run_id)
            ).all()
            return [
                ListingSnapshot(
                    observation_key=listing_key_from_parts(
                        auction_id=row.auction_id,
                        item_id=row.item_id,
                        market=row.market,
                        quantity=row.quantity,
                        unit_price=row.unit_price,
                        buyout=row.buyout,
                        bid=row.bid,
                    ),
                    auction_id=row.auction_id,
                    item_id=row.item_id,
                    market=row.market,
                    quantity=row.quantity,
                    unit_price=row.unit_price,
                    buyout=row.buyout,
                    bid=row.bid,
                    time_left=row.time_left,
                )
                for row in rows
            ]

    def missing_metadata_item_ids(self, item_ids: Iterable[int]) -> set[int]:
        item_id_set = set(item_ids)
        if not item_id_set:
            return set()

        with Session(self.engine) as session:
            existing_ids = set(
                session.scalars(
                    select(ItemMetadataRecord.item_id).where(ItemMetadataRecord.item_id.in_(item_id_set))
                ).all()
            )
        return item_id_set - existing_ids

    def upsert_item_metadata(self, metadata_items: Iterable[ItemMetadata]) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as session:
            for metadata in metadata_items:
                record = session.get(ItemMetadataRecord, metadata.item_id)
                if record is None:
                    record = ItemMetadataRecord(item_id=metadata.item_id, updated_at=now)
                    session.add(record)

                record.name = metadata.name
                record.quality = metadata.quality
                record.item_class = metadata.item_class
                record.item_subclass = metadata.item_subclass
                record.inventory_type = metadata.inventory_type
                record.item_level = metadata.item_level
                record.required_level = metadata.required_level
                record.purchase_price = metadata.purchase_price
                record.sell_price = metadata.sell_price
                record.max_count = metadata.max_count
                record.is_equippable = metadata.is_equippable
                record.is_stackable = metadata.is_stackable
                record.icon_url = metadata.icon_url
                record.updated_at = now
            session.commit()

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


def _ensure_sqlite_compatible_schema(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        _ensure_sqlite_columns(
            connection,
            "item_summaries",
            {
                "first_quartile_unit_price": "integer",
                "third_quartile_unit_price": "integer",
            },
        )
        _ensure_sqlite_columns(
            connection,
            "item_history_metrics",
            {
                "first_quartile_unit_price": "integer",
                "third_quartile_unit_price": "integer",
            },
        )
        connection.exec_driver_sql(
            """
            create table if not exists sell_through_metrics (
                id integer primary key,
                fetch_run_id integer not null,
                item_id integer not null,
                market varchar(32) not null,
                disappeared_listing_count integer not null,
                disappeared_quantity integer not null,
                disappeared_value integer,
                observed_listing_count integer not null,
                observed_quantity integer not null,
                sell_through_ratio_bps integer not null,
                confidence integer not null,
                foreign key(fetch_run_id) references fetch_runs (id)
            )
            """
        )
        connection.exec_driver_sql(
            "create index if not exists ix_sell_through_metrics_fetch_run_id on sell_through_metrics (fetch_run_id)"
        )
        connection.exec_driver_sql(
            "create index if not exists ix_sell_through_metrics_item_id on sell_through_metrics (item_id)"
        )


def _ensure_sqlite_columns(connection: Connection, table_name: str, columns: dict[str, str]) -> None:
    existing_columns = {
        str(row[1])
        for row in connection.exec_driver_sql(f"pragma table_info({table_name})")
    }
    for column_name, column_type in columns.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(f"alter table {table_name} add column {column_name} {column_type}")
