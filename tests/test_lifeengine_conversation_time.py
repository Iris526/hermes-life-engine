"""v0.19 对话时间仲裁与用户活动时间窗。

这些测试覆盖“聊天不自动打断日程”和“用户当前事件会随时间收束”两条体验
约束：自拍/生图/穿搭类回应不占 Agent 日程；明确邀请现在一起做事只生成
`do_now` 建议；用户说自己在吃饭后，过了预期结束时间就不能再被当成仍在吃。
"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home_conversation_time"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.19 conversation time test agent")
    rt.commit_canon()
    rt.control("resume")


def test_conversation_time_schema_tables_exist(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert _SCHEMA_VERSION >= 66
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"conversation_activity_judgments", "user_activity_spans"}.issubset(tables)
    finally:
        rt.close()


def test_background_image_or_outfit_request_does_not_occupy_agent_schedule(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.assess_incoming_message(
            session_id="s-bg",
            turn_id="t-bg",
            sender_id="ringo",
            platform="qqbot",
            text="自拍一张给我看，顺便看看今天怎么穿。",
        )
        judgment = out["interaction_judgment"]
        assert judgment["judgment_type"] == "background_response"
        assert judgment["occupies_agent_time"] is False
        assert judgment["recommended_action"] == "answer_without_reschedule"

        ctx = rt.build_context_for_turn("s-bg", "t-bg", "自拍一张给我看，顺便看看今天怎么穿。", sender_id="ringo", platform="qqbot")
        assert "background_response" in ctx
        assert "answer_without_reschedule" in ctx
    finally:
        rt.close()


def test_occupy_now_chat_recommends_do_now_without_creating_event(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.assess_incoming_message(
            session_id="s-buy",
            turn_id="t-buy",
            sender_id="ringo",
            platform="qqbot",
            text="我们现在一起去买衣服吧。",
        )
        judgment = out["interaction_judgment"]
        assert judgment["judgment_type"] == "occupy_now"
        assert judgment["occupies_agent_time"] is True
        assert judgment["recommended_action"] == "life_event.do_now"

        event_count = rt.conn.execute("SELECT COUNT(*) FROM events WHERE owner_kind='agent'").fetchone()[0]
        assert event_count == 0
    finally:
        rt.close()


def test_user_current_activity_becomes_likely_ended_in_context(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.build_context_for_turn("s-meal", "t-meal-1", "我在吃饭，等下聊。", sender_id="ringo", platform="qqbot")
        span = rt.conn.execute(
            "SELECT * FROM user_activity_spans WHERE user_id=? AND activity_type='meal' ORDER BY created_at DESC LIMIT 1",
            ("ringo",),
        ).fetchone()
        assert span and span["status"] == "active"

        rt.conn.execute(
            """UPDATE user_activity_spans
                  SET expected_end_at_ts=0,
                      expected_end_at='1970-01-01T00:00:00+00:00',
                      expires_at_ts=9999999999,
                      expires_at='2286-11-20T17:46:39+00:00'
                WHERE id=?""",
            (span["id"],),
        )
        ctx = rt.build_context_for_turn("s-meal", "t-meal-2", "刚才那个事继续说。", sender_id="ringo", platform="qqbot")
        updated = rt.conn.execute("SELECT status FROM user_activity_spans WHERE id=?", (span["id"],)).fetchone()
        assert updated["status"] == "likely_ended"
        assert "likely_ended" in ctx
        assert "不要继续断言用户仍在做这件事" in ctx
    finally:
        rt.close()


def test_question_about_agent_activity_does_not_create_user_activity_span(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.build_context_for_turn("s-other", "t-other", "你在吃饭吗？", sender_id="ringo", platform="qqbot")
        count = rt.conn.execute("SELECT COUNT(*) FROM user_activity_spans WHERE user_id=?", ("ringo",)).fetchone()[0]
        assert count == 0
    finally:
        rt.close()


def test_question_about_agent_completion_does_not_close_user_activity_span(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        rt.build_context_for_turn("s-close", "t-close-1", "我在吃饭，等下聊。", sender_id="ringo", platform="qqbot")
        span = rt.conn.execute(
            "SELECT * FROM user_activity_spans WHERE user_id=? AND activity_type='meal' ORDER BY created_at DESC LIMIT 1",
            ("ringo",),
        ).fetchone()
        assert span and span["status"] == "active"

        rt.build_context_for_turn("s-close", "t-close-2", "你吃完了吗？", sender_id="ringo", platform="qqbot")
        updated = rt.conn.execute("SELECT status FROM user_activity_spans WHERE id=?", (span["id"],)).fetchone()
        assert updated["status"] == "active"
    finally:
        rt.close()
