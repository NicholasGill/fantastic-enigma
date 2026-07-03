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


def test_recipe_refs_are_effective_tracked_items() -> None:
    config = TrackerConfig.model_validate(
        {
            "items": [{"id": 210930, "name": "Bismuth", "market": "commodity"}],
            "recipes": [
                {
                    "id": "refine-bismuth",
                    "name": "Refine Bismuth",
                    "output": {"item_id": 210931, "name": "Bismuth", "market": "commodity", "quantity": 1},
                    "ingredients": [
                        {"item_id": 210930, "name": "Bismuth", "market": "commodity", "quantity": 5}
                    ],
                }
            ],
        }
    )

    assert config.commodity_item_ids == {210930, 210931}
    assert [item.id for item in config.all_tracked_items] == [210930, 210931]


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


def test_recipe_refs_reject_conflicting_markets() -> None:
    with pytest.raises(ValidationError, match="item 210930 cannot use multiple markets"):
        TrackerConfig.model_validate(
            {
                "connected_realm_id": 3683,
                "items": [{"id": 210930, "market": "commodity"}],
                "recipes": [
                    {
                        "id": "bad",
                        "output": {"item_id": 210931, "market": "commodity", "quantity": 1},
                        "ingredients": [{"item_id": 210930, "market": "realm", "quantity": 1}],
                    }
                ],
            }
        )
