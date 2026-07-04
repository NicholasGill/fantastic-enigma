from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    delete,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from wow_auction_tracker.auction import AuctionListing, ItemHistoryMetric, ItemSummary
from wow_auction_tracker.config import Market, TrackerConfig, TrackedItem
from wow_auction_tracker.features.crafting import CraftOpportunityObservation
from wow_auction_tracker.features.lifecycle import ListingObservation, ListingSnapshot, listing_key_from_parts
from wow_auction_tracker.features.metadata import ItemMetadata
from wow_auction_tracker.features.opportunities import BuyOpportunityObservation
from wow_auction_tracker.features.player import (
    AddonImportResult,
    PlayerAuctionOutcome,
    PlayerAuctionPost,
    PlayerAuctionPurchase,
    row_hash,
)
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
    craft_opportunity_observations: Mapped[list[CraftOpportunityObservationRecord]] = relationship(
        back_populates="fetch_run"
    )


class AddonImportRecord(Base):
    __tablename__ = "addon_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    addon_version: Mapped[int | None] = mapped_column(Integer)
    owned_snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mail_event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    purchase_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    malformed_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    posts: Mapped[list[PlayerAuctionPostRecord]] = relationship(back_populates="import_record")
    outcomes: Mapped[list[PlayerAuctionOutcomeRecord]] = relationship(back_populates="import_record")
    purchases: Mapped[list[PlayerAuctionPurchaseRecord]] = relationship(back_populates="import_record")


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
    age_seconds: Mapped[int | None] = mapped_column(Integer)
    undercut_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price_change: Mapped[int | None] = mapped_column(Integer)

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


class ItemDailyMetricRecord(Base):
    __tablename__ = "item_daily_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    low_unit_price: Mapped[int | None] = mapped_column(Integer)
    first_quartile_unit_price: Mapped[int | None] = mapped_column(Integer)
    median_unit_price: Mapped[int | None] = mapped_column(Integer)
    third_quartile_unit_price: Mapped[int | None] = mapped_column(Integer)
    weighted_average_unit_price: Mapped[int | None] = mapped_column(Integer)
    average_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    average_listing_count: Mapped[float] = mapped_column(Float, nullable=False)
    disappeared_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    probable_sold_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    demand_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    first_fetch_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_fetch_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemAnomalyRecord(Base):
    __tablename__ = "item_anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_run_id: Mapped[int] = mapped_column(ForeignKey("fetch_runs.id"), nullable=False, index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    anomaly_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_value: Mapped[int | None] = mapped_column(Integer)
    observed_value: Mapped[int | None] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


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


class CraftOpportunityObservationRecord(Base):
    __tablename__ = "craft_opportunity_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_run_id: Mapped[int] = mapped_column(ForeignKey("fetch_runs.id"), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    recipe_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    recipe_name: Mapped[str | None] = mapped_column(String(255))
    output_item_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    output_market: Mapped[str] = mapped_column(String(32), nullable=False)
    output_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    craft_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    craft_cost_unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    output_min_unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_target_unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    auction_deposit_unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    ah_savings: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_profit: Mapped[int] = mapped_column(Integer, nullable=False)
    max_craft_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False)

    fetch_run: Mapped[FetchRun] = relationship(back_populates="craft_opportunity_observations")


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
    row_hash: Mapped[str | None] = mapped_column(String(64), index=True)
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
    row_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)

    import_record: Mapped[AddonImportRecord] = relationship(back_populates="outcomes")


