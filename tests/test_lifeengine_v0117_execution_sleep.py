from __future__ import annotations

import os
from pathlib import Path

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home_v0117"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.11.7 sleep-aware execution simulator test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.control("module", key="execution", value="auto")


def create_short_sleep_day_state(rt: LifeEngineRuntime):
    plan = rt.sleep_tool(
        "plan",
        planned_sleep_at="2026-06-10T23:00:00+00:00",
        planned_wake_at="2026-06-11T07:00:00+00:00",
        timezone_name="UTC",
    )
    plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
    rt.sleep_tool("start", sleep_plan_id=plan_id, now="2026-06-11T03:00:00+00:00")
    wake = rt.sleep_tool("wake", sleep_plan_id=plan_id, now="2026-06-11T05:00:00+00:00")
    return wake["results"][0]["result"]["sleep_day_state"]


def create_all_nighter_day_state(rt: LifeEngineRuntime):
    plan = rt.sleep_tool(
        "plan",
        planned_sleep_at="2026-06-10T23:00:00+00:00",
        planned_wake_at="2026-06-11T07:00:00+00:00",
        timezone_name="UTC",
    )
    plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
    day = rt.sleep_tool("all_nighter", sleep_plan_id=plan_id)["sleep_day_state"]
    return day


def create_scheduled_event(rt: LifeEngineRuntime, *, title: str, importance: int, event_type: str = "work"):
    event_out = rt.event_tool(
        "create",
        title=title,
        event_type=event_type,
        event_category=event_type,
        status="planned",
        importance=importance,
        priority=importance,
        resource_costs={"energy": -12, "focus": -10},
    )
    event_id = event_out["receipt"]["facts"][0]["evidence"]["event_id"]
    schedule_out = rt.event_tool(
        "schedule",
        event_id=event_id,
        start="2026-06-11T10:00:00+00:00",
        end="2026-06-11T11:00:00+00:00",
        timezone_name="UTC",
    )
    block_id = [f for f in schedule_out["receipt"]["facts"] if f["kind"] == "schedule"][0]["evidence"]["schedule_block_id"]
    return event_id, block_id


def test_v0117_schema_and_table(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.13.0"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "execution_sleep_adjustments" in tables
    finally:
        rt.close()


def test_execution_downshifts_work_event_when_sleep_debt_is_high(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        day = create_short_sleep_day_state(rt)
        assert day["recovery_pressure"] >= 60
        event_id, block_id = create_scheduled_event(rt, title="写一份报告", importance=85, event_type="work")

        out = rt.execution("run", schedule_block_id=block_id)
        assert out["ok"] is True
        assert out["decision"]["decision_type"] == "partial"
        assert out["decision"]["sleep_adjustment"]["adjustment_type"] == "sleep_pressure_downshifted"
        event = rt.event_tool("get", event_id=event_id)["event"]
        assert event["status"] == "partial"
        adjustments = rt.execution("sleep_adjustments")["sleep_adjustments"]
        assert any(a["event_id"] == event_id for a in adjustments)
        explained = rt.traces("explain", event_id=event_id)
        assert explained["execution_sleep_adjustments"]
    finally:
        rt.close()


def test_execution_postpones_lower_importance_event_after_all_nighter(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        day = create_all_nighter_day_state(rt)
        assert day["all_nighter"] is True
        event_id, block_id = create_scheduled_event(rt, title="整理工作材料", importance=45, event_type="work")

        out = rt.execution("run", schedule_block_id=block_id)
        assert out["decision"]["decision_type"] == "postponed"
        assert out["decision"]["sleep_adjustment"]["adjustment_type"] == "sleep_pressure_postponed"
        event = rt.event_tool("get", event_id=event_id)["event"]
        assert event["status"] in {"rescheduled", "scheduled"}
        adjustments = rt.execution("sleep_adjustments")
        assert adjustments["sleep_adjustments"][0]["severity"] == "severe"
    finally:
        rt.close()


def test_execution_sleep_context_action_exposes_pressure(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        create_short_sleep_day_state(rt)
        ctx = rt.execution("sleep_context")["sleep_context"]
        assert ctx["sleep_day_state_id"]
        assert ctx["should_downshift"] is True
        assert ctx["severity"] in {"moderate", "severe"}
    finally:
        rt.close()
