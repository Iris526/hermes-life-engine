from __future__ import annotations

import os
import shutil

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v07"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate_agent(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，允许自治规划，目标和资源都由 LifeOps 记录。")
    rt.commit_canon()
    rt.control("resume")


def test_manual_autonomy_run_commits_goal_next_event_with_receipt(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        goal = rt.goals("create", session_id="s7", turn_id="g1", title="准备七月考试", goal_type="study", priority=80)
        gid = goal["results"][0]["result"]["id"]

        out = rt.autonomy("run", session_id="s7", turn_id="a1")
        assert out["ok"] is True
        assert out["decision"]["status"] == "committed"
        assert out["commit"] and out["commit"]["receipt"]["facts"]

        events = rt.event_tool("list")["events"]
        auto_events = [e for e in events if e["title"] == "推进目标：准备七月考试"]
        assert auto_events
        event_id = auto_events[0]["id"]
        links = rt.goals("dependencies")  # smoke: goals API remains usable
        explained = rt.traces("explain", transaction_id=out["commit"]["transaction_id"])
        assert any(f["fact_kind"] == "event" for f in explained["facts"])
        assert rt.audit_final_output("我给自己安排了推进目标：准备七月考试。", session_id="s7", turn_id="a1") is None
        # The event was linked to the goal by CREATE_EVENT(goal_id=...).
        progress = rt.goals("progress", goal_id=gid)["progress"]
        assert any(link["event_id"] == event_id for link in progress["links"])
    finally:
        rt.close()


def test_heartbeat_autonomy_runs_when_gate_allows(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        rt.control("module", key="autonomy", value="low_spontaneity")
        rt.goals("create", title="建立健身习惯", goal_type="health", priority=75)

        tick = rt.tick(now="2026-06-07T09:00:00+00:00", manual=False)
        assert tick["ok"] is True
        assert tick["autonomy"]["decision"]["status"] in {"committed", "proposed"}
        assert tick["autonomy"]["commit"] is not None
        decisions = rt.autonomy("list")["decisions"]
        assert decisions and decisions[0]["result_transaction_id"]
        assert any(e["title"] == "推进目标：建立健身习惯" for e in rt.event_tool("list")["events"])
    finally:
        rt.close()


def test_low_energy_autonomy_prefers_recovery_event(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        rt.control("module", key="autonomy", value="full")
        rt.resources("define", key="energy", display_name="Energy", initial=5)
        rt.goals("create", title="完成创作项目", goal_type="creative", priority=90)

        tick = rt.tick(now="2026-06-07T10:00:00+00:00", manual=False)
        assert tick["autonomy"]["commit"] is not None
        events = rt.event_tool("list")["events"]
        assert any(e["title"] == "休息并恢复精力" for e in events)
    finally:
        rt.close()
