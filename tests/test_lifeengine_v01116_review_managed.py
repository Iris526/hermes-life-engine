import tempfile

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.reply_gate import create_delayed_reply
from lifeengine.cli import slash_life


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01116_")
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


def test_schema_v36_and_managed_review_tables(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 36
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "human_review_managed_loop_state" in tables
        assert "human_review_managed_loop_runs" in tables
    finally:
        conn.close()


def test_managed_review_loop_disabled_by_default(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="do not auto release yet", reason="test")
        out = rt.review("managed_run", trigger_source="manual")
        assert out["status"] == "blocked"
        assert out["allowed"] is False
        delayed = rt.reply("list")
        assert delayed["delayed_replies"][0]["status"] == "pending"
    finally:
        rt.close()


def test_managed_review_loop_applies_safe_delayed_reply_when_enabled(hermes_home):
    rt = LifeEngineRuntime()
    try:
        _enable_managed(rt)
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="agent managed release", reason="test")
        out = rt.review("managed_run", trigger_source="manual")
        assert out["ok"] is True
        assert out["status"] == "applied"
        assert out["managed_run"]["applied_count"] >= 1
        delayed = rt.reply("list")
        assert delayed["delayed_replies"][0]["status"] == "released"
        state = rt.review("managed_state")
        assert state["state"]["action_count"] >= 1
    finally:
        rt.close()


def test_heartbeat_runs_agent_managed_review_when_policy_allows(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.setup("Agent for heartbeat managed review test.")
        rt.commit_canon()
        rt.control("resume")
        _enable_managed(rt, agent_managed_trigger_sources=["heartbeat"])
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="heartbeat release", reason="test")
        tick = rt.tick(manual=False)
        assert tick["ok"] is True
        managed = tick.get("managed_review") or {}
        assert managed.get("status") in {"applied", "noop"}
        delayed = rt.reply("list")
        assert delayed["delayed_replies"][0]["status"] == "released"
        runs = rt.review("managed_runs")
        assert runs["managed_runs"]
    finally:
        rt.close()


def test_managed_review_loop_respects_daily_action_limit(hermes_home):
    rt = LifeEngineRuntime()
    try:
        _enable_managed(rt, agent_managed_daily_action_limit=1)
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="one", reason="test")
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="two", reason="test")
        out = rt.review("managed_run", trigger_source="manual")
        assert out["managed_run"]["applied_count"] == 1
        delayed = rt.reply("list")
        statuses = [d["status"] for d in delayed["delayed_replies"]]
        assert statuses.count("released") == 1
        second = rt.review("managed_run", trigger_source="manual")
        assert second["status"] in {"blocked", "skipped"}
        assert "limit" in " ".join(second.get("decision", {}).get("reasons", []))
    finally:
        rt.close()


def test_managed_review_slash_surface(hermes_home):
    rt = LifeEngineRuntime()
    try:
        _enable_managed(rt)
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="slash managed", reason="test")
    finally:
        rt.close()
    out = slash_life("review managed_preview")
    assert "managed_run" in out or "selected_item_ids" in out
