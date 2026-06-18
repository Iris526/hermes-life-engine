"""The heartbeat reflects what the agent is *currently* doing: a scheduled
event whose window covers now flips realtime state to busy + the event to
in_progress (so the WebUI sprite/dialogue show work, not idle); once the window
passes it falls back to idle."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016rt"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，好奇而勤奋。")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _state(rt):
    return rt.event_tool("state")["realtime_state"]


def _schedule(rt, title, start, end, **kw):
    ev = rt.event_tool("create", title=title, event_type=kw.get("event_type", "work"),
                       event_category=kw.get("event_category", "work"), status="planned")
    eid = ev["results"][0]["result"]["id"]
    rt.event_tool("schedule", event_id=eid, start=start, end=end, timezone_name="UTC",
                  interruptibility=kw.get("interruptibility") or {})
    return eid


def test_active_window_makes_agent_busy_and_event_in_progress(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        eid = _schedule(rt, "归明观摆摊卖符", "2026-06-15T11:50:00+00:00", "2026-06-15T12:50:00+00:00")
        rt.tick(now="2026-06-15T12:00:00+00:00", manual=False)   # now is inside the window
        st = _state(rt)
        assert st["mode"] == "busy"
        assert st["active_event_id"] == eid
        assert rt.event_tool("get", event_id=eid)["event"]["status"] == "in_progress"
    finally:
        rt.close()


def test_uninterruptible_window_sets_that_mode(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        _schedule(rt, "闭关绘符", "2026-06-15T11:50:00+00:00", "2026-06-15T12:50:00+00:00",
                  interruptibility={"level": "uninterruptible"})
        rt.tick(now="2026-06-15T12:00:00+00:00", manual=False)
        st = _state(rt)
        assert st["mode"] == "uninterruptible_event"
        assert st["reply_mode"] == "defer_until_event_end"
    finally:
        rt.close()


def test_falls_back_to_idle_after_window(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        _schedule(rt, "归明观摆摊卖符", "2026-06-15T11:50:00+00:00", "2026-06-15T12:50:00+00:00")
        rt.tick(now="2026-06-15T12:00:00+00:00", manual=False)
        assert _state(rt)["mode"] == "busy"
        rt.tick(now="2026-06-15T13:30:00+00:00", manual=False)   # window has passed
        st = _state(rt)
        assert st["mode"] == "idle"
        assert not st.get("active_event_id")
    finally:
        rt.close()


def test_sleep_state_is_not_overridden(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        # asleep + an active-window event → sleep wins, sync must not flip to busy
        _schedule(rt, "归明观摆摊卖符", "2026-06-15T11:50:00+00:00", "2026-06-15T12:50:00+00:00")
        rt.event_tool("update_state", mode="asleep")
        rt.tick(now="2026-06-15T12:00:00+00:00", manual=False)
        assert _state(rt)["mode"] == "asleep"
    finally:
        rt.close()
