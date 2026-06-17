"""v0.15.0 proactive cooldown: the post-send cooldown window is actually
enforced (it was written but never read), and honors the configured minutes."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone, timedelta

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine import proactive
from lifeengine.proactive import (
    _within_cooldown, ensure_proactive_state, create_proactive_intent,
    evaluate_proactive_intent, mark_outbox_sent,
)


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v015pc"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，好奇而勤奋。")
    rt.commit_canon()
    rt.control("resume")


def _hi_intent(rt, aid, summary="想跟你分享一件事"):
    return create_proactive_intent(
        rt.conn, aid, target_type="user", intent_type="share", summary=summary,
        importance=90, urgency=90, novelty=90, relationship_relevance=90,
        privacy_level="user_visible", status="generated", source="test",
    )


def test_within_cooldown_helper():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert _within_cooldown({"next_allowed_proactive_at": future}) is True
    assert _within_cooldown({"next_allowed_proactive_at": past}) is False
    assert _within_cooldown({}) is False
    assert _within_cooldown(None) is False


def test_cooldown_window_queues_even_under_daily_cap(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        aid = DEFAULT_AGENT_ID
        ensure_proactive_state(rt.conn, aid, "anonymous-user")
        # Inside the cooldown window, but daily budget NOT exhausted — so this
        # exercises the cooldown gate specifically, not the per-day cap.
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        rt.conn.execute(
            "UPDATE agent_user_proactive_state SET next_allowed_proactive_at=?, daily_sent_count=0 WHERE agent_id=?",
            (future, aid),
        )
        intent = _hi_intent(rt, aid)
        out = evaluate_proactive_intent(
            rt.conn, aid, intent["id"],
            control={"module_gates": {"proactive": "auto_send"}}, manual=False,
        )
        ev = out["evaluated"][0]
        assert ev["decision"] == "queue_pending"
        assert "cooldown" in ev["reason"]
        assert ev["outbox"] is None  # not delivered
    finally:
        rt.close()


def test_send_sets_cooldown_then_blocks_next(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        aid = DEFAULT_AGENT_ID
        # First intent delivers (auto_send, manual to bypass per-day cap=1 for the act of sending).
        i1 = _hi_intent(rt, aid, "第一条")
        out1 = evaluate_proactive_intent(rt.conn, aid, i1["id"], control={"module_gates": {"proactive": "auto_send"}}, manual=True)
        outbox = out1["evaluated"][0]["outbox"]
        assert outbox is not None
        mark_outbox_sent(rt.conn, aid, outbox["id"], manual=True)
        # Cooldown is now armed; an auto (non-manual) follow-up is held by it.
        state = ensure_proactive_state(rt.conn, aid, "anonymous-user")
        assert _within_cooldown(state) is True
        # reset daily count so the cap doesn't mask the cooldown
        rt.conn.execute("UPDATE agent_user_proactive_state SET daily_sent_count=0 WHERE agent_id=?", (aid,))
        i2 = _hi_intent(rt, aid, "第二条")
        out2 = evaluate_proactive_intent(rt.conn, aid, i2["id"], control={"module_gates": {"proactive": "auto_send"}}, manual=False)
        ev2 = out2["evaluated"][0]
        assert ev2["decision"] == "queue_pending"
        assert "cooldown" in ev2["reason"]
    finally:
        rt.close()
