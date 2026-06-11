from __future__ import annotations

import os
import shutil

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v09"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent；启用叙事执行模拟器和低戏剧性小意外；天气可以影响外出计划。")
    rt.commit_canon()
    rt.control("resume")


def _past_event(rt: LifeEngineRuntime, title: str, event_type: str = "study", importance: int = 70, resource_costs=None):
    ev = rt.event_tool("create", title=title, event_type=event_type, importance=importance, source="agent_prediction", resource_costs=resource_costs or {})
    event_id = ev["results"][0]["result"]["id"]
    block = rt.event_tool(
        "schedule",
        event_id=event_id,
        start="2026-06-07T10:00:00+00:00",
        end="2026-06-07T11:00:00+00:00",
        timezone_name="UTC",
    )
    block_id = block["results"][0]["result"]["id"]
    return event_id, block_id


def test_execution_simulator_completes_event_and_creates_serendipity(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.resources("define", key="energy", display_name="Energy", initial=50)
        event_id, _block_id = _past_event(rt, "完成第一章复习", "study", 80, {"energy": -5})
        tick = rt.tick(now="2026-06-07T11:01:00+00:00")
        assert tick["ok"] is True
        decision = tick["completed"][0]["execution_decision"]
        assert decision["decision_type"] == "completed"
        assert decision["status"] == "committed"
        events = rt.event_tool("list", status="completed")["events"]
        assert any(e["id"] == event_id for e in events)
        ser = rt.execution("serendipity")["serendipity"]
        assert ser
        assert any("复习" in s["title"] for s in ser)
        # Final gate sees the completed event and serendipity through receipts/canonical state.
        assert rt.audit_final_output("我完成了第一章复习，也记录了一个复习时发现的小点。", session_id="s9", turn_id="t9") is None
    finally:
        rt.close()


def test_execution_simulator_postpones_outdoor_plan_on_bad_weather(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.truth("observe", domain="weather", authority="external_tool", result={"condition": "rain", "summary": "rain in the afternoon"}, ttl_minutes=120)
        event_id, block_id = _past_event(rt, "下午出门买裙子", "purchase", 65, {})
        tick = rt.tick(now="2026-06-07T11:01:00+00:00")
        decision = tick["completed"][0]["execution_decision"]
        assert decision["decision_type"] == "postponed"
        events = rt.event_tool("list") ["events"]
        ev = [e for e in events if e["id"] == event_id][0]
        assert ev["status"] == "scheduled"  # old block rescheduled; replacement block puts event back on schedule
        explained = rt.traces("explain", transaction_id=tick["completed"][0]["commit"]["transaction_id"])
        assert any("天气" in f["claim_text"] or "rescheduled" in f["claim_text"] for f in explained["facts"])
    finally:
        rt.close()


def test_execution_simulator_partial_when_important_event_has_resource_shortage(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.resources("define", key="money.jpy", display_name="JPY", resource_class="fungible", unit="JPY", initial=5)
        event_id, _block_id = _past_event(rt, "购买重要教材", "purchase", 90, {"money.jpy": -10})
        tick = rt.tick(now="2026-06-07T11:01:00+00:00")
        decision = tick["completed"][0]["execution_decision"]
        assert decision["decision_type"] == "partial"
        events = rt.event_tool("list", status="partial")["events"]
        assert any(e["id"] == event_id for e in events)
        refs = rt.goals("reflections", limit=10)["reflections"]
        assert any("资源不足" in r["content"] for r in refs)
        intents = rt.proactive("list") ["intents"]
        assert any("资源不足" in i["summary"] for i in intents)
    finally:
        rt.close()


def test_life_execution_manual_run_uses_receipts(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        event_id, block_id = _past_event(rt, "整理书桌", "routine", 50, {})
        out = rt.execution("run", schedule_block_id=block_id, session_id="s9", turn_id="e1")
        assert out["ok"] is True
        assert out["decision"]["status"] == "committed"
        assert out["commit"] is not None
        assert any(f["kind"] in {"event_result", "schedule"} for f in out["commit"]["receipt"]["facts"])
        assert rt.audit_final_output("我整理书桌完成了。", session_id="s9", turn_id="e1") is None
    finally:
        rt.close()
