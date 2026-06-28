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

By default, data is written to `data/auction_tracker.sqlite3`. Override it with:

```bash
DATABASE_URL="sqlite:///data/dev.sqlite3" uv run wow-auctions fetch
```

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
