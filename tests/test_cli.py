import csv
from pathlib import Path

import pytest

from wow_auction_tracker.auction import AuctionListing, calculate_item_history_metrics, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.cli import build_parser
from wow_auction_tracker.cli import _database_size_label
from wow_auction_tracker.cli import _format_file_size
from wow_auction_tracker.cli import main
from wow_auction_tracker.features.crafting import CraftOpportunityObservation
from wow_auction_tracker.features.lifecycle import build_listing_observations
from wow_auction_tracker.features.market_data import api_path_for_market, write_raw_auction_snapshot
from wow_auction_tracker.features.metadata import ItemMetadata
from wow_auction_tracker.storage import AuctionRepository, create_db_engine, init_db


def test_init_db_command_creates_sqlite_database(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "init-db"])

    assert exit_code == 0
    assert db_path.exists()


def test_schedule_command_requires_positive_interval() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["schedule", "--interval-minutes", "0"])


def test_dashboard_command_accepts_dev_mode() -> None:
    parser = build_parser()

    args = parser.parse_args(["dashboard", "--dev-mode", "--reload"])

    assert args.command == "dashboard"
    assert args.dev_mode is True
    assert args.reload is True


def test_database_size_label_reports_sqlite_file_size(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    db_path.write_bytes(b"x" * 2048)

    assert _database_size_label(f"sqlite:///{db_path}") == "2.0 KiB"


def test_db_stats_command_prints_table_counts(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(1, 210930, Market.COMMODITY, 2, 100, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
    ]
    repository.complete_fetch_run(run_id, listings, summarize_listings(listings))

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "db", "stats"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Successful fetch runs: 1" in captured
    assert "auction_listings: 1" in captured


def test_db_prune_raw_listings_preserves_summary_rows(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(1, 210930, Market.COMMODITY, 2, 100, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
    ]
    repository.complete_fetch_run(run_id, listings, summarize_listings(listings))

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "db", "prune-raw-listings", "--before-days", "0"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Deleted 1 raw auction listings" in captured
    assert repository.database_stats()["table_counts"]["auction_listings"] == 0
    assert repository.database_stats()["table_counts"]["item_summaries"] == 1


def test_format_file_size_uses_binary_units() -> None:
    assert _format_file_size(12) == "12 B"
    assert _format_file_size(1024 * 1024) == "1.0 MiB"


def test_report_latest_command_prints_summary_rows(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    repository.upsert_item_metadata(
        [
            ItemMetadata(
                item_id=210930,
                name="Bismuth",
                quality="COMMON",
                item_class="Tradeskill",
                item_subclass="Metal & Stone",
                inventory_type="NON_EQUIP",
                item_level=70,
                required_level=1,
                purchase_price=2500,
                sell_price=500,
                max_count=0,
                is_equippable=False,
                is_stackable=True,
                icon_url=None,
            )
        ]
    )
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(1, 210930, Market.COMMODITY, 2, 100, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
    ]
    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        (),
        build_listing_observations(listings, []),
    )

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "report", "latest", "--limit", "1"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Bismuth" in captured
    assert "Item ID" in captured


def test_report_item_command_prints_history_rows(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(1, 210930, Market.COMMODITY, 2, 100, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
    ]
    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        (),
        build_listing_observations(listings, []),
    )

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "report", "item", "--item-id", "210930"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Run ID" in captured
    assert "210930" in captured


def test_report_crafts_command_prints_craft_signals(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    _store_craft_signal(db_path)

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "report", "crafts", "--limit", "1"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Recipe" in captured
    assert "Refine Bismuth" in captured
    assert "Bismuth" in captured


def test_report_anomalies_command_prints_recent_anomalies(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]})

    for price in (100, 100, 100, 250):
        run_id = repository.start_fetch_run(config)
        listings = [
            AuctionListing(run_id, 210930, Market.COMMODITY, 10, price, None, None, "LONG", {"id": run_id})
        ]
        repository.complete_fetch_run(
            run_id,
            listings,
            summarize_listings(listings),
            calculate_item_history_metrics(listings),
        )

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "report", "anomalies", "--limit", "1"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "price_spike" in captured
    assert "210930" in captured


def test_report_quality_command_prints_recent_quality_events(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    repository.fail_fetch_run(run_id, "api unavailable")

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "report", "quality", "--limit", "1"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "api_failure" in captured
    assert "api unavailable" in captured


def test_replay_raw_command_rebuilds_derived_rows(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    config_path = tmp_path / "items.yaml"
    config_path.write_text(
        """
items:
  - id: 210930
    name: Bismuth
    market: commodity
""",
        encoding="utf-8",
    )
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    raw_snapshot, _payload = write_raw_auction_snapshot(
        {"auctions": [{"id": 99, "item": {"id": 210930}, "quantity": 5, "unit_price": 250}]},
        root_dir=tmp_path / "raw",
        fetch_run_id=run_id,
        region="us",
        locale="en_US",
        namespace="dynamic-us",
        market=Market.COMMODITY,
        connected_realm_id=None,
        api_path=api_path_for_market(Market.COMMODITY, None),
    )
    repository.store_raw_auction_snapshot(raw_snapshot)
    stale_listing = [
        AuctionListing(1, 210930, Market.COMMODITY, 1, 100, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
    ]
    repository.complete_fetch_run(run_id, stale_listing, summarize_listings(stale_listing))

    exit_code = main([
        "--config",
        str(config_path),
        "--database-url",
        f"sqlite:///{db_path}",
        "replay-raw",
        "--from-run-id",
        str(run_id),
        "--to-run-id",
        str(run_id),
    ])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "Replayed 1 raw fetch run" in captured
    assert repository.list_auction_listings(run_id)[0].auction_id == 99


def test_export_latest_command_writes_csv(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    output_path = tmp_path / "latest.csv"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    repository.upsert_item_metadata(
        [
            ItemMetadata(
                item_id=210930,
                name="Bismuth",
                quality="COMMON",
                item_class="Tradeskill",
                item_subclass="Metal & Stone",
                inventory_type="NON_EQUIP",
                item_level=70,
                required_level=1,
                purchase_price=2500,
                sell_price=500,
                max_count=0,
                is_equippable=False,
                is_stackable=True,
                icon_url="https://example.test/icon.jpg",
            )
        ]
    )
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(1, 210930, Market.COMMODITY, 2, 100, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
    ]
    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        (),
        build_listing_observations(listings, []),
    )

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "export", "latest", "--output", str(output_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == ""
    rows = list(csv.DictReader(output_path.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 1
    assert rows[0]["item_id"] == "210930"
    assert rows[0]["name"] == "Bismuth"
    assert rows[0]["recommendation_action"] == "watch"
    assert rows[0]["recommended_buy_price"] == ""
    assert rows[0]["recommended_sell_price"] == ""
    assert rows[0]["recommended_buy_unit_price"] == ""
    assert rows[0]["recommended_sell_unit_price"] == ""
    assert rows[0]["recommended_sell_price_source"] == ""


def test_export_item_command_writes_csv_file(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    output_path = tmp_path / "item.csv"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(1, 210930, Market.COMMODITY, 2, 100, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
    ]
    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        (),
        build_listing_observations(listings, []),
    )

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "export", "item", "--item-id", "210930", "--output", str(output_path)])

    rows = list(csv.DictReader(output_path.read_text(encoding="utf-8").splitlines()))
    assert exit_code == 0
    assert len(rows) == 1
    assert rows[0]["item_id"] == "210930"
    assert rows[0]["fetch_run_id"] == str(run_id)
    assert rows[0]["listing_count"] == "1"


def test_export_recommendations_command_writes_csv_stdout(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    listings = [
        AuctionListing(1, 210930, Market.COMMODITY, 2, 100, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
    ]
    repository.complete_fetch_run(
        run_id,
        listings,
        summarize_listings(listings),
        (),
        build_listing_observations(listings, []),
    )

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "export", "recommendations", "--limit", "1"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    rows = list(csv.DictReader(captured.splitlines()))
    assert len(rows) == 1
    assert rows[0]["item_id"] == "210930"
    assert rows[0]["action"] == "watch"
    assert rows[0]["recommended_buy_price"] == ""
    assert rows[0]["recommended_sell_price"] == ""
    assert rows[0]["recommended_buy_unit_price"] == ""
    assert rows[0]["recommended_sell_unit_price"] == ""
    assert rows[0]["recommended_sell_price_source"] == ""


def test_export_crafts_command_writes_csv_stdout(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    _store_craft_signal(db_path)

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "export", "crafts"])
    captured = capsys.readouterr().out

    assert exit_code == 0
    rows = list(csv.DictReader(captured.splitlines()))
    assert len(rows) == 1
    assert rows[0]["recipe_id"] == "refine-bismuth"
    assert rows[0]["output_item_id"] == "210931"
    assert rows[0]["expected_profit"] == "420"
    assert rows[0]["reasons"] == "profitable craft"


def test_recompute_inference_command_updates_probable_sales(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    config = TrackerConfig.model_validate({"items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}]})

    for quantity in (10, 6):
        run_id = repository.start_fetch_run(config)
        listings = [
            AuctionListing(1, 210930, Market.COMMODITY, quantity, 250, None, None, "LONG", {"id": 1, "item": {"id": 210930}})
        ]
        repository.complete_fetch_run(run_id, listings, summarize_listings(listings))

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "recompute-inference"])
    captured = capsys.readouterr().out
    recommendation = main(["--database-url", f"sqlite:///{db_path}", "export", "recommendations", "--limit", "1"])
    rows = list(csv.DictReader(capsys.readouterr().out.splitlines()))

    assert exit_code == 0
    assert "Recomputed inference" in captured
    assert recommendation == 0
    assert rows[0]["recommended_sell_unit_price"] == "250"
    assert rows[0]["recommended_sell_price_source"] == "probable_sold"


def test_export_latest_command_writes_headers_for_empty_results(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    output_path = tmp_path / "latest.csv"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "export", "latest", "--output", str(output_path)])

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").startswith("item_id,name,market")
    assert capsys.readouterr().out == ""


def test_import_addon_command_stores_saved_variables(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    saved_variables = tmp_path / "WowAuctionTracker.lua"
    saved_variables.write_text(
        """
        WowAuctionTrackerDB = {
          ["version"] = 1,
          ["owned_snapshots"] = {
            { ["auction_id"] = 42, ["item_id"] = 210930, ["quantity"] = 5, ["unit_price"] = 10000 },
          },
          ["mail_events"] = {
            { ["outcome"] = "expired", ["first_item_id"] = 210930, ["first_item_count"] = 5 },
          },
          ["purchase_events"] = {
            {
              ["event_type"] = "commodity_purchase_succeeded",
              ["item_id"] = 210930,
              ["quantity"] = 5,
              ["unit_price"] = 10000,
              ["total_price"] = 50000,
            },
          },
        }
        """,
        encoding="utf-8",
    )

    exit_code = main([
        "--database-url",
        f"sqlite:///{db_path}",
        "import-addon",
        "--saved-variables",
        str(saved_variables),
    ])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "1 owned auction rows, 1 mail rows, 1 purchase rows" in captured
    assert "3 inserted" in captured


def test_player_report_and_export_commands_use_imported_outcomes(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"
    saved_variables = tmp_path / "WowAuctionTracker.lua"
    saved_variables.write_text(
        """
        WowAuctionTrackerDB = {
          ["version"] = 1,
          ["owned_snapshots"] = {
            {
              ["observed_at"] = 1710000000,
              ["snapshot_id"] = "Alice-1710000000",
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["auction_id"] = 42,
              ["item_id"] = 210930,
              ["quantity"] = 5,
              ["unit_price"] = 10000,
            },
          },
          ["mail_events"] = {
            {
              ["observed_at"] = 1710000300,
              ["character"] = "Alice",
              ["realm"] = "Dalaran",
              ["mail_index"] = 1,
              ["outcome"] = "sold",
              ["money"] = 45000,
              ["first_item_name"] = "Bismuth",
              ["first_item_id"] = 210930,
              ["first_item_count"] = 5,
            },
          },
        }
        """,
        encoding="utf-8",
    )

    assert main([
        "--database-url",
        f"sqlite:///{db_path}",
        "import-addon",
        "--saved-variables",
        str(saved_variables),
    ]) == 0
    capsys.readouterr()

    assert main(["--database-url", f"sqlite:///{db_path}", "report", "player", "--limit", "1"]) == 0
    report_output = capsys.readouterr().out
    assert "Bismuth" in report_output
    assert "Alice" in report_output

    csv_path = tmp_path / "player-performance.csv"
    assert main([
        "--database-url",
        f"sqlite:///{db_path}",
        "export",
        "player-performance",
        "--output",
        str(csv_path),
    ]) == 0
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    assert rows[0]["name"] == "Bismuth"
    assert rows[0]["sold_count"] == "1"


def _store_craft_signal(db_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    repository = AuctionRepository(engine)
    repository.upsert_item_metadata(
        [
            ItemMetadata(
                item_id=210931,
                name="Bismuth",
                quality="COMMON",
                item_class="Tradeskill",
                item_subclass="Metal & Stone",
                inventory_type="NON_EQUIP",
                item_level=70,
                required_level=1,
                purchase_price=2500,
                sell_price=500,
                max_count=0,
                is_equippable=False,
                is_stackable=True,
                icon_url=None,
            )
        ]
    )
    config = TrackerConfig.model_validate({"items": [{"id": 210931, "name": "Bismuth", "market": "commodity"}]})
    run_id = repository.start_fetch_run(config)
    repository.complete_fetch_run(
        run_id,
        [],
        [],
        craft_opportunity_observations=[
            CraftOpportunityObservation(
                recipe_id="refine-bismuth",
                recipe_name="Refine Bismuth",
                output_item_id=210931,
                output_market="commodity",
                output_quantity=1,
                craft_cost=560,
                craft_cost_unit_price=560,
                output_min_unit_price=900,
                sell_target_unit_price=1000,
                auction_deposit_unit_price=20,
                ah_savings=340,
                expected_profit=420,
                max_craft_quantity=2,
                confidence=80,
                reasons=["profitable craft"],
            )
        ],
    )