class PlayerAuctionPurchaseRecord(Base):
    __tablename__ = "player_auction_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    addon_import_id: Mapped[int] = mapped_column(ForeignKey("addon_imports.id"), nullable=False, index=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    character: Mapped[str | None] = mapped_column(String(128), index=True)
    realm: Mapped[str | None] = mapped_column(String(128), index=True)
    market: Mapped[str | None] = mapped_column(String(32))
    auction_id: Mapped[int | None] = mapped_column(Integer, index=True)
    item_id: Mapped[int | None] = mapped_column(Integer, index=True)
    quantity: Mapped[int | None] = mapped_column(Integer)
    unit_price: Mapped[int | None] = mapped_column(Integer)
    total_price: Mapped[int | None] = mapped_column(Integer)
    row_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)

    import_record: Mapped[AddonImportRecord] = relationship(back_populates="purchases")


class PlayerAuctionMatchRecord(Base):
    __tablename__ = "player_auction_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outcome_id: Mapped[int] = mapped_column(ForeignKey("player_auction_outcomes.id"), nullable=False, index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("player_auction_posts.id"), nullable=False, index=True)
    item_id: Mapped[int | None] = mapped_column(Integer, index=True)
    character: Mapped[str | None] = mapped_column(String(128), index=True)
    realm: Mapped[str | None] = mapped_column(String(128), index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    elapsed_seconds: Mapped[int | None] = mapped_column(Integer)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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

            self._upsert_tracked_items(session, config.all_tracked_items)
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
        craft_opportunity_observations: Iterable[CraftOpportunityObservation] = (),
    ) -> None:
        history_metric_list = list(history_metrics)
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

            for metric in history_metric_list:
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
                        age_seconds=observation.age_seconds,
                        undercut_count=observation.undercut_count,
                        unit_price_change=observation.unit_price_change,
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

            for opportunity in craft_opportunity_observations:
                session.add(
                    CraftOpportunityObservationRecord(
                        fetch_run_id=fetch_run_id,
                        observed_at=run.started_at,
                        recipe_id=opportunity.recipe_id,
                        recipe_name=opportunity.recipe_name,
                        output_item_id=opportunity.output_item_id,
                        output_market=opportunity.output_market,
                        output_quantity=opportunity.output_quantity,
                        craft_cost=opportunity.craft_cost,
                        craft_cost_unit_price=opportunity.craft_cost_unit_price,
                        output_min_unit_price=opportunity.output_min_unit_price,
                        sell_target_unit_price=opportunity.sell_target_unit_price,
                        auction_deposit_unit_price=opportunity.auction_deposit_unit_price,
                        ah_savings=opportunity.ah_savings,
                        expected_profit=opportunity.expected_profit,
                        max_craft_quantity=opportunity.max_craft_quantity,
                        confidence=opportunity.confidence,
                        reasons_json=json.dumps(opportunity.reasons),
                    )
                )

            run.finished_at = datetime.now(UTC)
            run.status = "success"
            _rebuild_daily_metrics_for_date(session, run.started_at.date())
            _record_item_anomalies(session, run, history_metric_list)
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

    def fetch_run_expected_interval_seconds(self, fetch_run_id: int) -> int | None:
        with Session(self.engine) as session:
            return session.scalar(select(FetchRun.expected_interval_seconds).where(FetchRun.id == fetch_run_id))

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
                        "age_seconds": observation.age_seconds,
                        "undercut_count": observation.undercut_count,
                        "unit_price_change": observation.unit_price_change,
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

            with Session(bind=connection) as session:
                run = session.get(FetchRun, fetch_run_id)
                if run is not None:
                    _rebuild_daily_metrics_for_date(session, run.started_at.date())

    def list_listing_snapshots(self, fetch_run_id: int) -> list[ListingSnapshot]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(AuctionListingRecord).where(AuctionListingRecord.fetch_run_id == fetch_run_id)
            ).all()
            observation_ages = {
                row.observation_key: row.age_seconds
                for row in session.scalars(
                    select(ListingObservationRecord).where(
                        ListingObservationRecord.fetch_run_id == fetch_run_id,
                        ListingObservationRecord.status.in_(("new", "active", "changed")),
                    )
                ).all()
            }
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
                    age_seconds=observation_ages.get(
                        listing_key_from_parts(
                            auction_id=row.auction_id,
                            item_id=row.item_id,
                            market=row.market,
                            quantity=row.quantity,
                            unit_price=row.unit_price,
                            buyout=row.buyout,
                            bid=row.bid,
                        )
                    ),
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

    def import_addon_data(self, result: AddonImportResult, *, replace_existing_source: bool = False) -> int:
        with Session(self.engine) as session:
            if replace_existing_source:
                existing_import_ids = list(
                    session.scalars(
                        select(AddonImportRecord.id).where(AddonImportRecord.source_path == str(result.source_path))
                    ).all()
                )
                if existing_import_ids:
                    session.execute(
                        delete(PlayerAuctionPostRecord).where(
                            PlayerAuctionPostRecord.addon_import_id.in_(existing_import_ids)
                        )
                    )
                    session.execute(
                        delete(PlayerAuctionOutcomeRecord).where(
                            PlayerAuctionOutcomeRecord.addon_import_id.in_(existing_import_ids)
                        )
                    )
                    session.execute(
                        delete(PlayerAuctionPurchaseRecord).where(
                            PlayerAuctionPurchaseRecord.addon_import_id.in_(existing_import_ids)
                        )
                    )
                    session.execute(delete(PlayerAuctionMatchRecord))
                    session.execute(
                        delete(AddonImportRecord).where(AddonImportRecord.id.in_(existing_import_ids))
                    )

            existing_hashes = {
                "post": set(
                    session.scalars(
                        select(PlayerAuctionPostRecord.row_hash).where(PlayerAuctionPostRecord.row_hash.is_not(None))
                    ).all()
                ),
                "outcome": set(
                    session.scalars(
                        select(PlayerAuctionOutcomeRecord.row_hash).where(
                            PlayerAuctionOutcomeRecord.row_hash.is_not(None)
                        )
                    ).all()
                ),
                "purchase": set(
                    session.scalars(
                        select(PlayerAuctionPurchaseRecord.row_hash).where(
                            PlayerAuctionPurchaseRecord.row_hash.is_not(None)
                        )
                    ).all()
                ),
            }

            import_record = AddonImportRecord(
                imported_at=datetime.now(UTC),
                source_path=str(result.source_path),
                addon_version=result.addon_version,
                owned_snapshot_count=len(result.posts),
                mail_event_count=len(result.outcomes),
                purchase_event_count=len(result.purchases),
                inserted_row_count=0,
                skipped_duplicate_count=0,
                malformed_row_count=result.malformed_row_count,
            )
            session.add(import_record)
            session.flush()

            inserted_row_count = 0
            skipped_duplicate_count = 0
            for post in result.posts:
                post_hash = _post_row_hash(post)
                if post_hash in existing_hashes["post"]:
                    skipped_duplicate_count += 1
                    continue
                existing_hashes["post"].add(post_hash)
                inserted_row_count += 1
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
                        row_hash=post_hash,
                        raw_json=json.dumps(post.raw, sort_keys=True),
                    )
                )

            for outcome in result.outcomes:
                outcome_hash = _outcome_row_hash(outcome)
                if outcome_hash in existing_hashes["outcome"]:
                    skipped_duplicate_count += 1
                    continue
                existing_hashes["outcome"].add(outcome_hash)
                inserted_row_count += 1
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
                        row_hash=outcome_hash,
                        raw_json=json.dumps(outcome.raw, sort_keys=True),
                    )
                )

            for purchase in result.purchases:
                purchase_hash = _purchase_row_hash(purchase)
                if purchase_hash in existing_hashes["purchase"]:
                    skipped_duplicate_count += 1
                    continue
                existing_hashes["purchase"].add(purchase_hash)
                inserted_row_count += 1
                session.add(
                    PlayerAuctionPurchaseRecord(
                        addon_import_id=import_record.id,
                        observed_at=purchase.observed_at,
                        event_type=purchase.event_type,
                        character=purchase.character,
                        realm=purchase.realm,
                        market=purchase.market,
                        auction_id=purchase.auction_id,
                        item_id=purchase.item_id,
                        quantity=purchase.quantity,
                        unit_price=purchase.unit_price,
                        total_price=purchase.total_price,
                        row_hash=purchase_hash,
                        raw_json=json.dumps(purchase.raw, sort_keys=True),
                    )
                )

            import_record.inserted_row_count = inserted_row_count
            import_record.skipped_duplicate_count = skipped_duplicate_count
            session.flush()
            _rebuild_player_auction_matches(session)
            session.commit()
            return import_record.id

    def database_stats(self) -> dict[str, object]:
        tables = [
            FetchRun,
            AuctionListingRecord,
            ItemSummaryRecord,
            ItemHistoryMetricRecord,
            ListingObservationRecord,
            SellThroughMetricRecord,
            ItemDailyMetricRecord,
            ItemAnomalyRecord,
            BuyOpportunityObservationRecord,
            CraftOpportunityObservationRecord,
            AddonImportRecord,
            PlayerAuctionPostRecord,
            PlayerAuctionOutcomeRecord,
            PlayerAuctionPurchaseRecord,
            PlayerAuctionMatchRecord,
        ]
        with Session(self.engine) as session:
            table_counts = {
                table.__tablename__: int(session.scalar(select(func.count()).select_from(table)) or 0)
                for table in tables
            }
            oldest_snapshot = session.scalar(select(func.min(FetchRun.started_at)))
            newest_snapshot = session.scalar(select(func.max(FetchRun.started_at)))
            successful_runs = int(
                session.scalar(select(func.count()).select_from(FetchRun).where(FetchRun.status == "success")) or 0
            )
        return {
            "table_counts": table_counts,
            "oldest_snapshot": oldest_snapshot,
            "newest_snapshot": newest_snapshot,
            "successful_fetch_runs": successful_runs,
        }

    def list_recent_anomalies(self, *, limit: int = 10) -> list[dict[str, object]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(ItemAnomalyRecord)
                .order_by(ItemAnomalyRecord.detected_at.desc(), ItemAnomalyRecord.severity.desc())
                .limit(limit)
            ).all()
            names = {
                item_id: name
                for item_id, name in session.execute(
                    select(ItemMetadataRecord.item_id, ItemMetadataRecord.name).where(
                        ItemMetadataRecord.item_id.in_({row.item_id for row in rows} or {-1})
                    )
                ).all()
                if name is not None
            }
            return [
                {
                    "fetch_run_id": row.fetch_run_id,
                    "detected_at": row.detected_at,
                    "item_id": row.item_id,
                    "name": names.get(row.item_id),
                    "market": row.market,
                    "anomaly_type": row.anomaly_type,
                    "severity": row.severity,
                    "baseline_value": row.baseline_value,
                    "observed_value": row.observed_value,
                    "explanation": row.explanation,
                }
                for row in rows
            ]

    def vacuum(self) -> None:
        if self.engine.dialect.name != "sqlite":
            raise RuntimeError("vacuum is only supported for SQLite databases")
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("vacuum"))

    def prune_raw_listings_before(self, before: datetime) -> int:
        with Session(self.engine) as session:
            run_ids = list(
                session.scalars(
                    select(FetchRun.id).where(FetchRun.started_at < before, FetchRun.status == "success")
                ).all()
            )
            if not run_ids:
                return 0
            result = session.execute(
                delete(AuctionListingRecord).where(AuctionListingRecord.fetch_run_id.in_(run_ids))
            )
            session.commit()
            return int(result.rowcount or 0)

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


