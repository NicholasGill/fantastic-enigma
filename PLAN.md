# WoW Auction Tracker Plan

## Goal

Build a local auction-history system that turns repeated Blizzard auction
snapshots into useful pricing and demand signals for tracked items.

## Snapshot Inference Features

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

- [ ] Add recommendation explanations and thresholds.
  - Show buy target, sell target, expected margin, confidence, and top scoring
    factors together.
  - Make margin threshold and minimum confidence configurable.
  - Explain when an item is rejected because margin, demand, or confidence is
    too low.

- [ ] Add portfolio-style shopping list output.
  - Group recommendations into "buy now", "watch", and "avoid".
  - Include suggested max quantity to buy based on recent demand and current
    inventory depth.
  - Export a shopping list CSV with item ID, buy price, sell price, max quantity,
    score, and reasons.

- [ ] Track buy-opportunity appearances.
  - [x] Record new or newly discounted auction listings below the prior
    recommended buy price.
  - [x] Store item ID, fetch run ID, auction ID, observed unit price, quantity,
    buy target, sell target, potential profit, available quantity at or below
    buy target, recommendation score, confidence, and listing status.
  - Track when an opportunity first appears, remains available, disappears, and
    reappears.
  - Report opportunity duration and estimated missed/available profit over time.
  - Use this history to identify items that repeatedly become buyable below
    target.

- [ ] Add realm and commodity comparison support.
  - Keep separate scoring for realm auctions and regional commodities.
  - Allow tracked items to include desired market type and preferred realm.
  - Highlight items where local realm pricing diverges from commodity trends.

- [ ] Add configurable item groups.
  - Support groups in `config/items.yaml`, such as herbs, ore, enchanting, and
    crafted reagents.
  - Filter CLI reports and dashboard tables by group.
  - Show group-level summary metrics and recommendation counts.

## Dashboard Features

- [ ] Add dashboard filters and sorting controls.
  - Sort by score, buy price, sell price, sell-through, min change, quantity,
    and confidence.
  - Filter by item group, market, action, and minimum confidence.
  - Preserve selected filters in the URL query string.

- [ ] Add richer item detail pages.
  - Show price history, quantity history, sell-through history, and player
    outcome history for one item.
  - Include current recommendation explanation and recent anomaly events.
  - Add CSV export links for the selected item.

- [ ] Add buy opportunities dashboard.
  - Show current items with listings below the recommended buy price.
  - Show when each opportunity first appeared, last appeared, current duration,
    min price, buy target, sell target, potential profit, and quantity available.
  - Include historical opportunity charts by item and time of day.
  - Filter opportunities by item group, crafting quality, minimum profit,
    recommendation confidence, and whether the opportunity is still active.
  - Export opportunity history to CSV.

- [ ] Add dashboard health panel.
  - Show scheduler status, last successful fetch, last failed fetch, database
    size, and snapshot cadence.
  - Warn when snapshots are stale or Blizzard credentials are missing.
  - Show recent errors with enough context to debug failed fetches.

## Quant Platform Roadmap

These items describe the larger portfolio-ready platform direction. They are
not required for the current local SQLite dashboard, but they capture the
missing scope from a full auction-house quantitative trading system.

### Market Data Platform

- [ ] Preserve full raw Blizzard API responses before filtering tracked items.
  - Store raw payload metadata, retrieval timestamp, region, namespace,
    connected realm, market type, API URL, and response checksum.
  - Keep filtered local tables for day-to-day use, but make raw snapshots
    replayable for future parsers and backfills.

- [ ] Split realm auctions and regional commodities into explicit market
  pipelines.
  - Model commodities as region-wide markets and realm items as connected-realm
    markets.
  - Keep independent freshness checks, snapshot counts, and derived metrics for
    each market type.

- [ ] Add historical price-change and risk metrics.
  - Calculate 1-hour, 24-hour, and 7-day price changes.
  - Calculate historical volatility, moving averages, percentile rank, market
    depth, and liquidity score.
  - Use first quartile, median, and percentile fields as primary price signals.

- [ ] Add data-quality monitoring.
  - Detect stale snapshots, missing configured items or realms, API failures,
    repeated identical snapshots, and sudden record-count changes.
  - Store quality events and expose them in CLI reports and the dashboard health
    panel.

- [ ] Add replay support for derived processing.
  - Rebuild lifecycle observations, sell-through metrics, recommendations,
    opportunities, anomalies, and rollups from historical raw snapshots.
  - Make replay idempotent so processing can be rerun safely after scoring
    changes.

- [ ] Add optional production data stores.
  - Keep SQLite as the local default.
  - Add PostgreSQL support for users, positions, watchlists, transactions, and
    dashboard state.
  - Add BigQuery export or sync for long-term raw snapshots and historical
    analytics.

