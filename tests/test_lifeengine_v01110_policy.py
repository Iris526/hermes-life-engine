import os
import tempfile

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.cli import slash_life
from lifeengine.reply_gate import create_delayed_reply, release_delayed_replies


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01110_")
    monkeypatch.setenv("HERMES_HOME", d)
    return d


def test_schema_v30_and_policy_tables(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 30
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "sleep_reply_dream_policies" in tables
        assert "sleep_reply_dream_policy_audits" in tables
        assert "sleep_reply_dream_policy_suggestions" in tables
    finally:
        conn.close()


def test_policy_defaults_preset_and_explain(hermes_home):
    rt = LifeEngineRuntime()
    try:
        out = rt.policy("get")
        assert out["ok"] is True
        assert out["policy"]["effective_policy"]["profile"] == "balanced"
        assert "ReplyGate" in out["explanation"]["summary"]
        night = rt.policy("preset", preset="night_owl")
        assert night["preset"] == "night_owl"
        assert night["policy"]["effective_policy"]["sleep"]["bedtime_window"][0] == "00:30"
        history = rt.policy("audits")
        assert history["audits"]
    finally:
        rt.close()


def test_policy_patch_changes_delayed_digest_template(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.policy("set", policy_patch={"reply": {"delayed_digest": {"template": "醒来看到 {count} 条：{summary}"}}})
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="第一条消息", reason="test")
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="第二条消息", reason="test")
            rel = release_delayed_replies(rt.conn, "agent", "default-agent", reason="policy test")
        assert rel["digest"]["summary_text"].startswith("醒来看到 2 条：")
    finally:
        rt.close()


def test_policy_suggestions_and_context(hermes_home):
    rt = LifeEngineRuntime()
    try:
        out = rt.policy("suggestions")
        assert out["ok"] is True
        rt.setup("测试 Agent。")
        rt.commit_canon()
        rt.control("resume")
        ctx = rt.build_context_for_turn("s1", "t1", "hello")
        assert "progressive_slim_context" in ctx
        assert "tool_map" in ctx
    finally:
        rt.close()


def test_slash_policy_help(hermes_home):
    assert "/life policy" in slash_life("help")
    out = slash_life("policy")
    assert "effective_policy" in out
