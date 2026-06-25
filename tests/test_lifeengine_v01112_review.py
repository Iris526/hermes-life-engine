import tempfile
import json
from datetime import datetime, timezone, timedelta

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.cli import slash_life
from lifeengine.reply_gate import create_delayed_reply


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01112_")
    monkeypatch.setenv("HERMES_HOME", d)
    return d


def _result(commit: dict, index: int = 0) -> dict:
    return ((commit.get("results") or [])[index].get("result") or {})


def test_schema_v32_and_review_tables(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 32
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "human_review_runs" in tables
        assert "human_review_items" in tables
    finally:
        conn.close()


def test_review_aggregates_delayed_reply_and_persists(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="睡醒以后告诉我你的梦。", reason="test")
        out = rt.review("summary")
        assert out["ok"] is True
        assert "LifeEngine Review" in out["rendered"]
        assert any(i["item_type"] == "delayed_reply" for i in out["items"])
        runs = rt.review("runs")
        assert runs["runs"]
        got = rt.review("get_run", review_run_id=out["review_run_id"])
        assert got["run"]["items"]
    finally:
        rt.close()


def test_review_surfaces_policy_conflict(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.policy("set", policy_patch={"reply": {"gate_mode": "strict", "call_words": []}})
        out = rt.review("summary")
        assert any(i["item_type"] == "policy_conflict" for i in out["items"])
        assert "策略" in out["rendered"]
    finally:
        rt.close()


def test_review_explains_quiet_hours_proactive_wait(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，允许主动聊天。")
        rt.commit_canon()
        rt.control("resume")
        rt.control("module", key="proactive", value="auto_send")
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=1)).strftime("%H:%M")
        end = (now + timedelta(hours=1)).strftime("%H:%M")
        row = rt.conn.execute(
            "SELECT id, data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id='default-agent' AND status='active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        data = json.loads(row["data_json"])
        data.setdefault("proactive", {})
        data["proactive"].update({"timezone": "UTC", "quiet_hours": {"start": start, "end": end, "timezone": "UTC"}})
        rt.conn.execute("UPDATE canon_versions SET data_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))
        created = rt.proactive(
            "create",
            summary="我想告诉你刚才的小进展",
            target_type="user",
            target_id="u1",
            intent_type="report_progress",
            importance=95,
            urgency=90,
            novelty=90,
            relationship_relevance=95,
            privacy_level="safe_to_share",
        )
        intent_id = created["results"][0]["result"]["id"]
        evaluated = rt.proactive("evaluate", intent_id=intent_id)
        assert evaluated["results"][0]["result"]["evaluated"][0]["reason"] == "quiet hours active"

        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "proactive_intent")
        assert "安静时段" in item["title"]
        assert item["title"].startswith("我有想说的话")
        assert "我先不打扰" in item["message"]
        assert item["action_hint"]["intent_id"] == intent_id
        assert "正在避开安静时段" in review["rendered"]
        assert "Agent 有想说的话" not in review["rendered"]
    finally:
        rt.close()


def test_review_surfaces_recent_suppressed_proactive_intent(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，主动消息需要可解释。")
        rt.commit_canon()
        rt.control("resume")
        rt.control("module", key="proactive", value="off")
        created = rt.proactive(
            "create",
            summary="我想问问你睡前是不是还在忙",
            target_type="user",
            target_id="u1",
            intent_type="ask_about_user",
            importance=80,
            urgency=70,
            novelty=70,
            relationship_relevance=95,
            privacy_level="safe_to_share",
        )
        intent_id = created["results"][0]["result"]["id"]
        evaluated = rt.proactive("evaluate", intent_id=intent_id)
        assert evaluated["results"][0]["result"]["evaluated"][0]["decision"] == "suppress"

        review = rt.review("summary")

        item = next(i for i in review["items"] if i["item_type"] == "proactive_suppressed")
        assert item["severity"] == "info"
        assert item["source_id"] == intent_id
        assert "没有打扰你" in item["title"]
        assert "proactive module off" in item["message"]
        assert item["action_hint"]["tool"] == "life_proactive"
        assert item["action_hint"]["action"] == "inspect_suppressed"
        assert item["action_hint"]["intent_id"] == intent_id
        assert "主动消息：近 48 小时压下 1 条" in review["rendered"]
        assert f"intent_id={intent_id}" in review["rendered"]
        assert "可选：leave_suppressed/adjust_policy/create_new_intent" in review["rendered"]
    finally:
        rt.close()


def test_review_plan_for_suppressed_proactive_is_manual_review(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，主动消息需要可解释。")
        rt.commit_canon()
        rt.control("resume")
        rt.control("module", key="proactive", value="auto_send")
        created = rt.proactive(
            "create",
            summary="这条重要度太低，不该主动打扰。",
            target_type="user",
            target_id="u1",
            intent_type="idle_share",
            importance=5,
            urgency=5,
            novelty=5,
            relationship_relevance=5,
            privacy_level="safe_to_share",
        )
        intent_id = created["results"][0]["result"]["id"]
        rt.proactive("evaluate", intent_id=intent_id)
        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "proactive_suppressed")

        preview = rt.review("preview_action", item_id=item["id"])

        assert preview["ok"] is True
        assert preview["plan"]["application_type"] == "manual_review"
        assert preview["plan"]["tool"] == "life_proactive"
        assert preview["plan"]["action"] == "inspect_suppressed"
        assert preview["plan"]["intent_id"] == intent_id
        assert preview["plan"]["safe_auto"] is False
        assert preview["plan"]["requires_choice"] is True
    finally:
        rt.close()


def test_review_lifecycle_cleanup_names_stale_pending_intent_ids(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，主动状态需要可解释。")
        rt.commit_canon()
        rt.control("resume")
        rt.control("module", key="proactive", value="pending_only")
        created = rt.proactive(
            "create",
            summary="这条先留在待说箱，稍后会被人工压下。",
            target_type="user",
            target_id="u1",
            intent_type="idle_share",
            importance=75,
            urgency=55,
            novelty=70,
            relationship_relevance=90,
            privacy_level="safe_to_share",
        )
        intent_id = created["results"][0]["result"]["id"]
        rt.proactive("evaluate", intent_id=intent_id)
        # Simulate live-plugin divergence: the intent became terminal, but the
        # per-user pending state still points at it.
        rt.conn.execute(
            """UPDATE proactive_intents
                  SET status='suppressed', suppressed_at=datetime('now'),
                      suppression_reason='manual divergence fixture',
                      updated_at=datetime('now')
                WHERE id=?""",
            (intent_id,),
        )

        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "proactive_lifecycle_cleanup")

        assert item["severity"] == "warning"
        assert item["action_hint"]["tool"] == "life_proactive"
        assert item["action_hint"]["action"] == "cleanup"
        assert item["action_hint"]["stale_state_count"] == 1
        assert item["action_hint"]["stale_state_user_ids"] == ["u1"]
        assert item["action_hint"]["stale_state_intent_ids"] == [intent_id]
        assert "陈旧待说状态 1 条" in review["rendered"]
        assert f"stale_state_intent_ids={intent_id}" in review["rendered"]
    finally:
        rt.close()


def test_review_batch_proactive_cleanup_respects_section_scope(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，主动消息清理只能属于 proactive 分区。")
        rt.commit_canon()
        rt.control("resume")
        rt.control("module", key="proactive", value="auto_send")
        created = rt.proactive(
            "create",
            summary="这条会制造一个孤儿 outbox。",
            target_type="user",
            target_id="u1",
            intent_type="report_progress",
            importance=95,
            urgency=90,
            novelty=80,
            relationship_relevance=90,
            privacy_level="safe_to_share",
        )
        intent_id = created["results"][0]["result"]["id"]
        evaluated = rt.proactive("evaluate", intent_id=intent_id, draft_text="这条孤儿消息应该只在主动分区清理。")
        outbox_id = evaluated["results"][0]["result"]["evaluated"][0]["outbox"]["id"]
        rt.conn.execute("DELETE FROM proactive_intents WHERE id=?", (intent_id,))

        review = rt.review("summary")
        assert any(i["item_type"] == "proactive_lifecycle_cleanup" for i in review["items"])

        world_preview = rt.review("batch_preview", review_run_id=review["review_run_id"], section="world")
        proactive_preview = rt.review("batch_preview", review_run_id=review["review_run_id"], section="proactive")

        assert world_preview["plan"]["selected_count"] == 0
        assert proactive_preview["plan"]["selected_count"] == 1
        assert proactive_preview["plan"]["items"][0]["item_type"] == "proactive_lifecycle_cleanup"
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "queued"
    finally:
        rt.close()


def test_review_dismiss_item(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="测试待处理消息", reason="test")
        out = rt.review("summary")
        item = next(i for i in out["items"] if i["item_type"] == "delayed_reply")
        dismissed = rt.review("dismiss", item_id=item["id"])
        assert dismissed["item"]["status"] == "dismissed"
    finally:
        rt.close()


def test_life_review_tool_and_slash_surface(hermes_home):
    rt = LifeEngineRuntime()
    try:
        out = rt.review("summary")
        assert out["rendered"].startswith("LifeEngine Review")
    finally:
        rt.close()
    slash = slash_life("review")
    assert "LifeEngine Review" in slash
    assert "/life review" in slash_life("help")


def test_review_surfaces_stale_social_request_with_action_hint(hermes_home):
    rt = LifeEngineRuntime()
    try:
        requester = _result(rt.social("create_entity", entity_kind="client", display_name="陈掌柜"))
        target = _result(rt.social("create_entity", entity_kind="place", display_name="归明观"))
        request = _result(rt.social(
            "record_request",
            requester_entity_id=requester["id"],
            target_entity_id=target["id"],
            request_type="fieldwork_request",
            topic="night_noise",
            summary="陈掌柜请明灯看一眼夜里反复响动的铺面。",
        ))
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        rt.conn.execute(
            "UPDATE social_requests SET created_at=?, updated_at=? WHERE id=?",
            (old_ts, old_ts, request["id"]),
        )

        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "social_request")
        assert item["severity"] == "warning"
        assert item["source_id"] == request["id"]
        assert item["action_hint"]["tool"] == "life_social"
        assert item["action_hint"]["action"] == "request_transition"
        assert item["action_hint"]["request_id"] == request["id"]
        assert item["action_hint"]["stale"] is True
        assert "陈掌柜请明灯看一眼" in item["message"]
        assert "社会请求：活跃 1 条" in review["rendered"]
        assert f"request_id={request['id']}" in review["rendered"]
        assert "建议优先看一眼" in review["rendered"]
    finally:
        rt.close()


def test_review_surfaces_world_model_action_hints(hermes_home):
    rt = LifeEngineRuntime()
    try:
        condition = _result(rt.world(
            "condition",
            key="hazard.rain_shelter",
            title="雨棚巷灵压偏高",
            condition_type="hazard",
            severity=78,
            intensity=64,
            summary="雨棚巷一带灵压升高，外勤需要绕开或先确认。",
        ))
        route = _result(rt.world(
            "route",
            key="route.old_bridge",
            name="旧桥小路",
            route_type="path",
            risk_level=82,
            status="blocked",
            points=[{"x": 12, "y": 20}, {"x": 68, "y": 44}],
        ))
        faction = _result(rt.social("create_entity", entity_kind="faction", display_name="巡城司"))
        presence = _result(rt.world(
            "upsert_faction_presence",
            faction_entity_id=faction["id"],
            scope_kind="world",
            influence=76,
            stance="contested",
            summary="巡城司对外围通行口径变得强硬。",
        ))

        review = rt.review("summary")
        by_type = {i["item_type"]: i for i in review["items"]}
        assert by_type["world_condition"]["source_id"] == condition["id"]
        assert by_type["world_condition"]["action_hint"]["condition_id"] == condition["id"]
        assert by_type["world_condition"]["action_hint"]["tool"] == "life_world"
        assert by_type["world_route"]["source_id"] == route["id"]
        assert by_type["world_route"]["action_hint"]["route_id"] == route["id"]
        assert by_type["world_faction_presence"]["source_id"] == presence["id"]
        assert by_type["world_faction_presence"]["action_hint"]["presence_id"] == presence["id"]
        assert "世界模型：待整理 状态=1，路线=1，势力=1" in review["rendered"]
        assert f"condition_id={condition['id']}" in review["rendered"]
        assert f"route_id={route['id']}" in review["rendered"]
        assert f"presence_id={presence['id']}" in review["rendered"]
    finally:
        rt.close()
