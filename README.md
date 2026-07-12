# WoW Auction Tracker

Local World of Warcraft auction snapshot tracker. Stage 1 fetches configured
Retail US auction data from the official Blizzard APIs and stores filtered
listings in SQLite for later pricing recommendations.

## Setup

Install dependencies with `uv`:

```bash
uv sync --extra dev
```

Create a local config from the example:

```bash
cp config/items.example.yaml config/items.yaml
```

Set Blizzard API credentials from the Battle.net developer portal. For local
development, keep them in an ignored `.env` file:

```env
BLIZZARD_CLIENT_ID="..."
BLIZZARD_CLIENT_SECRET="..."
```

Load the file before running commands that call Blizzard APIs:

```bash
set -a
. .env
set +a
```

## Commands

Initialize the local database:

```bash
uv run wow-auctions init-db
```

Fetch one configured auction snapshot:

```bash
uv run wow-auctions fetch
```

Fetches preserve full Blizzard auction payloads as gzipped JSON under
`data/raw_snapshots/` before filtering tracked items. The database stores raw
snapshot metadata, including market type, API path, payload hash, payload size,
auction count, and item count. Override the raw snapshot directory with
`WAT_RAW_SNAPSHOT_DIR`.

Run snapshots repeatedly at a fixed interval:

```bash
uv run wow-auctions schedule --interval-minutes 30
```

For a bounded run, add `--max-runs`:

```bash
uv run wow-auctions schedule --interval-minutes 30 --max-runs 8
```

By default, data is written to `data/auction_tracker.sqlite3`. Override it with:

```bash
DATABASE_URL="sqlite:///data/dev.sqlite3" uv run wow-auctions fetch
```

Start the local dashboard:

```bash
uv run wow-auctions dashboard
```

Open `http://127.0.0.1:8000` to inspect database size, snapshot counts, latest
item summaries, recommendations, recent runs, and per-item price history. Use
`--port` if port 8000 is already in use.

Stored price summaries include minimum, first quartile, median, and third
quartile unit prices. The dashboard emphasizes minimum, buy, sell, deposit, and
net profit per unit so the active view stays focused on flip decisions.

Snapshot fetches also collect missing item metadata from Blizzard's item and
media endpoints, including item quality, class, subclass, stackability, vendor
prices, and icon URL.

Snapshot fetches also infer sell-through by comparing consecutive snapshots.
Missing listings are aggregated by item as disappeared listing count,
disappeared quantity, disappeared value, sell-through ratio, and confidence.
This is an estimate only: missing listings may have sold, expired, been
cancelled, or been reposted.

Completed fetches also rebuild daily item rollups and flag unusual market
events. Daily rollups preserve low, quartile, weighted-average, quantity,
listing-count, disappeared-quantity, probable-sold, and demand-confidence
signals for longer-range analysis. Anomaly rows flag price spikes, price
crashes, and inventory droughts against recent item baselines.

Show current item recommendations from stored snapshot history:

```bash
uv run wow-auctions recommend --limit 10
```

Backtest a simple snapshot-history trading strategy:

```bash
uv run wow-auctions backtest --lookback-runs 6 --max-position-quantity 20
uv run wow-auctions backtest --output backtest-summary.csv --trades-output backtest-trades.csv
```

Show profitable craft signals from the latest snapshot:

```bash
uv run wow-auctions report crafts --limit 10
```

Show recent price and inventory anomalies:

```bash
uv run wow-auctions report anomalies --limit 10
```

Show recent market data quality events:

```bash
uv run wow-auctions report quality --limit 10
```

Quality events flag empty payloads, repeated raw payload hashes, missing
configured items, stale snapshot cadence, large record-count changes, and fetch
failures.

Import companion addon SavedVariables after copying or pointing at the game
file:

```bash
uv run wow-auctions import-addon --saved-variables "/path/to/WowAuctionTracker.lua"
```

Repeated imports are row-deduped. The command reports inserted rows, skipped
duplicates, and malformed rows.

Print the latest stored item summaries:

```bash
uv run wow-auctions report latest --limit 10
```

Print snapshot history for one item:

```bash
uv run wow-auctions report item --item-id 210930
```

Show personal auction performance from imported addon rows:

```bash
uv run wow-auctions report player --limit 10
uv run wow-auctions report player --days 30 --limit 10
```

Export stored data to CSV:

```bash
uv run wow-auctions export latest --output latest.csv
uv run wow-auctions export item --item-id 210930 --output item-history.csv
uv run wow-auctions export recommendations --limit 10
uv run wow-auctions export crafts --output craft-signals.csv
uv run wow-auctions export player-performance --days 30 --output player-performance.csv
```

Inspect and maintain the local SQLite database:

```bash
uv run wow-auctions db stats
uv run wow-auctions db vacuum
uv run wow-auctions db prune-raw-listings --before-days 30
```

Raw listing pruning deletes old `auction_listings` rows only. Summaries,
history metrics, sell-through metrics, daily rollups, opportunities, and player
data are preserved.

Rebuild derived rows from preserved raw Blizzard payloads:

```bash
uv run wow-auctions replay-raw --from-run-id 100 --to-run-id 120
```

