from __future__ import annotations

import os
import shutil

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v091"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent；v0.9.1 doctor health check。")
    rt.commit_canon()
    rt.control("resume")


def test_doctor_reports_ok_for_fresh_active_agent(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.resources("define", key="energy", display_name="Energy", initial=30)
        out = rt.doctor(include_samples=True)
        assert out["ok"] is True
        assert out["status"] in {"ok", "warn", "warning"}
        assert {"journal_hash_chain", "resources", "event_lifecycle", "wake_jobs"}.issubset(set(out["checks"].keys()))
        runtime_names = {c["name"] for c in out["runtime_checks"]}
        assert {"sqlite_vec", "schema_version", "resource_ledger"}.issubset(runtime_names)
    finally:
        rt.close()


def test_doctor_detects_resource_ledger_drift(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.resources("define", key="energy", display_name="Energy", initial=30)
        with rt.conn:
            rt.conn.execute(
                "UPDATE resource_accounts SET current_value=999 WHERE owner_kind='agent' AND owner_id='default-agent' AND resource_key='energy'"
            )
        out = rt.doctor(include_samples=True)
        assert out["ok"] is False
        resource_check = out["checks"]["resources"]
        assert resource_check["ok"] is False
        assert resource_check.get("mismatches")
    finally:
        rt.close()


def test_lifecycle_validator_rejects_reopening_completed_event(tmp_path):
    import pytest
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ev = rt.event_tool("create", title="完成一个闭环测试事件", source="agent_prediction")
        event_id = ev["results"][0]["result"]["id"]
        rt.event_tool("complete", event_id=event_id, summary="done")
        with pytest.raises(Exception):
            rt.event_tool("transition", event_id=event_id, status="planned", reason="invalid reopen")
    finally:
        rt.close()


def test_lifecycle_validator_rejects_scheduling_terminal_event(tmp_path):
    import pytest
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ev = rt.event_tool("create", title="已完成后不能再排期", source="agent_prediction")
        event_id = ev["results"][0]["result"]["id"]
        rt.event_tool("complete", event_id=event_id, summary="done")
        with pytest.raises(Exception):
            rt.event_tool(
                "schedule",
                event_id=event_id,
                start="2026-06-08T10:00:00+00:00",
                end="2026-06-08T11:00:00+00:00",
                timezone_name="UTC",
            )
    finally:
        rt.close()
