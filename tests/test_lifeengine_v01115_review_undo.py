import tempfile

import pytest

from lifeengine.db import connect
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.reply_gate import create_delayed_reply
from lifeengine.cli import slash_life


@pytest.fixture()
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v01115_")
    monkeypatch.setenv("HERMES_HOME", d)
    return d


def test_schema_v35_and_undo_tables(hermes_home):
    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 35
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "human_review_undo_runs" in tables
        assert "human_review_undo_items" in tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(human_review_action_runs)")}
        assert "undo_status" in cols
        assert "undo_run_id" in cols
    finally:
        conn.close()


def test_delayed_reply_apply_can_be_undone(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="醒来后统一回复我。", reason="test")
        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "delayed_reply")
        applied = rt.review("apply", item_id=item["id"])
        assert applied["applied"] is True
        action_id = applied["action_run"]["id"]
        preview = rt.review("undo_preview", action_run_id=action_id)
        assert preview["plan"]["supported"] is True
        assert preview["plan"]["undo_type"] == "reopen_delayed_replies"
        undone = rt.review("undo", action_run_id=action_id)
        assert undone["undone"] is True
        delayed = rt.reply("list")
        assert delayed["delayed_replies"][0]["status"] == "pending"
        undos = rt.review("undo_runs")
        assert undos["undo_runs"]
        assert rt.review("get_undo", undo_run_id=undone["undo_run"]["id"])["undo_run"]["status"] == "undone"
    finally:
        rt.close()


def test_recovery_sleep_review_action_can_be_undone_before_started(hermes_home):
    rt = LifeEngineRuntime()
    try:
        rt.conn.execute(
            """INSERT INTO sleep_day_states(id, owner_kind, owner_id, date_key, planned_sleep_minutes, actual_sleep_minutes,
                 sleep_debt_delta_minutes, cumulative_sleep_debt_minutes, recovery_pressure, nap_recommended)
                 VALUES('day1','agent','default-agent','2026-06-10',450,240,210,210,88,1)"""
        )
        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "sleep_state")
        applied = rt.review("apply", item_id=item["id"])
        assert applied["applied"] is True
        action_id = applied["action_run"]["id"]
        preview = rt.review("undo_preview", action_run_id=action_id)
        assert preview["plan"]["supported"] is True
        assert preview["plan"]["undo_type"] == "cancel_recovery_sleep_plan"
        undone = rt.review("undo", action_run_id=action_id)
        assert undone["undone"] is True
        spid = undone["plan"]["sleep_plan_id"]
        row = rt.conn.execute("SELECT status FROM sleep_plans WHERE id=?", (spid,)).fetchone()
        assert row[0] == "cancelled"
    finally:
        rt.close()


def test_batch_undo_reopens_batch_released_delayed_replies(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="batch undo one", reason="test")
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="batch undo two", reason="test")
        review = rt.review("summary")
        applied = rt.review("apply_all", review_run_id=review["review_run_id"], section="reply")
        assert applied["applied"] is True
        batch_id = applied["batch_run"]["id"]
        preview = rt.review("batch_undo_preview", batch_run_id=batch_id)
        assert preview["plan"]["supported_count"] >= 1
        undone = rt.review("batch_undo", batch_run_id=batch_id)
        assert undone["undone"] is True
        delayed = rt.reply("list")
        assert all(d["status"] == "pending" for d in delayed["delayed_replies"])
    finally:
        rt.close()


def test_review_undo_slash_surface(hermes_home):
    rt = LifeEngineRuntime()
    try:
        with rt.conn:
            create_delayed_reply(rt.conn, "agent", "default-agent", message_text="slash undo", reason="test")
        review = rt.review("summary")
        item = next(i for i in review["items"] if i["item_type"] == "delayed_reply")
        applied = rt.review("apply", item_id=item["id"])
        action_id = applied["action_run"]["id"]
    finally:
        rt.close()
    out = slash_life(f"review undo_preview {action_id}")
    assert "reopen_delayed_replies" in out or "undo_type" in out
