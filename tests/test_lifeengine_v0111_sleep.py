from __future__ import annotations

import os
import shutil

from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v0111"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.11.1 sleep test agent")
    rt.commit_canon()
    rt.control("resume")


def test_v0111_schema_sleep_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert _SCHEMA_VERSION >= 29
        for table in ["sleep_plans", "sleep_sessions", "sleep_session_state_transitions"]:
            assert rt.conn.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone(), table
    finally:
        rt.close()


def test_v0111_plan_core_sleep_creates_event_schedule_and_wake_jobs(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.sleep_tool(
            "plan",
            planned_start="2026-06-10T23:30:00+00:00",
            planned_end="2026-06-11T07:00:00+00:00",
            wake_policy="alarm",
            alarm_at="2026-06-11T07:00:00+00:00",
        )
        res = out["results"][0]["result"]
        plan = res["sleep_plan"]
        event = res["event"]
        block = res["schedule_block"]
        assert plan["planned_duration_minutes"] == 450
        assert event["event_category"] == "sleep"
        assert block["block_type"] == "sleep"
        jobs = rt.conn.execute("SELECT reason FROM wake_jobs WHERE target_id=? ORDER BY wake_at_ts", (plan["id"],)).fetchall()
        assert [j["reason"] for j in jobs] == ["sleep_plan_start", "sleep_plan_wake"]
        facts = out["receipt"]["facts"]
        assert any(f["kind"] == "sleep_plan" for f in facts)
    finally:
        rt.close()


def test_v0111_start_and_wake_sleep_updates_actual_state_and_resources(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.resources("define", key="energy", display_name="Energy", resource_class="capacity", unit="points", min_value=0, max_value=100, initial=20)
        rt.resources("define", key="sleep_debt", display_name="Sleep debt", resource_class="capacity", unit="minutes", min_value=0, max_value=10000, initial=0)
        plan_out = rt.sleep_tool("plan", planned_start="2026-06-10T23:30:00+00:00", planned_end="2026-06-11T07:00:00+00:00")
        plan = plan_out["results"][0]["result"]["sleep_plan"]
        start = rt.sleep_tool("start", sleep_plan_id=plan["id"], now="2026-06-11T00:30:00+00:00")
        sess = start["results"][0]["result"]["sleep_session"]
        assert start["results"][0]["result"]["realtime_state"]["mode"] == "asleep"
        wake = rt.sleep_tool("wake", sleep_session_id=sess["id"], now="2026-06-11T06:30:00+00:00", wake_cause="alarm", quality_score=70)
        result = wake["results"][0]["result"]
        session = result["sleep_session"]
        assert session["actual_duration_minutes"] == 360
        assert session["wake_cause"] == "alarm"
        assert result["realtime_state"]["mode"] == "idle"
        assert any(l["resource_key"] == "energy" for l in result["ledger"])
        assert any(l["resource_key"] == "sleep_debt" for l in result["ledger"])
        assert rt.conn.execute("SELECT COUNT(*) FROM sleep_session_state_transitions WHERE sleep_session_id=?", (sess["id"],)).fetchone()[0] >= 2
    finally:
        rt.close()


def test_v0111_heartbeat_starts_and_wakes_sleep(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.sleep_tool("plan", planned_start="2026-06-10T23:30:00+00:00", planned_end="2026-06-11T07:00:00+00:00")
        plan = out["results"][0]["result"]["sleep_plan"]
        tick1 = rt.tick(now="2026-06-10T23:31:00+00:00")
        assert tick1["status"] == "done"
        state = rt.sleep_tool("status")["sleep"]["realtime_state"]
        assert state["mode"] == "asleep"
        tick2 = rt.tick(now="2026-06-11T07:01:00+00:00")
        assert tick2["status"] == "done"
        sessions = rt.sleep_tool("sessions")["sleep_sessions"]
        assert sessions[0]["status"] in {"completed", "awake", "interrupted"}
        assert sessions[0]["sleep_plan_id"] == plan["id"]
    finally:
        rt.close()
