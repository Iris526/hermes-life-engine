from __future__ import annotations

import json
import os
import shutil


RESIDUE_TERMS = (
    "灵铢", "归明观", "符纸", "朱砂", "香案", "净符", "晨巡",
    "雨棚巷", "第七城", "师兄", "明灯", "Asia/Tokyo", "Asia-Tokyo",
)
SOCIAL_SLOT_RESIDUE_TERMS = ("道观", "宫观", "香客", "主顾", "香客口碑")


def fresh_home(tmp_path):
    """为单个测试创建隔离 HERMES_HOME。

    输入是 pytest tmp_path；输出是全新的测试 home 路径。调用方是本文件每个测试。
    副作用仅限设置进程环境变量并清理该临时目录，失败时由 pytest 暴露文件系统错误。
    """
    home = tmp_path / "hermes_home_character_externalization"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _contains_residue(value) -> bool:
    """检查序列化结果里是否包含本切片要迁出的角色残留词。

    输入是任意可 JSON 序列化的测试结果；输出为是否命中残留词。调用方是默认
    兼容和现代 Canon 两个回归测试。函数无业务副作用，序列化失败会直接让测试失败。
    """
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return any(term in text for term in RESIDUE_TERMS)


def _result(commit: dict, index: int = 0) -> dict:
    """从 LifeOps commit 结果里取指定 op 的 result。

    输入是 runtime 工具返回的 commit dict 和结果下标；输出是该 op 的 result。
    调用方是本文件社会投影测试。函数只读传入对象；结构缺失时返回空 dict，让
    断言在后续字段检查处失败。
    """
    return ((commit.get("results") or [])[index].get("result") or {})


def _slot_text(rt, owner_id: str) -> str:
    """读取当前 owner 已落库的社会槽定义文本。

    输入是测试 runtime 和 owner_id；输出是稳定 JSON 文本。调用方用它断言默认
    agent 仍有 skin 槽、现代 owner 不继承角色槽。函数只读测试库。
    """
    rows = rt.conn.execute(
        """SELECT slot_type, key, label, description
           FROM worldview_slot_definitions
           WHERE owner_kind='agent' AND owner_id=?
           ORDER BY slot_type, key""",
        (owner_id,),
    ).fetchall()
    return json.dumps([dict(row) for row in rows], ensure_ascii=False, sort_keys=True)


class _FakeUsage:
    """模拟 LifeAuthor usage 统计。

    该结构只服务本文件 fake LLM，生命周期限于单次 `_idle_prompt` 调用。业务代码
    只读取这些固定 token/cost 字段写审计，测试断言不依赖数值。
    """

    input_tokens = 12
    output_tokens = 8
    total_tokens = 20
    cost_usd = 0.0


class _FakeResult:
    """承载 fake LLM 返回给 LifeAuthor 的结构化结果。

    输入是 parsed dict；输出对象暴露 LifeAuthor 期望的 parsed/provider/model/usage
    字段。作用域仅限本文件 prompt 断言，不代表真实宿主模型合同变更。
    """

    def __init__(self, parsed):
        self.parsed = parsed
        self.usage = _FakeUsage()
        self.provider = "fake"
        self.model = "fake-model"


class _FakeLlm:
    """记录 LifeAuthor 调用参数的离线 fake LLM。

    输入是固定 parsed；输出由 `complete_structured` 包成 `_FakeResult`。调用方是
    `_idle_prompt`，用于读取 idle prompt instructions，不访问网络或宿主模型。
    """

    def __init__(self, parsed):
        self._parsed = parsed
        self.calls: list[dict] = []

    def complete_structured(self, **kwargs):
        """记录一次 LifeAuthor 调用并返回固定 parsed。"""
        self.calls.append(kwargs)
        return _FakeResult(self._parsed)


