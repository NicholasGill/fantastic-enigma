from wow_auction_tracker.features.metadata import parse_item_metadata


def test_parse_item_metadata_extracts_item_and_icon_fields() -> None:
    item_payload = {
        "id": 210930,
        "name": "Bismuth",
        "quality": {"type": "COMMON", "name": "Common"},
        "item_class": {"name": "Tradeskill"},
        "item_subclass": {"name": "Metal & Stone"},
        "inventory_type": {"type": "NON_EQUIP"},
        "level": 70,
        "required_level": 1,
        "purchase_price": 2500,
        "sell_price": 500,
        "max_count": 0,
        "is_equippable": False,
        "is_stackable": True,
    }
    media_payload = {
        "assets": [
            {"key": "icon", "value": "https://render.worldofwarcraft.com/us/icons/56/inv_ore_bismuth.jpg"}
        ]
    }

    metadata = parse_item_metadata(item_payload, media_payload)

    assert metadata.item_id == 210930
    assert metadata.name == "Bismuth"
    assert metadata.quality == "COMMON"
    assert metadata.item_class == "Tradeskill"
    assert metadata.item_subclass == "Metal & Stone"
    assert metadata.is_stackable is True
    assert metadata.icon_url == "https://render.worldofwarcraft.com/us/icons/56/inv_ore_bismuth.jpg"
