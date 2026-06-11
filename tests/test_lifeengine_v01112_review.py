import tempfile

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
