from __future__ import annotations

import os
from pathlib import Path

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home_v0116"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.11.6 sleep-aware autonomy test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.control("module", key="autonomy", value="full")


def define_body_resources(rt: LifeEngineRuntime):
    for key, initial in [("energy", 70), ("focus", 70), ("mood", 60), ("fatigue", 0)]:
        rt.resources("define", key=key, display_name=key, initial=initial)


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


def test_v0116_schema_and_table(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.7"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "autonomy_sleep_adjustments" in tables
    finally:
        rt.close()


def test_autonomy_high_sleep_debt_prefers_recovery_sleep_plan(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        define_body_resources(rt)
        day = create_short_sleep_day_state(rt)
        assert day["recovery_pressure"] >= 60
        rt.goals("create", title="推进高强度学习项目", goal_type="study", priority=90)

        out = rt.autonomy("run", now="2026-06-11T09:00:00+00:00")
        assert out["ok"] is True
        assert out["decision"]["status"] == "committed"
        assert "sleep" in out["decision"]["score"]
        assert "sleep debt" in out["decision"]["reason"] or "fatigue" in out["decision"]["reason"]
        facts = out["commit"]["receipt"]["facts"]
        assert any(f.get("kind") == "sleep_plan" for f in facts)
        plans = rt.sleep_tool("plans")["sleep_plans"]
        assert any(p["plan_type"] == "recovery_sleep" for p in plans)
        adjustments = rt.autonomy("sleep_adjustments")["sleep_adjustments"]
        assert adjustments and adjustments[0]["adjustment_type"] == "recovery_sleep_planned"
    finally:
        rt.close()


def test_autonomy_downshifts_goal_step_when_recovery_plan_exists(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        define_body_resources(rt)
        create_short_sleep_day_state(rt)
        # Existing recovery plan prevents duplicate recovery sleep; autonomy should downshift goal work.
        rt.sleep_tool("recovery_plan", threshold=60, duration_minutes=30)
        rt.goals("create", title="写完创作项目", goal_type="creative", priority=85)

        out = rt.autonomy("run", now="2026-06-11T10:00:00+00:00")
        assert out["commit"] is not None
        events = rt.event_tool("list")["events"]
        light = [e for e in events if e["title"] == "轻量推进目标：写完创作项目"]
        assert light
        assert "sleep_adjusted" in light[0].get("tags", [])
        adjustments = rt.autonomy("sleep_adjustments")["sleep_adjustments"]
        assert any(a["adjustment_type"] == "goal_step_downshifted" for a in adjustments)
    finally:
        rt.close()


def test_autonomy_sleep_context_action_exposes_latest_sleep_state(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        create_short_sleep_day_state(rt)
        ctx = rt.autonomy("sleep_context", now="2026-06-11T11:00:00+00:00")["sleep_context"]
        assert ctx["sleep_day_state_id"]
        assert ctx["recovery_pressure"] >= 60
        assert ctx["should_recover"] is True
        assert ctx["severity"] in {"moderate", "severe"}
    finally:
        rt.close()
