# Architecture

This project is a local auction analytics application. It collects World of
Warcraft auction-house snapshots from Blizzard APIs, imports personal auction
activity from the companion addon, stores everything in SQLite, and exposes the
results through CLI reports, CSV exports, and a Flask dashboard.

## System Context

```mermaid
flowchart LR
  Operator["User / Operator"]
  Config["config/items.yaml\ntracked items, realm, recipes"]
  Env["Environment\nBlizzard credentials, DATABASE_URL"]
  Blizzard["Blizzard API\nauth, realm auctions, commodities,\nitem metadata and media"]
  Addon["WoW Addon\naddons/WowAuctionTracker"]
  SavedVariables["WoW SavedVariables\nWowAuctionTracker.lua"]
  CLI["wow-auctions CLI\nsrc/wow_auction_tracker/cli.py"]
  Dashboard["Flask Dashboard\nfeatures/dashboard/server.py"]
  DB[("SQLite Database\ndata/auction_tracker.sqlite3")]

  Operator --> CLI
  Operator --> Dashboard
  Operator --> Addon
  Config --> CLI
  Env --> CLI
  CLI --> Blizzard
  Blizzard --> CLI
  Addon --> SavedVariables
  SavedVariables --> CLI
  SavedVariables --> Dashboard
  CLI --> DB
  Dashboard --> DB
```

## Runtime Components

```mermaid
flowchart TB
  subgraph CLI["Command Layer"]
    Commands["wow-auctions commands\ninit-db, fetch, schedule, import-addon,\nrecommend, report, export, dashboard"]
  end

  subgraph Sources["Inputs"]
    ItemConfig["config loader\nwow_auction_tracker/config.py"]
    BlizzardClient["BlizzardClient\nclients/blizzard.py"]
    AddonImporter["SavedVariables parser\nfeatures/player/addon_import.py"]
  end

  subgraph Pipeline["Snapshot Pipeline"]
    FetchStore["fetch_and_store\nfeatures/snapshots.py"]
    AuctionParsing["auction parsing and summaries\nauction/snapshots.py"]
    Metadata["item metadata\nfeatures/metadata/items.py"]
    Lifecycle["listing lifecycle\nfeatures/lifecycle/observations.py"]
    SellThrough["sell-through inference\nfeatures/sellthrough/inference.py"]
    BuySignals["buy opportunities\nfeatures/opportunities/tracking.py"]
    CraftSignals["craft opportunities\nfeatures/crafting/opportunities.py"]
  end

  subgraph Analytics["Read Models and Analytics"]
    Recommendations["RecommendationEngine\nfeatures/recommendations/engine.py"]
    DashboardStore["DashboardDataStore\nfeatures/dashboard/server.py"]
  end

  subgraph Persistence["Persistence"]
    Repository["AuctionRepository\nstorage/db.py"]
    SQLite[("SQLite via SQLAlchemy\nand sqlite3 dashboard reads")]
  end

  subgraph Outputs["Outputs"]
    Reports["CLI reports"]
    Exports["CSV exports"]
    WebUI["Dashboard web UI"]
  end

  Commands --> ItemConfig
  Commands --> BlizzardClient
  Commands --> AddonImporter
  Commands --> FetchStore
  Commands --> Recommendations
  Commands --> DashboardStore

  FetchStore --> AuctionParsing
  FetchStore --> Metadata
  FetchStore --> Lifecycle
  FetchStore --> SellThrough
  FetchStore --> BuySignals
  FetchStore --> CraftSignals

  AuctionParsing --> Repository
  Metadata --> Repository
  Lifecycle --> Repository
  SellThrough --> Repository
  BuySignals --> Repository
  CraftSignals --> Repository
  AddonImporter --> Repository

  Repository --> SQLite
  Recommendations --> Repository
  DashboardStore --> SQLite
  Reports --> DashboardStore
  Exports --> DashboardStore
  WebUI --> DashboardStore
```

## Snapshot Fetch Flow

