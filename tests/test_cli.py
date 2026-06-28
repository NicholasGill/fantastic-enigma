from pathlib import Path

from wow_auction_tracker.cli import main


def test_init_db_command_creates_sqlite_database(tmp_path: Path) -> None:
    db_path = tmp_path / "auction_tracker.sqlite3"

    exit_code = main(["--database-url", f"sqlite:///{db_path}", "init-db"])

    assert exit_code == 0
    assert db_path.exists()
