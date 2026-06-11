from __future__ import annotations

import sqlite3
from pathlib import Path

from lifeengine.webui.reader import LifeEngineReader, map_avatar_state, resolve_lifeengine_db
from lifeengine.webui.server import create_app


def _make_webui_db(tmp_path: Path) -> Path:
    db = tmp_path / "lifeengine.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        PRAGMA user_version=39;
        CREATE TABLE engine_control(owner_kind TEXT, owner_id TEXT, engine_state TEXT, module_gates_json TEXT, workspace_json TEXT, heartbeat_json TEXT);
        INSERT INTO engine_control VALUES('agent','iris','active','{}','{}','{}');
        CREATE TABLE agent_realtime_state(owner_kind TEXT, owner_id TEXT, mode TEXT, active_event_id TEXT, active_action_id TEXT, active_schedule_block_id TEXT, active_sleep_session_id TEXT, interruptibility_level TEXT, reply_mode TEXT, lease_expires_at TEXT, lease_expires_at_ts INTEGER, body_state_json TEXT, mind_state_json TEXT, environment_state_json TEXT, updated_at TEXT);
        INSERT INTO agent_realtime_state VALUES('agent','iris','asleep','event_sleep',NULL,'block_sleep','sleep_1','sleep_interruptible','defer_or_wake',NULL,NULL,'{"energy":20,"fatigue":80}','{}','{}','2026-06-11T00:00:00');
        CREATE TABLE events(id TEXT, owner_kind TEXT, owner_id TEXT, title TEXT, event_type TEXT, event_category TEXT, activity_domain TEXT, subtype TEXT, status TEXT, importance INTEGER, priority INTEGER, location_json TEXT, interruptibility_json TEXT, tags_json TEXT, attributes_json TEXT, participants_json TEXT, state_effects_json TEXT, resource_costs_json TEXT, schedule_block_ids_json TEXT, dependency_ids_json TEXT, updated_at TEXT);
        INSERT INTO events VALUES('event_sleep','agent','iris','核心睡眠','core_sleep','sleep','sleep',NULL,'in_progress',80,50,'{}','{}','[]','{}','[]','{}','{}','[]','[]','2026-06-11T00:00:00');
        CREATE TABLE schedule_blocks(id TEXT, owner_kind TEXT, owner_id TEXT, event_id TEXT, action_id TEXT, block_type TEXT, start TEXT, end TEXT, start_ts INTEGER, end_ts INTEGER, actual_start TEXT, actual_end TEXT, status TEXT, interruptibility_json TEXT);
        INSERT INTO schedule_blocks VALUES('block_sleep','agent','iris','event_sleep',NULL,'sleep','2026-06-11T00:00:00','2026-06-11T07:30:00',0,4102444800,NULL,NULL,'in_progress','{}');
        CREATE TABLE resource_accounts(owner_kind TEXT, owner_id TEXT, resource_key TEXT, current_value REAL, unit TEXT, capacity REAL, state TEXT);
        INSERT INTO resource_accounts VALUES('agent','iris','energy',20,'points',100,'available');
        CREATE TABLE sleep_day_states(id TEXT, owner_kind TEXT, owner_id TEXT, date_key TEXT, recovery_pressure INTEGER, cumulative_sleep_debt_minutes INTEGER, fatigue_delta INTEGER, body_state_json TEXT, mind_state_json TEXT, created_at TEXT);
        INSERT INTO sleep_day_states VALUES('sds1','agent','iris','2026-06-11',70,120,20,'{}','{}','2026-06-11T08:00:00');
        CREATE TABLE human_review_items(id TEXT, owner_kind TEXT, owner_id TEXT, item_type TEXT, severity TEXT, title TEXT, message TEXT, source_table TEXT, source_id TEXT, action_hint_json TEXT, status TEXT, created_at TEXT);
        INSERT INTO human_review_items VALUES('ri1','agent','iris','sleep_state','warning','睡眠债需要注意','睡眠债 120 分钟','sleep_day_states','sds1','{"tool":"life_sleep","action":"recovery_plan"}','open','2026-06-11T08:00:00');
        CREATE TABLE dream_entries(id TEXT, owner_kind TEXT, owner_id TEXT, title TEXT, summary TEXT, content TEXT, share_text TEXT, truth_layer TEXT, emotional_tone TEXT, symbols_json TEXT, created_at TEXT);
        INSERT INTO dream_entries VALUES('d1','agent','iris','雨棚巷的梦','梦见检查符纸。','梦见检查符纸。','我梦见自己在雨棚巷找符纸。','dream_symbolic','calm','[]','2026-06-11T07:31:00');
        CREATE TABLE delayed_replies(id TEXT, owner_kind TEXT, owner_id TEXT, status TEXT, message_text TEXT, created_at TEXT);
        CREATE TABLE proactive_intents(owner_kind TEXT, owner_id TEXT, summary TEXT, status TEXT, created_at TEXT);
        CREATE TABLE proactive_outbox(owner_kind TEXT, owner_id TEXT, draft_text TEXT, status TEXT, created_at TEXT);
        CREATE TABLE life_journal(id TEXT, owner_kind TEXT, owner_id TEXT, entry_type TEXT, source TEXT, source_turn_id TEXT, source_tick_id TEXT, created_at TEXT);
        INSERT INTO life_journal VALUES('j1','agent','iris','sleep_started','heartbeat',NULL,'tick1','2026-06-11T00:00:00');
        """
    )
    conn.close()
    return db


def test_webui_reader_snapshot(tmp_path):
    db = _make_webui_db(tmp_path)
    reader = LifeEngineReader(str(db))
    snap = reader.snapshot('agent','iris')
    assert snap['avatar']['sprite_state'] == 'sleep'
    assert snap['schedule']['items'][0]['event_title'] == '核心睡眠'
    assert snap['review_items'][0]['title'] == '睡眠债需要注意'
    assert snap['dreams'][0]['truth_layer'] == 'dream_symbolic'


def test_resolve_lifeengine_dir(tmp_path):
    db = _make_webui_db(tmp_path)
    assert resolve_lifeengine_db(str(tmp_path)) == db.resolve()


def test_create_app_smoke(tmp_path):
    db = _make_webui_db(tmp_path)
    app = create_app(str(db))
    assert app.title == 'LifeEngine WebUI'
