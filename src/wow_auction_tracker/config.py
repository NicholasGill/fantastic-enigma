from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Market(StrEnum):
    REALM = "realm"
    COMMODITY = "commodity"


class TrackedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)
    name: str | None = None
    market: Market


class TrackerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str = "us"
    locale: str = "en_US"
    connected_realm_id: int | None = Field(default=None, gt=0)
    items: list[TrackedItem] = Field(min_length=1)

    @field_validator("region")
    @classmethod
    def normalize_region(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("region is required")
        return value

    @field_validator("locale")
    @classmethod
    def validate_locale(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("locale is required")
        return value

    @model_validator(mode="after")
    def validate_items(self) -> TrackerConfig:
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("tracked item ids must be unique")

        if any(item.market == Market.REALM for item in self.items) and self.connected_realm_id is None:
            raise ValueError("connected_realm_id is required for realm items")

        return self

    @property
    def commodity_item_ids(self) -> set[int]:
        return {item.id for item in self.items if item.market == Market.COMMODITY}

    @property
    def realm_item_ids(self) -> set[int]:
        return {item.id for item in self.items if item.market == Market.REALM}


def load_config(path: Path) -> TrackerConfig:
    with path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file)

    if raw_config is None:
        raise ValueError(f"{path} is empty")

    return TrackerConfig.model_validate(raw_config)
