# WoW Auction Tracker Completed Features

This file records completed features moved out of `PLAN.md` so the plan can
stay focused on future work.

## Engineering

- [x] Add GitHub Actions for Python linting and unit tests.
  - Run Ruff and pytest for pushes and pull requests with Python 3.12 and the
    locked `uv` development environment.
  - Build package artifacts after successful main-branch checks.

## Dashboard Features

- [x] Add richer item detail pages.
  - Link dashboard market, buy, sell, and recommendation rows to one item page.
  - Show current quartile prices, price and quantity history, and selectable
    24-hour, 7-day, 30-day, and all-history ranges.
  - Smooth price history with range-aware rolling medians and an
    outlier-resistant scale while retaining a raw-data chart mode and the
    original snapshot values.
  - Keep item-page price charts focused on one median series while retaining
    quartile cards and raw snapshot columns for deeper inspection.
  - Show the seven most recent local calendar dates in the 7-day range and
    label every date and weekday on their respective charts.
  - Aggregate first-quartile, median, and third-quartile prices by local hour
    and weekday, using median bucket values for outlier-resistant charts.
  - Include sell-through estimates, current recommendation explanations,
    anomaly events, and imported player outcome history.
  - Batch latest-snapshot listing lookups for dashboard item quality and
    recommendation prices to keep tab loading fast as history grows.
  - Group same-name crafting reagents into tier families, compare current
    five-unit-depth prices, and highlight price inversions in a dedicated table.
  - Suppress buy targets and buy scores for a tier when an equal or higher tier
    has the same or lower current price, while preserving sell signals.

## Snapshot Inference Features

- [x] Make snapshot pricing resistant to disconnected auction tiers.
  - Split sufficiently populated listing samples at price jumps greater than
    2x and derive quartiles, median, and weighted average from the densest band.
  - Preserve raw listing counts, total quantity, and the actual minimum listing
    price so filtered analytics do not hide available auctions.
  - Leave sparse samples unchanged and prefer the lower band on equal-density
    ties to keep estimates conservative.

- [x] Store item metadata from Blizzard item and media endpoints.
  - Capture item name, quality, class, subclass, item level, stackability, vendor
    sell price, and icon URL.
  - Refresh metadata when an item is first tracked.

- [x] Track listing observations across snapshots.
  - Record each observed auction listing by Blizzard auction ID, item ID, market,
    quantity, price, and snapshot time.
  - Keep enough data to detect whether a listing is new, still present, changed,
    or missing in later snapshots.

- [x] Infer listing lifecycle status.
  - Mark listings as `active`, `missing`, or `ended_estimated`.
  - Treat missing listings as uncertain because they may have sold, expired,
    been cancelled, or been reposted.
  - Store `new`, `active`, `changed`, and `missing` observations. Deeper
    `ended_estimated` confidence fields still belong with sell-through scoring.

- [x] Generate item-level historical metrics.
  - Calculate min, first quartile, median, third quartile, and weighted average
    unit price per snapshot.
  - Track total listed quantity, listing count, and lowest-price quantity.
  - Persist metrics in `item_history_metrics` for new snapshots.
  - Calculate moving averages over recent snapshots.

- [x] Estimate demand and sell-through signals.
  - Compare consecutive snapshots for disappeared listings.
  - Estimate disappeared quantity and disappeared value by item.
  - Produce conservative demand scores from repeated disappearance patterns.
  - Store per-snapshot inferred metrics in `sell_through_metrics`.

- [x] Add initial recommendation engine.
  - Score tracked items from current price versus recent median price.
  - Include conservative demand proxy from recent quantity drops.
  - Include a recommended sell price from recent first-quartile pricing.
  - Show score, confidence, and reasons in the CLI and dashboard.

- [x] Add reporting commands.
  - Show latest item summaries in gold/silver/copper.
  - Show price history for one item.
  - Show inferred recommendation scores for tracked items.

- [x] Export summaries to CSV for spreadsheet analysis.

- [x] Add built-in scheduled snapshot support.
  - Add a built-in `wow-auctions schedule --interval-minutes ...` command.
  - Document cron/systemd-style external scheduler options for periodic
    `wow-auctions fetch`.

- [x] Add scheduler safety metadata.
  - Add safeguards to avoid overlapping fetch runs.
  - Record expected snapshot interval for inference calculations.

- [x] Add daily rollups and trend metrics.
  - Roll per-snapshot data into `item_daily_metrics`.
  - Track daily low, first quartile, median, third quartile, average quantity,
    listing count, disappeared quantity, and demand confidence.
  - Use rollups for long-range charts so the dashboard stays fast as the SQLite
    database grows.

- [x] Detect price anomalies and market events.
  - Flag sudden price spikes, crashes, and inventory droughts against recent
    baselines.
  - Store anomaly rows with item ID, run ID, severity, and explanation.
  - Surface anomalies in CLI reports. Dashboard anomaly views still belong with
    richer item detail pages.

- [x] Improve sell-through confidence scoring.
  - Account for expected snapshot interval and actual elapsed time between runs.
  - Down-weight repeated identical snapshots, API refresh gaps, and likely
    cancel/repost churn.
  - Keep confidence conservative when disappearance evidence is ambiguous.

