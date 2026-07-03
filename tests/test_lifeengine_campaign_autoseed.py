"""Campaign auto-seed — idle heartbeat can start a new self-driven arc.

This file covers audit item 轴三-4: when no active campaign exists and the
campaign layer has been idle long enough, heartbeat pre-authors a campaign_seed
blueprint outside the write transaction and the in-transaction campaign runner
creates exactly one active campaign from it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from lifeengine import life_author
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.runtime import LifeEngineRuntime


def _fresh_home(tmp_path: Path, name: str) -> Path:
    """创建 campaign auto-seed 测试专用的隔离 Hermes home。

    输入是 pytest 临时目录和场景名；输出是新的 HERMES_HOME 路径。调用方是本文件
    内每个测试。副作用是设置环境变量并删除旧目录，避免 campaign idle 时间戳和
    fake host 调用记录跨测试泄漏。
    """
    home = tmp_path / name
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    return home


def _setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化只关注 campaign auto-seed 的测试 Agent。

    输入是当前 runtime；输出为空。调用方式是同步测试夹具；副作用是创建 active
    Canon、恢复 engine、初始化资源，并关闭与本场景无关的 heartbeat authoring gate，
    让 fake host 调用只来自 campaign_seed。
    """
    rt.setup("campaign auto-seed test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")
    for key in ("autonomy", "reflection", "companion", "dream", "proactive", "daily_rhythm", "schedule", "venture", "meals"):
        rt.control("module", key=key, value="off")


def _seed_life_signals(rt: LifeEngineRuntime) -> None:
    """写入 auto-seed brief 使用的观点和目标。

    输入是当前 runtime；输出为空。调用方是需要满足“brief 来自 salient opinions +
    top active goals”的测试。副作用只通过公开工具写入一条 active opinion 和一条
    active goal，不创建 campaign。
    """
    rt.opinion("record", target="夜市", opinion_type="like", strength=0.85, confidence=0.8, reason="晚上去总觉得生活亮起来")
    rt.goals("create", title="把夏夜摊位认真张罗起来", goal_type="lifestyle", priority=90, description="想把最近喜欢的夜市和手作摊位连成一件真正的大事")


class _FakeUsage:
    """测试用模型用量对象。

    该结构模拟 host usage，仅用于 LifeAuthor 审计写入；业务断言不依赖这些固定值，
    生命周期只覆盖单个测试进程。
    """

    input_tokens = 120
    output_tokens = 80
    total_tokens = 200
    cost_usd = 0.001


class _FakeResult:
    """测试用结构化模型返回。

    该结构承载 LifeAuthor 读取的 parsed/provider/model/usage 字段；调用方是
    `_FakeLlm.complete_structured`。它不访问网络、不写业务表，只把预置 blueprint
    包装成 host facade 兼容形状。
    """

    def __init__(self, parsed: dict[str, Any]):
        """保存 fake host 本次返回的结构化内容。"""
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _FakeLlm:
    """测试用 host LLM 替身。

    输入是 campaign blueprint；输出是 `_FakeResult`。它记录所有
    complete_structured 调用参数，供测试确认 heartbeat 只在有资格 auto-seed 时调用
    `life_author:campaign_seed`。
    """

    def __init__(self, blueprint: dict[str, Any]):
        """初始化一个总是返回同一 campaign blueprint 的 fake host。"""
        self._blueprint = blueprint
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs):
        """记录调用并返回预置 campaign blueprint。"""
        self.calls.append(kwargs)
        return _FakeResult(self._blueprint)


def _blueprint(title: str = "夏夜手作摊位计划") -> dict[str, Any]:
    """生成测试用 campaign_seed blueprint。

    输入是 campaign 标题；输出符合 `campaigns.SEED_SCHEMA` 的最小多阶段资料片蓝图。
    调用方用它验证 create path 使用模型返回内容，而不是模板内容。
    """
    return {
        "title": title,
        "description": "把最近喜欢的夜市和手作摊位铺成一条连续几天的小计划。",
        "importance": 72,
        "phases": [
            {
                "title": "试探",
                "kind": "预兆",
                "duration_days": 3,
                "daily_spawns": 0,
                "spawn_template": {"title": "整理摊位灵感", "event_type": "personal", "importance": 55, "duration_minutes": 45},
            },
            {
                "title": "成形",
                "kind": "升温",
                "duration_days": 4,
                "daily_spawns": 0,
                "one_time_events": [{"title": "定下摊位主题", "event_type": "personal", "importance": 70, "duration_minutes": 60}],
            },
        ],
    }


