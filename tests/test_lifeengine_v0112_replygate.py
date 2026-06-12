from __future__ import annotations

import os
from pathlib import Path

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.constants import PLUGIN_VERSION


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.11.3 reply gate test agent")
    rt.commit_canon()
    rt.control("resume")


def test_v0112_schema_reply_gate_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.6"
        assert _SCHEMA_VERSION >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"reply_gate_decisions", "delayed_replies", "call_overrides", "reply_gate_recoveries"}.issubset(tables)
    finally:
        rt.close()


def test_reply_gate_advisory_does_not_defer_sleeping_agent(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        plan = rt.sleep_tool("plan_day", date="2026-06-10", bedtime="23:00", wake_time="07:00", timezone_name="UTC")
        sleep_plan_id = (plan["receipt"]["facts"][0]["evidence"] or {}).get("sleep_plan_id")
        assert sleep_plan_id
        rt.sleep_tool("start", sleep_plan_id=sleep_plan_id, now="2026-06-10T23:05:00+00:00")
        out = rt.assess_incoming_message(session_id="s1", turn_id="t1", sender_id="u1", text="你睡了吗？")
        assert out["decision"]["decision"] == "advisory"
        assert not out.get("delayed_reply")
    finally:
        rt.close()


def test_reply_gate_auto_defers_ordinary_message_while_asleep(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.control("module", key="reply_gate", value="auto")
        plan = rt.sleep_tool("plan_day", date="2026-06-10", bedtime="23:00", wake_time="07:00", timezone_name="UTC")
        sleep_plan_id = (plan["receipt"]["facts"][0]["evidence"] or {}).get("sleep_plan_id")
        rt.sleep_tool("start", sleep_plan_id=sleep_plan_id, now="2026-06-10T23:05:00+00:00")
        out = rt.assess_incoming_message(session_id="s1", turn_id="t1", sender_id="u1", text="普通消息，醒来再说")
        assert out["decision"]["decision"] == "defer"
        assert out["delayed_reply"]["status"] == "pending"
        status = rt.reply("status")
        assert len(status["reply_gate"]["pending_delayed_replies"]) == 1
    finally:
        rt.close()


def test_life_call_wakes_sleep_and_releases_delayed_replies(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.control("module", key="reply_gate", value="auto")
        plan = rt.sleep_tool("plan_day", date="2026-06-10", bedtime="23:00", wake_time="07:00", timezone_name="UTC")
        sleep_plan_id = (plan["receipt"]["facts"][0]["evidence"] or {}).get("sleep_plan_id")
        rt.sleep_tool("start", sleep_plan_id=sleep_plan_id, now="2026-06-10T23:05:00+00:00")
        rt.assess_incoming_message(session_id="s1", turn_id="t1", sender_id="u1", text="醒来后回复我")
        call = rt.call(reason="test emergency call", user_id="u1", session_id="s1", turn_id="t2", message_text="call")
        assert call["ok"] is True
        assert call["receipt"]["facts"][0]["kind"] == "reply_gate"
        status = rt.reply("status")
        assert status["reply_gate"]["realtime_state"]["reply_mode"] == "immediate"
        assert len(status["reply_gate"]["pending_delayed_replies"]) == 0
        sessions = rt.sleep_tool("sessions")
        assert sessions["sleep_sessions"][0]["status"] in {"interrupted", "completed"}
    finally:
        rt.close()


def test_uninterruptible_event_defer_and_call_override(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.control("module", key="reply_gate", value="auto")
        ev = rt.event_tool("create", title="不可打断的仪式", status="planned", event_category="work", interruptibility={"level":"uninterruptible"})
        event_id = ev["receipt"]["facts"][0]["evidence"]["event_id"]
        rt.event_tool("update_state", mode="uninterruptible_event", active_event_id=event_id, interruptibility_level="uninterruptible", reply_mode="defer_until_event_end", lease_expires_at="2030-06-10T12:00:00+00:00")
        out = rt.assess_incoming_message(session_id="s2", turn_id="t1", sender_id="u1", text="普通消息")
        assert out["decision"]["decision"] == "defer"
        call = rt.call(reason="break loop", user_id="u1", session_id="s2", turn_id="t2")
        assert call["ok"] is True
        state = rt.event_tool("state")
        assert state["realtime_state"]["reply_mode"] == "immediate"
    finally:
        rt.close()
