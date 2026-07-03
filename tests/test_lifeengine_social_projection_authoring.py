from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from lifeengine import life_author, venture
from lifeengine.db import transaction
from lifeengine.events import complete_event
from lifeengine.jsonutil import loads
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.social_projector import (
    prepare_completed_event_projection_authoring_for_block,
    prepare_venture_sale_settlement_authoring,
    project_completed_event,
    project_venture_sale_settlement,
)


def _fresh_home(tmp_path: Path, name: str) -> Path:
    """创建 social projection authoring 测试专用的隔离 home。

    输入是 pytest 临时目录和场景名；输出是新的 HERMES_HOME 路径。调用方是本文件
    每个测试。副作用是设置环境变量并删除旧目录，避免 fake host 调用数、projection
    ledger 和 social facts 在测试之间串扰。
    """
    home = tmp_path / name
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _setup_agent(rt: LifeEngineRuntime) -> None:
    """初始化只关注社会投影的测试 Agent。

    输入是当前 runtime；输出为空。调用方式是同步测试夹具；副作用是创建并启用
    canon、命名主体、初始化资源，并关闭无关 heartbeat 生成式模块，让 fake host
    调用主要来自 execution completion 与 social projection rumor authoring。
    """
    rt.setup("一个会经营小摊、也会接外勤委托的生活主体。")
    rt.commit_canon()
    rt.rename("明灯")
    rt.control("resume")
    rt.living("init_resources")
    for key in ("autonomy", "reflection", "companion", "dream", "proactive"):
        rt.control("module", key=key, value="off")


class _FakeUsage:
    """测试用模型用量对象。

    该结构模拟 host LLM usage，仅用于 LifeAuthor 审计记录；业务断言不依赖这些数值。
    生命周期限于单个测试进程。
    """

    input_tokens = 40
    output_tokens = 20
    total_tokens = 60
    cost_usd = 0.0002


class _FakeResult:
    """测试用结构化模型返回。

    该结构承载 LifeAuthor 读取的 parsed/provider/model/usage 字段；调用方是
    `_FakeLlm.complete_structured`。它不访问网络、不写业务表，只包装预置 parsed。
    """

    def __init__(self, parsed: dict[str, Any]):
        """保存本次 fake host 返回的结构化内容。"""
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _FakeLlm:
    """测试用 host LLM 替身。

    输入是按 LifeAuthor purpose 分发的 parsed JSON；输出是 `_FakeResult`。它记录
    每次 complete_structured 调用，供测试确认 social_projection_rumor 只 author 一次。
    """

    def __init__(self, parsed_by_purpose: dict[str, dict[str, Any]]):
        """初始化一个可在测试中动态替换 parsed 的 fake host。"""
        self.parsed_by_purpose = parsed_by_purpose
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs):
        """记录调用并返回当前 purpose 对应的结构化结果。"""
        self.calls.append(kwargs)
        return _FakeResult(self.parsed_by_purpose.get(str(kwargs.get("purpose") or ""), {}))


