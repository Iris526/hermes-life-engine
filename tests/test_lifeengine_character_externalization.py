from __future__ import annotations

import json
import os
import shutil


RESIDUE_TERMS = ("灵铢", "归明观", "符纸", "朱砂", "香案", "净符", "晨巡")


def fresh_home(tmp_path):
    """为单个测试创建隔离 HERMES_HOME。

    输入是 pytest tmp_path；输出是全新的测试 home 路径。调用方是本文件每个测试。
    副作用仅限设置进程环境变量并清理该临时目录，失败时由 pytest 暴露文件系统错误。
    """
    home = tmp_path / "hermes_home_character_externalization"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _contains_residue(value) -> bool:
    """检查序列化结果里是否包含本切片要迁出的角色残留词。

    输入是任意可 JSON 序列化的测试结果；输出为是否命中残留词。调用方是默认
    兼容和现代 Canon 两个回归测试。函数无业务副作用，序列化失败会直接让测试失败。
    """
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return any(term in text for term in RESIDUE_TERMS)


def test_default_agent_keeps_legacy_living_skin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(fresh_home(tmp_path)))
    from lifeengine.runtime import LifeEngineRuntime

    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，heartbeat 要能自己补齐当天生活节奏。")
        committed = rt.commit_canon()["canon"]
        assert committed["data"]["living"]["skin"] == "guimingguan"

        resources = rt.living("init_resources")
        assert "money.lingzhu" in resources["resource_keys"]

        rhythm = rt.living("day_rhythm", date="2030-01-02")
        assert rhythm["ok"] is True
        assert rhythm["event_ids"]
        rows = rt.conn.execute(
            "SELECT title, payload_json FROM life_rhythm_items WHERE owner_kind='agent' AND owner_id='default-agent' ORDER BY start",
        ).fetchall()
        payload = [dict(row) for row in rows]
        assert _contains_residue(payload)
        assert "归明观晨巡与开观" in rhythm["rendered"]
        assert "晨巡" in json.dumps(payload, ensure_ascii=False)
    finally:
        rt.close()


def test_modern_canon_without_living_skin_has_no_character_residue(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(fresh_home(tmp_path)))
    from lifeengine.canon import get_active_canon
    from lifeengine.constants import DEFAULT_CANON_TEMPLATE
    from lifeengine.living import supply_items
    from lifeengine.runtime import LifeEngineRuntime

    rt = LifeEngineRuntime()
    owner_id = "modern-illustrator"
    try:
        assert not _contains_residue(DEFAULT_CANON_TEMPLATE)

        rt.setup("名字是 凛，是现代插画师，生活在现代城市，货币用日元。", "agent", owner_id)
        committed = rt.commit_canon("agent", owner_id)["canon"]
        assert not (committed["data"].get("living") or {}).get("skin")

        resources = rt.living("init_resources", owner_id=owner_id)
        rhythm = rt.living("day_rhythm", owner_id=owner_id, date="2030-01-02")
        canon = get_active_canon(rt.conn, "agent", owner_id)
        supplies = supply_items(canon=canon)

        rows = rt.conn.execute(
            "SELECT title, payload_json FROM life_rhythm_items WHERE owner_kind='agent' AND owner_id=? ORDER BY start",
            (owner_id,),
        ).fetchall()
        proactive_rows = rt.conn.execute(
            "SELECT summary FROM proactive_intents WHERE agent_id=? ORDER BY created_at",
            (owner_id,),
        ).fetchall()
        resource_state = rt.resources("list", owner_id=owner_id)["resources"]

        produced = {
            "resource_rendered": resources["rendered"],
            "resource_state": resource_state,
            "rhythm_rendered": rhythm["rendered"],
            "rhythm_items": [dict(row) for row in rows],
            "supplies": supplies,
            "proactive_summaries": [row["summary"] for row in proactive_rows],
        }
        assert rhythm["event_ids"] == []
        assert supplies == []
        assert not _contains_residue(produced)
    finally:
        rt.close()
