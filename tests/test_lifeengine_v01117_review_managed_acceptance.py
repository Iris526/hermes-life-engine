import tempfile

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.reply_gate import create_delayed_reply
from lifeengine.cli import slash_life


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01117_")
    monkeypatch.setenv("HERMES_HOME", d)
    return d


def _enable_managed(rt: LifeEngineRuntime, **patch):
    base = {
        "allow_agent_managed_loop": True,
        "agent_managed_trigger_sources": ["heartbeat", "manual", "cli", "slash"],
        "agent_managed_daily_action_limit": 5,
        "agent_managed_failure_budget": 2,
        "agent_managed_sections": ["reply", "sleep", "dream", "proactive", "policy"],
        "agent_managed_safe_only": True,
    }
    base.update(patch)
    return rt.review("set_policy", policy_patch=base)


def test_schema_v37_and_managed_acceptance_tables(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 37
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "human_review_managed_acceptance_runs" in tables
        assert "human_review_managed_acceptance_scenarios" in tables
        assert "human_review_managed_stress_runs" in tables
    finally:
        conn.close()


def test_managed_review_duplicate_tick_is_idempotent(hermes_home):
    rt = LifeEngineRuntime()
    try:
        _enable_managed(rt, agent_managed_trigger_sources=["heartbeat"])
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="dup tick", reason="test")
        first = rt.review("managed_run", trigger_source="heartbeat", tick_id="tick-dup-1")
        second = rt.review("managed_run", trigger_source="heartbeat", tick_id="tick-dup-1")
        assert first["status"] in {"applied", "noop"}
        assert second["status"] == "duplicate_tick"
        rows = rt.conn.execute(
            "SELECT COUNT(*) FROM human_review_managed_loop_runs WHERE owner_kind='agent' AND owner_id='default-agent' AND tick_id='tick-dup-1'"
        ).fetchone()[0]
        assert rows == 1
    finally:
        rt.close()


def test_managed_review_acceptance_runner_records_scenarios(hermes_home):
    rt = LifeEngineRuntime()
    try:
        out = rt.review("managed_acceptance", stress_count=8)
        assert out["ok"] is True
        run = out["acceptance_run"]
        assert run["status"] == "passed"
        assert run["passed_count"] >= 5
        assert run["failed_count"] == 0
        keys = {s["scenario_key"] for s in run["scenarios"]}
        assert "MGR01_DISABLED_BY_DEFAULT" in keys
        assert "MGR04_DUPLICATE_TICK_IDEMPOTENCY" in keys
        runs = rt.review("managed_acceptance_runs")
        assert runs["acceptance_runs"]
        got = rt.review("get_managed_acceptance", acceptance_run_id=run["id"])
        assert got["acceptance_run"]["id"] == run["id"]
    finally:
        rt.close()


def test_managed_review_stress_respects_limit(hermes_home):
    rt = LifeEngineRuntime()
    try:
        out = rt.review("managed_stress", count=18, limit=7)
        assert out["ok"] is True
        sr = out["stress_run"]
        assert sr["created_count"] == 18
        assert sr["applied_count"] <= 7
        assert sr["selected_count"] <= 7
        assert sr["output"]["released_count"] == sr["applied_count"]
        assert rt.review("managed_stress_runs")["stress_runs"]
        got = rt.review("get_managed_stress", stress_run_id=sr["id"])
        assert got["stress_run"]["id"] == sr["id"]
    finally:
        rt.close()


def test_managed_acceptance_slash_surface(hermes_home):
    out = slash_life("review managed_acceptance")
    assert "acceptance_run" in out or "MGR01_DISABLED_BY_DEFAULT" in out