### Opportunity Engine

- [ ] Formalize opportunity scoring.
  - Score opportunities from discount, liquidity, confidence, expected margin,
    and volatility penalty.
  - Store factor-level scores so recommendations can explain exactly why an
    item was selected or rejected.

- [ ] Add historical-undervaluation detection.
  - Compare current price against rolling 14-day and 30-day medians and
    percentiles.
  - Flag items below configurable percentile and discount thresholds.

- [ ] Add market-depth opportunity analysis.
  - Calculate the capital required to buy through cheap listings to the next
    visible price level.
  - Warn when the required quantity is too large relative to estimated daily
    demand or recent player sale history.

- [ ] Add supported realm-arbitrage analysis.
  - Compare realm-specific items only where supported game mechanics make
    movement legitimate.
  - Include auction house cut, deposits, transfer or crafting costs, expected
    sale price, and liquidity risk.
  - Exclude unsupported gold transfers and third-party gold-sale workflows.

- [ ] Expand crafting optimization.
  - Represent recipes as graphs from raw materials to intermediate materials to
    crafted outputs.
  - Account for output quantity, multicraft/resourcefulness assumptions where
    available, auction fees, concentration or cooldown limits, capital required,
    and estimated time to sell.

- [ ] Add seasonal and event features.
  - Track raid resets, weekly profession resets, holidays, weekends, common raid
    windows, patch launches, and expansion launches as model features.
  - Compare event-aware baselines against ordinary moving averages.

### Backtesting

- [ ] Add a strategy-rule backtesting engine.
  - Allow strategies to define buy rules, sell rules, max position size,
    minimum liquidity, and confidence thresholds.
  - Support rules such as price percentile, discount from median, daily volume,
    and expected margin.

- [ ] Simulate realistic trade execution.
  - Account for auction house cut, deposits, failed auctions, partial fills,
    limited market depth, holding time, capital constraints, relisting behavior,
    and price slippage.
  - Avoid assuming every listing can be bought or sold at the minimum displayed
    price.

- [ ] Report backtest performance.
  - Show total return, return on invested gold, maximum drawdown, win rate,
    median holding period, profit per day, capital utilization,
    risk-adjusted return, and expected-versus-realized profit.
  - Compare strategies against simple baselines such as no-trade and
    buy-and-hold.

### Portfolio And Trading Journal

- [ ] Add portfolio positions.
  - Track item, quantity held, average acquisition price, current market value,
    realized profit, unrealized profit, days held, capital at risk, and expected
    time to sell.
  - Build positions from manual entries and imported addon purchase/sale rows.

- [ ] Add portfolio risk controls.
  - Track maximum gold allocated to one item, maximum exposure by item group or
    profession, maximum volatile-item exposure, minimum cash reserve,
    diversification score, and slow-moving inventory alerts.

- [ ] Add a personal trading journal.
  - Record purchases, listings, sales, expirations, cancellations, strategy tag,
    notes, and prediction snapshot.
  - Compare recommendation predictions against actual player outcomes.

### WoW Token Valuation

- [ ] Add regional WoW Token price ingestion.
  - Store token price history separately from auction item prices.
  - Refresh token prices on the same schedule or a lower-frequency schedule.

- [ ] Add token-equivalent portfolio valuation.
  - Calculate token equivalent from portfolio gold divided by current regional
    token gold price.
  - Calculate Battle.net purchasing-power equivalent using the supported
    regional redemption value.
  - Label the figure as analytical purchasing power, not cash value and not
    withdrawable.

- [ ] Add token-adjusted performance metrics.
  - Show profit in gold, token equivalent, Battle.net purchasing-power
    equivalent, game-time months, and active-time return.
  - Keep Blizzard policy boundaries visible anywhere real-world equivalent
    values appear.

### Recommendation Explanations And Alerts

- [ ] Generate decision-support explanations.
  - Explain selection reason, expected margin, confidence, liquidity, worst-case
    estimate, recommended max purchase quantity, and invalidation conditions.
  - Show rejected items with the threshold that failed.

- [ ] Add alert rules.
  - Support price below threshold, price below historical percentile, large
    supply reduction, sudden volume increase, crafting margin above threshold,
    position risk-limit breach, inventory held too long, auction expiration, and
    token-price movement.

- [ ] Add alert delivery channels.
  - Support dashboard feed first.
  - Add Discord webhooks, email, browser notifications, and configurable quiet
    hours.
  - Keep alerts informational only; do not automate in-game buying, selling,
    clicking, or character actions.

### Machine Learning

