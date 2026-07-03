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


class RecipeItemRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: int = Field(gt=0)
    name: str | None = None
    market: Market
    quantity: int = Field(gt=0)


class RecipeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    output: RecipeItemRef
    ingredients: list[RecipeItemRef] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("recipe id is required")
        return value


class TrackerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str = "us"
    locale: str = "en_US"
    connected_realm_id: int | None = Field(default=None, gt=0)
    items: list[TrackedItem] = Field(min_length=1)
    recipes: list[RecipeConfig] = Field(default_factory=list)

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

        recipe_ids = [recipe.id for recipe in self.recipes]
        if len(recipe_ids) != len(set(recipe_ids)):
            raise ValueError("recipe ids must be unique")

        markets_by_item_id: dict[int, Market] = {}
        for item in self.items:
            _validate_item_market(markets_by_item_id, item.id, item.market)
        for recipe in self.recipes:
            for ref in [recipe.output, *recipe.ingredients]:
                _validate_item_market(markets_by_item_id, ref.item_id, ref.market)

        if any(item.market == Market.REALM for item in self.all_tracked_items) and self.connected_realm_id is None:
            raise ValueError("connected_realm_id is required for realm items")

        return self

    @property
    def all_tracked_items(self) -> list[TrackedItem]:
        items_by_id: dict[int, TrackedItem] = {item.id: item for item in self.items}
        for recipe in self.recipes:
            for ref in [recipe.output, *recipe.ingredients]:
                if ref.item_id not in items_by_id:
                    items_by_id[ref.item_id] = TrackedItem(id=ref.item_id, name=ref.name, market=ref.market)
        return sorted(items_by_id.values(), key=lambda item: (item.market.value, item.id))

    @property
    def commodity_item_ids(self) -> set[int]:
        return {item.id for item in self.all_tracked_items if item.market == Market.COMMODITY}

    @property
    def realm_item_ids(self) -> set[int]:
        return {item.id for item in self.all_tracked_items if item.market == Market.REALM}


def load_config(path: Path) -> TrackerConfig:
    with path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file)

    if raw_config is None:
        raise ValueError(f"{path} is empty")

    return TrackerConfig.model_validate(raw_config)


def _validate_item_market(markets_by_item_id: dict[int, Market], item_id: int, market: Market) -> None:
    existing_market = markets_by_item_id.get(item_id)
    if existing_market is not None and existing_market != market:
        raise ValueError(f"item {item_id} cannot use multiple markets")
    markets_by_item_id[item_id] = market
