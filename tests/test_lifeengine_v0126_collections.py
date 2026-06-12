import tempfile

import pytest

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime, format_result
from lifeengine.cli import slash_life


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v0126_")
    monkeypatch.setenv("HERMES_HOME", d)
    yield d


def test_version_schema_and_collection_tables():
    assert PLUGIN_VERSION == "0.13.0"
    assert _SCHEMA_VERSION >= 43
    rt = LifeEngineRuntime()
    try:
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "item_collections" in tables
        assert "collection_items" in tables
        assert "collection_item_assets" in tables
        assert "outfit_plans" in tables
    finally:
        rt.close()


def test_default_collections_are_editable_and_custom_collection_can_be_created():
    rt = LifeEngineRuntime()
    try:
        out = rt.collection("init")
        assert out["ok"] is True
        types = {c["collection_type"] for c in out["collections"]}
        assert {"wardrobe", "shoe_cabinet", "sock_drawer", "accessory_cabinet", "vanity"}.issubset(types)
        custom = rt.collection("create_collection", collection_type="weapon_cabinet", name="武器柜", image_generation_rule={"views":["front_view","side_view","detail_view"]})
        assert custom["collection"]["collection_type"] == "weapon_cabinet"
        updated = rt.collection("update_collection", collection_type="weapon_cabinet", name="备用武器柜")
        assert updated["collection"]["name"] == "备用武器柜"
    finally:
        rt.close()


def test_add_wardrobe_item_creates_pending_asset_generation_jobs():
    rt = LifeEngineRuntime()
    try:
        rt.collection("init")
        out = rt.collection("add_item", collection_type="wardrobe", name="白色短上衣", description="轻薄棉混纺短上衣", material_spec={"material":"cotton blend"})
        assert out["ok"] is True
        item = out["item"]
        assert item["name"] == "白色短上衣"
        assert item["asset_bundle"]["status"] == "needs_generation"
        assert len(item["assets"]) >= 3
        assert all(a["status"] == "pending_generation" for a in item["assets"])
        assert "不画穿在人身上" in " ".join(a.get("prompt_text") or "" for a in item["assets"])
    finally:
        rt.close()


def test_closet_outfit_uses_only_collection_items():
    rt = LifeEngineRuntime()
    try:
        rt.collection("init")
        rt.collection("add_item", collection_type="wardrobe", name="黑色长裙")
        rt.collection("add_item", collection_type="shoe_cabinet", name="黑色短靴")
        rt.collection("add_item", collection_type="sock_drawer", name="中筒黑袜")
        rt.collection("add_item", collection_type="accessory_cabinet", name="铜铃挂件")
        rt.collection("add_item", collection_type="vanity", name="双丸子头发型方案")
        out = rt.collection("outfit")
        assert out["ok"] is True
        assert "今日穿搭" in out["rendered"]
        assert len(out["outfit_plan"]["item_ids"]) >= 5
    finally:
        rt.close()


def test_slash_closet_is_human_readable():
    rendered = slash_life("closet init")
    assert "物品集合" in rendered
    assert "衣橱" in rendered
