# WoW Auction Tracker Plan

## Goal

Build a local auction-history system that turns repeated Blizzard auction
snapshots into useful pricing and demand signals for tracked items.

Completed features are tracked in `completed.md`.

## Recommendation Features

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