- [ ] Add sale-probability modeling after rule-based scoring is stable.
  - Predict probability that an item sells within 12, 24, and 48 hours.
  - Use features such as price relative to rolling median, quantity listed, time
    of day, day of week, realm population proxy, recent price movement, recent
    quantity movement, item category, and event flags.

- [ ] Add price forecasting with intervals.
  - Predict a price range rather than a single point estimate.
  - Compare forecasts against moving-median and percentile baselines.

- [ ] Add advanced anomaly detection.
  - Detect suspiciously cheap listings, temporary supply shocks, likely data
    errors, market manipulation patterns, and large sellers entering or leaving
    a market.

### Production Engineering

- [ ] Add a service-oriented API and frontend path.
  - Keep the current local Flask dashboard until it becomes limiting.
  - Add FastAPI when external clients, authentication, or a separate frontend
    need a stable API contract.
  - Add a React/TypeScript dashboard when the UI needs richer interaction than
    the server-rendered local dashboard.

- [ ] Add cloud/event-driven deployment options.
  - Package ingestion as Cloud Run Jobs or equivalent scheduled workers.
  - Use Pub/Sub-style events to trigger processing of new snapshots.
  - Add Docker Compose for local multi-service development.

- [ ] Add infrastructure and CI/CD.
  - Add Terraform for cloud resources.
  - Add GitHub Actions for tests, linting, packaging, and deployment.
  - Add migration tooling before relying on non-SQLite production databases.

- [ ] Add observability and reliability hardening.
  - Add structured logging, OpenTelemetry tracing, Prometheus metrics, rate-limit
    handling, exponential backoff, dead-letter queues, data lineage, load tests,
    and a cost dashboard.
  - Extend OAuth token caching and refresh behavior for long-running workers.

## Data Model Ideas

- `item_metadata`: durable Blizzard item details and icon media.
- `listing_observations`: one row per observed listing per fetch run.
- `listing_lifecycles`: best-effort status for listings across snapshots.
- `item_history_metrics`: per-item metrics calculated for each fetch run.
- `sell_through_metrics`: per-item inferred disappearance metrics per fetch run.
- `item_daily_metrics`: rolled-up daily price, availability, and demand signals.
- `item_anomalies`: detected price, inventory, and demand anomalies.
- `buy_opportunity_observations`: snapshots where the current market is below
  the recommended buy target.
- `player_auction_matches`: links addon mailbox outcomes to likely auction
  posts with confidence.
- `import_row_hashes`: deduplication keys for repeated SavedVariables imports.
- `raw_auction_snapshots`: full Blizzard API payloads and retrieval metadata.
- `market_quality_events`: stale, missing, duplicated, or abnormal snapshot
  signals.
- `token_prices`: regional WoW Token price history.
- `portfolio_positions`: current inventory, cost basis, valuation, and risk
  fields.
- `trade_journal_entries`: player-entered or addon-imported trading events.
- `strategy_definitions`: serialized backtest and scanner strategy rules.
- `backtest_runs`: strategy execution summaries and assumptions.
- `backtest_trades`: simulated purchases, listings, sales, fees, and slippage.
- `alert_rules`: user-configured alert definitions.
- `alert_events`: triggered alerts and delivery state.

## Implementation Order

1. Add daily rollups and dashboard sorting/filtering.
2. Add addon import deduplication and player outcome matching.
3. Add player performance reports and CSV exports.
4. Improve sell-through confidence using snapshot cadence and churn signals.
5. Add richer recommendation explanations, expected margin, and shopping list
   exports.
6. Add buy-opportunity tracking and the dedicated opportunities dashboard.
7. Add anomaly detection and item detail pages.
8. Add database maintenance commands and long-term retention controls.
9. Preserve full raw API snapshots and add replayable processing.
10. Add formal opportunity factor scoring and historical-undervaluation
    detection.
11. Add portfolio positions, trading journal entries, and token-equivalent
    valuation.
12. Add liquidity-aware strategy backtesting.
13. Add alert rules and dashboard/Discord delivery.
14. Add optional PostgreSQL and BigQuery backends.
15. Add FastAPI, React, and cloud/event-driven deployment only after the local
    product proves the core trading workflow.

## Constraints

- Blizzard auction APIs expose current listings, not completed sales.
- Inferred demand must be labeled as an estimate.
- Raw Blizzard payloads should remain available for debugging and future parsing.
- Local secrets, SQLite databases, and local item config stay ignored by git.
- WoW Token dollar-equivalent displays must be labeled as Battle.net
  purchasing-power estimates, not cash value or withdrawable income.
- Do not build features around unsupported gold transfers, third-party gold
  sales, or automated in-game buying, selling, clicking, or character actions.