```mermaid
sequenceDiagram
  participant User
  participant CLI as wow-auctions fetch/schedule
  participant Config as config/items.yaml
  participant Blizzard as BlizzardClient
  participant Pipeline as fetch_and_store
  participant Repo as AuctionRepository
  participant DB as SQLite

  User->>CLI: fetch or schedule
  CLI->>Config: load tracked realm, commodities, recipes
  CLI->>Blizzard: create authenticated client
  CLI->>Pipeline: fetch_and_store(config, client, repository)
  Pipeline->>Repo: start_fetch_run()
  Repo->>DB: insert fetch_runs row
  Pipeline->>Repo: missing_metadata_item_ids()
  Pipeline->>Blizzard: fetch item metadata/media
  Pipeline->>Repo: upsert_item_metadata()
  Pipeline->>Blizzard: fetch realm and commodity auctions
  Pipeline->>Pipeline: filter tracked items and summarize prices
  Pipeline->>Pipeline: derive lifecycle, sell-through, buy, and craft signals
  Pipeline->>Repo: complete_fetch_run(...)
  Repo->>DB: write listings, summaries, metrics, opportunities
  Repo->>DB: rebuild daily metrics and record anomalies
  Pipeline-->>CLI: FetchResult
```

## Addon Import Flow

```mermaid
sequenceDiagram
  participant WoW as WoW Client
  participant Addon as WowAuctionTracker Addon
  participant SV as SavedVariables file
  participant CLI as import-addon or Dashboard import
  participant Parser as import_saved_variables()
  participant Repo as AuctionRepository
  participant DB as SQLite

  WoW->>Addon: auction-house, mailbox, and purchase events
  Addon->>Addon: debounce event captures and skip duplicate session rows
  Addon->>SV: write owned_snapshots, mail_events, purchase_events
  CLI->>Parser: parse WowAuctionTracker.lua
  Parser->>Parser: normalize and dedupe rows
  Parser-->>CLI: AddonImportResult
  CLI->>Repo: import_addon_data(result)
  Repo->>DB: insert addon_imports
  Repo->>DB: insert player auction posts, outcomes, purchases
  Repo->>DB: match owned posts to mail outcomes
```

## Stored Data Groups

```mermaid
erDiagram
  FETCH_RUNS ||--o{ AUCTION_LISTINGS : contains
  FETCH_RUNS ||--o{ ITEM_SUMMARIES : summarizes
  FETCH_RUNS ||--o{ ITEM_HISTORY_METRICS : records
  FETCH_RUNS ||--o{ LISTING_OBSERVATIONS : derives
  FETCH_RUNS ||--o{ SELL_THROUGH_METRICS : derives
  FETCH_RUNS ||--o{ BUY_OPPORTUNITY_OBSERVATIONS : derives
  FETCH_RUNS ||--o{ CRAFT_OPPORTUNITY_OBSERVATIONS : derives
  FETCH_RUNS ||--o{ ITEM_ANOMALIES : detects

  ADDON_IMPORTS ||--o{ PLAYER_AUCTION_POSTS : imports
  ADDON_IMPORTS ||--o{ PLAYER_AUCTION_OUTCOMES : imports
  ADDON_IMPORTS ||--o{ PLAYER_AUCTION_PURCHASES : imports
  PLAYER_AUCTION_POSTS ||--o{ PLAYER_AUCTION_MATCHES : matched_to
  PLAYER_AUCTION_OUTCOMES ||--o{ PLAYER_AUCTION_MATCHES : matched_to

  ITEM_METADATA ||--o{ TRACKED_ITEMS : describes
  ITEM_DAILY_METRICS }o--|| TRACKED_ITEMS : aggregates
```

## Key Design Notes

- The CLI is the orchestration boundary. It wires configuration, credentials,
  database setup, fetch/import commands, reports, exports, and dashboard startup.
- `AuctionRepository` is the write-side persistence boundary for snapshot and
  addon import data. The dashboard uses direct SQLite reads through
  `DashboardDataStore` for read-optimized overview payloads.
- Snapshot-derived demand is inferred from listing disappearance between fetch
  runs, so sell-through metrics are estimates rather than confirmed sales.
- The addon remains a minimal SavedVariables recorder. It captures player-owned
  auction activity in-game and leaves matching, dedupe across imports, and
  profit/loss calculations to the Python application.
