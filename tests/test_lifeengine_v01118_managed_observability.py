import tempfile

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.reply_gate import create_delayed_reply
from lifeengine.cli import slash_life


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01118_")
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


def test_schema_v38_and_managed_observability_tables(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 38
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "human_review_managed_observability_reports" in tables
        assert "human_review_managed_release_readiness_reports" in tables
    finally:
        conn.close()


def test_managed_observability_reports_disabled_state(hermes_home):
    rt = LifeEngineRuntime()
    try:
        out = rt.review("managed_observability")
        assert out["ok"] is True
        assert out["readiness_status"] in {"disabled", "needs_review", "blocked"}
        assert out["report_id"]
        reports = rt.review("managed_observability_reports")
        assert reports["reports"]
        got = rt.review("get_managed_observability", report_id=out["report_id"])
        assert got["report"]["id"] == out["report_id"]
        assert "Managed Review" in out["rendered"]
    finally:
        rt.close()


def test_managed_release_readiness_blocked_until_acceptance_and_stress(hermes_home):
    rt = LifeEngineRuntime()
    try:
        _enable_managed(rt)
        out = rt.review("managed_release_readiness")
        assert out["ok"] is True
        assert out["readiness_status"] in {"blocked", "ready_with_warnings"}
        assert any(c["name"] == "acceptance_passed" for c in out["checks"])
        reports = rt.review("managed_release_readiness_reports")
        assert reports["reports"]
    finally:
        rt.close()


def test_human_review_surfaces_managed_review_warnings(hermes_home):
    rt = LifeEngineRuntime()
    try:
        _enable_managed(rt)
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="pending", reason="test")
        out = rt.review("summary")
        assert "managed_review" in out["summary"]
        assert "自主管理" in out["rendered"]
        assert all(i["item_type"] != "managed_review_observability" for i in out["items"])
    finally:
        rt.close()


def test_managed_observability_slash_surface(hermes_home):
    out = slash_life("review managed_observability")
    assert "Managed Review" in out or "readiness_status" in out
