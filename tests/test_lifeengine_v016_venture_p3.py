"""经营系统 P3: opportunity triggers — 委托/客人 arrive on their own per an
arrival rate (engine-driven, not improvised on request), each landing as a
conflict-arbitrated event that carries the pay."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine import venture


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016p3"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，好奇而勤奋。")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _opp_events(rt, title):
    return [e for e in rt.event_tool("list")["events"]
            if e["title"] == title and (e.get("attributes") or {}).get("opportunity")]


def _block(rt, event_id):
    r = rt.conn.execute("SELECT start_ts, end_ts FROM schedule_blocks WHERE event_id=? LIMIT 1", (event_id,)).fetchone()
    return (int(r["start_ts"]), int(r["end_ts"])) if r and r["start_ts"] is not None else None


def test_opportunity_target_is_deterministic():
    act = {"id": "recact_x", "arrival": {"per_day": 2}}
    assert venture.opportunity_target(act, "2026-06-15") == 2
    assert venture.opportunity_target({"id": "recact_x", "arrival": {"per_day": 0}}, "2026-06-15") == 0
    # fractional per_day averages out but is reproducible for a given day
    a = {"id": "recact_y", "arrival": {"per_day": 1.5}}
    assert venture.opportunity_target(a, "2026-06-15") == venture.opportunity_target(a, "2026-06-15")


def test_opportunities_arrive_on_their_own(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        reg = rt.activity("register", title="接净符委托", trigger_kind="opportunity",
                          arrival={"per_day": 2, "duration_minutes": 60}, timezone="UTC",
                          location_kind="flexible", location="十二城",
                          resource_costs={"money.lingzhu": 50, "energy": -20})
        aid = reg["receipt"]["facts"][0]["evidence"]["activity_id"]
        t = rt.tick(now="2026-06-15T09:00:00+00:00", manual=False)
        assert t["venture_opportunities"]["status"] == "ok"
        assert t["venture_opportunities"]["count"] == 2   # per_day=2 → 2 land, unasked
        evs = _opp_events(rt, "接净符委托")
        assert len(evs) == 2
        # each委托 carries the pay
        assert evs[0]["resource_costs"].get("money.lingzhu") == 50
        # an opportunity venture is NOT materialized on a cadence
        occ = rt.conn.execute("SELECT COUNT(*) c FROM venture_occurrences WHERE activity_id=?", (aid,)).fetchone()
        assert occ["c"] == 0
        # the two arrivals are conflict-arbitrated → non-overlapping
        b = sorted([_block(rt, e["id"]) for e in evs])
        assert b[0] and b[1] and b[0][1] <= b[1][0]
        # idempotent within the day: re-ticking does not pile on more arrivals
        t2 = rt.tick(now="2026-06-15T10:00:00+00:00", manual=False)
        assert t2["venture_opportunities"]["count"] == 0
        assert len(_opp_events(rt, "接净符委托")) == 2
    finally:
        rt.close()


def test_new_day_brings_fresh_opportunities(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.activity("register", title="接委托", trigger_kind="opportunity",
                    arrival={"per_day": 1, "duration_minutes": 60}, timezone="UTC",
                    resource_costs={"money.lingzhu": 40})
        rt.tick(now="2026-06-15T09:00:00+00:00", manual=False)
        n1 = len(_opp_events(rt, "接委托"))
        rt.tick(now="2026-06-17T09:00:00+00:00", manual=False)  # a later day
        n2 = len(_opp_events(rt, "接委托"))
        assert n2 > n1  # fresh arrivals on the new day
    finally:
        rt.close()