def _rebuild_daily_metrics_for_date(session: Session, metric_date: date) -> None:
    runs = session.scalars(
        select(FetchRun)
        .where(func.date(FetchRun.started_at) == metric_date.isoformat(), FetchRun.status.in_(("running", "success")))
        .order_by(FetchRun.id)
    ).all()
    run_ids = [run.id for run in runs]
    session.execute(delete(ItemDailyMetricRecord).where(ItemDailyMetricRecord.metric_date == metric_date))
    if not run_ids:
        return

    history_rows = session.scalars(
        select(ItemHistoryMetricRecord).where(ItemHistoryMetricRecord.fetch_run_id.in_(run_ids))
    ).all()
    sell_through_rows = session.scalars(
        select(SellThroughMetricRecord).where(SellThroughMetricRecord.fetch_run_id.in_(run_ids))
    ).all()
    sell_through_by_key: dict[tuple[int, str], list[SellThroughMetricRecord]] = {}
    for row in sell_through_rows:
        sell_through_by_key.setdefault((row.item_id, row.market), []).append(row)

    grouped: dict[tuple[int, str], list[ItemHistoryMetricRecord]] = {}
    for row in history_rows:
        grouped.setdefault((row.item_id, row.market), []).append(row)

    now = datetime.now(UTC)
    for (item_id, market), rows in grouped.items():
        related_sell_through = sell_through_by_key.get((item_id, market), [])
        session.add(
            ItemDailyMetricRecord(
                metric_date=metric_date,
                item_id=item_id,
                market=market,
                snapshot_count=len(rows),
                low_unit_price=_min_optional(row.min_unit_price for row in rows),
                first_quartile_unit_price=_average_optional(row.first_quartile_unit_price for row in rows),
                median_unit_price=_average_optional(row.median_unit_price for row in rows),
                third_quartile_unit_price=_average_optional(row.third_quartile_unit_price for row in rows),
                weighted_average_unit_price=_average_optional(row.weighted_average_unit_price for row in rows),
                average_quantity=sum(row.total_quantity for row in rows) / len(rows),
                average_listing_count=sum(row.listing_count for row in rows) / len(rows),
                disappeared_quantity=sum(row.disappeared_quantity for row in related_sell_through),
                probable_sold_quantity=sum(row.probable_sold_quantity for row in related_sell_through),
                demand_confidence=_average_optional(row.confidence for row in related_sell_through) or 0,
                first_fetch_run_id=min(row.fetch_run_id for row in rows),
                last_fetch_run_id=max(row.fetch_run_id for row in rows),
                updated_at=now,
            )
        )


