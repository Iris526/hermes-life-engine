from __future__ import annotations

import os
import shutil
from pathlib import Path

from lifeengine import life_author
from lifeengine import proactive as proactive_module
from lifeengine.runtime import LifeEngineRuntime


def _fresh_home(tmp_path: Path) -> Path:
    """创建本文件专用的隔离 Hermes home。

    输入是 pytest 临时目录；输出是新的 HERMES_HOME 路径。调用方是 auto_send
    heartbeat authoring 回归测试。副作用是更新环境变量并清理旧目录；失败直接让
    测试失败，避免跨测试复用旧数据库影响 outbox 文案断言。
    """
    home = tmp_path / "hermes_home_auto_send_authoring"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _setup_auto_send_agent(rt: LifeEngineRuntime) -> None:
    """初始化只测试 proactive auto_send 的运行状态。

    输入是当前测试 runtime；输出为空。调用方式是同步测试夹具；副作用是创建并启用
    canon、恢复 engine、打开 proactive auto_send，并关闭其它 heartbeat 生成式模块，
    让 fake LLM 调用只来自 outbox authoring 路径。
    """
    rt.setup("测试 Agent，主动消息需要像自然聊天。")
    rt.commit_canon()
    rt.control("resume")
    rt.control("module", key="autonomy", value="off")
    rt.control("module", key="reflection", value="off")
    rt.control("module", key="companion", value="off")
    rt.control("module", key="dream", value="off")
    rt.control("module", key="proactive", value="auto_send")


class _FakeUsage:
    """测试用模型计费信息。

    该结构模拟 host LLM usage 对象，仅用于 LifeAuthor 写审计；业务逻辑不依赖
    这些固定数字，生命周期只覆盖单个测试进程。
    """

    input_tokens = 50
    output_tokens = 20
    total_tokens = 70
    cost_usd = 0.0003


class _FakeResult:
    """测试用结构化模型返回。

    该结构承载 LifeAuthor 读取的 parsed/provider/model/usage 字段；调用方是
    `_FakeLlm.complete_structured`。它不访问网络、不写数据库，只把预置 parsed
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
    调用参数，供测试确认 heartbeat auto_send 走到了 LifeAuthor outbox kind。
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


def _create_auto_send_intent(rt: LifeEngineRuntime) -> str:
    """创建一条 heartbeat auto_send 会尝试投递的主动意图。

    输出是 proactive intent id；调用方随后运行 heartbeat tick。该意图分数足够高、
    隐私安全，并明确发给测试用户，从而能进入 auto_send outbox 分支。
    """
    created = rt.proactive(
        "create",
        summary="午后整理了明天的安排，想告诉你下一步。",
        target_type="user",
        target_id="u1",
        intent_type="report_progress",
        importance=95,
        urgency=90,
        novelty=85,
        relationship_relevance=90,
        privacy_level="safe_to_share",
    )
    return created["results"][0]["result"]["id"]


def _draft_for_intent(rt: LifeEngineRuntime, intent_id: str) -> str:
    """读取指定 intent 对应的 queued outbox 文案。

    输入是 runtime 和 intent id；输出是落库 draft_text。调用方是本文件断言逻辑；
    副作用是只读 proactive outbox。如果没有匹配 outbox，测试会以 StopIteration
    失败，直接暴露 heartbeat 没有进入 auto_send 写出路径。
    """
    outbox = rt.proactive("outbox")["outbox"]
    row = next(o for o in outbox if o["intent_id"] == intent_id and o["status"] == "queued")
    return row["draft_text"]


def test_heartbeat_auto_send_outbox_uses_life_author(tmp_path):
    """验证 heartbeat auto_send 写出的 outbox 文案优先来自 LifeAuthor。"""
    _fresh_home(tmp_path)
    authored_text = "我刚把明天的安排顺了一遍，想先跟你说一声，下一步我会稳着推进。"
    fake = _FakeLlm({"message_text": authored_text, "emotional_tone": "steady"})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_auto_send_agent(rt)
        intent_id = _create_auto_send_intent(rt)

        rt.tick(now="2026-06-20T10:00:00+00:00", manual=False)

        assert _draft_for_intent(rt, intent_id) == authored_text
        assert len(fake.calls) == 1
        assert fake.calls[0]["purpose"] == "life_author:proactive_outbox"
        assert "午后整理了明天的安排" in fake.calls[0]["input"][0]["text"]
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_heartbeat_auto_send_outbox_preserves_no_host_fallback_byte_for_byte(tmp_path):
    """验证无 host 时 heartbeat auto_send 逐字保留现有 fallback 行为。"""
    _fresh_home(tmp_path)
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_auto_send_agent(rt)
        intent_id = _create_auto_send_intent(rt)
        intent = rt.proactive("get", intent_id=intent_id)["intent"]
        expected = proactive_module._fallback_outbox_text(intent)

        rt.tick(now="2026-06-20T10:00:00+00:00", manual=False)

        assert _draft_for_intent(rt, intent_id) == expected
    finally:
        life_author.set_test_llm(None)
        rt.close()