def _idle_prompt(rt, owner_id: str) -> str:
    """直接调用 companion idle author 并返回 LifeAuthor instructions。

    输入是测试 runtime 和 agent owner_id；输出是 companion 传给 LifeAuthor 的 prompt。
    调用方用它断言默认 skin 有称呼、现代 no-skin agent 省略称呼。副作用仅限设置
    并恢复测试 LLM，以及写一条 LifeAuthor 审计记录。
    """
    from lifeengine import companion as companion_module
    from lifeengine import life_author

    fake = _FakeLlm({"summary": "今天画了一张小草图，忽然想起你。", "emotional_tone": "warm"})
    life_author.set_test_llm(fake)
    try:
        parsed = companion_module._author_idle_line(rt.conn, owner_id, "anonymous-user", trace_id=None)
        assert parsed and parsed["summary"]
        assert fake.calls
        return fake.calls[-1]["instructions"]
    finally:
        life_author.set_test_llm(None)


def test_default_agent_keeps_legacy_living_skin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(fresh_home(tmp_path)))
    from lifeengine.canon import (
        canon_companion_address_terms,
        canon_context_intent_keywords,
        canon_evidence_object_groups,
        canon_timezone,
    )
    from lifeengine.context_policy import infer_turn_domains
    from lifeengine.proactive import _fallback_outbox_text
    from lifeengine.receipts import claim_matches_evidence
    from lifeengine.runtime import LifeEngineRuntime

    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，heartbeat 要能自己补齐当天生活节奏。")
        committed = rt.commit_canon()["canon"]
        assert committed["data"]["living"]["skin"] == "guimingguan"
        assert canon_timezone(committed["data"]) == "Asia/Tokyo"
        assert "符纸" in canon_evidence_object_groups(committed["data"])["work_item"]
        assert "灵铢" in canon_context_intent_keywords(committed["data"])["resource"]
        assert infer_turn_domains("灵铢还够吗？", canon=committed["data"]) == ["resource"]
        assert claim_matches_evidence("我处理了符纸。", ["完成了朱砂准备"], canon=committed["data"])
        assert _fallback_outbox_text(
            {"summary": "师兄刚才提到的事"},
            address_terms=canon_companion_address_terms(committed["data"]),
        ) == "师兄刚才提到的事。"

        resources = rt.living("init_resources")
        assert "money.lingzhu" in resources["resource_keys"]

        rhythm = rt.living("day_rhythm", date="2030-01-02")
        assert rhythm["ok"] is True
        assert rhythm["event_ids"]
        rows = rt.conn.execute(
            "SELECT title, payload_json FROM life_rhythm_items WHERE owner_kind='agent' AND owner_id='default-agent' ORDER BY start",
        ).fetchall()
        payload = [dict(row) for row in rows]
        assert _contains_residue(payload)
        assert "归明观晨巡与开观" in rhythm["rendered"]
        assert "晨巡" in json.dumps(payload, ensure_ascii=False)

        ev = _result(rt.event_tool(
            "create",
            title="归明观午后摆摊卖净符",
            event_type="work",
            activity_domain="venture",
            tags=["摆摊", "归明观", "净符"],
            attributes={
                "wish_topic": "general_blessing",
                "venue_name": "归明观",
                "customer_group_name": "东市香客",
            },
            resource_costs={},
        ))
        projected = _result(rt.event_tool("complete", event_id=ev["id"], summary="卖符顺利，香客愿意再来。")).get("social_projection") or {}
        assert projected["projected"] is True
        default_slot_text = _slot_text(rt, "default-agent")
        assert "道观/宫观" in default_slot_text
        assert "香客/主顾" in default_slot_text
        assert "香客口碑" in default_slot_text

        default_prompt = _idle_prompt(rt, "default-agent")
        assert "可以自然叫他“师兄”" in default_prompt
    finally:
        rt.close()


