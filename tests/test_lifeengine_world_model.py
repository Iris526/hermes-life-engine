"""结构化世界本体：地图、地点、知识条目和势力影响。

这些测试用一组样例世界观名词，但核心断言只关心结构：文字内容可以保存和展示，
是否在当前场景生效必须由 profile/region/place/lore/presence 的 key、id、scope
和 LifeOps 账本决定。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
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
        rules={
            "time_flow": "real_time",
            "currency": "灵铢",
            "map": {
                "title": "第七城近郊图",
                "width": 100,
                "height": 100,
                "unit": "grid",
                "terrain_layers": [
                    {"key": "outer_waste", "name": "城外荒原", "terrain": "wasteland", "x": 0, "y": 0, "width": 100, "height": 100},
                    {"key": "storm_channel", "name": "风暴沟", "terrain": "water", "x": 4, "y": 70, "width": 92, "height": 12},
                ],
            },
        },
    ))
    region = _result(rt.world(
        "region",
        key="city.seventh",
        name="第七城",
        region_type="city",
        summary="雨棚巷所在的边城。",
        traits={"security": "contested", "terrain": "urban_ruins", "map": {"x": 18, "y": 18, "width": 58, "height": 48}},
    ))
    place = _result(rt.world(
        "place",
        key="place.rain_shelter",
        name="雨棚巷",
        place_type="street",
        region_id=region["id"],
        summary="旧雨棚和符线节点密集的街巷。",
        coordinates={"x": 36, "y": 42, "terrain": "street", "importance": 55},
    ))
    other_place = _result(rt.world(
        "place",
        key="place.south_gate",
        name="南门集",
        place_type="market",
        region_id=region["id"],
        summary="另一处市场。",
        coordinates={"x": 58, "y": 58, "terrain": "market", "importance": 64},
    ))
    shrine = _result(rt.world(
        "place",
        key="place.guiming_shrine",
        name="归明观",
        place_type="shrine",
        region_id=region["id"],
        summary="明灯常去修补符线的道观。",
        coordinates={"x": 43, "y": 34, "terrain": "urban_ruins", "importance": 92, "marker_role": "important_building"},
        traits={"important": True, "building_type": "shrine"},
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
        "shrine": shrine,
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
            location={"name": "雨棚巷"},
        ))
        assert event["location"]["world_place_id"] == seeded["place"]["id"]
        rt.event_tool("update_state", mode="busy", active_event_id=event["id"])
        ctx = rt.build_context_for_turn("s1", "t1", "这里是什么地方？")
        assert "world_context" in ctx
        assert "雨棚巷只在地点命中时生效。" in ctx
        assert "南门集的规矩不能污染雨棚巷场景。" not in ctx
    finally:
        rt.close()


def test_effective_context_prioritizes_specific_scope_over_global_limit(tmp_path: Path) -> None:
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        seeded = _seed_world(rt)
        for i in range(12):
            rt.world(
                "upsert_lore",
                key=f"lore.global.{i}",
                title=f"世界规则 {i}",
                scope_kind="world",
                content=f"世界级背景 {i}",
            )

        scoped = rt.world("context", place_id=seeded["place"]["id"], limit=5)["world_context"]
        keys = [item["key"] for item in scoped["lore"]]
        assert seeded["place_lore"]["key"] in keys
        assert keys[0] == seeded["place_lore"]["key"]
        assert len(keys) == 5
    finally:
        rt.close()


def test_world_map_has_terrain_buildings_and_actor_marker(tmp_path: Path) -> None:
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        seeded = _seed_world(rt)
        event = _result(rt.event_tool(
            "create",
            title="雨棚巷值守",
            location={"name": "雨棚巷"},
        ))
        tool_map = rt.world("map", location={"name": "雨棚巷"})["world_map"]
        assert tool_map["actor"]["status"] == "located"
        rt.event_tool("update_state", mode="busy", active_event_id=event["id"])
        db = str(db_path())
    finally:
        rt.close()

    snap = LifeEngineReader(db).snapshot("agent", "default-agent")
    world_map = snap["world_model"]["map"]
    assert world_map["canvas"]["title"] == "第七城近郊图"
    assert any(t["terrain"] == "wasteland" for t in world_map["terrain"])
    assert any(t["terrain"] == "urban_ruins" and t["name"] == "第七城" for t in world_map["terrain"])
    assert any(m["id"] == seeded["shrine"]["id"] and m["marker_role"] == "important_building" for m in world_map["markers"])
    assert world_map["actor"]["status"] == "located"
    assert world_map["actor"]["place_id"] == seeded["place"]["id"]
    assert world_map["actor"]["label"] == "明灯"


def test_archive_blocks_or_cascades_dependents(tmp_path: Path) -> None:
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        seeded = _seed_world(rt)

        with pytest.raises(Exception, match="active dependents"):
            rt.world("archive", object_kind="place", object_id=seeded["place"]["id"])
        with pytest.raises(Exception, match="faction_presence archive requires"):
            rt.world("archive", object_kind="faction_presence", key="not.valid.for.presence")

        archived = _result(rt.world(
            "archive",
            object_kind="place",
            object_id=seeded["place"]["id"],
            cascade=True,
        ))
        assert archived["status"] == "archived"
        assert any(item["object_kind"] == "lore" and item["object_id"] == seeded["place_lore"]["id"] for item in archived["archived_dependents"])
        assert all(item["key"] != seeded["place_lore"]["key"] for item in rt.world("lore")["lore"])
        assert all(item["id"] != seeded["place"]["id"] for item in rt.world("places")["places"])
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
    assert world["map"]["counts"]["important_markers"] >= 1

    client = TestClient(create_app(db))
    endpoint = client.get("/api/world_model").json()
    assert endpoint["counts"]["places"] >= 2
    assert any(p["id"] == seeded["place"]["id"] for p in endpoint["places"])
    assert any(m["id"] == seeded["shrine"]["id"] for m in endpoint["map"]["markers"])


def test_webui_world_action_can_write_and_archive(tmp_path: Path) -> None:
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        db = str(db_path())
    finally:
        rt.close()

    client = TestClient(create_app(db))
    created = client.post("/api/action", json={
        "action": "world",
        "payload": {"world_action": "region", "key": "web.region", "name": "网页区域"},
    }).json()
    assert created["ok"] is True
    endpoint = client.get("/api/world_model").json()
    assert any(r["key"] == "web.region" for r in endpoint["regions"])

    archived = client.post("/api/action", json={
        "action": "world",
        "payload": {"world_action": "archive", "object_kind": "region", "key": "web.region"},
    }).json()
    assert archived["ok"] is True
    endpoint = client.get("/api/world_model").json()
    assert all(r["key"] != "web.region" for r in endpoint["regions"])
