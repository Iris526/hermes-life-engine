from __future__ import annotations

import os
import shutil
import sqlite3

import pytest

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.doctor import check_trace_coverage
from lifeengine.receipts import claim_matches_evidence
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.cli import slash_life


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v010"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.11.0 测试 Agent。")
    rt.commit_canon()
    rt.control("resume")


def test_v010_version_schema_and_new_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.4"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        for table in [
            "trace_coverage_reports",
            "failed_lifeops_audits",
            "acceptance_reports",
            "v1_rc_checklists",
            "api_freeze_snapshots",
            "command_surface_profiles",
        ]:
            assert rt.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone(), table
    finally:
        rt.close()


def test_v010_final_gate_semantic_matching_does_not_use_time_overlap():
    assert claim_matches_evidence("我今天中午吃了咖喱饭。", ["今天中午吃了咖喱饭"])
    assert not claim_matches_evidence("我今天中午买了一条裙子。", ["今天中午吃了咖喱饭"])
    assert not claim_matches_evidence("我今天中午去了巴黎。", ["今天中午吃了咖喱饭"])
    assert not claim_matches_evidence("我今天中午吃了咖喱饭。", ["明天下午买裙子 status=planned"])


def test_v010_failed_lifeops_leave_durable_trace_without_transaction(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        before = rt.conn.execute("SELECT COUNT(*) FROM life_transactions").fetchone()[0]
        with pytest.raises(Exception):
            rt.commit_ops([
                {
                    "type": "RESOURCE_DELTA",
                    "payload": {
                        "resource_key": "undefined.energy",
                        "delta": -1,
                        "reason": "should fail",
                    },
                }
            ])
        after = rt.conn.execute("SELECT COUNT(*) FROM life_transactions").fetchone()[0]
        assert after == before
        assert rt.conn.execute("SELECT COUNT(*) FROM trace_runs WHERE trace_type='life_commit_failed'").fetchone()[0] >= 1
        assert rt.conn.execute("SELECT COUNT(*) FROM audit_log WHERE audit_type='life_commit_failed'").fetchone()[0] >= 1
        assert rt.conn.execute("SELECT COUNT(*) FROM failed_lifeops_audits").fetchone()[0] >= 1
    finally:
        rt.close()


def test_v010_trace_explain_event_and_trace_coverage(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.event_tool("create", title="完成第一章复习", event_type="study", status="planned")
        event_id = out["results"][0]["result"]["id"]
        rt.event_tool("schedule", event_id=event_id, start="2026-06-10T09:00:00+00:00", end="2026-06-10T10:00:00+00:00")
        explain = rt.traces("explain", event_id=event_id)
        for key in ["event", "schedule_blocks", "wake_jobs", "actions", "results", "resource_ledger", "journal"]:
            assert key in explain
        cov = check_trace_coverage(rt.conn, "agent", "default-agent", write_report=True)
        assert cov["ok"] is True
        assert rt.conn.execute("SELECT COUNT(*) FROM trace_coverage_reports").fetchone()[0] >= 1
    finally:
        rt.close()


def test_v010_acceptance_release_surfaces_and_simple_human_commands(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        integration = rt.upgrade("integration_check")
        assert integration["ok"] is True
        surface = rt.upgrade("surface")
        assert surface["ok"] is True
        assert len(surface["surface"]["minimal_human_commands"]) <= 12
        freeze = rt.upgrade("api_freeze")
        assert freeze["ok"] is True
        acceptance = rt.upgrade("acceptance")
        assert acceptance["ok"] is True
        assert acceptance["summary"]["passed"] == acceptance["summary"]["scenarios"] == 5
        release = rt.upgrade("release_readiness")
        assert release["ok"] is True
    finally:
        rt.close()


def test_v010_slash_upgrade_acceptance_and_help(tmp_path):
    fresh_home(tmp_path)
    help_text = slash_life("help")
    assert "/life setup" in help_text
    assert "通常不需要人类记住内部工具" in help_text
    advanced = slash_life("advanced")
    assert "acceptance" in advanced
    result = slash_life("upgrade acceptance")
    assert '"ok": true' in result
