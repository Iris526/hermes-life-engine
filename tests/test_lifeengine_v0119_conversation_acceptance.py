from __future__ import annotations

import os
from pathlib import Path

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home_v0119"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def test_v0119_schema_and_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.6"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "sleep_reply_dream_conversation_acceptance_runs" in tables
        assert "sleep_reply_dream_conversation_acceptance_scenarios" in tables
    finally:
        rt.close()


def test_sleep_reply_dream_conversation_acceptance_runs_synthetic_scenarios(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        out = rt.upgrade("sleep_reply_dream_conversation_acceptance")
        assert out["ok"] is True, out
        assert out["summary"]["passed"] == 6
        assert out["summary"]["failed"] == 0
        assert out["synthetic_owner_id"] != "default-agent"
        keys = {s["key"] for s in out["scenarios"]}
        assert "CRD01_CHAT_DELAYS_SLEEP_ACTUAL_DIFFERS" in keys
        assert "CRD02_CALL_WAKES_SLEEP_AND_RELEASES_DIGEST" in keys
        assert "CRD03_WAKE_DREAM_SHARE_INTENT" in keys
        assert "CRD05_INTERRUPTED_SLEEP_AFFECTS_NEXT_EXECUTION" in keys
        runs = rt.upgrade("sleep_reply_dream_conversation_acceptance_runs")
        assert runs["runs"]
        got = rt.upgrade("sleep_reply_dream_conversation_acceptance_get", acceptance_run_id=out["acceptance_run_id"])
        assert got["ok"] is True
        assert len(got["run"]["scenarios"]) == 6
        assert all(s["status"] == "passed" for s in got["run"]["scenarios"])
    finally:
        rt.close()


def test_conversation_acceptance_aliases(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        out = rt.upgrade("crd_acceptance")
        assert out["ok"] is True, out
        runs = rt.upgrade("crd_acceptance_runs")
        assert any(r["id"] == out["acceptance_run_id"] for r in runs["runs"])
    finally:
        rt.close()
