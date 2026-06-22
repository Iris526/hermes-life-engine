from __future__ import annotations

import os
import shutil
from pathlib import Path

from lifeengine import life_author
from lifeengine.runtime import LifeEngineRuntime


def _fresh_home(tmp_path: Path) -> Path:
    """为主动消息写作测试创建隔离的 Hermes home。

    输入是 pytest 提供的临时目录；输出是本测试进程独占的 HERMES_HOME。
    调用方是本文件内的回归测试。副作用是写入环境变量并删除旧目录，
    失败会直接让测试失败，避免污染其它 LifeEngine 状态。
    """
    home = tmp_path / "hermes_home_proactive_authoring"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化一个允许主动发送的测试 Agent。

    输入是当前测试持有的 runtime；输出为空。调用方式是同步测试夹具；
    副作用是创建 canon、恢复运行状态，并把 proactive 门控切到 auto_send，
    让 evaluate 流程能真实进入 outbox 写入分支。
    """
    rt.setup("归明观里摆摊卖符的人，说话应该像一个有自己脾气和生活感的熟人。")
    rt.commit_canon()
    rt.control("resume")
    rt.control("module", key="proactive", value="auto_send")


class _FakeUsage:
    """测试用模型用量对象。

    它模拟 host PluginLlm 返回的 usage 字段，只在本测试进程内有效；
    LifeAuthor 用它记录 author run，业务代码不会读取这些固定数值。
    """

    input_tokens = 60
    output_tokens = 30
    total_tokens = 90
    cost_usd = 0.0004


class _FakeResult:
    """测试用结构化模型结果。

    该对象承载 LifeAuthor 期望的 parsed/provider/model/usage 字段；
    生命周期仅覆盖一次 fake complete_structured 调用，用来验证 outbox
    文案确实来自角色化写作层。
    """

    def __init__(self, parsed: dict):
        """保存 fake 模型返回的结构化 JSON。"""
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _FakeLlm:
    """测试用 host LLM 替身。

    输入是预设 parsed JSON；输出是 PluginLlm 形状的结果对象。它记录每次
    调用参数，供测试确认 proactive_outbox 的 prompt 拿到了原始意图上下文。
    """

    def __init__(self, parsed: dict):
        """初始化本测试专用的结构化模型替身。"""
        self._parsed = parsed
        self.calls: list[dict] = []

    def complete_structured(self, **kwargs):
        """返回预设结构化结果并记录调用。

        调用方是 LifeAuthor.author；副作用只是在内存中追加调用参数，不访问
        网络、不读写数据库。失败语义由测试直接暴露为异常。
        """
        self.calls.append(kwargs)
        return _FakeResult(self._parsed)


def _create_resource_shortage_intent(rt: LifeEngineRuntime) -> str:
    """创建一条复现截图问题的主动意图。

    输出是 proactive intent id；调用方随后执行 evaluate。该意图故意保留
    “资源不足/重新规划”的原始摘要，用来证明最终 outbox 不再照抄系统播报。
    """
    created = rt.proactive(
        "create",
        summary="『归明观摆摊卖符』遇到资源不足，想重新规划。",
        target_type="user",
        target_id="u1",
        intent_type="report_failure",
        importance=95,
        urgency=90,
        novelty=80,
        relationship_relevance=90,
        privacy_level="safe_to_share",
    )
    return created["results"][0]["result"]["id"]


def test_proactive_outbox_uses_life_author_for_final_message(tmp_path):
    """验证主动 outbox 文案会走 LifeAuthor，而不是旧模板拼接。"""
    _fresh_home(tmp_path)
    fake = _FakeLlm({
        "message_text": "摊子这会儿有点撑不住，我想先把符纸收一收，换个稳点的法子。",
        "emotional_tone": "soft",
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        intent_id = _create_resource_shortage_intent(rt)

        evaluated = rt.proactive("evaluate", intent_id=intent_id)

        item = evaluated["results"][0]["result"]["evaluated"][0]
        assert item["decision"] == "outbox_queued"
        assert item["outbox"]["draft_text"] == "摊子这会儿有点撑不住，我想先把符纸收一收，换个稳点的法子。"
        assert "我有件事想跟你说" not in item["outbox"]["draft_text"]
        assert "资源不足" not in item["outbox"]["draft_text"]
        assert fake.calls
        assert fake.calls[0]["purpose"] == "life_author:proactive_outbox"
        assert "归明观摆摊卖符" in fake.calls[0]["input"][0]["text"]
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_proactive_outbox_fallback_avoids_mechanical_system_phrase(tmp_path):
    """验证无模型兜底也不再使用截图里的机械开场和系统词。"""
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        rt.control("module", key="life_author", value="off")
        intent_id = _create_resource_shortage_intent(rt)

        evaluated = rt.proactive("evaluate", intent_id=intent_id)

        item = evaluated["results"][0]["result"]["evaluated"][0]
        text = item["outbox"]["draft_text"]
        assert item["decision"] == "outbox_queued"
        assert "我有件事想跟你说" not in text
        assert "资源不足" not in text
        assert "重新规划" not in text
        assert "归明观摆摊卖符" in text
        assert "稳一点" in text
    finally:
        rt.close()