def _record_item_anomalies(
    session: Session,
    run: FetchRun,
    current_metrics: list[ItemHistoryMetric],
    *,
    baseline_runs: int = 12,
) -> None:
    session.execute(delete(ItemAnomalyRecord).where(ItemAnomalyRecord.fetch_run_id == run.id))
    for metric in current_metrics:
        prior_rows = session.scalars(
            select(ItemHistoryMetricRecord)
            .where(
                ItemHistoryMetricRecord.fetch_run_id < run.id,
                ItemHistoryMetricRecord.item_id == metric.item_id,
                ItemHistoryMetricRecord.market == metric.market.value,
            )
            .order_by(ItemHistoryMetricRecord.fetch_run_id.desc())
            .limit(baseline_runs)
        ).all()
        if len(prior_rows) < 3:
            continue

        baseline_min = _average_optional(row.min_unit_price for row in prior_rows)
        if baseline_min and metric.min_unit_price:
            _maybe_add_price_anomaly(session, run, metric, baseline_min)

        baseline_quantity = _average_optional(row.total_quantity for row in prior_rows)
        if baseline_quantity and baseline_quantity >= 10 and metric.total_quantity <= baseline_quantity * 0.25:
            severity = round(min(100.0, ((baseline_quantity - metric.total_quantity) / baseline_quantity) * 100))
            session.add(
                ItemAnomalyRecord(
                    fetch_run_id=run.id,
                    detected_at=run.started_at,
                    item_id=metric.item_id,
                    market=metric.market.value,
                    anomaly_type="inventory_drought",
                    severity=severity,
                    baseline_value=baseline_quantity,
                    observed_value=metric.total_quantity,
                    explanation=(
                        f"quantity {metric.total_quantity} is far below recent baseline {baseline_quantity}"
                    ),
                )
            )


