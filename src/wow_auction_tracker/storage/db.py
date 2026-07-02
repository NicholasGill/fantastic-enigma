from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import (
    Boolean,
    DateTime,
    delete,
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
from wow_auction_tracker.features.opportunities import BuyOpportunityObservation
from wow_auction_tracker.features.player import AddonImportResult, PlayerAuctionOutcome, PlayerAuctionPost
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
    expected_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    listings: Mapped[list[AuctionListingRecord]] = relationship(back_populates="fetch_run")
    summaries: Mapped[list[ItemSummaryRecord]] = relationship(back_populates="fetch_run")
    history_metrics: Mapped[list[ItemHistoryMetricRecord]] = relationship(back_populates="fetch_run")
    listing_observations: Mapped[list[ListingObservationRecord]] = relationship(back_populates="fetch_run")
    sell_through_metrics: Mapped[list[SellThroughMetricRecord]] = relationship(back_populates="fetch_run")
    buy_opportunity_observations: Mapped[list[BuyOpportunityObservationRecord]] = relationship(back_populates="fetch_run")


class AddonImportRecord(Base):
    __tablename__ = "addon_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    addon_version: Mapped[int | None] = mapped_column(Integer)
    owned_snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mail_event_count: Mapped[int] = mapped_column(Integer, nullable=False)

    posts: Mapped[list[PlayerAuctionPostRecord]] = relationship(back_populates="import_record")
    outcomes: Mapped[list[PlayerAuctionOutcomeRecord]] = relationship(back_populates="import_record")


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
    inferred_outcome: Mapped[str | None] = mapped_column(String(32), index=True)
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
    probable_sold_listing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    probable_sold_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    probable_sold_value: Mapped[int | None] = mapped_column(Integer)
    probable_sold_average_unit_price: Mapped[int | None] = mapped_column(Integer)
    observed_listing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_through_ratio_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)

    fetch_run: Mapped[FetchRun] = relationship(back_populates="sell_through_metrics")


class BuyOpportunityObservationRecord(Base):
    __tablename__ = "buy_opportunity_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_run_id: Mapped[int] = mapped_column(ForeignKey("fetch_runs.id"), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    auction_id: Mapped[int | None] = mapped_column(Integer, index=True)
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_target_unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_target_unit_price: Mapped[int | None] = mapped_column(Integer)
    potential_profit: Mapped[int | None] = mapped_column(Integer)
    available_quantity_at_or_below_buy_target: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation_score: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    listing_status: Mapped[str] = mapped_column(String(32), nullable=False)

    fetch_run: Mapped[FetchRun] = relationship(back_populates="buy_opportunity_observations")


class PlayerAuctionPostRecord(Base):
    __tablename__ = "player_auction_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    addon_import_id: Mapped[int] = mapped_column(ForeignKey("addon_imports.id"), nullable=False, index=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(128), index=True)
    reason: Mapped[str | None] = mapped_column(String(64))
    character: Mapped[str | None] = mapped_column(String(128), index=True)
    realm: Mapped[str | None] = mapped_column(String(128), index=True)
    auction_id: Mapped[int | None] = mapped_column(Integer, index=True)
    item_id: Mapped[int | None] = mapped_column(Integer, index=True)
    quantity: Mapped[int | None] = mapped_column(Integer)
    unit_price: Mapped[int | None] = mapped_column(Integer)
    buyout: Mapped[int | None] = mapped_column(Integer)
    bid_amount: Mapped[int | None] = mapped_column(Integer)
    time_left_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(64))
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)

    import_record: Mapped[AddonImportRecord] = relationship(back_populates="posts")


