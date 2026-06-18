"""v0.16.0 recurring activities (营生): the heartbeat materializes a registered
occupation into one scheduled event per due day (idempotent), cadence is
respected, and cancel stops future occurrences — all engine-enforced."""
from __future__ import annotations

import os
import shutil
from datetime import date

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID, PLUGIN_VERSION
from lifeengine.db import _SCHEMA_VERSION


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime, desc: str = "测试 Agent，好奇而勤奋。"):
    rt.setup(desc)
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _titles(rt, title):
    return [e for e in rt.event_tool("list")["events"] if e["title"] == title]


def test_v016_schema_and_version(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert PLUGIN_VERSION == "0.17.0"
        assert _SCHEMA_VERSION >= 52
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 52
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"recurring_activities", "recurring_activity_occurrences"}.issubset(tables)
    finally:
        rt.close()


def test_daily_activity_materializes_once_per_day(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.activity("register", title="在东市摆摊", activity_type="work", cadence_kind="daily",
                    start_time="10:00", end_time="14:00", timezone="UTC",
                    resource_costs={"money.lingzhu": 30, "energy": -14})
        t1 = rt.tick(now="2026-06-15T15:00:00+00:00", manual=False)
        assert t1["recurring_activities"]["status"] == "ok"
        assert t1["recurring_activities"]["count"] == 1
        stalls = _titles(rt, "在东市摆摊")
        assert len(stalls) == 1
        assert stalls[0]["resource_costs"].get("money.lingzhu") == 30
        # idempotent: a second tick the same day does not duplicate
        t2 = rt.tick(now="2026-06-15T15:30:00+00:00", manual=False)
        assert t2["recurring_activities"]["count"] == 0
        assert len(_titles(rt, "在东市摆摊")) == 1
        # next day: a fresh occurrence is materialized
        t3 = rt.tick(now="2026-06-16T15:00:00+00:00", manual=False)
        assert t3["recurring_activities"]["count"] == 1
        assert len(_titles(rt, "在东市摆摊")) == 2
    finally:
        rt.close()


def test_cancel_stops_materialization(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        reg = rt.activity("register", title="夜市摆摊", cadence_kind="daily",
                          start_time="19:00", end_time="22:00", resource_costs={"money.lingzhu": 20})
        aid = reg["receipt"]["facts"][0]["evidence"]["activity_id"]
        rt.activity("cancel", activity_id=aid)
        acts = rt.activity("list")["activities"]
        assert any(a["id"] == aid and a["status"] == "cancelled" for a in acts)
        t = rt.tick(now="2026-06-15T23:00:00+00:00", manual=False)
        assert t["recurring_activities"]["count"] == 0
        assert _titles(rt, "夜市摆摊") == []
    finally:
        rt.close()


def test_weekly_cadence_respects_weekday(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        day = date(2026, 6, 15)  # compute its weekday so the test is calendar-proof
        wd = day.weekday()
        other = (wd + 1) % 7
        rt.activity("register", title="赶集日摆摊", cadence_kind="weekly", weekdays=[other],
                    start_time="09:00", end_time="12:00", resource_costs={"money.lingzhu": 40})
        t_off = rt.tick(now="2026-06-15T13:00:00+00:00", manual=False)
        assert t_off["recurring_activities"]["count"] == 0
        assert _titles(rt, "赶集日摆摊") == []
        # update to today's weekday → materializes today
        acts = rt.activity("list")["activities"]
        aid = acts[0]["id"]
        rt.activity("update", activity_id=aid, weekdays=[wd])
        t_on = rt.tick(now="2026-06-15T13:30:00+00:00", manual=False)
        assert t_on["recurring_activities"]["count"] == 1
        assert len(_titles(rt, "赶集日摆摊")) == 1
    finally:
        rt.close()
