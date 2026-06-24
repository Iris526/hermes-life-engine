"""社会世界层：世界观槽、实体、声望、评价和流言。

这些测试刻意使用“学院/社团”作为样例世界观数据，但核心断言只关心槽机制：
LifeEngine 负责持久化通用社会事实，具体实体类型、关系轴、声望轴和流言渠道都由
世界观定义提供。
"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.18.x social world test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _result(commit: dict, index: int = 0) -> dict:
    return ((commit.get("results") or [])[index].get("result") or {})


def test_schema_v61_and_social_world_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert _SCHEMA_VERSION >= 61
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 64
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {
            "worldview_slot_definitions",
            "world_entities",
            "world_affiliations",
            "social_edges",
            "reputation_accounts",
            "reputation_events",
            "social_evaluations",
            "rumors",
            "rumor_exposures",
            "social_requests",
            "social_request_transitions",
        }.issubset(tables)
    finally:
        rt.close()


def test_social_slots_are_worldview_defined_not_core_enums(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.social("define_slot", slot_type="entity_kind", key="club",
                        label="社团", description="这个世界观里的学生组织")
        slot = _result(out)
        assert slot["slot_type"] == "entity_kind"
        assert slot["key"] == "club"

        axis = _result(rt.social("define_slot", slot_type="reputation_axis", key="craft_credit",
                                 label="手作信用"))
        assert axis["key"] == "craft_credit"
        request_type = _result(rt.social("define_slot", slot_type="request_type", key="commission",
                                         label="委托请求"))
        assert request_type["key"] == "commission"

        slots = rt.social("slots")["slots"]
        assert any(s["slot_type"] == "entity_kind" and s["key"] == "club" for s in slots)
        assert any(s["slot_type"] == "reputation_axis" and s["key"] == "craft_credit" for s in slots)
        assert any(s["slot_type"] == "request_type" and s["key"] == "commission" for s in slots)
    finally:
        rt.close()


def test_life_interface_exposes_social_domain(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        catalog = rt.interface("catalog")
        assert "social" in catalog["domains"]
        out = rt.interface("read", domain="social", view="summary")
        assert out["ok"] is True
        assert "social_world" in out
    finally:
        rt.close()


def test_social_graph_reputation_evaluation_and_rumor_flow(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        agent = _result(rt.social("create_entity", entity_kind="person", display_name="明灯",
                                  summary="当前生活主体"))
        club = _result(rt.social("create_entity", entity_kind="club", display_name="手作社",
                                 summary="重视作品交付的小社团"))
        observer = _result(rt.social("create_entity", entity_kind="person", display_name="社团前辈"))

        aff = _result(rt.social("link_affiliation", subject_entity_id=agent["id"],
                                faction_entity_id=club["id"], role="member", strength=0.8))
        assert aff["role"] == "member"

        edge = _result(rt.social("set_edge", source_entity_id=observer["id"],
                                 target_entity_id=agent["id"], axis="trust", value=35,
                                 confidence=0.7, visibility="known"))
        assert edge["axis"] == "trust"
        assert edge["value"] == 35

        rep = _result(rt.social("reputation_event", subject_entity_id=agent["id"],
                                audience_entity_id=club["id"], axis="craft_credit",
                                delta=18, reason="按时交付社团委托"))
        assert rep["account"]["value"] == 18
        rt.social("reputation_event", subject_entity_id=agent["id"],
                  audience_entity_id=club["id"], axis="craft_credit",
                  delta=-5, reason="迟到了一次")
        accounts = rt.social("reputation_accounts", subject_entity_id=agent["id"])["reputation"]
        assert any(a["axis"] == "craft_credit" and a["value"] == 13 for a in accounts)

        evaluation = _result(rt.social("evaluate", evaluator_entity_id=club["id"],
                                       subject_entity_id=agent["id"], axis="reliability",
                                       score=42, reason="最近交付稳定",
                                       truth_layer="social_perception"))
        assert evaluation["truth_layer"] == "social_perception"

        rumor = _result(rt.social("rumor", subject_entity_id=agent["id"],
                                  content="听说她最近接了一个很难的手作委托。",
                                  channel="club_chat", heat=0.7, credibility=0.4))
        assert rumor["truth_layer"] == "rumor_unverified"
        exposure = _result(rt.social("expose_rumor", rumor_id=rumor["id"],
                                     entity_id=observer["id"], exposure_state="heard"))
        assert exposure["exposure_state"] == "heard"

        summary = rt.social("summary")["social_world"]
        assert summary["entities"]
        assert summary["reputation"]
        assert summary["evaluations"]
        assert summary["rumors"]

        tx_count = rt.conn.execute(
            "SELECT COUNT(*) FROM life_ops WHERE op_type LIKE 'SOCIAL_%'"
        ).fetchone()[0]
        assert tx_count >= 8
    finally:
        rt.close()


def test_social_request_lifecycle_and_slot_advisories(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        requester = _result(rt.social("create_entity", entity_kind="client", display_name="委托人甲"))
        target = _result(rt.social("create_entity", entity_kind="shrine", display_name="归明观"))
        request = _result(rt.social(
            "record_request",
            requester_entity_id=requester["id"],
            target_entity_id=target["id"],
            request_type="fieldwork_request",
            topic="night_patrol",
            summary="想请明灯夜间巡查一处异常。",
            details={"preferred_time": "night"},
        ))
        assert request["status"] == "open"
        assert request["details"]["preferred_time"] == "night"

        accepted = _result(rt.social(
            "request_transition",
            request_id=request["id"],
            transition_action="accept",
            quote={"amount": 60, "currency": "灵铢"},
            reason="报价后接受",
        ))
        assert accepted["request"]["status"] == "accepted"
        assert accepted["request"]["quote"]["amount"] == 60

        converted = _result(rt.social(
            "request_transition",
            request_id=request["id"],
            transition_action="convert_event",
            linked_event_id="event-night-patrol",
        ))
        assert converted["request"]["status"] == "in_progress"
        assert converted["request"]["linked_event_id"] == "event-night-patrol"

        completed = _result(rt.social(
            "request_transition",
            request_id=request["id"],
            transition_action="complete",
            billing={"paid": 60, "currency": "灵铢"},
        ))
        assert completed["request"]["status"] == "completed"
        assert completed["request"]["billing"]["paid"] == 60

        transitions = rt.social("request_transitions", request_id=request["id"])["transitions"]
        assert [t["to_status"] for t in transitions][:3] == ["completed", "in_progress", "accepted"]
        advisories = rt.social("advisories")["advisories"]
        assert any(a["slot_type"] == "request_type" and a["key"] == "fieldwork_request" for a in advisories)
        assert any(a["slot_type"] == "entity_kind" and a["key"] == "client" for a in advisories)
    finally:
        rt.close()


def test_human_review_surfaces_active_social_requests_without_closing_them(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        requester = _result(rt.social("create_entity", entity_kind="client", display_name="委托人乙"))
        target = _result(rt.social("create_entity", entity_kind="shrine", display_name="归明观"))
        open_request = _result(rt.social(
            "record_request",
            requester_entity_id=requester["id"],
            target_entity_id=target["id"],
            request_type="fieldwork_request",
            topic="late_visit",
            summary="想请明灯今晚去看一处响动。",
        ))
        completed_request = _result(rt.social(
            "record_request",
            requester_entity_id=requester["id"],
            target_entity_id=target["id"],
            request_type="wish",
            topic="old_case",
            summary="一件已经处理完的旧愿望。",
            idempotency_key="test:completed-social-request",
        ))
        rt.social("request_transition", request_id=completed_request["id"], transition_action="complete")

        review = rt.review("summary")
        items = [i for i in review["items"] if i["item_type"] == "social_request"]

        assert review["summary"]["social_requests"]["active"] == 1
        assert "社会请求：活跃 1 条" in review["rendered"]
        assert f"request_id={open_request['id']}" in review["rendered"]
        assert "可选：accept/reject/convert_event" in review["rendered"]
        assert len(items) == 1
        assert items[0]["source_id"] == open_request["id"]
        assert items[0]["section"] == "social_world"
        assert items[0]["action_hint"]["tool"] == "life_social"
        assert items[0]["action_hint"]["action"] == "request_transition"
        assert items[0]["action_hint"]["request_id"] == open_request["id"]
        assert completed_request["id"] not in {i["source_id"] for i in items}

        plan = rt.review("preview_action", item_id=items[0]["id"])
        assert plan["plan"]["application_type"] == "manual_review"
        assert plan["plan"]["requires_choice"] is True
        assert plan["plan"]["request_id"] == open_request["id"]

        still_open = rt.social("requests", status="open")["requests"]
        assert any(r["id"] == open_request["id"] for r in still_open)
    finally:
        rt.close()


def test_social_world_surfaces_in_inner_life_context(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        agent = _result(rt.social("create_entity", entity_kind="person", display_name="明灯"))
        rt.social("reputation_event", subject_entity_id=agent["id"], axis="fame", delta=12,
                  reason="帮邻里解决了一件小事")
        rt.social("rumor", subject_entity_id=agent["id"], content="有人说她最近变得很可靠。",
                  channel="neighborhood", heat=0.5)
        ctx = rt.build_context_for_turn("s1", "t1", "今天怎么样？")
        assert "social_world" in ctx
        assert "rumor_unverified" in ctx
        assert "fame" in ctx
    finally:
        rt.close()
