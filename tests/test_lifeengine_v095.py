from __future__ import annotations

import json
import os
import shutil

from lifeengine.cli import slash_life
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.final_gate import detect_life_claim_items
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v095"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.9.5 FinalGate advisory UX test Agent。")
    rt.commit_canon()
    rt.control("resume")


def test_v095_schema_and_default_final_gate_advisory(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION and _SCHEMA_VERSION >= 29
        status = rt.status()
        assert status["control"]["module_gates"]["final_audit"] == "advisory"
        msg = rt.audit_final_output("我今天中午吃了咖喱饭。", session_id="s", turn_id="t")
        assert msg is None
        reports = rt.final_gate("reports")
        assert reports["reports"][0]["status"] == "advisory"
    finally:
        rt.close()


def test_v095_soft_plan_screenshot_style_is_not_hard_blocked(tmp_path):
    text = "我今天就更要好好做了：第七城雨棚巷那单虽然只是丁级小委托，但结果节点这种东西，越小越不能敷衍。\n所以今天安排大概是：\n上午收拾符纸、小铃铛；\n10 点半左右去第七城雨棚巷；"
    items = detect_life_claim_items(text)
    assert items
    assert all(i["severity"] == "soft" for i in items)
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.control("module", key="final_audit", value="strict")
        msg = rt.audit_final_output(text, session_id="s", turn_id="t")
        assert msg is None
        reports = rt.final_gate("reports")
        assert reports["reports"][0]["status"] == "advisory"
    finally:
        rt.close()


def test_v095_final_gate_feedback_is_internal_next_turn_context(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        assert rt.audit_final_output("我今天中午吃了咖喱饭。", session_id="s", turn_id="t") is None
        ctx = rt.build_context_for_turn(session_id="s2", turn_id="t2", user_message="继续", sender_id=None)
        assert "internal_final_gate_feedback" in ctx
        assert "Do NOT show this diagnostic to the user" in ctx
        # Consumed once.
        ctx2 = rt.build_context_for_turn(session_id="s3", turn_id="t3", user_message="继续", sender_id=None)
        assert "Do NOT show this diagnostic to the user" not in ctx2
    finally:
        rt.close()


def test_v095_human_command_surface_is_simple_but_advanced_available(tmp_path):
    fresh_home(tmp_path)
    help_text = slash_life("help")
    assert "/life setup" in help_text
    assert "/life advanced" in help_text
    assert "final_gate" not in help_text
    advanced = slash_life("advanced")
    assert "final_gate" in advanced
    backup = json.loads(slash_life("backup"))
    assert backup["ok"] is True