def _campaign_rows(rt: LifeEngineRuntime, *, status: str | None = None) -> list[dict[str, Any]]:
    """读取当前测试库里的 campaign 行。

    输入是 runtime 和可选状态；输出是按创建顺序排列的 dict 列表。调用方用它断言
    auto-seed 是否创建 active campaign。副作用为零。
    """
    if status:
        rows = rt.conn.execute(
            "SELECT * FROM campaigns WHERE owner_kind='agent' AND owner_id=? AND status=? ORDER BY created_at",
            (DEFAULT_AGENT_ID, status),
        ).fetchall()
    else:
        rows = rt.conn.execute(
            "SELECT * FROM campaigns WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at",
            (DEFAULT_AGENT_ID,),
        ).fetchall()
    return [dict(r) for r in rows]


def _campaign_seed_calls(fake: _FakeLlm) -> list[dict[str, Any]]:
    """筛出 fake host 收到的 campaign_seed 调用。"""
    return [c for c in fake.calls if c.get("purpose") == "life_author:campaign_seed"]


def test_heartbeat_autoseeds_one_campaign_from_fake_blueprint_when_idle(tmp_path):
    """有 fake host、无 active campaign、已 idle 时，heartbeat 自动创建一个 active campaign。"""
    _fresh_home(tmp_path, "hermes_campaign_autoseed_happy")
    fake = _FakeLlm(_blueprint())
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _seed_life_signals(rt)

        tick = rt.tick(now="2026-06-21T10:00:00+00:00", manual=False)

        rows = _campaign_rows(rt, status="active")
        assert tick["ok"] is True
        assert len(rows) == 1
        assert rows[0]["title"] == "夏夜手作摊位计划"
        assert rows[0]["source"] == "campaign_seed"
        assert rows[0]["status"] == "active"
        assert len(_campaign_seed_calls(fake)) == 1
        brief_text = (_campaign_seed_calls(fake)[0].get("input") or [{}])[0].get("text", "")
        assert "夜市" in brief_text
        assert "把夏夜摊位认真张罗起来" in brief_text
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_heartbeat_autoseed_degrades_to_noop_without_host_model(tmp_path):
    """无 host model 时 auto-seed 保持沉默，不创建 campaign。"""
    _fresh_home(tmp_path, "hermes_campaign_autoseed_no_host")
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _seed_life_signals(rt)

        tick = rt.tick(now="2026-06-21T10:00:00+00:00", manual=False)

        assert tick["ok"] is True
        assert _campaign_rows(rt) == []
        assert tick["campaigns"]["campaigns"] == []
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_heartbeat_autoseed_is_idempotent_after_first_seed(tmp_path):
    """同一 idle 窗口只 seed 一次；同一逻辑 tick 重跑和下一 tick 都不会追加 campaign。"""
    _fresh_home(tmp_path, "hermes_campaign_autoseed_idempotent")
    fake = _FakeLlm(_blueprint("只该出现一次的资料片"))
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _seed_life_signals(rt)

        rt.tick(now="2026-06-21T10:00:00+00:00", manual=False)
        rt.tick(now="2026-06-21T10:00:00+00:00", manual=False)
        rt.tick(now="2026-06-22T10:00:00+00:00", manual=False)

        rows = _campaign_rows(rt, status="active")
        assert len(rows) == 1
        assert rows[0]["title"] == "只该出现一次的资料片"
        assert len(_campaign_seed_calls(fake)) == 1
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_heartbeat_autoseed_skips_when_campaign_is_already_active(tmp_path):
    """已有 active campaign 时，即使有 fake host 和生活信号，也不会 auto-seed。"""
    _fresh_home(tmp_path, "hermes_campaign_autoseed_active_exists")
    fake = _FakeLlm(_blueprint("不应创建的资料片"))
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _seed_life_signals(rt)
        existing = rt.campaign(
            "register",
            title="已经在推进的资料片",
            timezone="UTC",
            start_date="2026-06-20",
            phases=[
                {
                    "title": "推进中",
                    "duration_days": 5,
                    "daily_spawns": 0,
                    "spawn_template": {"title": "继续推进", "event_type": "personal", "importance": 50, "duration_minutes": 30},
                }
            ],
        )["campaign"]

        rt.tick(now="2026-06-21T10:00:00+00:00", manual=False)

        rows = _campaign_rows(rt, status="active")
        assert len(rows) == 1
        assert rows[0]["id"] == existing["id"]
        assert _campaign_seed_calls(fake) == []
    finally:
        life_author.set_test_llm(None)
        rt.close()