def _maybe_add_price_anomaly(
    session: Session,
    run: FetchRun,
    metric: ItemHistoryMetric,
    baseline_min: int,
) -> None:
    if metric.min_unit_price is None:
        return
    ratio = metric.min_unit_price / baseline_min
    if ratio >= 1.5:
        anomaly_type = "price_spike"
        severity = round(min(100.0, (ratio - 1.0) * 100))
    elif ratio <= 0.5:
        anomaly_type = "price_crash"
        severity = round(min(100.0, (1.0 - ratio) * 100))
    else:
        return

    session.add(
        ItemAnomalyRecord(
            fetch_run_id=run.id,
            detected_at=run.started_at,
            item_id=metric.item_id,
            market=metric.market.value,
            anomaly_type=anomaly_type,
            severity=severity,
            baseline_value=baseline_min,
            observed_value=metric.min_unit_price,
            explanation=(
                f"minimum unit price {metric.min_unit_price} changed from recent baseline {baseline_min}"
            ),
        )
    )


def _average_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present))


def _min_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _rebuild_player_auction_matches(session: Session) -> None:
    session.execute(delete(PlayerAuctionMatchRecord))
    outcomes = session.scalars(
        select(PlayerAuctionOutcomeRecord)
        .where(
            PlayerAuctionOutcomeRecord.outcome.in_(("sold", "expired", "cancelled")),
            PlayerAuctionOutcomeRecord.item_id.is_not(None),
        )
        .order_by(PlayerAuctionOutcomeRecord.observed_at, PlayerAuctionOutcomeRecord.id)
    ).all()
    matched_at = datetime.now(UTC)
    for outcome in outcomes:
        post = _best_post_for_outcome(session, outcome)
        if post is None:
            continue
        elapsed_seconds = _elapsed_seconds(post.observed_at, outcome.observed_at)
        session.add(
            PlayerAuctionMatchRecord(
                outcome_id=outcome.id,
                post_id=post.id,
                item_id=outcome.item_id,
                character=outcome.character,
                realm=outcome.realm,
                outcome=outcome.outcome,
                confidence=_match_confidence(outcome, post, elapsed_seconds),
                elapsed_seconds=elapsed_seconds,
                matched_at=matched_at,
            )
        )


