"""经营系统 P1: a recurring activity is now a fuller venture — operation_model
+ location are stored and flow onto materialized events, and an *active*
venture's materialized block is conflict-arbitrated so the agent is never
double-booked (一人不能分身)."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016p1"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，好奇而勤奋。")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _event_by_title(rt, title):
    evs = [e for e in rt.event_tool("list")["events"] if e["title"] == title]
    return evs[0] if evs else None


def _block_for_event(rt, event_id):
    r = rt.conn.execute(
        "SELECT start_ts, end_ts FROM schedule_blocks WHERE event_id=? ORDER BY start_ts LIMIT 1",
        (event_id,),
    ).fetchone()
    return (int(r["start_ts"]), int(r["end_ts"])) if r and r["start_ts"] is not None else None


def test_venture_stores_operation_model_and_location(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.activity("register", title="东市摆摊", cadence_kind="daily",
                    start_time="10:00", end_time="12:00", timezone="UTC",
                    operation_model="active", location_kind="fixed", location="十二城东市",
                    resource_costs={"money.lingzhu": 25})
        act = rt.activity("list")["activities"][0]
        assert act["operation_model"] == "active"
        assert act["location_kind"] == "fixed"
        assert act["location"] == "十二城东市"
        rt.tick(now="2026-06-15T13:00:00+00:00", manual=False)
        ev = _event_by_title(rt, "东市摆摊")
        assert ev is not None
        assert (ev.get("location") or {}).get("name") == "十二城东市"
    finally:
        rt.close()


def test_active_ventures_do_not_double_book(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        # two active ventures with OVERLAPPING preferred windows
        rt.activity("register", title="上午摆摊", cadence_kind="daily",
                    start_time="10:00", end_time="12:00", timezone="UTC",
                    operation_model="active", resource_costs={"money.lingzhu": 20})
        rt.activity("register", title="正午摆摊", cadence_kind="daily",
                    start_time="11:00", end_time="13:00", timezone="UTC",
                    operation_model="active", resource_costs={"money.lingzhu": 20})
        rt.tick(now="2026-06-15T14:00:00+00:00", manual=False)
        b1 = _block_for_event(rt, _event_by_title(rt, "上午摆摊")["id"])
        b2 = _block_for_event(rt, _event_by_title(rt, "正午摆摊")["id"])
        assert b1 and b2
        # the two blocks must not overlap — one was shifted to a free slot
        lo, hi = sorted([b1, b2])
        assert lo[1] <= hi[0], f"venture blocks overlap: {b1} vs {b2}"
    finally:
        rt.close()


def test_self_service_venture_materializes(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        # self_service operation_model is stored and the venture still materializes
        # an event (its true no-agent-time, passive settlement behaviour is P4; in
        # P1 it is conflict-arbitrated like any other so nothing double-books).
        rt.activity("register", title="自助货架", cadence_kind="daily",
                    start_time="10:00", end_time="12:00", timezone="UTC",
                    operation_model="self_service", resource_costs={"money.lingzhu": 10})
        act = rt.activity("list")["activities"][0]
        assert act["operation_model"] == "self_service"
        rt.tick(now="2026-06-15T13:00:00+00:00", manual=False)
        ev = _event_by_title(rt, "自助货架")
        assert ev is not None
        assert _block_for_event(rt, ev["id"]) is not None
    finally:
        rt.close()