def _result(commit: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """从 runtime commit 结果中取出第 index 个 LifeOp result。"""
    return ((commit.get("results") or [])[index].get("result") or {})


def _create_stall_event(rt: LifeEngineRuntime, title: str, *,
                        venue_name: str = "街角摊",
                        customer_group_name: str = "附近来访者") -> dict[str, Any]:
    """创建一个会被 social_projector 分类为 stall 的事件。

    输入是 runtime、标题和可选场所/客群名；输出 event dict。调用方负责选择是否
    schedule、complete 或 project。副作用是通过正常 event_tool 写入事件。
    """
    return _result(rt.event_tool(
        "create",
        title=title,
        event_type="work",
        activity_domain="venture",
        tags=["摆摊", "护符"],
        attributes={
            "venue_name": venue_name,
            "customer_group_name": customer_group_name,
            "wish_topic": "general_blessing",
        },
        location={"name": venue_name, "kind": "freeform"},
        resource_costs={},
    ))


def _create_commission_event(rt: LifeEngineRuntime, title: str) -> dict[str, Any]:
    """创建一个会被 social_projector 分类为 commission 的事件。"""
    return _result(rt.event_tool(
        "create",
        title=title,
        event_type="commission",
        activity_domain="fieldwork",
        tags=["委托", "外勤"],
        participants=[{"role": "client", "name": "委托人甲"}],
        attributes={"commission_topic": "shop_noise"},
        resource_costs={},
    ))


def _schedule_event(rt: LifeEngineRuntime, event_id: str) -> dict[str, Any]:
    """给事件创建一个会在测试 tick 中到期的 schedule block。"""
    return _result(rt.event_tool(
        "schedule",
        event_id=event_id,
        start="2026-06-07T10:00:00+00:00",
        end="2026-06-07T11:00:00+00:00",
        timezone_name="UTC",
    ))


def _complete_without_projection(rt: LifeEngineRuntime, event_id: str, summary: str) -> None:
    """只完成事件，不触发 runtime 的 social projection 包装。

    输入是 runtime、event_id 和 completion summary；输出为空。调用方随后直接调用
    social_projector 函数。副作用是通过 events.complete_event 写 actions/results/
    event status，但不会调用 runtime `_project_completed_event_safe`。
    """
    with transaction(rt.conn):
        complete_event(rt.conn, "agent", "default-agent", event_id, summary, resource_deltas={}, source="test")


def _latest_rumor_for(rt: LifeEngineRuntime, target_id: str) -> dict[str, Any]:
    """读取某个 event/occurrence 目标上的最新 rumor。"""
    row = rt.conn.execute(
        "SELECT * FROM rumors WHERE target_id=? ORDER BY rowid DESC LIMIT 1",
        (target_id,),
    ).fetchone()
    assert row is not None
    return dict(row)


def _social_author_calls(fake: _FakeLlm) -> list[dict[str, Any]]:
    """筛出 social projection rumor 的 LifeAuthor 调用。"""
    return [c for c in fake.calls if c.get("purpose") == "life_author:social_projection_rumor"]


def _create_settled_sale_occurrence(rt: LifeEngineRuntime, title: str = "护符摊") -> tuple[str, str]:
    """创建一个已完成、已结算但尚未投影的经营 occurrence。

    输入是 runtime 和活动标题；输出 `(event_id, occurrence_id)`。副作用是注册 venture、
    创建并完成事件、写 venture_occurrences，再直接标记 sale_settled 供投影测试使用。
    """
    reg = rt.activity(
        "register",
        title=title,
        cadence_kind="daily",
        supply_chain={
            "goods_resource": "stock.hufu",
            "goods_name": "护符",
            "unit": "枚",
            "initial_stock": 10,
            "unit_price": 6,
            "demand_per_occurrence": 2,
            "money_resource": "money.test",
        },
        tags=["摆摊", "护符"],
    )
    activity_id = reg["receipt"]["facts"][0]["evidence"]["activity_id"]
    event = _create_stall_event(rt, title)
    venture.record_occurrence(rt.conn, "agent", "default-agent", activity_id, "2026-06-22", event["id"], None)
    _complete_without_projection(rt, event["id"], "当天经营结束，等待销售结算。")
    occ = rt.conn.execute("SELECT * FROM venture_occurrences WHERE event_id=?", (event["id"],)).fetchone()
    assert occ is not None
    rt.conn.execute(
        "UPDATE venture_occurrences SET sale_settled=1, sold_quantity=2, income=12 WHERE id=?",
        (occ["id"],),
    )
    return event["id"], occ["id"]


def test_heartbeat_social_projection_rumor_uses_life_author(tmp_path):
    """fake host 时，heartbeat 完成事件投影的 rumor content 来自 LifeAuthor。"""
    _fresh_home(tmp_path, "hermes_home_social_projection_authoring")
    authored_content = "有人把街角摊的这次收尾悄悄传开，说摊主待人稳，东西也让人安心。"
    fake = _FakeLlm({
        "life_author:execution_narrative": {
            "narrative": "街角摊顺利收摊，来访者愿意再停一停。",
            "memory": "这次街角摆摊收得顺，记下了来访者的好感。",
        },
        "life_author:social_projection_rumor": {"content": authored_content},
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        event = _create_stall_event(rt, "街角摆摊卖护符")
        _schedule_event(rt, event["id"])

        tick = rt.tick(now="2026-06-07T11:01:00+00:00", manual=False)

        assert tick["ok"] is True
        rumor = _latest_rumor_for(rt, event["id"])
        assert rumor["content"] == authored_content
        assert rumor["heat"] == 0.24
        assert rumor["truth_layer"] == "rumor_unverified"
        calls = _social_author_calls(fake)
        assert len(calls) == 1
        assert calls[0]["purpose"] == "life_author:social_projection_rumor"
        assert "街角摆摊卖护符" in calls[0]["input"][0]["text"]
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_social_projection_rumor_no_host_uses_byte_identical_fallback(tmp_path):
    """无 host 时，stall/commission rumor content 逐字保留旧固定 phrasing。"""
    _fresh_home(tmp_path, "hermes_home_social_projection_fallback")
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        stall = _create_stall_event(rt, "街角摆摊卖护符")
        stall_block = _schedule_event(rt, stall["id"])
        _complete_without_projection(rt, stall["id"], "卖得顺利，来访者愿意再来。")

        authored = prepare_completed_event_projection_authoring_for_block(
            rt.conn, "agent", "default-agent", stall_block, completion_authoring={"narrative": "卖得顺利，来访者愿意再来。"}
        )
        assert authored is None
        project_completed_event(rt.conn, "agent", "default-agent", stall["id"], summary="卖得顺利，来访者愿意再来。")
        stall_rumor = _latest_rumor_for(rt, stall["id"])
        assert stall_rumor["content"] == "有来访者低声说，街角摊这回经营顺利，明灯待人也算温和。"

        commission = _create_commission_event(rt, "上门处理铺子怪响")
        _complete_without_projection(rt, commission["id"], "问题没有完全解决，委托人还有些担心。")
        project_completed_event(rt.conn, "agent", "default-agent", commission["id"], summary="问题没有完全解决，委托人还有些担心。")
        commission_rumor = _latest_rumor_for(rt, commission["id"])
        assert commission_rumor["content"] == "有人私下担心，明灯这次外勤没有完全解决问题。"
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_social_projection_rumor_authoring_is_idempotent_for_event_and_occurrence(tmp_path):
    """同一 event/occurrence 二次投影不再 author，已存 rumor content 不改变。"""
    _fresh_home(tmp_path, "hermes_home_social_projection_idempotency")
    fake = _FakeLlm({
        "life_author:social_projection_rumor": {"content": "第一版社会传言正文"},
    })
    life_author.set_test_llm(fake)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        event = _create_stall_event(rt, "街角摆摊卖护符")
        block = _schedule_event(rt, event["id"])
        _complete_without_projection(rt, event["id"], "卖得顺利，来访者愿意再来。")

        authored = prepare_completed_event_projection_authoring_for_block(
            rt.conn, "agent", "default-agent", block, completion_authoring={"narrative": "卖得顺利，来访者愿意再来。"}
        )
        assert authored == {"content": "第一版社会传言正文"}
        first = project_completed_event(
            rt.conn, "agent", "default-agent", event["id"],
            summary="卖得顺利，来访者愿意再来。",
            rumor_authoring=authored,
        )
        assert first["projected"] is True
        stored_event_content = _latest_rumor_for(rt, event["id"])["content"]

        fake.parsed_by_purpose["life_author:social_projection_rumor"] = {"content": "第二版不应写入"}
        second_authored = prepare_completed_event_projection_authoring_for_block(
            rt.conn, "agent", "default-agent", block, completion_authoring={"narrative": "卖得顺利，来访者愿意再来。"}
        )
        second = project_completed_event(
            rt.conn, "agent", "default-agent", event["id"],
            summary="卖得顺利，来访者愿意再来。",
            rumor_authoring=second_authored,
        )
        assert second_authored is None
        assert second["projected"] is False
        assert second["reason"] == "already_projected"
        assert _latest_rumor_for(rt, event["id"])["content"] == stored_event_content

        fake.parsed_by_purpose["life_author:social_projection_rumor"] = {"content": "经营结算第一版传言"}
        sale_event_id, occurrence_id = _create_settled_sale_occurrence(rt, "护符摊")
        sale_authored = prepare_venture_sale_settlement_authoring(rt.conn, "agent", "default-agent", occurrence_id)
        assert sale_authored == {"content": "经营结算第一版传言"}
        sale_first = project_venture_sale_settlement(
            rt.conn, "agent", "default-agent", occurrence_id,
            rumor_authoring=sale_authored,
        )
        assert sale_first["projected"] is True
        stored_occurrence_content = _latest_rumor_for(rt, sale_event_id)["content"]

        fake.parsed_by_purpose["life_author:social_projection_rumor"] = {"content": "经营结算第二版不应写入"}
        sale_second_authored = prepare_venture_sale_settlement_authoring(rt.conn, "agent", "default-agent", occurrence_id)
        sale_second = project_venture_sale_settlement(
            rt.conn, "agent", "default-agent", occurrence_id,
            rumor_authoring=sale_second_authored,
        )
        assert sale_second_authored is None
        assert sale_second["projected"] is False
        assert sale_second["reason"] == "already_projected"
        assert _latest_rumor_for(rt, sale_event_id)["content"] == stored_occurrence_content
        assert len(_social_author_calls(fake)) == 2
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_social_projection_rumor_authoring_preserves_numbers_heat_and_truth_layers(tmp_path):
    """authoring 只改变 content，不改变 reputation/evaluation/rumor 的数值合同。"""
    _fresh_home(tmp_path, "hermes_home_social_projection_numbers")
    life_author.disable_test_llm()
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        scenarios = [
            ("stall_positive", _create_stall_event(rt, "晴天街角摆摊"), "卖得顺利，来访者愿意再来。", 0.24, "positive", {2.5, 3.0}),
            ("stall_negative", _create_stall_event(rt, "雨天街角摆摊"), "卖得不顺利，来访者担心效果。", 0.28, "concern", {-2.0, -2.5}),
            ("commission_positive", _create_commission_event(rt, "上门处理铺子怪响"), "勘察后给出处理办法，委托人表示感谢。", 0.22, "positive", {3.0, 5.0}),
            ("commission_negative", _create_commission_event(rt, "夜里处理铺子怪响"), "问题没有完全解决，委托人还有些担心。", 0.32, "concern", {-5.0, -3.0}),
        ]
        for label, event, summary, expected_heat, expected_sentiment, expected_deltas in scenarios:
            _complete_without_projection(rt, event["id"], summary)
            out = project_completed_event(
                rt.conn, "agent", "default-agent", event["id"],
                summary=summary,
                rumor_authoring={"content": f"{label} authored rumor wording"},
            )
            assert out["projected"] is True
            rumor = _latest_rumor_for(rt, event["id"])
            assert rumor["content"] == f"{label} authored rumor wording"
            assert rumor["heat"] == expected_heat
            assert rumor["sentiment"] == expected_sentiment
            assert rumor["truth_layer"] == "rumor_unverified"

            rep_rows = rt.conn.execute(
                "SELECT delta, evidence_json FROM reputation_events WHERE evidence_id=?",
                (event["id"],),
            ).fetchall()
            assert rep_rows
            assert {float(r["delta"]) for r in rep_rows} == expected_deltas
            for row in rep_rows:
                evidence = loads(row["evidence_json"], {})
                assert evidence["event_id"] == event["id"]

            eval_layers = {
                r["truth_layer"]
                for r in rt.conn.execute(
                    "SELECT truth_layer FROM social_evaluations WHERE target_id=?",
                    (event["id"],),
                ).fetchall()
            }
            assert eval_layers == {"social_perception"}
    finally:
        life_author.set_test_llm(None)
        rt.close()
