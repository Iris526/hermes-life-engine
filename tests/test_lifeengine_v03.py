from __future__ import annotations

import os
import shutil

import pytest

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v03"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，资源必须先定义，heartbeat 手动。")
    rt.commit_canon()
    rt.control("resume")


def test_convenience_event_writes_lifeops_receipt_and_final_gate(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.event_tool(
            "create",
            session_id="s1",
            turn_id="t1",
            title="明天下午买裙子",
            source="agent_prediction",
        )
        assert out["ok"] is True
        tx_id = out["transaction_id"]
        explained = rt.traces("explain", transaction_id=tx_id)
        assert explained["facts"]
        assert rt.audit_final_output("我明天下午计划买裙子。", session_id="s1", turn_id="t1") is None
        advisory = rt.audit_final_output("我今天中午吃了咖喱饭。", session_id="s2", turn_id="t2")
        assert advisory is None
        rt.control("module", key="final_audit", value="strict")
        blocked = rt.audit_final_output("我今天中午吃了咖喱饭。", session_id="s3", turn_id="t3")
        assert blocked and "缺少证据" in blocked
    finally:
        rt.close()


def test_resource_delta_requires_defined_resource_and_reconcile(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        with pytest.raises(Exception):
            rt.resources("delta", resource_key="magic", delta=5, operation="produce", reason="undefined")
        rt.resources("define", key="energy", display_name="Energy", initial=10)
        rt.resources("delta", resource_key="energy", delta=-3, operation="consume", reason="test")
        check = rt.resources("reconcile")
        assert check["ok"] is True
        assert (check.get("reconcile") or check.get("reconciliation"))["ok"] is True
    finally:
        rt.close()


def test_schedule_creates_wake_job_and_heartbeat_completes_event(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.resources("define", key="energy", display_name="Energy", initial=50)
        ev = rt.event_tool("create", title="下午学习", source="agent_prediction", resource_costs={"energy": -5})
        event_id = ev["results"][0]["result"]["id"]
        rt.event_tool(
            "schedule",
            event_id=event_id,
            start="2026-06-07T10:00:00+00:00",
            end="2026-06-07T11:00:00+00:00",
            timezone_name="UTC",
        )
        tick = rt.tick(now="2026-06-07T11:01:00+00:00")
        assert tick["ok"] is True
        events = rt.event_tool("list", status="completed")["events"]
        assert any(e["id"] == event_id for e in events)
    finally:
        rt.close()


def test_heartbeat_failed_lifeops_roll_back_partial_event_completion(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.control("module", key="autonomy", value="off")
        rt.control("module", key="managed_review_loop", value="off")
        ev = rt.event_tool(
            "create",
            title="带未定义收益资源的事件",
            event_type="work",
            source="agent_prediction",
            resource_costs={"stock.undefined": 1},
        )
        event_id = ev["results"][0]["result"]["id"]
        sch = rt.event_tool(
            "schedule",
            event_id=event_id,
            start="2026-06-07T10:00:00+00:00",
            end="2026-06-07T11:00:00+00:00",
            timezone_name="UTC",
        )
        block_id = sch["results"][0]["result"]["id"]

        tick = rt.tick(now="2026-06-07T11:01:00+00:00")

        assert tick["ok"] is False
        assert tick["status"] == "partial"
        assert any(j.get("status") == "failed" for j in tick["wake_jobs"])
        tx_count = rt.conn.execute(
            "SELECT COUNT(*) FROM life_transactions WHERE source='execution_simulator'",
        ).fetchone()[0]
        op_count = rt.conn.execute(
            "SELECT COUNT(*) FROM life_ops WHERE transaction_id IN (SELECT id FROM life_transactions WHERE source='execution_simulator')",
        ).fetchone()[0]
        event = rt.conn.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()
        block = rt.conn.execute("SELECT status FROM schedule_blocks WHERE id=?", (block_id,)).fetchone()
        actions = rt.conn.execute("SELECT COUNT(*) FROM actions WHERE event_id=?", (event_id,)).fetchone()[0]
        results = rt.conn.execute("SELECT COUNT(*) FROM results WHERE event_id=?", (event_id,)).fetchone()[0]
        assert tx_count == 0
        assert op_count == 0
        assert event["status"] == "scheduled"
        assert block["status"] == "planned"
        assert actions == 0
        assert results == 0
    finally:
        rt.close()


def test_heartbeat_completes_in_progress_capacity_shortage_block(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.control("module", key="autonomy", value="off")
        rt.control("module", key="daily_rhythm", value="off")
        rt.control("module", key="managed_review_loop", value="off")
        rt.resources("define", key="focus", display_name="Focus", resource_class="capacity", initial=0, min_value=0, max_value=100)
        ev = rt.event_tool(
            "create",
            title="常明净愿灯维护",
            event_type="maintenance",
            source="agent_prediction",
            resource_costs={"focus": -1},
        )
        event_id = ev["results"][0]["result"]["id"]
        sch = rt.event_tool(
            "schedule",
            event_id=event_id,
            start="2026-06-07T18:05:00+00:00",
            end="2026-06-07T18:20:00+00:00",
            timezone_name="UTC",
        )
        block_id = sch["results"][0]["result"]["id"]
        rt.event_tool("transition", event_id=event_id, status="in_progress")

        tick = rt.tick(now="2026-06-07T18:21:00+00:00")

        assert tick["ok"] is True
        event = rt.conn.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()
        block = rt.conn.execute("SELECT status FROM schedule_blocks WHERE id=?", (block_id,)).fetchone()
        decision = rt.conn.execute(
            "SELECT decision_type, status, reason FROM execution_decisions WHERE event_id=? ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        assert event["status"] == "completed"
        assert block["status"] == "completed"
        assert decision["decision_type"] == "completed"
        assert decision["status"] == "committed"
    finally:
        rt.close()


def test_heartbeat_postpones_in_progress_hard_shortage_legally(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.control("module", key="autonomy", value="off")
        rt.control("module", key="daily_rhythm", value="off")
        rt.control("module", key="managed_review_loop", value="off")
        rt.resources("define", key="money.coin", display_name="Coin", resource_class="currency", initial=0, min_value=0)
        ev = rt.event_tool(
            "create",
            title="买一包符纸",
            event_type="purchase",
            source="agent_prediction",
            resource_costs={"money.coin": -5},
        )
        event_id = ev["results"][0]["result"]["id"]
        sch = rt.event_tool(
            "schedule",
            event_id=event_id,
            start="2026-06-07T10:00:00+00:00",
            end="2026-06-07T10:30:00+00:00",
            timezone_name="UTC",
        )
        block_id = sch["results"][0]["result"]["id"]
        rt.event_tool("transition", event_id=event_id, status="in_progress")

        tick = rt.tick(now="2026-06-07T10:31:00+00:00")

        assert tick["ok"] is True
        old_block = rt.conn.execute("SELECT status FROM schedule_blocks WHERE id=?", (block_id,)).fetchone()
        event = rt.conn.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()
        new_blocks = rt.conn.execute(
            "SELECT COUNT(*) FROM schedule_blocks WHERE event_id=? AND status='planned'",
            (event_id,),
        ).fetchone()[0]
        decision = rt.conn.execute(
            "SELECT decision_type, status FROM execution_decisions WHERE event_id=? ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        assert old_block["status"] == "rescheduled"
        assert event["status"] == "scheduled"
        assert new_blocks == 1
        assert decision["decision_type"] == "postponed"
        assert decision["status"] == "committed"
    finally:
        rt.close()
