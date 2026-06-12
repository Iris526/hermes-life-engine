from __future__ import annotations

import os
import shutil

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v0111"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.11.1 SleepPlan/SleepSession 测试 Agent。")
    rt.commit_canon()
    rt.control("resume")


def test_v0111_version_schema_and_sleep_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.6"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        for table in ["sleep_plans", "sleep_sessions", "sleep_interruptions", "sleep_doctor_findings"]:
            assert rt.conn.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone(), table
        cols = {r[1] for r in rt.conn.execute("PRAGMA table_info(agent_state_snapshots)").fetchall()}
        assert "active_sleep_session_id" in cols
    finally:
        rt.close()


def test_sleep_plan_creates_sleep_event_schedule_and_wake_jobs(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.sleep(
            "plan",
            planned_sleep_at="2026-06-10T00:30:00+00:00",
            planned_wake_at="2026-06-10T08:00:00+00:00",
            date="2026-06-10",
            plan_type="core_sleep",
            alarm_at="2026-06-10T08:00:00+00:00",
            wake_policy="alarm",
        )
        res = out["results"][0]["result"]
        plan = res["sleep_plan"]
        assert plan["planned_duration_minutes"] == 450
        event = rt.event_tool("get", event_id=plan["event_id"])["event"]
        assert event["event_category"] == "sleep"
        assert event["event_type"] == "core_sleep"
        block = res["schedule_block"]
        assert block["block_type"] == "sleep"
        jobs = rt.conn.execute("SELECT reason FROM wake_jobs WHERE target_id=? ORDER BY reason", (plan["id"],)).fetchall()
        assert {j["reason"] for j in jobs} >= {"sleep_plan_start", "sleep_plan_wake"}
        facts = out["receipt"]["facts"]
        assert any(f["kind"] == "sleep_plan" for f in facts)
    finally:
        rt.close()


def test_sleep_start_and_wake_track_actual_session_and_realtime_state(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        plan = rt.sleep(
            "plan",
            planned_sleep_at="2026-06-10T00:30:00+00:00",
            planned_wake_at="2026-06-10T08:00:00+00:00",
            date="2026-06-10",
        )["results"][0]["result"]["sleep_plan"]
        start = rt.sleep("start", sleep_plan_id=plan["id"], now="2026-06-10T01:10:00+00:00")
        session = start["results"][0]["result"]["sleep_session"]
        state = rt.sleep("state")["realtime_state"]
        assert state["mode"] == "asleep"
        assert state["active_sleep_session_id"] == session["id"]
        wake = rt.sleep("wake", sleep_session_id=session["id"], now="2026-06-10T07:10:00+00:00", wake_cause="alarm")
        done = wake["results"][0]["result"]["sleep_session"]
        assert done["status"] in {"completed", "interrupted"}
        assert done["actual_duration_minutes"] == 360
        assert done["resource_effects"]["sleep_debt_delta_minutes"] == 90
        state2 = rt.sleep("state")["realtime_state"]
        assert state2["mode"] == "idle"
        assert state2["active_sleep_session_id"] is None
    finally:
        rt.close()


def test_sleep_interrupt_can_wake_and_record_interruption(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        plan = rt.sleep("plan", planned_sleep_at="2026-06-10T00:00:00+00:00", planned_wake_at="2026-06-10T08:00:00+00:00")["results"][0]["result"]["sleep_plan"]
        session = rt.sleep("start", sleep_plan_id=plan["id"], now="2026-06-10T00:00:00+00:00")["results"][0]["result"]["sleep_session"]
        out = rt.sleep("interrupt", sleep_session_id=session["id"], now="2026-06-10T03:00:00+00:00", user_id="user-a", caused_wake=True, reason="urgent call")
        result = out["results"][0]["result"]
        assert result["caused_wake"] is True
        woke = result["wake"]["sleep_session"]
        assert woke["status"] == "interrupted"
        got = rt.sleep("get_session", sleep_session_id=session["id"])
        assert got["interruptions"][0]["reason"] == "urgent call"
        assert got["interruptions"][0]["caused_wake"] == 1
    finally:
        rt.close()


def test_heartbeat_starts_and_wakes_sleep_via_wake_jobs(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        plan = rt.sleep("plan", planned_sleep_at="2026-06-10T00:00:00+00:00", planned_wake_at="2026-06-10T08:00:00+00:00")["results"][0]["result"]["sleep_plan"]
        tick1 = rt.tick(now="2026-06-10T00:01:00+00:00")
        assert any("sleep_start_commit" in c for c in tick1["completed"])
        assert rt.sleep("state")["realtime_state"]["mode"] == "asleep"
        tick2 = rt.tick(now="2026-06-10T08:01:00+00:00")
        assert any("sleep_wake_commit" in c for c in tick2["completed"])
        sessions = rt.sleep("sessions")["sleep_sessions"]
        assert sessions[0]["actual_duration_minutes"] == 480
        assert rt.sleep("get_plan", sleep_plan_id=plan["id"])["sleep_plan"]["status"] == "completed"
    finally:
        rt.close()
