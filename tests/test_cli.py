from pathlib import Path

import pytest

from wow_auction_tracker.cli import build_parser
from wow_auction_tracker.cli import main


def test_init_db_command_creates_sqlite_database(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "init-db"])

    assert exit_code == 0
    assert db_path.exists()


def test_schedule_command_requires_positive_interval() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["schedule", "--interval-minutes", "0"])