def test_modern_canon_without_living_skin_has_no_character_residue(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(fresh_home(tmp_path)))
    from lifeengine.canon import (
        canon_companion_address_terms,
        canon_context_intent_keywords,
        canon_evidence_object_groups,
        canon_timezone,
        get_active_canon,
    )
    from lifeengine.constants import DEFAULT_CANON_TEMPLATE
    from lifeengine.context_policy import infer_turn_domains
    from lifeengine.living import supply_items
    from lifeengine.proactive import _fallback_outbox_text
    from lifeengine.receipts import claim_matches_evidence
    from lifeengine.runtime import LifeEngineRuntime
    from lifeengine.schedule_view import _tz_from_canon

    rt = LifeEngineRuntime()
    owner_id = "modern-illustrator"
    try:
        assert not _contains_residue(DEFAULT_CANON_TEMPLATE)

        rt.setup("名字是 凛，是现代插画师，生活在现代城市，货币用日元。", "agent", owner_id)
        committed = rt.commit_canon("agent", owner_id)["canon"]
        assert not (committed["data"].get("living") or {}).get("skin")

        resources = rt.living("init_resources", owner_id=owner_id)
        rhythm = rt.living("day_rhythm", owner_id=owner_id, date="2030-01-02")
        canon = get_active_canon(rt.conn, "agent", owner_id)
        supplies = supply_items(canon=canon)
        assert canon_timezone(canon) == "UTC"
        assert _tz_from_canon(canon) == "UTC"
        assert canon_evidence_object_groups(canon) == {}
        assert canon_context_intent_keywords(canon) == {}
        assert canon_companion_address_terms(canon) == ()
        assert infer_turn_domains("灵铢还够吗？", canon=canon) == []
        assert not claim_matches_evidence("我处理了符纸。", ["完成了朱砂准备"], canon=canon)
        assert _fallback_outbox_text({"summary": "师兄提醒的事"}).startswith("我这边")

        rows = rt.conn.execute(
            "SELECT title, payload_json FROM life_rhythm_items WHERE owner_kind='agent' AND owner_id=? ORDER BY start",
            (owner_id,),
        ).fetchall()
        proactive_rows = rt.conn.execute(
            "SELECT summary FROM proactive_intents WHERE agent_id=? ORDER BY created_at",
            (owner_id,),
        ).fetchall()
        resource_state = rt.resources("list", owner_id=owner_id)["resources"]

        ev = _result(rt.event_tool(
            "create",
            owner_id=owner_id,
            title="周末创意市集摆摊",
            event_type="work",
            activity_domain="venture",
            tags=["stall", "shop"],
            attributes={
                "wish_topic": "poster_feedback",
                "venue_name": "独立创意市集",
                "customer_group_name": "路过顾客",
            },
            resource_costs={},
        ))
        projected = _result(rt.event_tool(
            "complete",
            owner_id=owner_id,
            event_id=ev["id"],
            summary="明信片卖得还不错，顾客说想看下一套。",
        )).get("social_projection") or {}
        assert projected["projected"] is True
        modern_slot_text = _slot_text(rt, owner_id)
        assert all(term not in modern_slot_text for term in SOCIAL_SLOT_RESIDUE_TERMS)
        social_request_text = json.dumps(
            [
                dict(row)
                for row in rt.conn.execute(
                    "SELECT summary FROM social_requests WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at",
                    (owner_id,),
                ).fetchall()
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        assert all(term not in social_request_text for term in SOCIAL_SLOT_RESIDUE_TERMS)

        modern_prompt = _idle_prompt(rt, owner_id)
        assert "师兄" not in modern_prompt
        assert "可以自然叫他" not in modern_prompt

        world_map = rt.world("map", owner_id=owner_id, location={"name": "独立创意市集"})["world_map"]
        assert (world_map.get("actor") or {}).get("label") == "凛"

        produced = {
            "resource_rendered": resources["rendered"],
            "resource_state": resource_state,
            "rhythm_rendered": rhythm["rendered"],
            "rhythm_items": [dict(row) for row in rows],
            "supplies": supplies,
            "proactive_summaries": [row["summary"] for row in proactive_rows],
            "world_map": world_map,
            "timezone": canon_timezone(canon),
        }
        assert rhythm["event_ids"] == []
        assert supplies == []
        assert not _contains_residue(produced)
    finally:
        rt.close()
