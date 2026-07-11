# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python 3.12 project managed with `uv`. The current
application code lives in `src/wow_auction_tracker`, tests live in `tests`, and
auction item configuration lives under `config`.

- `src/` for application code.
- `tests/` for automated tests, mirroring `src/` where practical.
- `config/items.example.yaml` for the committed starter item map.
- `config/items.yaml` for the ignored local item map used by commands.
- `assets/` for static data, images, or sample auction inputs.
- `addons/` for optional in-game companion addons.
- `docs/` for design notes and longer contributor documentation.

Keep generated outputs, local caches, dependency folders, and secrets out of version control.

## Build, Test, and Development Commands

Use `uv` for dependency management and command execution.

- `uv sync --extra dev`: install runtime and test dependencies.
- `uv run wow-auctions init-db`: initialize SQLite tables.
- `uv run wow-auctions fetch`: fetch one configured auction snapshot.
- `uv run wow-auctions dashboard`: start the local dashboard at
  `http://127.0.0.1:8000`.
- `uv run wow-auctions recommend --limit 10`: show snapshot-derived item
  recommendations.
- `uv run wow-auctions report latest --limit 10`: print the latest item
  summaries.
- `uv run wow-auctions report item --item-id 210930`: print one item's snapshot
  history.
- `uv run wow-auctions export latest --output latest.csv`: export the latest
  item summaries to CSV.
- `uv run wow-auctions schedule --interval-minutes 30`: fetch snapshots on a
  fixed interval.
- `uv run --extra dev pytest`: run the full test suite.

Before opening a pull request, run every command that applies to the files you changed.

Sell-through metrics are inferred from disappeared listings between snapshots.
Treat them as estimated demand signals, not confirmed sales.

Price presentation should prefer first quartile, median, and third quartile
fields when available so extreme listings do not dominate charts.

Recommended sell price should stay conservative: prefer recent average first
quartile pricing and fall back to median pricing when quartiles are unavailable.

The companion addon under `addons/WowAuctionTracker` records SavedVariables only.
Keep it minimal and avoid dependencies on third-party addon internals.

## Coding Style & Naming Conventions

Follow Python conventions with 4-space indentation, descriptive module names,
type hints for public boundaries, and small functions.

Use lowercase, hyphenated documentation names, for example `docs/api-notes.md`.
Use `PascalCase` for classes and `snake_case` for Python variables, functions,
and modules.

Keep `PLAN.md` focused on future work. When a planned feature is completed,
move its completed checklist entry and relevant notes from `PLAN.md` to
`completed.md` instead of leaving checked-off work in the active plan.

Keep feature-specific code grouped under `src/wow_auction_tracker/features/`.
Use `auction/` for auction parsing and summary models, `clients/` for external
API clients, and `storage/` for database models/repositories.

## Testing Guidelines

Add tests with the first meaningful application behavior. Test files should identify the unit or workflow under test, such as `tests/auction_parser.test.ts` or `tests/test_auction_parser.py`. Keep tests deterministic; use fixtures for sample auction data instead of live external services.

For changes that touch pricing logic, parsing, persistence, or external API integration, include both normal-case and edge-case coverage.

## Commit & Pull Request Guidelines

The existing Git history only contains initial commits, so no detailed commit convention is established. Use short, imperative commit messages such as `Add auction parser fixtures` or `Document development commands`.

Pull requests should include a summary, reason for the change, verification commands, and screenshots or sample output when user-facing behavior changes. Link related issues when available.

## Security & Configuration Tips

Do not commit API keys, account identifiers, tokens, local `.env` files, local
SQLite databases, or `config/items.yaml`. Keep Blizzard credentials in
environment variables or the ignored `.env` file documented in `README.md`.

The default starter map tracks The War Within reagent commodities and uses US
Dalaran's connected realm ID, `3683`. Verify realm and item IDs against the
official Blizzard API before changing them.
