from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from lifeengine import life_author
from lifeengine.runtime import LifeEngineRuntime


def _fresh_home(tmp_path: Path, name: str) -> Path:
    """创建 serendipity authoring 测试专用的隔离 Hermes home。

    输入是 pytest 临时目录和测试场景名；输出是新的 HERMES_HOME 路径。调用方是本文件
    内每个回归测试。副作用是设置环境变量并删除旧目录，避免不同 fake host 场景共享
    数据库状态后影响 serendipity 文案和数量断言。
    """
    home = tmp_path / name
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化只关注 execution/serendipity 的测试 Agent。

    输入是当前测试 runtime；输出为空。调用方式是同步测试夹具；副作用是创建并启用
    canon，同时关闭其它 heartbeat 生成式模块，让 fake LLM 调用只来自 execution
    completion 和 serendipity authoring 路径。
    """
    rt.setup("测试 Agent；完成日程后偶尔会记录生活里冒出来的小发现。")
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

    input_tokens = 50
    output_tokens = 25
    total_tokens = 75
    cost_usd = 0.0003


class _FakeResult:
    """测试用结构化模型返回。

    该结构承载 LifeAuthor 读取的 parsed/provider/model/usage 字段；调用方是
    `_FakeLlm.complete_structured`。它不访问网络、不写业务表，只把预置 parsed
    包装成 host facade 兼容形状。
    """

    def __init__(self, parsed: dict[str, Any]):
        """保存本次 fake host 返回的结构化内容。"""
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _FakeLlm:
    """测试用 host LLM 替身。

    输入是按 purpose 分发的 parsed JSON；输出是 `_FakeResult`。它记录
    complete_structured 调用参数，供测试确认 serendipity 走到了 LifeAuthor 的
    serendipity kind。
    """

    def __init__(self, parsed_by_purpose: dict[str, dict[str, Any]]):
        """初始化一个按 LifeAuthor purpose 返回固定 parsed 的 fake host。"""
        self._parsed_by_purpose = parsed_by_purpose
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs):
        """记录调用并返回当前 purpose 对应的结构化结果。

        调用方是 `life_author.author`；副作用只是在内存中追加调用参数，不访问网络。
        未配置的 purpose 返回空对象，用来暴露调用方自己的 degrade-safe fallback。
        """
        self.calls.append(kwargs)
        return _FakeResult(self._parsed_by_purpose.get(str(kwargs.get("purpose") or ""), {}))


def _create_serendipity_candidate(
    rt: LifeEngineRuntime,
    title: str,
    *,
    event_type: str = "study",
    importance: int = 80,
) -> tuple[str, str]:
    """创建一条会自然完成并触发 serendipity 的 scheduled event。

    输入是 runtime、事件标题、事件类型和重要度；输出是 `(event_id, block_id)`。
    调用方随后运行 heartbeat tick。事件显式使用空 resource_costs，避免资源结算
    干扰本文件只关注的 serendipity 文案和触发数量断言。
    """
    event = rt.event_tool(
        "create",
        title=title,
        event_type=event_type,
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


def _serendipity_op(decision: dict[str, Any]) -> dict[str, Any]:
    """从 execution decision 的 proposed ops 中取出 serendipity op。"""
    matches = [op for op in decision.get("proposed_ops", []) if op.get("type") == "CREATE_SERENDIPITY_EVENT"]
    assert len(matches) == 1
    return matches[0]


def _latest_serendipity(rt: LifeEngineRuntime) -> dict[str, Any]:
    """读取最新落库的 serendipity_events 记录。"""
    rows = rt.execution("serendipity")["serendipity"]
    assert len(rows) == 1
    return rows[0]


def _serendipity_child_event(rt: LifeEngineRuntime, trigger_event_id: str) -> dict[str, Any]:
    """读取 `CREATE_SERENDIPITY_EVENT` 同步创建的 completed child event。"""
    row = rt.conn.execute(
        """SELECT title, description, event_type, parent_event_id
             FROM events
             WHERE parent_event_id=? AND event_type='serendipity'
             ORDER BY rowid DESC LIMIT 1""",
        (trigger_event_id,),
    ).fetchone()
    assert row is not None
    return dict(row)


def test_heartbeat_serendipity_uses_life_author_for_title_and_description(tmp_path):
    """验证 heartbeat 生成的 serendipity title/description 优先来自 LifeAuthor。"""
    _fresh_home(tmp_path, "hermes_home_serendipity_authoring")
    authored_title = "路边一盏小灯忽然亮起来"
    authored_description = "散步收尾时，刚好看见转角的小灯亮起，像给这段路补了个温柔的句号。"
    fake = _FakeLlm({
        "life_author:execution_narrative": {
            "narrative": "傍晚散步顺利结束，脚步慢慢收回来了。",
            "memory": "傍晚走了一圈，身体松了一点。",
        },
        "life_author:serendipity": {
            "title": authored_title,
            "description": authored_description,
        },
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        event_id, _block_id = _create_serendipity_candidate(rt, "傍晚绕街区散步", event_type="walk")

        tick = rt.tick(now="2026-06-07T11:01:00+00:00", manual=False)

        assert tick["ok"] is True
        decision = tick["completed"][0]["execution_decision"]
        op_payload = _serendipity_op(decision)["payload"]
        assert op_payload["title"] == authored_title
        assert op_payload["description"] == authored_description
        stored = _latest_serendipity(rt)
        assert stored["title"] == authored_title
        assert stored["description"] == authored_description
        child = _serendipity_child_event(rt, event_id)
        assert child["title"] == authored_title
        assert child["description"] == authored_description
        serendipity_calls = [c for c in fake.calls if c.get("purpose") == "life_author:serendipity"]
        assert len(serendipity_calls) == 1
        assert "傍晚绕街区散步" in serendipity_calls[0]["input"][0]["text"]
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_heartbeat_serendipity_preserves_no_host_fallback_byte_for_byte(tmp_path):
    """验证无 host 时 serendipity title/description 逐字保留旧模板。"""
    _fresh_home(tmp_path, "hermes_home_serendipity_fallback")
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        title = "完成第一章复习"
        event_id, _block_id = _create_serendipity_candidate(rt, title, event_type="study")

        tick = rt.tick(now="2026-06-07T11:01:00+00:00", manual=False)

        assert tick["ok"] is True
        expected_title = "复习时发现了一个需要补强的小点"
        expected_description = f"这个小事件由『{title}』执行后的叙事模拟产生。"
        op_payload = _serendipity_op(tick["completed"][0]["execution_decision"])["payload"]
        assert op_payload["title"] == expected_title
        assert op_payload["description"] == expected_description
        stored = _latest_serendipity(rt)
        assert stored["title"] == expected_title
        assert stored["description"] == expected_description
        child = _serendipity_child_event(rt, event_id)
        assert child["title"] == expected_title
        assert child["description"] == expected_description
    finally:
        life_author.set_test_llm(None)
        rt.close()


def _run_serendipity_case(tmp_path: Path, home_name: str, fake: _FakeLlm | None) -> tuple[dict[str, Any], int]:
    """运行一个用于比较有/无 host serendipity 触发形状的隔离场景。

    输入是临时目录、home 名称和可选 fake host；输出是 serendipity proposed payload
    与落库数量。调用方用它比较同一类事件在 fake/no-host 下只改变文本字段。
    """
    _fresh_home(tmp_path, home_name)
    if fake is None:
        life_author.disable_test_llm()
    else:
        life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _create_serendipity_candidate(rt, "完成第一章复习", event_type="study")
        tick = rt.tick(now="2026-06-07T11:01:00+00:00", manual=False)
        assert tick["ok"] is True
        payload = _serendipity_op(tick["completed"][0]["execution_decision"])["payload"]
        count = len(rt.execution("serendipity")["serendipity"])
        return payload, count
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_serendipity_authoring_does_not_change_which_event_fires(tmp_path):
    """验证有无 LifeAuthor 只改变 serendipity 文案，不改变触发数量和非文本字段。"""
    fallback_payload, fallback_count = _run_serendipity_case(tmp_path, "hermes_home_serendipity_shape_fallback", None)
    fake = _FakeLlm({
        "life_author:execution_narrative": {
            "narrative": "第一章复习收尾了，脑子里留下了几个清楚的标记。",
            "memory": "复习完第一章后，意识到有些小点还值得回头看。",
        },
        "life_author:serendipity": {
            "title": "复习边角里露出一个新疑问",
            "description": "收起笔记前，突然意识到有个概念虽然会用了，但还没真正讲明白。",
        },
    })
    authored_payload, authored_count = _run_serendipity_case(tmp_path, "hermes_home_serendipity_shape_authored", fake)

    assert fallback_count == authored_count == 1
    for key in ("serendipity_type", "intensity", "emotional_impact", "source"):
        assert fallback_payload[key] == authored_payload[key]
    assert fallback_payload["title"] == "复习时发现了一个需要补强的小点"
    assert authored_payload["title"] == "复习边角里露出一个新疑问"
    assert fallback_payload["description"] != authored_payload["description"]
    assert len([c for c in fake.calls if c.get("purpose") == "life_author:serendipity"]) == 1
