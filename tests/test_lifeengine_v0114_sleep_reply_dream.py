from __future__ import annotations

import os
from pathlib import Path

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home_v0114"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.11.4 sleep/reply/dream acceptance test agent")
    rt.commit_canon()
    rt.control("resume")


def _core_sleep(rt: LifeEngineRuntime) -> str:
    plan = rt.sleep_tool("plan", planned_sleep_at="2026-06-10T23:00:00+00:00", planned_wake_at="2026-06-11T07:00:00+00:00", timezone_name="UTC")
    plan_id = plan["receipt"]["facts"][0]["evidence"]["sleep_plan_id"]
    rt.sleep_tool("start", sleep_plan_id=plan_id, now="2026-06-10T23:00:00+00:00")
    wake = rt.sleep_tool("wake", sleep_plan_id=plan_id, now="2026-06-11T07:00:00+00:00")
    return wake["receipt"]["facts"][0]["evidence"]["sleep_session_id"]


def test_v0114_schema_and_acceptance_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.5"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"dream_repair_runs", "sleep_reply_dream_acceptance_runs", "sleep_reply_dream_acceptance_scenarios"}.issubset(tables)
    finally:
        rt.close()


def test_dream_audit_repair_applies_proposed_ops_and_resolves_findings(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ev = rt.event_tool("create", title="过期未结算小任务", event_type="work", event_category="work")
        event_id = ev["receipt"]["facts"][0]["evidence"]["event_id"]
        block = rt.event_tool("schedule", event_id=event_id, start="2026-06-10T10:00:00+00:00", end="2026-06-10T11:00:00+00:00")
        block_id = block["receipt"]["facts"][0]["evidence"]["schedule_block_id"]
        sleep_session_id = _core_sleep(rt)
        dream = rt.dream("run", sleep_session_id=sleep_session_id, force=True, create_share_intent=False)
        run_id = dream["receipt"]["facts"][0]["evidence"]["dream_run_id"]
        findings = rt.dream("findings", dream_run_id=run_id)["findings"]
        assert any(f["finding_type"] == "stale_schedule_block" and f["target_id"] == block_id for f in findings)
        preview = rt.dream("repair_plan", dream_run_id=run_id)
        assert any(op["type"] == "UPDATE_SCHEDULE_BLOCK_STATUS" for op in preview["ops"])
        repaired = rt.dream("repair", dream_run_id=run_id)
        assert repaired["ok"] is True
        assert repaired["commit"]["receipt"]["facts"]
        status = rt.conn.execute("SELECT status FROM schedule_blocks WHERE id=?", (block_id,)).fetchone()[0]
        assert status == "missed"
        resolved = rt.conn.execute("SELECT status,resolved_by_tx_id FROM dream_audit_findings WHERE target_id=?", (block_id,)).fetchone()
        assert resolved["status"] == "resolved"
        assert resolved["resolved_by_tx_id"] == repaired["commit"]["transaction_id"]
        repairs = rt.dream("repairs", dream_run_id=run_id)["repairs"]
        assert repairs and repairs[0]["status"] == "applied"
    finally:
        rt.close()


def test_sleep_reply_dream_acceptance_surface_records_scenarios(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.upgrade("sleep_reply_dream_acceptance")
        assert out["ok"] is True
        assert out["summary"]["scenarios"] == 6
        assert out["summary"]["failed"] == 0
        detail = rt.upgrade("sleep_reply_dream_acceptance_get", acceptance_run_id=out["acceptance_run_id"])
        assert len(detail["run"]["scenarios"]) == 6
    finally:
        rt.close()
