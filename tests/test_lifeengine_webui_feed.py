"""轴四: the WebUI LifeFeed surfaces the engine's "living evidence".

reader.life_feed merges the narrative rows the engine actually produces (diary /
dream / serendipity / companion outreach / self-narrative / rumor / persona
drift / campaign beats) into one time-ordered, cursor-paginated feed — so the
observatory shows her day as a story instead of only schedule blocks + bars.
"""
from __future__ import annotations

import os
import tempfile

from lifeengine.constants import DEFAULT_AGENT_ID as A
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.trace import new_id
from lifeengine.webui.reader import LifeEngineReader


def _seed(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", tempfile.mkdtemp(prefix="le_feed_"))
    rt = LifeEngineRuntime()
    rt.setup("feed test agent")
    rt.commit_canon()
    rt.control("resume")
    c = rt.conn
    with rt.conn:
        c.execute(
            "INSERT INTO diary_entries(id,owner_kind,owner_id,diary_type,date,content,privacy,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (new_id("d"), "agent", A, "daily", "2026-07-03", "把封面收尾了。", "safe_to_share", "2026-07-03 21:00:00"),
        )
        c.execute(
            "INSERT INTO dream_entries(id,owner_kind,owner_id,dream_run_id,content,summary,share_text,symbols_json,truth_layer,privacy,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("de"), "agent", A, new_id("dr"), "梦里找一支笔。", "找笔", "梦到在芦苇里找笔。", '["笔"]', "dream_symbolic", "safe_to_share", "shared", "2026-07-03 07:00:00"),
        )
        c.execute(
            "INSERT INTO proactive_intents(id,agent_id,target_type,target_id,intent_type,summary,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (new_id("pi"), A, "user", "ringo", "ask_about_user", "上次那个面试怎么样了？", "queued", "2026-07-03 12:00:00"),
        )
    from lifeengine.db import db_path
    path = str(db_path())
    rt.close()
    return path


def test_life_feed_merges_sources_time_ordered(monkeypatch):
    db = _seed(monkeypatch)
    feed = LifeEngineReader(db).life_feed("agent", A, limit=40)
    items = feed["items"]
    kinds = [it["kind"] for it in items]
    assert set(kinds) >= {"diary", "dream", "companion"}, kinds
    # strictly newest-first
    ts = [it["ts"] for it in items]
    assert ts == sorted(ts, reverse=True)
    # the diary (21:00) precedes the companion (12:00) precedes the dream (07:00)
    assert kinds.index("diary") < kinds.index("companion") < kinds.index("dream")
    # every item is a normalized card
    for it in items:
        assert it["ts"] and it["kind"] and it["icon"] and "text" in it and "title" in it


def test_life_feed_cursor_paginates(monkeypatch):
    db = _seed(monkeypatch)
    reader = LifeEngineReader(db)
    first = reader.life_feed("agent", A, limit=2)
    assert len(first["items"]) == 2
    assert first["next_cursor"]  # more remain
    older = reader.life_feed("agent", A, before=first["next_cursor"], limit=40)
    # the older page is strictly older than the cursor, no overlap
    assert all(it["ts"] < first["next_cursor"] for it in older["items"])
    assert older["items"], "cursor should return the remaining older item(s)"


def test_new_life_content_changes_snapshot_hash(monkeypatch):
    """The SSE fires (and the LifeFeed live-refreshes) only if new narrative
    content bumps the snapshot hash — via life_feed_head."""
    import sqlite3
    db = _seed(monkeypatch)
    reader = LifeEngineReader(db)
    snap1 = reader.snapshot("agent", A)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "INSERT INTO serendipity_events(id,owner_kind,owner_id,event_id,serendipity_type,title,description,intensity,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (new_id("se"), "agent", A, new_id("ev"), "minor_discovery", "捡到旧纽扣", "河滩上捡到一枚旧铜纽扣。", 40, "applied", "2026-07-03 23:30:00"),
        )
    conn.close()
    snap2 = reader.snapshot("agent", A)
    assert snap2["life_feed_head"] == "2026-07-03 23:30:00"
    assert snap2["life_feed_head"] != snap1["life_feed_head"]
    assert snap2["snapshot_hash"] != snap1["snapshot_hash"]
