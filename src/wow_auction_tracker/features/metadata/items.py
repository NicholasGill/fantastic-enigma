from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ItemMetadata:
    item_id: int
    name: str
    quality: str | None
    item_class: str | None
    item_subclass: str | None
    inventory_type: str | None
    item_level: int | None
    required_level: int | None
    purchase_price: int | None
    sell_price: int | None
    max_count: int | None
    is_equippable: bool | None
    is_stackable: bool | None
    icon_url: str | None


def parse_item_metadata(item_payload: dict[str, Any], media_payload: dict[str, Any] | None = None) -> ItemMetadata:
    item_id = int(item_payload["id"])
    return ItemMetadata(
        item_id=item_id,
        name=str(item_payload.get("name") or f"Item {item_id}"),
        quality=_nested_value(item_payload, "quality", "type"),
        item_class=_nested_value(item_payload, "item_class", "name"),
        item_subclass=_nested_value(item_payload, "item_subclass", "name"),
        inventory_type=_nested_value(item_payload, "inventory_type", "type"),
        item_level=_optional_int(item_payload.get("level")),
        required_level=_optional_int(item_payload.get("required_level")),
        purchase_price=_optional_int(item_payload.get("purchase_price")),
        sell_price=_optional_int(item_payload.get("sell_price")),
        max_count=_optional_int(item_payload.get("max_count")),
        is_equippable=_optional_bool(item_payload.get("is_equippable")),
        is_stackable=_optional_bool(item_payload.get("is_stackable")),
        icon_url=_icon_url(media_payload),
    )


def _nested_value(payload: dict[str, Any], *keys: str) -> str | None:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _icon_url(media_payload: dict[str, Any] | None) -> str | None:
    if not media_payload:
        return None
    assets = media_payload.get("assets", [])
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and asset.get("key") == "icon" and asset.get("value"):
            return str(asset["value"])
    return None
