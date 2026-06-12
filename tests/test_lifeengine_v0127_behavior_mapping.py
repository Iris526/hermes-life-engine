import tempfile

import pytest

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.cli import slash_life


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v0127_")
    monkeypatch.setenv("HERMES_HOME", d)
    yield d


def test_version_schema_and_behavior_tables():
    assert PLUGIN_VERSION == "0.12.9"
    assert _SCHEMA_VERSION == 44
    rt = LifeEngineRuntime()
    try:
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "behavior_mappings" in tables
        assert "behavior_mapping_sources" in tables
        assert "behavior_mapping_runs" in tables
    finally:
        rt.close()


def test_default_shopping_mapping_resolves_without_exposing_sources():
    rt = LifeEngineRuntime()
    try:
        rt.behavior("init")
        out = rt.behavior("resolve", behavior_key="shopping_clothes", context={"style_tags": "灵异日常", "season": "春"})
        assert out["ok"] is True
        assert "逛街买衣服" in out["rendered"]
        assert "淘宝" not in out["rendered"]
        assert "品牌官网" not in out["rendered"]
        assert "时尚期刊" not in out["rendered"]
        private = rt.behavior("resolve", behavior_key="shopping_clothes", include_private=True)
        assert private["private_execution_plan"]
    finally:
        rt.close()


def test_custom_behavior_mapping_can_be_changed_and_sources_added():
    rt = LifeEngineRuntime()
    try:
        m = rt.behavior("create", behavior_key="weapon_practice", public_label="去练一会儿铃剑", description="武器练习映射")
        assert m["mapping"]["public_label"] == "去练一会儿铃剑"
        src = rt.behavior("add_source", behavior_key="weapon_practice", name="训练手册", source_type="manual", metadata={"source_key":"training_manual"})
        assert src["source"]["source_key"] == "training_manual"
        resolved = rt.behavior("resolve", behavior_key="weapon_practice")
        assert "去练一会儿铃剑" in resolved["rendered"]
        assert "训练手册" not in resolved["rendered"]
    finally:
        rt.close()


def test_life_interface_behavior_domain():
    rt = LifeEngineRuntime()
    try:
        out = rt.interface("write", domain="behavior", intent="init")
        assert out["ok"] is True
        read = rt.interface("read", domain="behavior", view="summary")
        assert "行为映射" in read["rendered"]
        assert "淘宝" not in read["rendered"]
    finally:
        rt.close()


def test_slash_behavior_is_human_readable_and_hides_sources():
    rendered = slash_life("behavior init")
    assert "行为映射" in rendered
    assert "用户口径" in rendered
    assert "淘宝" not in rendered
    assert "品牌官网" not in rendered