class PlayerAuctionOutcomeRecord(Base):
    __tablename__ = "player_auction_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    addon_import_id: Mapped[int] = mapped_column(ForeignKey("addon_imports.id"), nullable=False, index=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    character: Mapped[str | None] = mapped_column(String(128), index=True)
    realm: Mapped[str | None] = mapped_column(String(128), index=True)
    mail_index: Mapped[int | None] = mapped_column(Integer)
    item_id: Mapped[int | None] = mapped_column(Integer, index=True)
    item_name: Mapped[str | None] = mapped_column(String(255))
    item_count: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    money: Mapped[int | None] = mapped_column(Integer)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)

    import_record: Mapped[AddonImportRecord] = relationship(back_populates="outcomes")


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

    def start_fetch_run(self, config: TrackerConfig, *, expected_interval_seconds: int | None = None) -> int:
        with Session(self.engine) as session:
            running_id = session.scalar(select(FetchRun.id).where(FetchRun.status == "running").limit(1))
            if running_id is not None:
                raise RuntimeError(f"fetch run {running_id} is already running")

            self._upsert_tracked_items(session, config.items)
            run = FetchRun(
                started_at=datetime.now(UTC),
                region=config.region,
                locale=config.locale,
                connected_realm_id=config.connected_realm_id,
                expected_interval_seconds=expected_interval_seconds,
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
        buy_opportunity_observations: Iterable[BuyOpportunityObservation] = (),
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
                        inferred_outcome=observation.inferred_outcome,
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
                        probable_sold_listing_count=metric.probable_sold_listing_count,
                        probable_sold_quantity=metric.probable_sold_quantity,
                        probable_sold_value=metric.probable_sold_value,
                        probable_sold_average_unit_price=metric.probable_sold_average_unit_price,
                        observed_listing_count=metric.observed_listing_count,
                        observed_quantity=metric.observed_quantity,
                        sell_through_ratio_bps=round(metric.sell_through_ratio * 10000),
                        confidence=metric.confidence,
                    )
                )

            for opportunity in buy_opportunity_observations:
                session.add(
                    BuyOpportunityObservationRecord(
                        fetch_run_id=fetch_run_id,
                        observed_at=run.started_at,
                        item_id=opportunity.item_id,
                        market=opportunity.market,
                        auction_id=opportunity.auction_id,
                        unit_price=opportunity.unit_price,
                        quantity=opportunity.quantity,
                        buy_target_unit_price=opportunity.buy_target_unit_price,
                        sell_target_unit_price=opportunity.sell_target_unit_price,
                        potential_profit=opportunity.potential_profit,
                        available_quantity_at_or_below_buy_target=(
                            opportunity.available_quantity_at_or_below_buy_target
                        ),
                        recommendation_score=opportunity.recommendation_score,
                        recommendation_confidence=opportunity.recommendation_confidence,
                        listing_status=opportunity.listing_status,
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

    def fetch_run_started_at(self, fetch_run_id: int) -> datetime | None:
        with Session(self.engine) as session:
            return session.scalar(select(FetchRun.started_at).where(FetchRun.id == fetch_run_id))

    def successful_fetch_run_ids(self) -> list[int]:
        with Session(self.engine) as session:
            return list(
                session.scalars(
                    select(FetchRun.id)
                    .where(FetchRun.status == "success")
                    .order_by(FetchRun.id)
                ).all()
            )

    def list_auction_listings(self, fetch_run_id: int) -> list[AuctionListing]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(AuctionListingRecord).where(AuctionListingRecord.fetch_run_id == fetch_run_id)
            ).all()
            return [
                AuctionListing(
                    auction_id=row.auction_id,
                    item_id=row.item_id,
                    market=Market(row.market),
                    quantity=row.quantity,
                    unit_price=row.unit_price,
                    buyout=row.buyout,
                    bid=row.bid,
                    time_left=row.time_left,
                    raw=json.loads(row.raw_json),
                )
                for row in rows
            ]

    def replace_inference(
        self,
        fetch_run_id: int,
        listing_observations: Iterable[ListingObservation],
        sell_through_metrics: Iterable[SellThroughMetric],
    ) -> None:
        with self.engine.begin() as connection:
            run_exists = connection.scalar(select(FetchRun.id).where(FetchRun.id == fetch_run_id))
            if run_exists is None:
                raise ValueError(f"fetch run {fetch_run_id} does not exist")

            connection.execute(
                delete(ListingObservationRecord).where(ListingObservationRecord.fetch_run_id == fetch_run_id)
            )
            connection.execute(
                delete(SellThroughMetricRecord).where(SellThroughMetricRecord.fetch_run_id == fetch_run_id)
            )

            observation_rows = [
                {
                        "fetch_run_id": fetch_run_id,
                        "observation_key": observation.observation_key,
                        "auction_id": observation.auction_id,
                        "item_id": observation.item_id,
                        "market": observation.market,
                        "status": observation.status,
                        "inferred_outcome": observation.inferred_outcome,
                        "quantity": observation.quantity,
                        "previous_quantity": observation.previous_quantity,
                        "unit_price": observation.unit_price,
                        "previous_unit_price": observation.previous_unit_price,
                        "buyout": observation.buyout,
                        "previous_buyout": observation.previous_buyout,
                        "bid": observation.bid,
                        "previous_bid": observation.previous_bid,
                        "time_left": observation.time_left,
                        "previous_time_left": observation.previous_time_left,
                }
                for observation in listing_observations
            ]
            if observation_rows:
                connection.execute(ListingObservationRecord.__table__.insert(), observation_rows)

            metric_rows = [
                {
                        "fetch_run_id": fetch_run_id,
                        "item_id": metric.item_id,
                        "market": metric.market,
                        "disappeared_listing_count": metric.disappeared_listing_count,
                        "disappeared_quantity": metric.disappeared_quantity,
                        "disappeared_value": metric.disappeared_value,
                        "probable_sold_listing_count": metric.probable_sold_listing_count,
                        "probable_sold_quantity": metric.probable_sold_quantity,
                        "probable_sold_value": metric.probable_sold_value,
                        "probable_sold_average_unit_price": metric.probable_sold_average_unit_price,
                        "observed_listing_count": metric.observed_listing_count,
                        "observed_quantity": metric.observed_quantity,
                        "sell_through_ratio_bps": round(metric.sell_through_ratio * 10000),
                        "confidence": metric.confidence,
                }
                for metric in sell_through_metrics
            ]
            if metric_rows:
                connection.execute(SellThroughMetricRecord.__table__.insert(), metric_rows)

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

    def import_addon_data(self, result: AddonImportResult) -> int:
        with Session(self.engine) as session:
            import_record = AddonImportRecord(
                imported_at=datetime.now(UTC),
                source_path=str(result.source_path),
                addon_version=result.addon_version,
                owned_snapshot_count=len(result.posts),
                mail_event_count=len(result.outcomes),
            )
            session.add(import_record)
            session.flush()

            for post in result.posts:
                session.add(
                    PlayerAuctionPostRecord(
                        addon_import_id=import_record.id,
                        observed_at=post.observed_at,
                        snapshot_id=post.snapshot_id,
                        reason=post.reason,
                        character=post.character,
                        realm=post.realm,
                        auction_id=post.auction_id,
                        item_id=post.item_id,
                        quantity=post.quantity,
                        unit_price=post.unit_price,
                        buyout=post.buyout,
                        bid_amount=post.bid_amount,
                        time_left_seconds=post.time_left_seconds,
                        status=post.status,
                        raw_json=json.dumps(post.raw, sort_keys=True),
                    )
                )

            for outcome in result.outcomes:
                session.add(
                    PlayerAuctionOutcomeRecord(
                        addon_import_id=import_record.id,
                        observed_at=outcome.observed_at,
                        character=outcome.character,
                        realm=outcome.realm,
                        mail_index=outcome.mail_index,
                        item_id=outcome.item_id,
                        item_name=outcome.item_name,
                        item_count=outcome.item_count,
                        outcome=outcome.outcome,
                        money=outcome.money,
                        raw_json=json.dumps(outcome.raw, sort_keys=True),
                    )
                )

            session.commit()
            return import_record.id

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
            "fetch_runs",
            {
                "expected_interval_seconds": "integer",
            },
        )
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
        _ensure_sqlite_columns(
            connection,
            "listing_observations",
            {
                "inferred_outcome": "varchar(32)",
            },
        )
        connection.exec_driver_sql(
            "create index if not exists ix_listing_observations_inferred_outcome "
            "on listing_observations (inferred_outcome)"
        )
        connection.exec_driver_sql(
            """
            create table if not exists sell_through_metrics (
                id integer primary key,
                fetch_run_id integer not null,
                observed_at datetime not null,
                item_id integer not null,
                market varchar(32) not null,
                disappeared_listing_count integer not null,
                disappeared_quantity integer not null,
                disappeared_value integer,
                probable_sold_listing_count integer not null default 0,
                probable_sold_quantity integer not null default 0,
                probable_sold_value integer,
                probable_sold_average_unit_price integer,
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
        _ensure_sqlite_columns(
            connection,
            "sell_through_metrics",
            {
                "probable_sold_listing_count": "integer not null default 0",
                "probable_sold_quantity": "integer not null default 0",
                "probable_sold_value": "integer",
                "probable_sold_average_unit_price": "integer",
            },
        )
        connection.exec_driver_sql(
            """
            create table if not exists buy_opportunity_observations (
                id integer primary key,
                fetch_run_id integer not null,
                observed_at datetime not null,
                item_id integer not null,
                market varchar(32) not null,
                auction_id integer,
                unit_price integer not null,
                quantity integer not null,
                buy_target_unit_price integer not null,
                sell_target_unit_price integer,
                potential_profit integer,
                available_quantity_at_or_below_buy_target integer not null,
                recommendation_score integer not null,
                recommendation_confidence integer not null,
                listing_status varchar(32) not null,
                foreign key(fetch_run_id) references fetch_runs (id)
            )
            """
        )
        for column in ("fetch_run_id", "item_id", "auction_id"):
            connection.exec_driver_sql(
                f"create index if not exists ix_buy_opportunity_observations_{column} "
                f"on buy_opportunity_observations ({column})"
            )
        _ensure_sqlite_columns(
            connection,
            "buy_opportunity_observations",
            {
                "observed_at": "datetime",
            },
        )
        connection.exec_driver_sql(
            "create index if not exists ix_buy_opportunity_observations_observed_at "
            "on buy_opportunity_observations (observed_at)"
        )
        connection.exec_driver_sql(
            """
            create table if not exists addon_imports (
                id integer primary key,
                imported_at datetime not null,
                source_path text not null,
                addon_version integer,
                owned_snapshot_count integer not null,
                mail_event_count integer not null
            )
            """
        )
        connection.exec_driver_sql(
            """
            create table if not exists player_auction_posts (
                id integer primary key,
                addon_import_id integer not null,
                observed_at datetime,
                snapshot_id varchar(128),
                reason varchar(64),
                character varchar(128),
                realm varchar(128),
                auction_id integer,
                item_id integer,
                quantity integer,
                unit_price integer,
                buyout integer,
                bid_amount integer,
                time_left_seconds integer,
                status varchar(64),
                raw_json text not null,
                foreign key(addon_import_id) references addon_imports (id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            create table if not exists player_auction_outcomes (
                id integer primary key,
                addon_import_id integer not null,
                observed_at datetime,
                character varchar(128),
                realm varchar(128),
                mail_index integer,
                item_id integer,
                item_name varchar(255),
                item_count integer,
                outcome varchar(32) not null,
                money integer,
                raw_json text not null,
                foreign key(addon_import_id) references addon_imports (id)
            )
            """
        )
        for table, column in (
            ("player_auction_posts", "addon_import_id"),
            ("player_auction_posts", "observed_at"),
            ("player_auction_posts", "snapshot_id"),
            ("player_auction_posts", "character"),
            ("player_auction_posts", "realm"),
            ("player_auction_posts", "auction_id"),
            ("player_auction_posts", "item_id"),
            ("player_auction_outcomes", "addon_import_id"),
            ("player_auction_outcomes", "observed_at"),
            ("player_auction_outcomes", "character"),
            ("player_auction_outcomes", "realm"),
            ("player_auction_outcomes", "item_id"),
            ("player_auction_outcomes", "outcome"),
        ):
            connection.exec_driver_sql(f"create index if not exists ix_{table}_{column} on {table} ({column})")


def _ensure_sqlite_columns(connection: Connection, table_name: str, columns: dict[str, str]) -> None:
    existing_columns = {
        str(row[1])
        for row in connection.exec_driver_sql(f"pragma table_info({table_name})")
    }
    for column_name, column_type in columns.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(f"alter table {table_name} add column {column_name} {column_type}")
