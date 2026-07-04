from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from lifeengine import companion as companion_module
from lifeengine import life_author
from lifeengine.canon import ensure_control, get_active_canon
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.conversation import temporal_gate, temporal_grounding
from lifeengine.heartbeat_authoring import prepare_heartbeat_authoring
from lifeengine.jsonutil import dumps, loads
from lifeengine.runtime import LifeEngineRuntime


def _fresh_home(tmp_path: Path, name: str) -> Path:
    """创建 temporal gate 测试专用隔离 home。

    输入是 pytest tmp_path 和场景名；输出是新的 HERMES_HOME。调用方是本文件测试；
    副作用是设置环境变量并删除旧目录，避免 proactive/outbox 状态跨测试污染。
    """
    home = tmp_path / name
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化允许主动 outbox、但禁用现场 LifeAuthor 的测试 Agent。

    输入是 runtime；输出为空。调用方是 proactive gate 集成测试；副作用是创建 active
    Canon、恢复 engine，并把 proactive 切到 auto_send，使 evaluate 能真实进入
    outbox/suppress 分支。life_author 关闭后，允许路径使用确定性 fallback。
    """
    rt.setup("temporal gate test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.control("module", key="proactive", value="auto_send")
    rt.control("module", key="life_author", value="off")


def _patch_meal_windows(rt: LifeEngineRuntime, times: dict[str, str], *,
                        window_minutes: int = 30,
                        timezone_name: str = "Asia/Tokyo") -> None:
    """把 active Canon 的日内窗口改成测试可控值。

    输入是 runtime、任意 Canon window key 到 HH:MM 的映射、窗口长度和时区；输出为空。
    调用方用它制造 small_hours 下 passed/in_window 的精确 `today_windows` facts。副作用
    只写当前测试 DB 的 active Canon，不改生产默认配置；key 可以是 dinner，也可以是
    非 meal 名称，证明门控不依赖餐名。
    """
    row = rt.conn.execute(
        "SELECT id, data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1",
        (DEFAULT_AGENT_ID,),
    ).fetchone()
    assert row is not None
    data = loads(row["data_json"], {})
    data.setdefault("schedule_rules", {})["timezone"] = timezone_name
    meals = data.setdefault("meals", {})
    meals["enabled"] = True
    meals["needs_food"] = True
    meals["times"] = dict(times)
    meals["window_minutes"] = int(window_minutes)
    rt.conn.execute("UPDATE canon_versions SET data_json=? WHERE id=?", (dumps(data), row["id"]))
    rt.conn.commit()


def _create_window_intent(rt: LifeEngineRuntime, window_key: str) -> str:
    """创建一条显式引用 Canon 日内窗口的用户向主动意图。

    输入是 runtime 和窗口 key；输出是 proactive intent id。调用方随后传入同源
    temporal facts 执行 evaluate。副作用是写入 proactive_intents；窗口引用放在
    delivery_policy.ref_window，代码不会解析摘要里的餐名。
    """
    created = rt.proactive(
        "create",
        summary=f"{window_key} 窗口到了，想提醒对方该吃点什么。",
        target_type="user",
        target_id="anonymous-user",
        intent_type="share_interesting",
        importance=95,
        urgency=85,
        novelty=80,
        relationship_relevance=90,
        privacy_level="safe_to_share",
        delivery_policy={"ref_window": window_key},
    )
    return created["results"][0]["result"]["id"]


def _facts(rt: LifeEngineRuntime, now: str) -> dict[str, Any]:
    """读取当前测试 Canon 下的 precise temporal_grounding facts。"""
    canon = get_active_canon(rt.conn, "agent", DEFAULT_AGENT_ID)
    return temporal_grounding(rt.conn, "agent", DEFAULT_AGENT_ID, canon=canon, now=now)


class _FakeUsage:
    """测试用 LifeAuthor usage 占位对象。

    该结构只在 companion gate 测试里模拟 host 返回形状；业务代码不读取这些固定值。
    """

    input_tokens = 10
    output_tokens = 5
    total_tokens = 15
    cost_usd = 0.0


class _FakeResult:
    """测试用 LifeAuthor structured result 包装。

    输入是 parsed dict；输出对象提供 LifeAuthor 读取的 parsed/provider/model/usage 字段。
    生命周期仅覆盖单个测试进程，不访问网络或数据库。
    """

    def __init__(self, parsed: dict[str, Any]):
        """保存 fake parsed 结果。"""
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _CountingLlm:
    """记录 LifeAuthor 是否被调用的 host 替身。

    输入是固定 parsed；输出是 `_FakeResult`。调用方用 `calls` 断言 temporal gate
    是否在生成前拦截 companion 候选。
    """

    def __init__(self, parsed: dict[str, Any]):
        """初始化固定返回值和调用记录。"""
        self._parsed = parsed
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs: Any) -> _FakeResult:
        """记录调用并返回固定结构化结果。"""
        self.calls.append(kwargs)
        return _FakeResult(self._parsed)


def test_temporal_gate_is_pure_and_generic_over_window_keys() -> None:
    """纯 helper 只看 relation/phase，不看 dinner/meal 名称。"""
    grounding = {
        "phase": "small_hours",
        "phase_label": "凌晨",
        "today_windows": [
            {"key": "studio_checkin", "relation": "passed", "minutes": 42, "recorded_today": False},
        ],
    }

    decision = temporal_gate(grounding, "companion_outreach", "studio_checkin")

    assert decision["suppress"] is True
    assert decision["reason"] == "passed_window_in_late_phase"
    assert decision["orientation"] == "past"
    assert decision["ref_window"]["key"] == "studio_checkin"
    assert decision["ref_window"]["minutes"] == 42

    allowed = temporal_gate(
        {**grounding, "today_windows": [{"key": "studio_checkin", "relation": "in_window", "minutes": 18}]},
        "companion_outreach",
        "studio_checkin",
    )
    assert allowed["suppress"] is False
    assert allowed["orientation"] == "present"


def test_passed_window_in_small_hours_suppresses_proactive_outbox(tmp_path: Path) -> None:
    """small_hours + passed ref_window 时，主动 outbox 在生成/发送前被确定性 suppress。"""
    _fresh_home(tmp_path, "hermes_temporal_gate_suppress")
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _patch_meal_windows(rt, {"dinner": "00:00"}, window_minutes=30)
        grounding = _facts(rt, "2026-07-03T01:00:00+09:00")
        dinner = next(w for w in grounding["today_windows"] if w["key"] == "dinner")
        assert grounding["phase"] == "small_hours"
        assert dinner["relation"] == "passed"
        assert dinner["minutes"] == 30

        intent_id = _create_window_intent(rt, "dinner")
        evaluated = rt.proactive(
            "evaluate",
            intent_id=intent_id,
            manual=True,
            temporal_grounding_facts=grounding,
        )

        item = evaluated["results"][0]["result"]["evaluated"][0]
        assert item["decision"] == "suppress"
        assert item["reason"] == "passed_window_in_late_phase"
        assert item["temporal_gate"]["orientation"] == "past"
        assert item["temporal_gate"]["ref_window"]["relation"] == "passed"
        assert item["outbox"] is None
        intent = rt.proactive("get", intent_id=intent_id)["intent"]
        assert intent["status"] == "suppressed"
        assert intent["decision"]["temporal_gate"]["ref_window"]["key"] == "dinner"
        assert rt.proactive("outbox")["outbox"] == []
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_in_window_ref_window_allows_proactive_outbox(tmp_path: Path) -> None:
    """同一通用窗口在 in_window 时允许 present-oriented 主动 outbox。"""
    _fresh_home(tmp_path, "hermes_temporal_gate_allow")
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _patch_meal_windows(rt, {"dinner": "00:00"}, window_minutes=30)
        grounding = _facts(rt, "2026-07-03T00:15:00+09:00")
        dinner = next(w for w in grounding["today_windows"] if w["key"] == "dinner")
        assert grounding["phase"] == "small_hours"
        assert dinner["relation"] == "in_window"
        assert dinner["minutes"] == 15

        intent_id = _create_window_intent(rt, "dinner")
        evaluated = rt.proactive(
            "evaluate",
            intent_id=intent_id,
            manual=True,
            temporal_grounding_facts=grounding,
        )

        item = evaluated["results"][0]["result"]["evaluated"][0]
        assert item["decision"] == "outbox_queued"
        assert item["temporal_gate"]["suppress"] is False
        assert item["temporal_gate"]["orientation"] == "present"
        assert item["temporal_gate"]["ref_window"]["relation"] == "in_window"
        assert item["outbox"]["status"] == "queued"
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_companion_ref_window_is_gated_before_authoring(tmp_path: Path, monkeypatch) -> None:
    """companion 候选显式引用 passed window 时，在 LifeAuthor 调用前被 suppress。"""
    _fresh_home(tmp_path, "hermes_temporal_gate_companion")
    fake = _CountingLlm({"summary": "该吃点什么了。", "emotional_tone": "warm"})
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _patch_meal_windows(rt, {"dinner": "00:00"}, window_minutes=30)
        grounding = _facts(rt, "2026-07-03T01:00:00+09:00")
        monkeypatch.setattr(
            companion_module,
            "_candidate",
            lambda *args, **kwargs: {"kind": "idle_share", "user_id": "anonymous-user", "ref_window": "dinner"},
        )

        package = companion_module.author_companion_for_tick(
            rt.conn,
            DEFAULT_AGENT_ID,
            control=ensure_control(rt.conn, "agent", DEFAULT_AGENT_ID),
            now="2026-07-03T01:00:00+09:00",
            trace_id=None,
            authoring_now={"time": grounding},
        )

        assert package is None
        assert fake.calls == []
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_prepare_heartbeat_authoring_carries_raw_temporal_facts(tmp_path: Path) -> None:
    """heartbeat authoring 包同时保留旧 authoring_now 和原始 data['time'] 事实。"""
    _fresh_home(tmp_path, "hermes_temporal_gate_authoring")
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        for key in ("autonomy", "reflection", "companion", "dream", "proactive"):
            rt.control("module", key=key, value="off")
        _patch_meal_windows(rt, {"dinner": "00:00"}, window_minutes=30)
        control = ensure_control(rt.conn, "agent", DEFAULT_AGENT_ID)

        package = prepare_heartbeat_authoring(
            rt.conn,
            "agent",
            DEFAULT_AGENT_ID,
            control,
            now="2026-07-03T01:00:00+09:00",
            tick_id="tick_temporal_gate",
            trace_id=None,
            manual=True,
        )

        assert package["time"]["phase"] == "small_hours"
        assert package["time"]["phase_label"] == "凌晨"
        assert package["authoring_now"]["time"] == package["time"]
        dinner = next(w for w in package["time"]["today_windows"] if w["key"] == "dinner")
        assert dinner["relation"] == "passed"
        assert dinner["minutes"] == 30
    finally:
        life_author.set_test_llm(None)
        rt.close()
