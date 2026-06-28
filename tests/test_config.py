from pathlib import Path

import pytest
from pydantic import ValidationError

from wow_auction_tracker.config import Market, TrackerConfig, load_config


def test_load_config_parses_items(tmp_path: Path) -> None:
    config_path = tmp_path / "items.yaml"
    config_path.write_text(
        """
region: US
locale: en_US
connected_realm_id: 3678
items:
  - id: 124105
    name: Starlight Rose
    market: commodity
  - id: 19019
    market: realm
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.region == "us"
    assert config.connected_realm_id == 3678
    assert config.commodity_item_ids == {124105}
    assert config.realm_item_ids == {19019}
    assert config.items[0].market == Market.COMMODITY


def test_rejects_duplicate_item_ids() -> None:
    with pytest.raises(ValidationError, match="tracked item ids must be unique"):
        TrackerConfig.model_validate(
            {
                "items": [
                    {"id": 1, "market": "commodity"},
                    {"id": 1, "market": "realm"},
                ],
                "connected_realm_id": 1,
            }
        )


def test_realm_items_require_connected_realm_id() -> None:
    with pytest.raises(ValidationError, match="connected_realm_id is required"):
        TrackerConfig.model_validate({"items": [{"id": 19019, "market": "realm"}]})