def _post_row_hash(post: PlayerAuctionPost) -> str:
    return row_hash(
        "post",
        {
            "raw": post.raw,
            "observed_at": post.observed_at.isoformat() if post.observed_at else None,
            "snapshot_id": post.snapshot_id,
            "character": post.character,
            "realm": post.realm,
            "auction_id": post.auction_id,
            "item_id": post.item_id,
            "quantity": post.quantity,
            "unit_price": post.unit_price,
            "buyout": post.buyout,
        },
    )


def _outcome_row_hash(outcome: PlayerAuctionOutcome) -> str:
    return row_hash(
        "outcome",
        {
            "raw": outcome.raw,
            "observed_at": outcome.observed_at.isoformat() if outcome.observed_at else None,
            "character": outcome.character,
            "realm": outcome.realm,
            "mail_index": outcome.mail_index,
            "item_id": outcome.item_id,
            "item_name": outcome.item_name,
            "item_count": outcome.item_count,
            "outcome": outcome.outcome,
            "money": outcome.money,
        },
    )


def _purchase_row_hash(purchase: PlayerAuctionPurchase) -> str:
    return row_hash(
        "purchase",
        {
            "raw": purchase.raw,
            "observed_at": purchase.observed_at.isoformat() if purchase.observed_at else None,
            "event_type": purchase.event_type,
            "character": purchase.character,
            "realm": purchase.realm,
            "market": purchase.market,
            "auction_id": purchase.auction_id,
            "item_id": purchase.item_id,
            "quantity": purchase.quantity,
            "unit_price": purchase.unit_price,
            "total_price": purchase.total_price,
        },
    )


