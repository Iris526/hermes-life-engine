from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from lifeengine import life_author
from lifeengine.constants import DEFAULT_AGENT_ID, DEFAULT_USER_ID
from lifeengine.conversation import record_turn_interaction
from lifeengine.runtime import LifeEngineRuntime


def _fresh_home(tmp_path: Path, name: str) -> Path:
    """创建 authoring grounding 测试专用的隔离 Hermes home。

    输入是 pytest 临时目录和场景名；输出是新的 HERMES_HOME 路径。调用方是本文件
    每个测试。副作用是设置环境变量并删除旧目录，避免不同时间上下文互相污染。
    """
    home = tmp_path / name
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化只关注 execution authoring 的测试 Agent。

    输入是当前 runtime；输出为空。调用方式是同步测试夹具；副作用是创建并启用
    canon，同时关闭其它 heartbeat 生成式模块，让 fake LLM 调用只来自 execution
    narrative authoring，便于断言 context 内的时间事实。
    """
    rt.setup("测试 Agent；会把完成的日程写成自然生活记录。")
    rt.commit_canon()
    rt.control("resume")
    rt.control("module", key="autonomy", value="off")
    rt.control("module", key="reflection", value="off")
    rt.control("module", key="companion", value="off")
    rt.control("module", key="dream", value="off")
    rt.control("module", key="proactive", value="off")


class _FakeUsage:
    """测试用模型用量对象。

    该结构模拟 host LLM usage，仅用于 LifeAuthor 审计写入；生命周期限于单个测试
    进程，业务断言不依赖这些固定数值。
    """

    input_tokens = 40
    output_tokens = 20
    total_tokens = 60
    cost_usd = 0.0002


class _FakeResult:
    """测试用结构化模型返回。

    该结构承载 LifeAuthor 读取的 parsed/provider/model/usage 字段；调用方是
    `_FakeLlm.complete_structured`。它不访问网络，只把测试预设 parsed 包装成
    host facade 兼容形状。
    """

    def __init__(self, parsed: dict[str, Any]):
        """保存本次 fake host 返回的结构化内容。"""
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _FakeLlm:
    """测试用 host LLM 替身。

    输入是预设 parsed JSON；输出是 `_FakeResult`。它记录 complete_structured
    调用参数，供测试确认 heartbeat authoring context 收到了结构化时间事实。
    """

    def __init__(self, parsed: dict[str, Any]):
        """初始化一个只返回固定 parsed 的 fake host。"""
        self._parsed = parsed
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs):
        """记录调用并返回固定结构化结果。

        调用方是 `life_author.author`；副作用只是在内存中追加调用参数，不访问网络。
        """
        self.calls.append(kwargs)
        return _FakeResult(self._parsed)


def _create_completed_candidate(rt: LifeEngineRuntime, title: str) -> tuple[str, str]:
    """创建一条会自然进入 completed 分支、但不触发 serendipity 的日程。

    输入是 runtime 和事件标题；输出是 `(event_id, block_id)`。事件重要度故意低于
    serendipity 门槛，让 fake host 调用只覆盖 execution_narrative。副作用是通过
    runtime 的事件工具创建 event 和 schedule block。
    """
    event = rt.event_tool(
        "create",
        title=title,
        event_type="routine",
        importance=50,
        source="agent_prediction",
        resource_costs={},
    )
    event_id = event["results"][0]["result"]["id"]
    block = rt.event_tool(
        "schedule",
        event_id=event_id,
        start="2026-06-07T02:00:00+09:00",
        end="2026-06-07T03:00:00+09:00",
        timezone_name="Asia/Tokyo",
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


def _execution_context_from_call(fake: _FakeLlm) -> dict[str, Any]:
    """从 fake host 调用参数里解析 LifeAuthor input JSON。"""
    execution_calls = [
        c for c in fake.calls
        if c.get("purpose") == "life_author:execution_narrative"
    ]
    assert len(execution_calls) == 1
    return json.loads(execution_calls[0]["input"][0]["text"])


def test_heartbeat_execution_authoring_context_contains_now_grounding(tmp_path):
    """验证 heartbeat execution authoring context 注入结构化当前时间事实。"""
    _fresh_home(tmp_path, "hermes_home_authoring_grounding")
    fake = _FakeLlm({
        "narrative": "凌晨把抽屉收好，桌面安静了下来。",
        "memory": "凌晨整理完抽屉，心里也清爽了一点。",
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        record_turn_interaction(
            rt.conn,
            "agent",
            DEFAULT_AGENT_ID,
            session_id="qq-session",
            turn_id="turn-before-heartbeat",
            user_id=DEFAULT_USER_ID,
            platform="qq",
            text="晚点再聊",
            now="2026-06-06T23:31:00+09:00",
            source="test_authoring_grounding",
        )
        event_id, _block_id = _create_completed_candidate(rt, "整理抽屉")

        tick = rt.tick(now="2026-06-07T03:01:00+09:00", manual=False)

        assert tick["ok"] is True
        assert _stored_result_summary(rt, event_id) == "凌晨把抽屉收好，桌面安静了下来。"
        context = _execution_context_from_call(fake)
        authoring_now = context["authoring_now"]
        assert authoring_now["now_local"] == "2026-06-07 03:01"
        assert authoring_now["timezone"] == "Asia/Tokyo"
        assert authoring_now["weekday"] == "周日"
        assert authoring_now["phase"] == "凌晨"
        assert authoring_now["since_last_exchange"] == {
            "minutes": 210,
            "human": "3小时30分钟前",
        }
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_heartbeat_execution_no_host_fallback_stays_byte_identical(tmp_path):
    """验证 no-host 路径不泄漏 grounding，也不改变执行完成 fallback 文本。"""
    _fresh_home(tmp_path, "hermes_home_authoring_grounding_no_host")
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        event_id, _block_id = _create_completed_candidate(rt, "整理抽屉")

        tick = rt.tick(now="2026-06-07T03:01:00+09:00", manual=False)

        assert tick["ok"] is True
        assert _stored_result_summary(rt, event_id) == "执行完成：整理抽屉"
        assert _stored_memory_content(rt, event_id) == "完成了『整理抽屉』。"
    finally:
        life_author.set_test_llm(None)
        rt.close()
