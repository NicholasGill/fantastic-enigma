# WoW Auction Tracker Plan

## Goal

Build a local auction-history system that turns repeated Blizzard auction
snapshots into useful pricing and demand signals for tracked items.

## Snapshot Inference Features

- [ ] Store item metadata from Blizzard item and media endpoints.
  - Capture item name, quality, class, subclass, item level, stackability, vendor
    sell price, and icon URL.
  - Refresh metadata only when an item is first tracked or explicitly requested.

- [ ] Track listing observations across snapshots.
  - Record each observed auction listing by Blizzard auction ID, item ID, market,
    quantity, price, and snapshot time.
  - Keep enough data to detect whether a listing is new, still present, changed,
    or missing in later snapshots.

- [ ] Infer listing lifecycle status.
  - Mark listings as `active`, `missing`, or `ended_estimated`.
  - Treat missing listings as uncertain because they may have sold, expired,
    been cancelled, or been reposted.
  - Store confidence fields rather than presenting inferred sales as facts.

- [ ] Generate item-level historical metrics.
  - Calculate min, median, and weighted average unit price per snapshot.
  - Track total listed quantity, listing count, and lowest-price quantity.
  - Calculate moving averages over recent snapshots.

- [ ] Estimate demand and sell-through signals.
  - Compare consecutive snapshots for disappeared listings.
  - Estimate disappeared quantity and disappeared value by item.
  - Produce conservative demand scores from repeated disappearance patterns.

- [ ] Add reporting commands.
  - Show latest item summaries in gold/silver/copper.
  - Show price history for one item.
  - Show inferred demand trends for tracked items.
  - Export summaries to CSV for spreadsheet analysis.

- [x] Add built-in scheduled snapshot support.
  - Add a built-in `wow-auctions schedule --interval-minutes ...` command.
  - Document cron/systemd-style external scheduler options for periodic
    `wow-auctions fetch`.

- [ ] Add scheduler safety metadata.
  - Add safeguards to avoid overlapping fetch runs.
  - Record expected snapshot interval for inference calculations.

## Data Model Ideas

- `item_metadata`: durable Blizzard item details and icon media.
- `listing_observations`: one row per observed listing per fetch run.
- `listing_lifecycles`: best-effort status for listings across snapshots.
- `item_history_metrics`: per-item metrics calculated for each fetch run.
- `item_daily_metrics`: rolled-up daily price, availability, and demand signals.

## Implementation Order

1. Add item metadata fetching and storage.
2. Add observation/lifecycle tables while preserving current raw listing storage.
3. Add consecutive-snapshot comparison logic.
4. Add history metric generation after each successful fetch.
5. Add CLI reporting commands for summaries, history, and demand signals.
6. Add CSV export and scheduled-fetch documentation.

## Constraints

- Blizzard auction APIs expose current listings, not completed sales.
- Inferred demand must be labeled as an estimate.
- Raw Blizzard payloads should remain available for debugging and future parsing.
- Local secrets, SQLite databases, and local item config stay ignored by git.