- [x] Add listing age and undercut tracking.
  - Estimate how long a listing remains visible across repeated snapshots.
  - Track undercut counts and price changes per item between snapshots.
  - Expose age and undercut pressure for future recommendation inputs.

- [x] Add database maintenance commands.
  - Add `wow-auctions db stats` for table sizes and oldest/newest snapshot.
  - Add `wow-auctions db vacuum` for SQLite cleanup.
  - Add optional retention pruning for raw listings while preserving summaries
    and rollups.

## Player Auction Features

- [x] Feature 1: Companion addon capture.
  - [x] Add minimal WoW companion addon.
  - [x] Record owned-auction snapshots from the auction house.
  - [x] Record auction-related mailbox rows for sale/expiry/cancel signals.
  - [x] Store data in `WowAuctionTrackerDB` SavedVariables.
  - [x] Capture deposit cost, auction duration, stack size, and posted unit
    price when available from the WoW API.
  - [x] Record whether an owned auction was manually cancelled or naturally
    expired when the signal is available.

- [x] Feature 2: Addon import and deduplication.
  - [x] Parse `WowAuctionTracker.lua`.
  - [x] Store player auction posts and outcomes in SQLite.
  - [x] Preserve raw addon rows for classifier improvements.
  - [x] Generate stable hashes for imported SavedVariables rows.
  - [x] Skip duplicate owned snapshots and mailbox events across repeated
    imports.
  - [x] Report inserted, skipped, and malformed row counts.

- [x] Feature 3: Player outcome matching and recommendation scoring.
  - [x] Blend player auction outcomes into recommendations.
  - [x] Prefer real player outcomes over inferred market sell-through when
    enough personal history exists.
  - [x] Link mailbox outcomes back to the most likely owned auction post by
    character, realm, item, quantity, and observed time.
  - [x] Store match confidence and keep unmatched rows for later review.
  - [x] Estimate time-to-sale and time-to-expiry from matched rows.

- [x] Feature 4: Player performance reports.
  - [x] Report sale rate, average proceeds, expired quantity, and cancelled
    quantity by item and character.
  - [x] Show best and worst personal auction items over configurable time
    windows.
  - [x] Export player outcome summaries to CSV.

## Recommendation Features

- [x] Add historical timing recommendations.
  - Compare current prices against prior week and multi-week baselines for the
    same item.
  - Detect recurring low-price and high-price windows by day of week and hour
    of day.
  - Recommend "best time to buy" when current price is low relative to the
    historical timing baseline.
  - Recommend "best time to sell" when current or upcoming windows historically
    support stronger first-quartile or median prices.
  - Show historical confidence based on number of weeks, snapshot density, and
    consistency of the pattern.
  - Include timing reasons in CLI, CSV exports, and dashboard recommendation
    cards.

- [x] Record buy-opportunity observations.
  - Record new or newly discounted auction listings below the prior recommended
    buy price.
  - Store item ID, fetch run ID, auction ID, observed unit price, quantity, buy
    target, sell target, potential profit, available quantity at or below buy
    target, recommendation score, confidence, and listing status.

## Backtesting

- [x] Add initial snapshot-history backtesting.
  - Add `wow-auctions backtest` for a configurable buy-low/sell-target
    strategy over stored successful snapshots.
  - Support lookback window, buy discount, sell markup, stop loss, minimum
    sell-through, max position quantity, max holding runs, starting cash, AH
    cut, and auction duration settings.
  - Simulate one open position per item, capital constraints, max available
    snapshot quantity, auction house cut, deposit estimates, target exits,
    stop-loss exits, max-holding exits, and mark-to-market open positions.
  - Report closed trades, open positions, win rate, realized profit,
    unrealized profit, total return, max drawdown, and average holding runs.
  - Export summary and trade CSV files.

## Market Data Platform

- [x] Preserve full raw Blizzard API responses before filtering tracked items.
  - Store raw payload metadata, retrieval timestamp, region, namespace,
    connected realm, market type, API URL, and response checksum.
  - Keep filtered local tables for day-to-day use, but make raw snapshots
    replayable for future parsers and backfills.

- [x] Split realm auctions and regional commodities into explicit market
  pipelines.
  - Model commodities as region-wide markets and realm items as connected-realm
    markets.
  - Keep independent freshness checks, snapshot counts, and derived metrics for
    each market type.

- [x] Add historical price-change and risk metrics.
  - Calculate 1-hour, 24-hour, and 7-day price changes.
  - Calculate historical volatility, moving averages, percentile rank, market
    depth, and liquidity score.
  - Use first quartile, median, and percentile fields as primary price signals.

- [x] Add data-quality monitoring.
  - Detect stale snapshots, missing configured items or realms, API failures,
    repeated identical snapshots, and sudden record-count changes.
  - Store quality events and expose them in CLI reports.

- [x] Add replay support for derived processing.
  - Rebuild lifecycle observations, sell-through metrics, recommendations,
    opportunities, anomalies, and rollups from historical raw snapshots.
  - Make replay idempotent so processing can be rerun safely after scoring
    changes.
