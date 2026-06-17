"""v0.15.0 mood subsystem: agent-triggerable emotional reactions that are
bounded, auditable, fed back into behavior, and keep the doctor invariant."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.resources import reconcile_resources
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine import emotion


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v015"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime, desc: str = "测试 Agent，好奇而勤奋。"):
    rt.setup(desc)
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def test_mood_reaction_moves_gauge_and_logs(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        before = emotion.current_mood(rt.conn, "agent", DEFAULT_AGENT_ID)
        out = rt.mood("react", delta=8, reason="收到 Ringo 的消息很开心")
        assert out["ok"] is True
        after = emotion.current_mood(rt.conn, "agent", DEFAULT_AGENT_ID)
        assert after == pytest.approx((before or 0) + 8)
        st = rt.mood("status")
        assert st["band"] in {"low", "neutral", "high"}
        assert any("Ringo" in (r.get("reason") or "") for r in st["recent"])
    finally:
        rt.close()


def test_mood_reaction_is_bounded(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.mood("react", delta=999, reason="爆表测试")
        st = rt.mood("status")
        assert abs(st["recent"][0]["delta"]) <= 20.0
    finally:
        rt.close()


def test_mood_bias_bands():
    assert emotion.mood_bias(-50)["band"] == "low"
    assert emotion.mood_bias(0)["band"] == "neutral"
    assert emotion.mood_bias(50)["band"] == "high"
    assert emotion.mood_bias(-50)["proactive_urge"] < 0
    assert emotion.mood_bias(50)["proactive_urge"] > 0


def test_mood_reaction_keeps_resource_invariant(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.mood("react", delta=-12, reason="委托出了点小岔子")
        rt.mood("react", delta=5, reason="后来补救好了")
        report = reconcile_resources(rt.conn, "agent", DEFAULT_AGENT_ID)
        assert not report.get("mismatches")
    finally:
        rt.close()