def _best_post_for_outcome(
    session: Session,
    outcome: PlayerAuctionOutcomeRecord,
) -> PlayerAuctionPostRecord | None:
    if outcome.item_id is None:
        return None
    conditions = [
        PlayerAuctionPostRecord.item_id == outcome.item_id,
    ]
    if outcome.character is not None:
        conditions.append(PlayerAuctionPostRecord.character == outcome.character)
    if outcome.realm is not None:
        conditions.append(PlayerAuctionPostRecord.realm == outcome.realm)
    if outcome.item_count is not None:
        conditions.append(PlayerAuctionPostRecord.quantity == outcome.item_count)
    if outcome.observed_at is not None:
        conditions.append(PlayerAuctionPostRecord.observed_at <= outcome.observed_at)

    return session.scalars(
        select(PlayerAuctionPostRecord)
        .where(*conditions)
        .order_by(PlayerAuctionPostRecord.observed_at.desc(), PlayerAuctionPostRecord.id.desc())
        .limit(1)
    ).first()


def _elapsed_seconds(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return max(0, round((finished_at - started_at).total_seconds()))


def _match_confidence(
    outcome: PlayerAuctionOutcomeRecord,
    post: PlayerAuctionPostRecord,
    elapsed_seconds: int | None,
) -> int:
    confidence = 45
    if outcome.character is not None and outcome.character == post.character:
        confidence += 15
    if outcome.realm is not None and outcome.realm == post.realm:
        confidence += 10
    if outcome.item_count is not None and outcome.item_count == post.quantity:
        confidence += 20
    if elapsed_seconds is not None and elapsed_seconds <= 7 * 24 * 60 * 60:
        confidence += 10
    return min(confidence, 100)


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
                "age_seconds": "integer",
                "undercut_count": "integer not null default 0",
                "unit_price_change": "integer",
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
            create table if not exists item_daily_metrics (
                id integer primary key,
                metric_date date not null,
                item_id integer not null,
                market varchar(32) not null,
                snapshot_count integer not null,
                low_unit_price integer,
                first_quartile_unit_price integer,
                median_unit_price integer,
                third_quartile_unit_price integer,
                weighted_average_unit_price integer,
                average_quantity float not null,
                average_listing_count float not null,
                disappeared_quantity integer not null,
                probable_sold_quantity integer not null,
                demand_confidence integer not null,
                first_fetch_run_id integer not null,
                last_fetch_run_id integer not null,
                updated_at datetime not null
            )
            """
        )
        for column in ("metric_date", "item_id"):
            connection.exec_driver_sql(
                f"create index if not exists ix_item_daily_metrics_{column} on item_daily_metrics ({column})"
            )
        connection.exec_driver_sql(
            """
            create table if not exists item_anomalies (
                id integer primary key,
                fetch_run_id integer not null,
                detected_at datetime not null,
                item_id integer not null,
                market varchar(32) not null,
                anomaly_type varchar(64) not null,
                severity integer not null,
                baseline_value integer,
                observed_value integer,
                explanation text not null,
                foreign key(fetch_run_id) references fetch_runs (id)
            )
            """
        )
        for column in ("fetch_run_id", "item_id", "anomaly_type"):
            connection.exec_driver_sql(
                f"create index if not exists ix_item_anomalies_{column} on item_anomalies ({column})"
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
            create table if not exists craft_opportunity_observations (
                id integer primary key,
                fetch_run_id integer not null,
                observed_at datetime not null,
                recipe_id varchar(128) not null,
                recipe_name varchar(255),
                output_item_id integer not null,
                output_market varchar(32) not null,
                output_quantity integer not null,
                craft_cost integer not null,
                craft_cost_unit_price integer not null,
                output_min_unit_price integer not null,
                sell_target_unit_price integer not null,
                auction_deposit_unit_price integer not null,
                ah_savings integer not null,
                expected_profit integer not null,
                max_craft_quantity integer not null,
                confidence integer not null,
                reasons_json text not null,
                foreign key(fetch_run_id) references fetch_runs (id)
            )
            """
        )
        for column in ("fetch_run_id", "observed_at", "recipe_id", "output_item_id"):
            connection.exec_driver_sql(
                f"create index if not exists ix_craft_opportunity_observations_{column} "
                f"on craft_opportunity_observations ({column})"
            )
        connection.exec_driver_sql(
            """
            create table if not exists addon_imports (
                id integer primary key,
                imported_at datetime not null,
                source_path text not null,
                addon_version integer,
                owned_snapshot_count integer not null,
                mail_event_count integer not null,
                purchase_event_count integer not null default 0,
                inserted_row_count integer not null default 0,
                skipped_duplicate_count integer not null default 0,
                malformed_row_count integer not null default 0
            )
            """
        )
        _ensure_sqlite_columns(
            connection,
            "addon_imports",
            {
                "purchase_event_count": "integer not null default 0",
                "inserted_row_count": "integer not null default 0",
                "skipped_duplicate_count": "integer not null default 0",
                "malformed_row_count": "integer not null default 0",
            },
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
                row_hash varchar(64),
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
                row_hash varchar(64),
                raw_json text not null,
                foreign key(addon_import_id) references addon_imports (id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            create table if not exists player_auction_purchases (
                id integer primary key,
                addon_import_id integer not null,
                observed_at datetime,
                event_type varchar(64) not null,
                character varchar(128),
                realm varchar(128),
                market varchar(32),
                auction_id integer,
                item_id integer,
                quantity integer,
                unit_price integer,
                total_price integer,
                row_hash varchar(64),
                raw_json text not null,
                foreign key(addon_import_id) references addon_imports (id)
            )
            """
        )
        _ensure_sqlite_columns(connection, "player_auction_posts", {"row_hash": "varchar(64)"})
        _ensure_sqlite_columns(connection, "player_auction_outcomes", {"row_hash": "varchar(64)"})
        _ensure_sqlite_columns(connection, "player_auction_purchases", {"row_hash": "varchar(64)"})
        connection.exec_driver_sql(
            """
            create table if not exists player_auction_matches (
                id integer primary key,
                outcome_id integer not null,
                post_id integer not null,
                item_id integer,
                character varchar(128),
                realm varchar(128),
                outcome varchar(32) not null,
                confidence integer not null,
                elapsed_seconds integer,
                matched_at datetime not null,
                foreign key(outcome_id) references player_auction_outcomes (id),
                foreign key(post_id) references player_auction_posts (id)
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
            ("player_auction_posts", "row_hash"),
            ("player_auction_outcomes", "addon_import_id"),
            ("player_auction_outcomes", "observed_at"),
            ("player_auction_outcomes", "character"),
            ("player_auction_outcomes", "realm"),
            ("player_auction_outcomes", "item_id"),
            ("player_auction_outcomes", "outcome"),
            ("player_auction_outcomes", "row_hash"),
            ("player_auction_purchases", "addon_import_id"),
            ("player_auction_purchases", "observed_at"),
            ("player_auction_purchases", "event_type"),
            ("player_auction_purchases", "character"),
            ("player_auction_purchases", "realm"),
            ("player_auction_purchases", "auction_id"),
            ("player_auction_purchases", "item_id"),
            ("player_auction_purchases", "row_hash"),
            ("player_auction_matches", "outcome_id"),
            ("player_auction_matches", "post_id"),
            ("player_auction_matches", "item_id"),
            ("player_auction_matches", "character"),
            ("player_auction_matches", "realm"),
            ("player_auction_matches", "outcome"),
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
