from __future__ import annotations

import os
import shutil

from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v08"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate_agent(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，允许主动聊天状态，但默认 pending_only；每天最多一次。")
    rt.commit_canon()
    rt.control("resume")


def test_proactive_pending_only_creates_intent_receipt_and_state(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        created = rt.proactive(
            "create",
            session_id="s8",
            turn_id="p1",
            summary="我今天散步看到一只很胖的猫，想下次告诉用户。",
            intent_type="share_interesting",
            importance=70,
            urgency=40,
            novelty=80,
            relationship_relevance=70,
        )
        assert created["ok"] is True
        receipt = created["receipt"]
        assert any(f["kind"] == "proactive" for f in receipt["facts"])
        intent_id = created["results"][0]["result"]["id"]

        evaluated = rt.proactive("evaluate", session_id="s8", turn_id="p2", intent_id=intent_id)
        assert evaluated["ok"] is True
        eval_result = evaluated["results"][0]["result"]
        assert eval_result["evaluated"][0]["decision"] == "queue_pending"
        assert rt.proactive("get", intent_id=intent_id)["intent"]["status"] == "queued"
        states = rt.proactive("state")["states"]
        assert states and states[0]["state"] == "has_something_to_share"
        assert rt.proactive("outbox")["outbox"] == []
    finally:
        rt.close()


def test_proactive_auto_send_queues_outbox_and_mark_sent_updates_state(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        rt.control("module", key="proactive", value="auto_send")
        created = rt.proactive(
            "create",
            summary="我把买裙子的计划推迟了，想告诉用户。",
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
        evaluated = rt.proactive("evaluate", intent_id=intent_id, draft_text="我把买裙子的计划推迟了，想告诉你一声。")
        item = evaluated["results"][0]["result"]["evaluated"][0]
        assert item["decision"] == "outbox_queued"
        outbox_id = item["outbox"]["id"]
        outbox = rt.proactive("outbox")["outbox"]
        assert any(o["id"] == outbox_id and o["status"] == "queued" for o in outbox)

        sent = rt.proactive("send", outbox_id=outbox_id, result={"delivered": True}, manual=True)
        assert sent["ok"] is True
        state = rt.proactive("state", target_user_id="u1")["state"]
        assert state["state"] == "cooldown"
        assert state["daily_sent_count"] == 1
        assert rt.proactive("get", intent_id=intent_id)["intent"]["status"] == "sent"
    finally:
        rt.close()


def test_proactive_privacy_suppression(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        rt.control("module", key="proactive", value="auto_send")
        created = rt.proactive(
            "create",
            summary="这是 Agent 私密日记，不应该发给用户。",
            target_type="user",
            target_id="u1",
            intent_type="self_reflection_share",
            privacy_level="agent_private",
            importance=100,
            urgency=100,
            novelty=100,
            relationship_relevance=100,
        )
        intent_id = created["results"][0]["result"]["id"]
        evaluated = rt.proactive("evaluate", intent_id=intent_id)
        item = evaluated["results"][0]["result"]["evaluated"][0]
        assert item["decision"] == "suppress"
        assert rt.proactive("get", intent_id=intent_id)["intent"]["status"] == "suppressed"
    finally:
        rt.close()


def test_heartbeat_evaluates_autonomy_generated_proactive_intent(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate_agent(rt)
        rt.control("module", key="autonomy", value="full")
        rt.control("module", key="proactive", value="pending_only")
        rt.goals("create", title="准备七月考试", goal_type="study", priority=90)
        tick = rt.tick(now="2026-06-07T10:00:00+00:00", manual=False)
        assert tick["ok"] is True
        assert tick["autonomy"]["commit"] is not None
        assert tick["proactive"]["commit"] is not None
        intents = rt.proactive("list", status="queued")["intents"]
        assert intents
        assert any("准备七月考试" in i["summary"] for i in intents)
    finally:
        rt.close()
