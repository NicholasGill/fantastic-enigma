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

- [ ] Add daily rollups and trend metrics.
  - Roll per-snapshot data into `item_daily_metrics`.
  - Track daily low, first quartile, median, third quartile, average quantity,
    listing count, disappeared quantity, and demand confidence.
  - Use rollups for long-range charts so the dashboard stays fast as the SQLite
    database grows.

- [ ] Detect price anomalies and market events.
  - Flag sudden price spikes, crashes, and inventory droughts against recent
    baselines.
  - Store anomaly rows with item ID, run ID, severity, and explanation.
  - Surface anomalies in CLI reports and the dashboard.

- [ ] Improve sell-through confidence scoring.
  - Account for expected snapshot interval and actual elapsed time between runs.
  - Down-weight repeated identical snapshots, API refresh gaps, and likely
    cancel/repost churn.
  - Separate "disappeared listing" and "probable sale" confidence in output.

- [ ] Add listing age and undercut tracking.
  - Estimate how long a listing remains visible across repeated snapshots.
  - Track undercut counts and price changes per item between snapshots.
  - Use age and undercut pressure as recommendation inputs.

- [ ] Add database maintenance commands.
  - Add `wow-auctions db stats` for table sizes and oldest/newest snapshot.
  - Add `wow-auctions db vacuum` for SQLite cleanup.
  - Add optional retention pruning for raw listings while preserving summaries
    and rollups.

## Player Auction Features

- [ ] Feature 1: Companion addon capture.
  - [x] Add minimal WoW companion addon.
  - [x] Record owned-auction snapshots from the auction house.
  - [x] Record auction-related mailbox rows for sale/expiry/cancel signals.
  - [x] Store data in `WowAuctionTrackerDB` SavedVariables.
  - [ ] Capture deposit cost, auction duration, stack size, and posted unit
    price when available from the WoW API.
  - [ ] Record whether an owned auction was manually cancelled or naturally
    expired when the signal is available.

- [ ] Feature 2: Addon import and deduplication.
  - [x] Parse `WowAuctionTracker.lua`.
  - [x] Store player auction posts and outcomes in SQLite.
  - [x] Preserve raw addon rows for classifier improvements.
  - [ ] Generate stable hashes for imported SavedVariables rows.
  - [ ] Skip duplicate owned snapshots and mailbox events across repeated
    imports.
  - [ ] Report inserted, skipped, and malformed row counts.

- [ ] Feature 3: Player outcome matching and recommendation scoring.
  - [x] Blend player auction outcomes into recommendations.
  - [x] Prefer real player outcomes over inferred market sell-through when
    enough personal history exists.
  - [ ] Link mailbox outcomes back to the most likely owned auction post by
    character, realm, item, quantity, and observed time.
  - [ ] Store match confidence and keep unmatched rows for later review.
  - [ ] Estimate time-to-sale and time-to-expiry from matched rows.

- [ ] Feature 4: Player performance reports.
  - [ ] Report sale rate, average proceeds, expired quantity, and cancelled
    quantity by item and character.
  - [ ] Show best and worst personal auction items over configurable time
    windows.
  - [ ] Export player outcome summaries to CSV.

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

## Constraints

- Blizzard auction APIs expose current listings, not completed sales.
- Inferred demand must be labeled as an estimate.
- Raw Blizzard payloads should remain available for debugging and future parsing.
- Local secrets, SQLite databases, and local item config stay ignored by git.
