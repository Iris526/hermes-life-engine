import os
import tempfile

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.cli import slash_life


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01111_")
    monkeypatch.setenv("HERMES_HOME", d)
    return d


def test_schema_v31_and_policy_acceptance_tables(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 31
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "sleep_reply_dream_policy_conflict_reports" in tables
        assert "sleep_reply_dream_policy_exports" in tables
        assert "sleep_reply_dream_policy_imports" in tables
        assert "sleep_reply_dream_policy_acceptance_runs" in tables
        assert "sleep_reply_dream_policy_acceptance_scenarios" in tables
    finally:
        conn.close()


def test_policy_conflict_detection_reports_hard_conflicts(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.policy("set", policy_patch={"sleep": {"target_sleep_minutes": 0}, "reply": {"gate_mode": "strict", "call_words": []}})
        out = rt.policy("conflicts")
        assert out["validation"]["ok"] is False
        assert out["validation"]["conflict_count"] >= 2
        reports = rt.policy("conflict_reports")
        assert reports["reports"]
    finally:
        rt.close()


def test_policy_export_import_roundtrip(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.policy("preset", preset="night_owl")
        exp = rt.policy("export")
        assert os.path.exists(exp["path"])
        inspect = rt.policy("inspect_export", path=exp["path"])
        assert inspect["inspection"]["policy"]["profile"] == "night_owl"
        imp = rt.policy("import", owner_id="agent-import-target", path=exp["path"], apply=True)
        assert imp["status"] == "applied"
        imported = rt.policy("get", owner_id="agent-import-target")
        assert imported["policy"]["effective_policy"]["profile"] == "night_owl"
        assert rt.policy("exports")["exports"]
        assert rt.policy("imports", owner_id="agent-import-target")["imports"]
    finally:
        rt.close()


def test_policy_acceptance_runner_passes_and_isolated(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.policy("preset", preset="balanced")
        before = rt.policy("get")["policy"]["effective_policy"]["profile"]
        out = rt.policy("acceptance")
        assert out["ok"] is True
        assert out["passed"] == 6
        assert out["failed"] == 0
        assert out["synthetic_owner_id"] != "default-agent"
        after = rt.policy("get")["policy"]["effective_policy"]["profile"]
        assert after == before
        runs = rt.policy("acceptance_runs")
        assert runs["runs"]
        got = rt.policy("acceptance_get", acceptance_run_id=out["acceptance_run_id"])
        assert len(got["run"]["scenarios"]) == 6
    finally:
        rt.close()


def test_slash_policy_conflicts_export_and_acceptance(hermes_home):
    assert "conflicts" in slash_life("policy conflicts") or "validation" in slash_life("policy conflicts")
    assert "export_id" in slash_life("policy export")
    assert "acceptance_run_id" in slash_life("policy acceptance")
