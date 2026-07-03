"""轴三-4：观点应反哺 autonomy 的目标选择。"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine import events as event_ops
from lifeengine import goals as goal_ops
from lifeengine import opinions
from lifeengine.autonomy import plan_autonomy
from lifeengine.canon import ensure_control
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.db import transaction
from lifeengine.runtime import LifeEngineRuntime


OWNER_KIND = "agent"
NOW = "2026-07-03T09:00:00+00:00"


def fresh_home(tmp_path: Path) -> Path:
    """为单个测试创建隔离的 HERMES_HOME。

    输入来自 pytest 的临时目录；输出是 runtime 初始化会使用的 home 路径。调用方是本
    文件的 setup helper；副作用仅为设置进程环境变量，不写业务数据库内容。
    """
    home = tmp_path / "hermes_home_opinion_autonomy"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化可运行 autonomy 的测试 agent。

    输入是新建 runtime；输出为空。调用方是本文件测试。副作用是提交最小 canon 并恢复
    engine control，让后续 `plan_autonomy` 使用真实控制记录；不创建 opinions 或 goals。
    """
    rt.setup("opinion-biased autonomy test agent")
    rt.commit_canon()
    rt.control("resume")


def create_goal(rt: LifeEngineRuntime, title: str, priority: int, *,
                description: str | None = None, goal_type: str = "lifestyle") -> dict:
    """创建 autonomy 候选目标。

    输入是 runtime、标题、优先级和可选描述/类型；输出是 goals 表中的 goal 行。调用方
    是本文件测试；副作用是写入 goals 与 journal，保持和产品工具相同的持久化形状。
    """
    return goal_ops.create_goal(
        rt.conn, OWNER_KIND, DEFAULT_AGENT_ID,
        title=title,
        description=description,
        goal_type=goal_type,
        priority=priority,
    )


def create_open_event_for_goal(rt: LifeEngineRuntime, goal_id: str) -> None:
    """给目标挂一个未完成事件，用来验证既有 open-event 选择规则。

    输入是 runtime 与目标 id；输出为空。调用方是无观点基线测试；副作用是创建 planned
    event 并写入 event_goal_links，使 `_active_events_for_goal` 能真实读到 pending 事件。
    """
    event = event_ops.create_event(
        rt.conn, OWNER_KIND, DEFAULT_AGENT_ID,
        title="已有待推进事项",
        event_type="study",
        source="test",
        status="planned",
        priority=90,
        goal_id=goal_id,
    )
    goal_ops.link_event_to_goal(rt.conn, OWNER_KIND, DEFAULT_AGENT_ID, goal_id, event["id"], source="test")


def plan_once(rt: LifeEngineRuntime) -> dict:
    """直接运行确定性的 autonomy planner。

    输入是已准备好数据的 runtime；输出是 `plan_autonomy` 写入并返回的 decision。调用方
    是本文件测试；副作用只是在 autonomy_decisions 和 journal 记录 planner 决策。
    `allow_authoring=False` 保证本测试只覆盖目标选择，不触发 LifeAuthor。
    """
    with transaction(rt.conn):
        control = ensure_control(rt.conn, OWNER_KIND, DEFAULT_AGENT_ID)
        return plan_autonomy(
            rt.conn,
            OWNER_KIND,
            DEFAULT_AGENT_ID,
            control,
            manual=True,
            now=NOW,
            allow_authoring=False,
        )


def test_no_opinions_keeps_priority_and_open_event_baseline(tmp_path: Path) -> None:
    """无观点时仍按旧顺序选择最高优先级且没有 open event 的目标。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        with transaction(rt.conn):
            blocked = create_goal(rt, "已有待办的高优先级目标", 95, goal_type="study")
            expected = create_goal(rt, "整理星图笔记", 80, goal_type="creative")
            create_goal(rt, "练习慢跑", 60, goal_type="health")
            create_open_event_for_goal(rt, blocked["id"])

        decision = plan_once(rt)

        assert decision["selected_goal_id"] == expected["id"]
        assert decision["score"]["goal_id"] == expected["id"]
        assert "opinion_affinity" not in decision["score"]
        assert decision["proposed_ops"][0]["payload"]["goal_id"] == expected["id"]
    finally:
        rt.close()


def test_salient_opinion_flips_to_lower_priority_matching_goal(tmp_path: Path) -> None:
    """强显著观点命中低优先级目标时，选择会被经历反哺而翻转。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        with transaction(rt.conn):
            high = create_goal(rt, "整理账本", 80, goal_type="finance")
            matched = create_goal(rt, "夜市风物志采集", 78, description="把夜市里的声音和摊位记下来", goal_type="creative")
            opinions.form_or_reinforce_opinion(
                rt.conn,
                DEFAULT_AGENT_ID,
                target="夜市",
                opinion_type="like",
                strength=1.0,
                confidence=1.0,
                reason="夜市让她觉得有生命力",
                source="test",
            )

        decision = plan_once(rt)

        assert decision["selected_goal_id"] == matched["id"]
        assert decision["selected_goal_id"] != high["id"]
        assert decision["score"]["opinion_affinity"]["selected_boost"] == 12.0
        assert decision["score"]["opinion_affinity"]["selected_rank_score"] == 90.0
    finally:
        rt.close()


def test_opinion_biased_autonomy_selection_is_deterministic(tmp_path: Path) -> None:
    """同一组 goals 与 opinions 下，多次 planner 调用选择同一个目标。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        with transaction(rt.conn):
            create_goal(rt, "整理账本", 80, goal_type="finance")
            matched = create_goal(rt, "夜市风物志采集", 78, goal_type="creative")
            opinions.form_or_reinforce_opinion(
                rt.conn,
                DEFAULT_AGENT_ID,
                target="夜市",
                opinion_type="like",
                strength=1.0,
                confidence=1.0,
                reason="夜市让她觉得有生命力",
                source="test",
            )

        selected_goal_ids = [plan_once(rt)["selected_goal_id"] for _ in range(3)]

        assert selected_goal_ids == [matched["id"], matched["id"], matched["id"]]
    finally:
        rt.close()
