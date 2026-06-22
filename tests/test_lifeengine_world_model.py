"""结构化世界本体：地图、地点、知识条目和势力影响。

这些测试用一组样例世界观名词，但核心断言只关心结构：文字内容可以保存和展示，
是否在当前场景生效必须由 profile/region/place/lore/presence 的 key、id、scope
和 LifeOps 账本决定。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from lifeengine.db import _SCHEMA_VERSION
from lifeengine.paths import db_path
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.webui.reader import LifeEngineReader
from lifeengine.webui.server import create_app


def fresh_home(tmp_path: Path) -> Path:
    """创建隔离的 Hermes home。"""
    home = tmp_path / "hermes_home_world"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    return home


def setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化一个可写 LifeEngine agent。"""
    rt.setup("v0.18.x world model test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _result(commit: dict, index: int = 0) -> dict:
    """取 LifeOps commit 的第 index 个领域结果。"""
    return ((commit.get("results") or [])[index].get("result") or {})


def _seed_world(rt: LifeEngineRuntime) -> dict[str, dict]:
    """写入一组带结构作用域的世界本体样例。"""
    profile = _result(rt.world(
        "profile",
        key="guiming",
        title="归明观世界",
        summary="废土边城里的修补者日常。",
        background_text="世界背景可以是文字，但不会靠提示词承诺生效。",
        rules={"time_flow": "real_time", "currency": "灵铢"},
    ))
    region = _result(rt.world(
        "region",
        key="city.seventh",
        name="第七城",
        region_type="city",
        summary="雨棚巷所在的边城。",
        traits={"security": "contested"},
    ))
    place = _result(rt.world(
        "place",
        key="place.rain_shelter",
        name="雨棚巷",
        place_type="street",
        region_id=region["id"],
        summary="旧雨棚和符线节点密集的街巷。",
    ))
    other_place = _result(rt.world(
        "place",
        key="place.south_gate",
        name="南门集",
        place_type="market",
        region_id=region["id"],
        summary="另一处市场。",
    ))
    world_lore = _result(rt.world(
        "upsert_lore",
        key="lore.world.rule",
        title="城外风暴规则",
        lore_type="rule",
        scope_kind="world",
        content="世界级规则会进入所有世界场景。",
        tags=["rule"],
    ))
    place_lore = _result(rt.world(
        "upsert_lore",
        key="lore.rain_shelter.node",
        title="雨棚巷节点",
        lore_type="background",
        scope_kind="place",
        scope_id=place["id"],
        content="雨棚巷只在地点命中时生效。",
        tags=["node"],
    ))
    other_lore = _result(rt.world(
        "upsert_lore",
        key="lore.south_gate.trade",
        title="南门交易规矩",
        lore_type="rule",
        scope_kind="place",
        scope_id=other_place["id"],
        content="南门集的规矩不能污染雨棚巷场景。",
        tags=["market"],
    ))
    faction = _result(rt.social(
        "create_entity",
        entity_kind="faction",
        display_name="巡城司",
        summary="掌控城内巡逻的势力。",
    ))
    presence = _result(rt.world(
        "upsert_faction_presence",
        faction_entity_id=faction["id"],
        scope_kind="region",
        scope_id=region["id"],
        influence=42,
        stance="contested",
        summary="巡城司在第七城有中等影响。",
    ))
    return {
        "profile": profile,
        "region": region,
        "place": place,
        "other_place": other_place,
        "world_lore": world_lore,
        "place_lore": place_lore,
        "other_lore": other_lore,
        "faction": faction,
        "presence": presence,
    }


def test_schema_v63_and_world_model_tables(tmp_path: Path) -> None:
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert _SCHEMA_VERSION >= 63
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 63
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {
            "world_profiles",
            "world_regions",
            "world_places",
            "world_lore_entries",
            "world_faction_presence",
        }.issubset(tables)
    finally:
        rt.close()


def test_world_model_effective_context_is_scope_bound(tmp_path: Path) -> None:
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        seeded = _seed_world(rt)

        scoped = rt.world("context", place_id=seeded["place"]["id"])["world_context"]
        contents = [item.get("content") for item in scoped["lore"]]
        assert "世界级规则会进入所有世界场景。" in contents
        assert "雨棚巷只在地点命中时生效。" in contents
        assert "南门集的规矩不能污染雨棚巷场景。" not in contents
        assert scoped["activation"]["place_id"] == seeded["place"]["id"]
        assert any(p["faction_name"] == "巡城司" and p["scope_kind"] == "region" for p in scoped["faction_presence"])

        via_interface = rt.interface("read", domain="world", view="context", place_key="place.rain_shelter")
        assert via_interface["world_context"]["activation"]["place_key"] == "place.rain_shelter"

        archived = _result(rt.world("archive", object_kind="lore", key=seeded["other_lore"]["key"]))
        assert archived["status"] == "archived"
        active_lore = rt.world("lore")["lore"]
        assert all(item["key"] != seeded["other_lore"]["key"] for item in active_lore)

        event = _result(rt.event_tool(
            "create",
            title="雨棚巷节点复查",
            location={"world_place_id": seeded["place"]["id"], "name": "雨棚巷"},
        ))
        rt.event_tool("update_state", mode="busy", active_event_id=event["id"])
        ctx = rt.build_context_for_turn("s1", "t1", "这里是什么地方？")
        assert "world_context" in ctx
        assert "雨棚巷只在地点命中时生效。" in ctx
        assert "南门集的规矩不能污染雨棚巷场景。" not in ctx
    finally:
        rt.close()


def test_webui_reader_and_endpoint_include_world_model(tmp_path: Path) -> None:
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        seeded = _seed_world(rt)
        db = str(db_path())
    finally:
        rt.close()

    reader = LifeEngineReader(db)
    snap = reader.snapshot("agent", "default-agent")
    world = snap["world_model"]
    assert any(r["name"] == "第七城" for r in world["regions"])
    assert any(p["name"] == "雨棚巷" and p["region_name"] == "第七城" for p in world["places"])
    assert any(l["scope_name"] == "雨棚巷" for l in world["lore"])
    assert any(p["faction_name"] == "巡城司" for p in world["faction_presence"])

    client = TestClient(create_app(db))
    endpoint = client.get("/api/world_model").json()
    assert endpoint["counts"]["places"] >= 2
    assert any(p["id"] == seeded["place"]["id"] for p in endpoint["places"])
