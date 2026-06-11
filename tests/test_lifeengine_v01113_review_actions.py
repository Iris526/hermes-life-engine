import tempfile

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.reply_gate import create_delayed_reply
from lifeengine.cli import slash_life


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01113_")
    monkeypatch.setenv("HERMES_HOME", d)
    return d


def test_schema_v33_and_review_action_table(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 33
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "human_review_action_runs" in tables
    finally:
        conn.close()


def test_review_preview_and_apply_delayed_reply(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="醒来后告诉我。", reason="test")
        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "delayed_reply")
        preview = rt.review("preview_action", item_id=item["id"])
        assert preview["ok"] is True
        assert preview["applied"] is False
        assert preview["plan"]["application_type"] == "lifeops"
        applied = rt.review("apply", item_id=item["id"])
        assert applied["ok"] is True
        assert applied["applied"] is True
        assert applied["action_run"]["status"] == "applied"
        assert applied["action_run"]["transaction_id"]
        delayed = rt.reply("list")
        assert delayed["delayed_replies"][0]["status"] == "released"
        got = rt.review("get_action", action_run_id=applied["action_run"]["id"])
        assert got["action_run"]["item_id"] == item["id"]
    finally:
        rt.close()


def test_review_user_confirmation_requires_choice_then_confirms(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.control("resume", "user", "anonymous-user", reason="test enable user life")
        ops = [{"type": "CREATE_EVENT", "payload": {"title": "用户明天去跑步", "event_type": "health", "status": "planned"}}]
        c = rt.confirmation("propose", "user", "anonymous-user", ops=ops, reason="用户计划需要确认")
        review = rt.review("summary", "user", "anonymous-user")
        item = next(i for i in review["items"] if i["item_type"] == "user_confirmation")
        needs = rt.review("apply", "user", "anonymous-user", item_id=item["id"])
        assert needs["needs_choice"] is True
        applied = rt.review("apply", "user", "anonymous-user", item_id=item["id"], choice="confirm")
        assert applied["ok"] is True
        assert applied["applied"] is True
        assert applied["output"]["confirmation"]["status"] == "confirmed"
        assert applied["action_run"]["transaction_id"]
    finally:
        rt.close()


def test_review_slash_apply_surface_available(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="测试 slash apply", reason="test")
        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "delayed_reply")
    finally:
        rt.close()
    out = slash_life(f"review preview {item['id']}")
    assert "application_type" in out or "lifeops" in out
