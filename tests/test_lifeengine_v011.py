from __future__ import annotations

import os
import shutil

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v011"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.11.0 Event V2 测试 Agent。")
    rt.commit_canon()
    rt.control("resume")


def test_v011_version_schema_and_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.12.9"
        assert _SCHEMA_VERSION >= 29
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        for table in [
            "event_state_transitions",
            "schedule_block_state_transitions",
            "action_state_transitions",
            "agent_realtime_state",
            "agent_state_snapshots",
        ]:
            assert rt.conn.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone(), table
    finally:
        rt.close()


def test_v011_event_v2_attributes_and_transition_history(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.event_tool(
            "create",
            title="第七城雨棚巷结果节点复查",
            event_type="repair_task",
            event_category="work",
            activity_domain="craft_commission",
            subtype="rain_shelter_node_review",
            tags=["commission", "minor_job"],
            attributes={"rank": "D", "expected_pay": "low"},
            location={"name": "第七城雨棚巷"},
            interruptibility={"level": "soft_interruptible", "max_delay_minutes": 30},
            state_effects={"energy": -12, "focus": -8},
        )
        event = out["results"][0]["result"]
        assert event["event_category"] == "work"
        assert event["activity_domain"] == "craft_commission"
        assert event["tags"] == ["commission", "minor_job"]
        assert event["attributes"]["rank"] == "D"
        got = rt.event_tool("get", event_id=event["id"])
        assert got["event"]["location"]["name"] == "第七城雨棚巷"
        assert len(got["transitions"]) == 1
        assert got["transitions"][0]["from_status"] is None
        assert got["transitions"][0]["to_status"] == "planned"
    finally:
        rt.close()


def test_v011_schedule_and_completion_have_full_transition_history(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ev = rt.event_tool("create", title="做半天小委托", event_type="work", event_category="work")["results"][0]["result"]
        sch = rt.event_tool(
            "schedule",
            event_id=ev["id"],
            start="2026-06-10T09:00:00+00:00",
            end="2026-06-10T12:00:00+00:00",
            interruptibility={"level": "soft_interruptible"},
        )["results"][0]["result"]
        assert sch["planned_duration_minutes"] == 180
        rt.event_tool("complete", event_id=ev["id"], summary="小委托完成")
        got = rt.event_tool("get", event_id=ev["id"])
        statuses = [t["to_status"] for t in got["transitions"]]
        assert statuses == ["planned", "scheduled", "completed"]
        assert got["event"]["actual_end"]
        assert got["event"]["closed_at"]
        assert got["event"]["actual_duration_minutes"] == 180
        strans = rt.event_tool("schedule_transitions", schedule_block_id=sch["id"])["transitions"]
        assert [t["to_status"] for t in strans] == ["planned", "completed"]
        explain = rt.traces("explain", event_id=ev["id"])
        assert explain["event_state_transitions"]
        assert explain["schedule_state_transitions"]
        assert explain["action_state_transitions"]
    finally:
        rt.close()


def test_v011_realtime_state_and_doctor_lease_warning(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ev = rt.event_tool("create", title="不可打断测试事件", event_category="work")["results"][0]["result"]
        out = rt.event_tool(
            "update_state",
            mode="uninterruptible_event",
            active_event_id=ev["id"],
            interruptibility_level="uninterruptible",
            reply_mode="defer_until_event_end",
            lease_expires_at="2000-01-01T00:00:00+00:00",
            body_state={"fatigue": 40},
        )
        assert out["ok"] is True
        state = rt.event_tool("state")["realtime_state"]
        assert state["mode"] == "uninterruptible_event"
        assert state["body_state"]["fatigue"] == 40
        assert rt.conn.execute("SELECT COUNT(*) FROM agent_state_snapshots").fetchone()[0] >= 1
        doctor = rt.doctor(write_audit=False)
        assert doctor["checks"]["realtime_state_lease"]["status"] in {"warn", "warning"}
    finally:
        rt.close()
