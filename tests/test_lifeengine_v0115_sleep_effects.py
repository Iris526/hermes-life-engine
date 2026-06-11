from __future__ import annotations

import os
from pathlib import Path

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home_v0115"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.11.5 sleep effects test agent")
    rt.commit_canon()
    rt.control("resume")


def define_body_resources(rt: LifeEngineRuntime):
    for key, initial in [("energy", 50), ("focus", 50), ("mood", 50), ("fatigue", 0)]:
        rt.resources("define", key=key, display_name=key, initial=initial)


def test_v0115_schema_and_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.5"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"sleep_day_states", "sleep_recovery_plans", "delayed_reply_digests", "dream_repair_policies"}.issubset(tables)
    finally:
        rt.close()


def test_short_sleep_records_day_state_and_recovery_pressure(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        define_body_resources(rt)
        plan = rt.sleep_tool("plan", planned_sleep_at="2026-06-10T23:00:00+00:00", planned_wake_at="2026-06-11T07:00:00+00:00", timezone_name="UTC")
        plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
        rt.sleep_tool("start", sleep_plan_id=plan_id, now="2026-06-11T03:00:00+00:00")
        wake = rt.sleep_tool("wake", sleep_plan_id=plan_id, now="2026-06-11T05:00:00+00:00")
        result = wake["results"][0]["result"]
        day = result["sleep_day_state"]
        assert day["sleep_debt_delta_minutes"] == 360
        assert day["recovery_pressure"] >= 60
        assert day["nap_recommended"] is True
        state = rt.sleep_tool("day_state")["sleep_day_state"]
        assert state["date_key"] == "2026-06-11"
        recovery = rt.sleep_tool("recovery_plan", threshold=60, duration_minutes=30)
        assert recovery["ok"] is True and recovery["planned"] is True
        assert recovery["sleep_plan"]["plan_type"] == "recovery_sleep"
    finally:
        rt.close()


def test_missed_core_sleep_records_all_nighter(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        plan = rt.sleep_tool("plan", planned_sleep_at="2026-06-10T23:00:00+00:00", planned_wake_at="2026-06-11T07:00:00+00:00", timezone_name="UTC")
        plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
        missed = rt.sleep_tool("wake", sleep_plan_id=plan_id, now="2026-06-11T07:05:00+00:00")
        out = missed["results"][0]["result"]
        assert out["missed"] is True
        day = out["sleep_day_state"]
        assert day["all_nighter"] is True
        assert day["actual_sleep_minutes"] == 0
        assert day["nap_recommended"] is True
    finally:
        rt.close()


def test_release_delayed_replies_creates_digest(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.reply("defer", message_text="第一条：你醒来后告诉我今天怎么安排。")
        rt.reply("defer", message_text="第二条：记得检查那个小任务。")
        released = rt.reply("release", reason="agent woke up")
        digest = released["results"][0]["result"]["digest"]
        assert digest["message_count"] == 2
        assert "第一条" in digest["summary_text"]
        digests = rt.reply("digests")["delayed_reply_digests"]
        assert digests and digests[0]["id"] == digest["id"]
    finally:
        rt.close()


def test_dream_repair_policy_off_blocks_repair_ops(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.dream("set_repair_policy", mode="off")
        # Create a stale schedule finding through DreamAudit.
        ev = rt.event_tool("create", title="过期未结算小任务", event_type="work", event_category="work")
        event_id = ev["receipt"]["facts"][0]["evidence"]["event_id"]
        rt.event_tool("schedule", event_id=event_id, start="2026-06-10T10:00:00+00:00", end="2026-06-10T11:00:00+00:00")
        plan = rt.sleep_tool("plan", planned_sleep_at="2026-06-10T23:00:00+00:00", planned_wake_at="2026-06-11T07:00:00+00:00", timezone_name="UTC")
        plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
        rt.sleep_tool("start", sleep_plan_id=plan_id, now="2026-06-10T23:00:00+00:00")
        wake = rt.sleep_tool("wake", sleep_plan_id=plan_id, now="2026-06-11T07:00:00+00:00")
        sleep_session_id = wake["results"][0]["result"]["sleep_session"]["id"]
        dream = rt.dream("run", sleep_session_id=sleep_session_id, force=True, create_share_intent=False)
        run_id = dream["receipt"]["facts"][0]["evidence"]["dream_run_id"]
        plan_out = rt.dream("repair_plan", dream_run_id=run_id)
        assert plan_out["policy_blocked"] is True
        assert plan_out["ops"] == []
        rt.dream("set_repair_policy", mode="auto_safe")
        plan_out2 = rt.dream("repair_plan", dream_run_id=run_id)
        assert any(op["type"] == "UPDATE_SCHEDULE_BLOCK_STATUS" for op in plan_out2["ops"])
    finally:
        rt.close()
