import tempfile

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.reply_gate import create_delayed_reply
from lifeengine.cli import slash_life


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01114_")
    monkeypatch.setenv("HERMES_HOME", d)
    return d


def test_schema_v34_and_review_batch_tables(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 34
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "human_review_action_policies" in tables
        assert "human_review_batch_runs" in tables
        assert "human_review_batch_items" in tables
    finally:
        conn.close()


def test_review_action_policy_get_and_patch(hermes_home):
    rt = LifeEngineRuntime()
    try:
        policy = rt.review("policy")
        assert policy["review_action_policy"]["policy"]["allow_safe_batch"] is True
        patched = rt.review("set_policy", policy_patch={"max_batch_items": 3, "require_dry_run_first": True})
        assert patched["ok"] is True
        assert patched["policy"]["max_batch_items"] == 3
        assert patched["policy"]["require_dry_run_first"] is True
    finally:
        rt.close()


def test_batch_preview_and_apply_safe_delayed_replies(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="第一条延迟消息", reason="test")
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="第二条延迟消息", reason="test")
        review = rt.review("summary")
        assert sum(1 for i in review["items"] if i["item_type"] == "delayed_reply") >= 2
        preview = rt.review("batch_preview", review_run_id=review["review_run_id"], section="reply")
        assert preview["ok"] is True
        assert preview["applied"] is False
        assert preview["plan"]["selected_count"] >= 2
        applied = rt.review("apply_all", review_run_id=review["review_run_id"], section="reply")
        assert applied["ok"] is True
        assert applied["applied"] is True
        assert applied["batch_run"]["status"] in {"applied", "partial"}
        assert len(applied["batch_run"]["items"]) >= 2
        delayed = rt.reply("list")
        assert all(d["status"] == "released" for d in delayed["delayed_replies"])
    finally:
        rt.close()


def test_batch_apply_skips_unsafe_user_confirmation(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.control("resume", "user", "anonymous-user", reason="enable user")
        ops = [{"type": "CREATE_EVENT", "payload": {"title": "用户明天跑步", "event_type": "health", "status": "planned"}}]
        rt.confirmation("propose", "user", "anonymous-user", ops=ops, reason="needs confirm")
        review = rt.review("summary", "user", "anonymous-user")
        preview = rt.review("batch_preview", "user", "anonymous-user", review_run_id=review["review_run_id"], section="confirmations")
        assert preview["plan"]["selected_count"] == 0
        applied = rt.review("apply_all", "user", "anonymous-user", review_run_id=review["review_run_id"], section="confirmations")
        assert applied["applied"] is False
    finally:
        rt.close()


def test_review_slash_batch_preview_surface(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="slash batch", reason="test")
        rt.review("summary")
    finally:
        rt.close()
    out = slash_life("review batch_preview reply")
    assert "selected_count" in out or "batch_run" in out