Replay refilters raw snapshots with the current config and rebuilds listings,
summaries, history metrics, lifecycle observations, sell-through metrics,
opportunities, anomalies, daily rollups, and quality events for successful fetch
runs in the selected range.

Recommendations are conservative estimates based on current price versus recent
median price, inferred sell-through, recent quantity drops, listing scarcity,
recent price trend, snapshot count, and imported player auction outcomes when
available. Blizzard's auction APIs expose current listings, not completed
purchases, so imported personal sale and expiry signals are preferred over
inferred market sell-through once enough personal history exists. Trend score is
shown as a 0-100 market-risk signal: 50 is flat, higher is rising, and lower is
falling.

History metrics also include data-engineering risk fields for price change over
recent windows, historical volatility, percentile rank, market depth, and
liquidity. These fields are derived from stored snapshot history and rebuilt
during raw replay.

Recommendation output includes a recommended per-unit sell price when there is
inferred sale evidence. It uses the average unit price of disappeared listings
classified as probable sales and listings whose observed quantity decreased
between snapshots. If no inferred sale evidence exists, the sell price is left
blank instead of falling back to quartiles or medians.

Profit estimates subtract the estimated 48-hour auction deposit per unit from
the sell-minus-buy spread. The deposit estimate uses Blizzard item metadata's
vendor sell price field and the standard 48-hour deposit rate.

Craft signals are based on manually configured recipes. Recipe ingredient and
output items are fetched automatically even when they are not duplicated under
`items`. A craft signal appears only when the latest ingredient cost is below
the current output auction price and the output has a conservative sell target
from inferred or personal sale evidence.

## Companion Addon

The `addons/WowAuctionTracker` directory contains a minimal Retail addon for
capturing mailbox auction outcomes and character gold snapshots into WoW
SavedVariables. Install it by copying that directory to:

```text
World of Warcraft/_retail_/Interface/AddOns/WowAuctionTracker
```

In game, use `/wat mail` at the mailbox and `/wat gold` to force-record the
current wallet balance. Auction-house capture is disabled for now, so the addon
does not register auction-house events, query owned auctions, or hook auction
purchase APIs. It records auction-related mailbox rows and character gold
snapshots to:

```text
World of Warcraft/_retail_/WTF/Account/<ACCOUNT>/SavedVariables/WowAuctionTracker.lua
```

SavedVariables are written after `/reload`, logout, or game exit. Import them
with `uv run wow-auctions import-addon --saved-variables ...` to store mailbox
sale/expiry/cancel outcomes, gold snapshots, dedupe counts, and the raw addon
rows in SQLite. The importer remains compatible with older addon files that
contain owned-auction or purchase rows.

## Project Layout

- `auction/`: auction listing parsing and summary models.
- `clients/`: external API clients, currently Blizzard.
- `features/dashboard/`: local dashboard server and API.
- `features/recommendations/`: snapshot-derived recommendation scoring.
- `features/scheduler/`: repeated snapshot runner.
- `features/snapshots.py`: fetch-and-store workflow.
- `storage/`: SQLAlchemy models and repository code.
- `addons/WowAuctionTracker/`: optional in-game addon for player auction data.

## Snapshot Cadence

Choose a snapshot interval based on the signal you want:

| Interval | Pros | Cons |
| --- | --- | --- |
| 5-10 minutes | Best for detecting fast listing churn and short-lived undercuts. | More API calls, larger database, noisier inference, and more sensitivity to cancel/repost behavior. |
| 15-30 minutes | Good balance for commodity price history and demand estimates. | Can miss very fast flips or brief price spikes. |
| 60 minutes | Lower storage and API usage; useful for broad trend tracking. | Weaker sell-through inference because many listing changes happen between snapshots. |
| 4-24 hours | Good for long-term market history and daily summaries. | Poor for lifecycle inference; disappearing listings are much harder to interpret. |

The built-in `schedule` command is simple and works well while the terminal or
host process stays alive. For unattended collection, use an external scheduler
such as cron, systemd timers, launchd, or a container scheduler to run
`uv run wow-auctions fetch` at the desired cadence. External schedulers are more
resilient across reboots and crashes, while the built-in scheduler is easier to
start and stop manually.

## Configuration

The local config file is `config/items.yaml` and is intentionally ignored by git.
Use `config/items.example.yaml` as the template.

```yaml
region: us
locale: en_US
connected_realm_id: 3683
items:
  - id: 210930
    name: Bismuth
    market: commodity
  - id: 210933
    name: Aqirite
    market: commodity
  - id: 210796
    name: Mycobloom
    market: commodity
  - id: 219947
    name: Storm Dust
    market: commodity
recipes:
  - id: refine-bismuth-r2
    name: Refine Bismuth
    output:
      item_id: 210931
      name: Bismuth
      market: commodity
      quantity: 1
    ingredients:
      - item_id: 210930
        name: Bismuth
        market: commodity
        quantity: 5
```

The example config tracks a starter set of The War Within reagent commodities
and uses US Dalaran's connected realm ID, `3683`. Use `market: commodity` for
regional commodities and `market: realm` for connected-realm auctions. The
official Blizzard auction APIs return current listings, not completed sales or
buyer counts, so future recommendations will infer demand from repeated
snapshots.

## Tests

Run the test suite:

```bash
uv run --extra dev pytest
```
