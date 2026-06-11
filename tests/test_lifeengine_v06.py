from __future__ import annotations

import os
import shutil

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v06"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate_agent(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，支持长期目标、生活弧线和事件拆解。")
    rt.commit_canon()
    rt.control("resume")


def test_goal_and_life_arc_receipts_and_final_gate(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        goal = rt.goals("create", session_id="s6", turn_id="t6", title="准备七月考试", goal_type="study", progress=0, priority=80)
        assert goal["ok"] is True
        explained = rt.traces("explain", transaction_id=goal["transaction_id"])
        assert any(f["fact_kind"] == "goal" for f in explained["facts"])
        assert rt.audit_final_output("我的目标是准备七月考试。", session_id="s6", turn_id="t6") is None

        gid = goal["results"][0]["result"]["id"]
        arc = rt.goals("arc", title="考试准备生活弧线", arc_type="study", goal_id=gid, stage="起步", progress=0)
        assert arc["ok"] is True
        arcs = rt.goals("arcs")["life_arcs"]
        assert any(a["title"] == "考试准备生活弧线" for a in arcs)
    finally:
        rt.close()


def test_event_decomposition_goal_progress_and_dependencies(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        goal = rt.goals("create", title="通过模拟考试", goal_type="study", progress=0)
        gid = goal["results"][0]["result"]["id"]
        parent = rt.event_tool("create", title="准备模拟考试", event_type="study", status="planned")
        parent_id = parent["results"][0]["result"]["id"]
        decomp = rt.goals(
            "decompose",
            parent_event_id=parent_id,
            goal_id=gid,
            children=[
                {"title": "买模拟题教材", "event_type": "purchase", "weight": 1.0},
                {"title": "完成第一套模拟题", "event_type": "study", "weight": 1.0},
            ],
        )
        assert decomp["ok"] is True
        children = decomp["results"][0]["result"]["children"]
        assert len(children) == 2
        deps = rt.goals("dependencies", parent_event_id=parent_id)["dependencies"]
        assert len(deps) == 2

        rt.event_tool("complete", event_id=children[0]["id"], summary="买到了教材")
        progress = rt.goals("progress", goal_id=gid)["progress"]
        assert progress["computed_progress"] == 50
        updated = rt.goals("update_progress", goal_id=gid)
        assert updated["ok"] is True
        goals = rt.goals("list")["goals"]
        assert any(g["id"] == gid and g["progress"] == 50 for g in goals)

        rt.event_tool("complete", event_id=children[1]["id"], summary="完成第一套")
        recomputed = rt.goals("recompute_event", event_id=parent_id)
        assert recomputed["ok"] is True
        event = rt.traces("explain", event_id=parent_id)["event"]
        assert event["progress"] == 100
    finally:
        rt.close()


def test_decompose_requires_parent_event(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        try:
            rt.goals("decompose", parent_event_id="event_missing", children=[{"title": "子任务"}])
            assert False, "expected validation failure"
        except Exception as exc:
            assert "event not found" in str(exc)
    finally:
        rt.close()
