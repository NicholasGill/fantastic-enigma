from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PlayerAuctionPost:
    observed_at: datetime | None
    snapshot_id: str | None
    reason: str | None
    character: str | None
    realm: str | None
    auction_id: int | None
    item_id: int | None
    quantity: int | None
    unit_price: int | None
    buyout: int | None
    bid_amount: int | None
    time_left_seconds: int | None
    status: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PlayerAuctionOutcome:
    observed_at: datetime | None
    character: str | None
    realm: str | None
    mail_index: int | None
    item_id: int | None
    item_name: str | None
    item_count: int | None
    outcome: str
    money: int | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class AddonImportResult:
    source_path: Path
    addon_version: int | None
    posts: list[PlayerAuctionPost]
    outcomes: list[PlayerAuctionOutcome]


def import_saved_variables(path: Path) -> AddonImportResult:
    payload = _parse_saved_variables(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SavedVariables root must be a table")

    owned_rows = _list_of_dicts(payload.get("owned_snapshots"))
    mail_rows = _list_of_dicts(payload.get("mail_events"))
    return AddonImportResult(
        source_path=path,
        addon_version=_int_or_none(payload.get("version")),
        posts=[_post_from_row(row) for row in owned_rows],
        outcomes=[_outcome_from_row(row) for row in mail_rows],
    )


def _post_from_row(row: dict[str, Any]) -> PlayerAuctionPost:
    return PlayerAuctionPost(
        observed_at=_datetime_from_epoch(row.get("observed_at")),
        snapshot_id=_str_or_none(row.get("snapshot_id")),
        reason=_str_or_none(row.get("reason")),
        character=_str_or_none(row.get("character")),
        realm=_str_or_none(row.get("realm")),
        auction_id=_int_or_none(row.get("auction_id")),
        item_id=_int_or_none(row.get("item_id")),
        quantity=_int_or_none(row.get("quantity")),
        unit_price=_int_or_none(row.get("unit_price")),
        buyout=_int_or_none(row.get("buyout")),
        bid_amount=_int_or_none(row.get("bid_amount")),
        time_left_seconds=_int_or_none(row.get("time_left_seconds")),
        status=_str_or_none(row.get("status")),
        raw=row,
    )


def _outcome_from_row(row: dict[str, Any]) -> PlayerAuctionOutcome:
    return PlayerAuctionOutcome(
        observed_at=_datetime_from_epoch(row.get("observed_at")),
        character=_str_or_none(row.get("character")),
        realm=_str_or_none(row.get("realm")),
        mail_index=_int_or_none(row.get("mail_index")),
        item_id=_int_or_none(row.get("first_item_id")),
        item_name=_str_or_none(row.get("first_item_name")),
        item_count=_int_or_none(row.get("first_item_count")) or _int_or_none(row.get("item_count")),
        outcome=_str_or_none(row.get("outcome")) or "unknown",
        money=_int_or_none(row.get("money")),
        raw=row,
    )


def _parse_saved_variables(text: str) -> Any:
    match = re.search(r"WowAuctionTrackerDB\s*=\s*", text)
    if match is None:
        raise ValueError("WowAuctionTrackerDB assignment not found")
    parser = _LuaTableParser(text[match.end():])
    return parser.parse_value()


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _datetime_from_epoch(value: Any) -> datetime | None:
    parsed = _int_or_none(value)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed, UTC)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


class _LuaTableParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def parse_value(self) -> Any:
        self._skip_ws()
        char = self._peek()
        if char == "{":
            return self._parse_table()
        if char in {'"', "'"}:
            return self._parse_string()
        if char == "-" or char.isdigit():
            return self._parse_number()
        name = self._parse_identifier()
        if name == "true":
            return True
        if name == "false":
            return False
        if name == "nil":
            return None
        return name

    def _parse_table(self) -> dict[str, Any] | list[Any]:
        self._expect("{")
        array_values: list[Any] = []
        keyed_values: dict[str, Any] = {}
        next_index = 1
        while True:
            self._skip_ws()
            if self._peek() == "}":
                self.index += 1
                break

            key: str | int | None = None
            if self._peek() == "[":
                self.index += 1
                raw_key = self.parse_value()
                self._skip_ws()
                self._expect("]")
                self._skip_ws()
                self._expect("=")
                key = raw_key
            else:
                start = self.index
                if self._peek().isalpha() or self._peek() == "_":
                    identifier = self._parse_identifier()
                    self._skip_ws()
                    if self._peek() == "=":
                        self.index += 1
                        key = identifier
                    else:
                        self.index = start

            value = self.parse_value()
            if key is None:
                array_values.append(value)
                next_index += 1
            elif isinstance(key, int):
                keyed_values[str(key)] = value
                next_index = max(next_index, key + 1)
            else:
                keyed_values[str(key)] = value

            self._skip_ws()
            if self._peek() in {",", ";"}:
                self.index += 1

        if keyed_values:
            for offset, value in enumerate(array_values, start=1):
                keyed_values[str(offset)] = value
            return keyed_values
        return array_values

    def _parse_string(self) -> str:
        quote = self._peek()
        self.index += 1
        chars: list[str] = []
        while self.index < len(self.text):
            char = self.text[self.index]
            self.index += 1
            if char == quote:
                return "".join(chars)
            if char == "\\" and self.index < len(self.text):
                escaped = self.text[self.index]
                self.index += 1
                chars.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
            else:
                chars.append(char)
        raise ValueError("unterminated string")

    def _parse_number(self) -> int | float:
        start = self.index
        if self._peek() == "-":
            self.index += 1
        while self._peek().isdigit():
            self.index += 1
        if self._peek() == ".":
            self.index += 1
            while self._peek().isdigit():
                self.index += 1
            return float(self.text[start:self.index])
        return int(self.text[start:self.index])

    def _parse_identifier(self) -> str:
        start = self.index
        while self._peek().isalnum() or self._peek() == "_":
            self.index += 1
        if self.index == start:
            raise ValueError(f"unexpected character {self._peek()!r}")
        return self.text[start:self.index]

    def _skip_ws(self) -> None:
        while True:
            while self._peek().isspace():
                self.index += 1
            if self.text[self.index:self.index + 2] == "--":
                while self.index < len(self.text) and self.text[self.index] != "\n":
                    self.index += 1
                continue
            return

    def _peek(self) -> str:
        if self.index >= len(self.text):
            return ""
        return self.text[self.index]

    def _expect(self, expected: str) -> None:
        self._skip_ws()
        if self._peek() != expected:
            raise ValueError(f"expected {expected!r}, got {self._peek()!r}")
        self.index += 1
