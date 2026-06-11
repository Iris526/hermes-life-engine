from __future__ import annotations

import os
from pathlib import Path

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.11.3 dream test agent")
    rt.commit_canon()
    rt.control("resume")


def _sleep_and_wake(rt: LifeEngineRuntime, *, start="2026-06-10T23:00:00+00:00", wake="2026-06-11T07:00:00+00:00") -> str:
    plan = rt.sleep_tool("plan", planned_sleep_at=start, planned_wake_at=wake, timezone_name="UTC")
    sleep_plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
    rt.sleep_tool("start", sleep_plan_id=sleep_plan_id, now=start)
    wake_out = rt.sleep_tool("wake", sleep_plan_id=sleep_plan_id, now=wake)
    return wake_out["receipt"]["facts"][0]["evidence"]["sleep_session_id"]


def test_v0113_schema_and_dream_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.1"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"dream_runs", "dream_audit_findings", "dream_entries"}.issubset(tables)
    finally:
        rt.close()


def test_manual_dream_run_after_core_sleep_creates_entry_memory_and_proactive_intent(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.memory("remember", content="今天的小单子让我有点在意，但我把结果节点整理好了。", memory_type="episodic", source="agent_retro_assertion")
        sleep_session_id = _sleep_and_wake(rt)
        dream = rt.dream("run", sleep_session_id=sleep_session_id)
        fact = dream["receipt"]["facts"][0]
        assert fact["kind"] == "dream"
        ev = fact["evidence"]
        assert ev["sleep_session_id"] == sleep_session_id
        assert ev["dream_entry_id"]
        assert ev["truth_layer"] == "dream_symbolic"
        status = rt.dream("status")
        assert status["recent_entries"][0]["truth_layer"] == "dream_symbolic"
        assert status["recent_runs"][0]["proactive_intent_id"]
        proactive = rt.proactive("list")
        assert any(i["intent_type"] == "self_reflection_share" for i in proactive["intents"])
    finally:
        rt.close()


def test_heartbeat_wake_runs_dream_automatically(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        plan = rt.sleep_tool("plan", planned_sleep_at="2026-06-10T23:00:00+00:00", planned_wake_at="2026-06-11T07:00:00+00:00", timezone_name="UTC")
        sleep_plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
        # A single late tick claims start and wake jobs in order; wake triggers dream run.
        out = rt.tick(now="2026-06-11T07:05:00+00:00")
        assert out["ok"] is True
        assert any(item.get("dream_commit") for item in out.get("completed", []))
        runs = rt.dream("list")
        assert runs["runs"]
        assert runs["runs"][0]["sleep_session_id"]
    finally:
        rt.close()


def test_short_nap_dream_is_skipped_by_default(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        plan = rt.sleep_tool("nap", planned_sleep_at="2026-06-10T13:00:00+00:00", planned_wake_at="2026-06-10T13:20:00+00:00", timezone_name="UTC")
        sleep_plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
        rt.sleep_tool("start", sleep_plan_id=sleep_plan_id, now="2026-06-10T13:00:00+00:00")
        wake = rt.sleep_tool("wake", sleep_plan_id=sleep_plan_id, now="2026-06-10T13:20:00+00:00")
        sleep_session_id = wake["receipt"]["facts"][0]["evidence"]["sleep_session_id"]
        dream = rt.dream("run", sleep_session_id=sleep_session_id)
        run = dream["receipt"]["facts"][0]["evidence"]["dream_run_id"]
        detail = rt.dream("get", dream_run_id=run)["dream_run"]
        assert detail["status"] == "skipped"
        assert detail["findings"][0]["finding_type"] == "dream_skipped"
    finally:
        rt.close()


def test_doctor_warns_when_completed_core_sleep_lacks_dream(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        _sleep_and_wake(rt)
        doctor = rt.doctor(include_samples=True)
        names = {c["name"]: c for c in doctor["checks"]}
        assert names["dreams"]["status"] in {"warn", "warning"}
    finally:
        rt.close()
