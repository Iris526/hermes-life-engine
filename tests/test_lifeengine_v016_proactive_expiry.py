"""Proactive intents now expire on their own: a default TTL per intent type at
creation, the heartbeat sweep retiring past-TTL ones, and a by-age backstop that
cleans undelivered/legacy intents — so the agent is only ever offered fresh
things to say (a days-old dream won't be delivered)."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone, timedelta

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine import proactive
from lifeengine.proactive import create_proactive_intent, evaluate_proactive_intent, expire_intents, get_proactive_intent


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016px"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，好奇而勤奋。")
    rt.commit_canon()
    rt.control("resume")


def test_intents_get_a_default_ttl_by_type(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        dream = create_proactive_intent(rt.conn, DEFAULT_AGENT_ID, intent_type="dream_share", summary="昨晚的梦")
        helpv = create_proactive_intent(rt.conn, DEFAULT_AGENT_ID, intent_type="ask_for_help", summary="想请你帮个忙")
        assert dream["expires_at"] is not None and helpv["expires_at"] is not None
        # a dream goes stale sooner than a request for help
        assert int(dream["expires_at_ts"]) < int(helpv["expires_at_ts"])
    finally:
        rt.close()


def test_explicit_expiry_is_respected_and_swept(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        it = create_proactive_intent(rt.conn, DEFAULT_AGENT_ID, intent_type="dream_share", summary="过期的梦", expires_at=past)
        out = expire_intents(rt.conn, DEFAULT_AGENT_ID)
        assert it["id"] in out["expired"]
        assert get_proactive_intent(rt.conn, it["id"])["status"] == "expired"
    finally:
        rt.close()


def test_by_age_backstop_retires_stale_legacy_intents(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        it = create_proactive_intent(rt.conn, DEFAULT_AGENT_ID, intent_type="dream_share", summary="很久以前的梦")
        # simulate a legacy/TTL-less intent created 10 days ago but with a far-future TTL
        rt.conn.execute(
            "UPDATE proactive_intents SET created_at=datetime('now','-10 days'), expires_at_ts=? WHERE id=?",
            (int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()), it["id"]),
        )
        out = expire_intents(rt.conn, DEFAULT_AGENT_ID)
        assert it["id"] in out["expired"]      # retired by age despite the future TTL
    finally:
        rt.close()


def test_expiry_cleans_queued_outbox_and_pending_state(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        aid = DEFAULT_AGENT_ID
        intent = create_proactive_intent(
            rt.conn,
            aid,
            target_type="user",
            target_id="u1",
            intent_type="dream_share",
            summary="这条梦境分享后来过期了",
            importance=95,
            urgency=90,
            novelty=90,
            relationship_relevance=95,
            privacy_level="safe_to_share",
        )
        queued = evaluate_proactive_intent(
            rt.conn,
            aid,
            intent["id"],
            control={"module_gates": {"proactive": "auto_send"}},
            draft_text="这条过期后不应该再发。",
        )["evaluated"][0]
        assert queued["decision"] == "outbox_queued"
        outbox_id = queued["outbox"]["id"]
        state = rt.proactive("state", target_user_id="u1")["state"]
        assert intent["id"] in state["pending_intent_ids"]

        past_ts = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        rt.conn.execute("UPDATE proactive_intents SET expires_at_ts=? WHERE id=?", (past_ts, intent["id"]))
        out = expire_intents(rt.conn, aid)

        assert intent["id"] in out["expired"]
        assert out["state_rows_changed"] == 1
        assert get_proactive_intent(rt.conn, intent["id"])["status"] == "expired"
        outbox = {o["id"]: o for o in rt.proactive("outbox")["outbox"]}
        assert outbox[outbox_id]["status"] == "expired"
        assert outbox[outbox_id]["suppression_reason"] == "intent expired"
        state = rt.proactive("state", target_user_id="u1")["state"]
        assert intent["id"] not in state["pending_intent_ids"]
        assert state["state"] == "silent"
    finally:
        rt.close()


def test_heartbeat_sweeps_expired_so_agent_only_sees_fresh(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        stale = create_proactive_intent(rt.conn, DEFAULT_AGENT_ID, intent_type="dream_share", summary="过期梦", expires_at=past)
        fresh = create_proactive_intent(rt.conn, DEFAULT_AGENT_ID, intent_type="ask_for_help", summary="新鲜事")
        rt.tick(now=datetime.now(timezone.utc).isoformat(), manual=False)
        assert get_proactive_intent(rt.conn, stale["id"])["status"] == "expired"
        assert get_proactive_intent(rt.conn, fresh["id"])["status"] in {"generated", "queued"}
    finally:
        rt.close()
