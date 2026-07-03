from __future__ import annotations

import os
import shutil
from pathlib import Path

from lifeengine import life_author
from lifeengine.runtime import LifeEngineRuntime


def _fresh_home(tmp_path: Path) -> Path:
    """创建执行叙事 authoring 测试专用的隔离 Hermes home。

    输入是 pytest 临时目录；输出是新的 HERMES_HOME 路径。调用方是本文件内
    每个回归测试。副作用是设置环境变量并删除旧目录，避免跨测试复用旧数据库影响
    result summary 和 memory content 的逐字断言。
    """
    home = tmp_path / "hermes_home_execution_narrative_authoring"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化只关注 execution completion 的测试 Agent。

    输入是当前测试 runtime；输出为空。调用方式是同步测试夹具；副作用是创建并启用
    canon，同时关闭其它 heartbeat 生成式模块，让 fake LLM 调用只来自 execution
    narrative authoring 路径。
    """
    rt.setup("测试 Agent；完成事件时要写得像亲历后的生活记录。")
    rt.commit_canon()
    rt.control("resume")
    rt.control("module", key="autonomy", value="off")
    rt.control("module", key="reflection", value="off")
    rt.control("module", key="companion", value="off")
    rt.control("module", key="dream", value="off")
    rt.control("module", key="proactive", value="off")


class _FakeUsage:
    """测试用模型用量对象。

    该结构模拟 host LLM usage，仅用于 LifeAuthor 写审计；业务逻辑不依赖这些固定值，
    生命周期只覆盖单个测试进程。
    """

    input_tokens = 40
    output_tokens = 20
    total_tokens = 60
    cost_usd = 0.0002


class _FakeResult:
    """测试用结构化模型返回。

    该结构承载 LifeAuthor 读取的 parsed/provider/model/usage 字段；调用方是
    `_FakeLlm.complete_structured`。它不访问网络、不写业务表，只把预置 parsed
    包装成 host facade 兼容形状。
    """

    def __init__(self, parsed: dict):
        """保存本次 fake host 返回的结构化内容。"""
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _FakeLlm:
    """测试用 host LLM 替身。

    输入是预设 parsed JSON；输出是 `_FakeResult`。它记录 complete_structured
    调用参数，供测试确认 execution completion 走到了 LifeAuthor 的
    execution_narrative kind。
    """

    def __init__(self, parsed: dict):
        """初始化一个只返回固定 parsed 的 fake host。"""
        self._parsed = parsed
        self.calls: list[dict] = []

    def complete_structured(self, **kwargs):
        """记录调用并返回固定结构化结果。

        调用方是 `life_author.author`；副作用只是在内存中追加调用参数，不访问网络。
        """
        self.calls.append(kwargs)
        return _FakeResult(self._parsed)


def _create_completed_candidate(rt: LifeEngineRuntime, title: str, *, importance: int = 70) -> tuple[str, str]:
    """创建一条会自然进入 completed 分支的 scheduled event。

    输入是 runtime、事件标题和重要度；输出是 `(event_id, block_id)`。调用方随后运行
    heartbeat tick 或 manual execution。事件显式使用空 resource_costs，避免资源结算
    干扰本文件只关注的人类可见 narrative/memory 断言。
    """
    event = rt.event_tool(
        "create",
        title=title,
        event_type="routine",
        importance=importance,
        source="agent_prediction",
        resource_costs={},
    )
    event_id = event["results"][0]["result"]["id"]
    block = rt.event_tool(
        "schedule",
        event_id=event_id,
        start="2026-06-07T10:00:00+00:00",
        end="2026-06-07T11:00:00+00:00",
        timezone_name="UTC",
    )
    block_id = block["results"][0]["result"]["id"]
    return event_id, block_id


def _stored_result_summary(rt: LifeEngineRuntime, event_id: str) -> str:
    """读取事件完成后写入 results.summary 的文本。"""
    row = rt.conn.execute(
        "SELECT summary FROM results WHERE event_id=? ORDER BY rowid DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    assert row is not None
    return row["summary"]


def _stored_memory_content(rt: LifeEngineRuntime, event_id: str) -> str:
    """读取事件完成后写入 memories.content 的文本。"""
    row = rt.conn.execute(
        "SELECT content FROM memories WHERE event_id=? ORDER BY rowid DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    assert row is not None
    return row["content"]


def test_heartbeat_execution_completion_uses_life_author_for_result_and_memory(tmp_path):
    """验证 heartbeat 完成事件时 result summary 和 memory 优先来自 LifeAuthor。"""
    _fresh_home(tmp_path)
    authored_narrative = "把旧书和笔记都归好后，书桌终于清爽了些。"
    authored_memory = "收完旧书和笔记，心里也跟着轻了一点。"
    fake = _FakeLlm({"narrative": authored_narrative, "memory": authored_memory})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        event_id, _block_id = _create_completed_candidate(rt, "整理旧书和笔记")

        tick = rt.tick(now="2026-06-07T11:01:00+00:00", manual=False)

        assert tick["ok"] is True
        assert _stored_result_summary(rt, event_id) == authored_narrative
        assert _stored_memory_content(rt, event_id) == authored_memory
        assert len(fake.calls) == 1
        assert fake.calls[0]["purpose"] == "life_author:execution_narrative"
        assert "整理旧书和笔记" in fake.calls[0]["input"][0]["text"]
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_manual_execution_completion_uses_pretransaction_life_author(tmp_path):
    """验证 life_execution 手动执行也在事务外预生成完成叙事。"""
    _fresh_home(tmp_path)
    authored_narrative = "窗台擦亮以后，屋里像多进来了一点光。"
    authored_memory = "擦完小窗台，觉得房间明亮了些。"
    fake = _FakeLlm({"narrative": authored_narrative, "memory": authored_memory})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        event_id, block_id = _create_completed_candidate(rt, "擦干净小窗台")

        out = rt.execution("run", schedule_block_id=block_id)

        assert out["ok"] is True
        assert _stored_result_summary(rt, event_id) == authored_narrative
        assert _stored_memory_content(rt, event_id) == authored_memory
        assert len(fake.calls) == 1
        assert fake.calls[0]["purpose"] == "life_author:execution_narrative"
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_execution_completion_preserves_no_host_fallback_byte_for_byte(tmp_path):
    """验证无 host 时完成结果和记忆逐字保留旧模板。"""
    _fresh_home(tmp_path)
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        title = "整理晨间清单"
        event_id, _block_id = _create_completed_candidate(rt, title)

        tick = rt.tick(now="2026-06-07T11:01:00+00:00", manual=False)

        assert tick["ok"] is True
        assert _stored_result_summary(rt, event_id) == f"执行完成：{title}"
        assert _stored_memory_content(rt, event_id) == f"完成了『{title}』。"
    finally:
        life_author.set_test_llm(None)
        rt.close()
