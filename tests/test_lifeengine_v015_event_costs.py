"""v0.15.0 agent-judged event costs: the engine fills a sane baseline only when
the agent gives none for a recognized activity, and the agent's own estimate
(including an explicit {} = free) always wins."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine import event_costs


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v015c"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime, desc: str = "测试 Agent，好奇而勤奋。"):
    rt.setup(desc)
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _event(rt, eid):
    return rt.event_tool("get", event_id=eid)["event"]


def test_estimator_scales_and_skips_unknown():
    # recognized type scales with duration
    short = event_costs.estimate_event_cost("work", 30)
    long = event_costs.estimate_event_cost("work", 180)
    assert short["energy"] < 0 and long["energy"] < short["energy"]
    # pleasant types carry a mood bump
    assert event_costs.estimate_event_cost("relationship", 60).get("mood", 0) > 0
    # unknown/marker types are never guessed
    assert event_costs.estimate_event_cost("other", 60) == {}
    assert event_costs.estimate_event_cost(None, 60) == {}


def test_create_event_fills_baseline_when_absent(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        out = rt.event_tool("create", title="接一个净符委托", event_type="commission",
                            planned_start="2026-06-07T13:00:00+00:00",
                            planned_end="2026-06-07T15:00:00+00:00")
        eid = out["results"][0]["result"]["id"]
        ev = _event(rt, eid)
        assert ev["resource_costs"].get("energy", 0) < 0  # not free anymore
    finally:
        rt.close()


def test_explicit_costs_win_and_empty_means_free(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        # explicit estimate is preserved verbatim
        out = rt.event_tool("create", title="写报告", event_type="work", resource_costs={"energy": -3})
        ev = _event(rt, out["results"][0]["result"]["id"])
        assert ev["resource_costs"] == {"energy": -3}
        # explicit {} means genuinely free
        out2 = rt.event_tool("create", title="发呆一会儿", event_type="work", resource_costs={})
        ev2 = _event(rt, out2["results"][0]["result"]["id"])
        assert ev2["resource_costs"] == {}
    finally:
        rt.close()


def test_estimate_cost_action(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        res = rt.event_tool("estimate_cost", event_type="study", duration_minutes=60)
        assert res["ok"] and res["resource_costs"]["energy"] < 0
    finally:
        rt.close()
