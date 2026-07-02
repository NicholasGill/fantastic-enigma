import csv
from pathlib import Path

import pytest

from wow_auction_tracker.auction import AuctionListing, summarize_listings
from wow_auction_tracker.config import Market, TrackerConfig
from wow_auction_tracker.cli import build_parser
from wow_auction_tracker.cli import _database_size_label
from wow_auction_tracker.cli import _format_file_size
from wow_auction_tracker.cli import main
from wow_auction_tracker.features.lifecycle import build_listing_observations
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
    assert "1 owned auction rows, 1 mail rows" in captured
