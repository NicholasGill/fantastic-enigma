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
- `docs/` for design notes and longer contributor documentation.

Keep generated outputs, local caches, dependency folders, and secrets out of version control.

## Build, Test, and Development Commands

Use `uv` for dependency management and command execution.

- `uv sync --extra dev`: install runtime and test dependencies.
- `uv run wow-auctions init-db`: initialize SQLite tables.
- `uv run wow-auctions fetch`: fetch one configured auction snapshot.
- `uv run --extra dev pytest`: run the full test suite.

Before opening a pull request, run every command that applies to the files you changed.

## Coding Style & Naming Conventions

Follow Python conventions with 4-space indentation, descriptive module names,
type hints for public boundaries, and small functions.

Use lowercase, hyphenated documentation names, for example `docs/api-notes.md`.
Use `PascalCase` for classes and `snake_case` for Python variables, functions,
and modules.

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
