from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.webui.reader import LifeEngineReader
from lifeengine.webui.server import create_app


def _make_detail_db(tmp_path: Path) -> Path:
    db = tmp_path / "lifeengine.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        PRAGMA user_version=39;
        CREATE TABLE engine_control(owner_kind TEXT, owner_id TEXT, engine_state TEXT, module_gates_json TEXT, workspace_json TEXT, heartbeat_json TEXT);
        INSERT INTO engine_control VALUES('agent','iris','active','{}','{}','{}');
        CREATE TABLE agent_realtime_state(owner_kind TEXT, owner_id TEXT, mode TEXT, active_event_id TEXT, active_action_id TEXT, active_schedule_block_id TEXT, active_sleep_session_id TEXT, interruptibility_level TEXT, reply_mode TEXT, lease_expires_at TEXT, lease_expires_at_ts INTEGER, body_state_json TEXT, mind_state_json TEXT, environment_state_json TEXT, updated_at TEXT);
        INSERT INTO agent_realtime_state VALUES('agent','iris','busy','event_work',NULL,'block_work',NULL,'soft_interruptible','immediate',NULL,NULL,'{"energy":60,"fatigue":25}','{"focus":70}','{}','2026-06-11T10:00:00');
        CREATE TABLE events(id TEXT, owner_kind TEXT, owner_id TEXT, title TEXT, event_type TEXT, event_category TEXT, activity_domain TEXT, subtype TEXT, status TEXT, importance INTEGER, priority INTEGER, planned_start TEXT, planned_end TEXT, actual_start TEXT, actual_end TEXT, location_json TEXT, interruptibility_json TEXT, tags_json TEXT, attributes_json TEXT, participants_json TEXT, state_effects_json TEXT, resource_costs_json TEXT, schedule_block_ids_json TEXT, dependency_ids_json TEXT, updated_at TEXT);
        INSERT INTO events VALUES('event_work','agent','iris','第七城雨棚巷结果节点复查','repair_task','work','craft_commission','rain_shelter_node_review','in_progress',70,50,'2026-06-11T10:00:00','2026-06-11T12:00:00',NULL,NULL,'{"name":"第七城雨棚巷"}','{"level":"soft_interruptible"}','["commission"]','{}','[]','{}','{}','[]','[]','2026-06-11T10:00:00');
        CREATE TABLE schedule_blocks(id TEXT, owner_kind TEXT, owner_id TEXT, event_id TEXT, action_id TEXT, block_type TEXT, start TEXT, end TEXT, start_ts INTEGER, end_ts INTEGER, actual_start TEXT, actual_end TEXT, status TEXT, interruptibility_json TEXT);
        INSERT INTO schedule_blocks VALUES('block_work','agent','iris','event_work',NULL,'work','2026-06-11T10:00:00','2026-06-11T12:00:00',0,4102444800,'2026-06-11T10:10:00',NULL,'in_progress','{"level":"soft_interruptible"}');
        CREATE TABLE event_state_transitions(id TEXT, owner_kind TEXT, owner_id TEXT, event_id TEXT, from_status TEXT, to_status TEXT, reason TEXT, source TEXT, transaction_id TEXT, op_id TEXT, receipt_id TEXT, schedule_block_id TEXT, action_id TEXT, result_id TEXT, occurred_at TEXT, occurred_at_ts INTEGER, metadata_json TEXT, trace_id TEXT);
        INSERT INTO event_state_transitions VALUES('tr1','agent','iris','event_work','scheduled','in_progress','started','heartbeat','tx1',NULL,NULL,'block_work',NULL,NULL,'2026-06-11T10:10:00',1,'{}','trace1');
        CREATE TABLE schedule_block_state_transitions(id TEXT, owner_kind TEXT, owner_id TEXT, schedule_block_id TEXT, event_id TEXT, from_status TEXT, to_status TEXT, reason TEXT, source TEXT, transaction_id TEXT, op_id TEXT, receipt_id TEXT, occurred_at TEXT, occurred_at_ts INTEGER, metadata_json TEXT, trace_id TEXT);
        INSERT INTO schedule_block_state_transitions VALUES('str1','agent','iris','block_work','event_work','planned','in_progress','started','heartbeat','tx1',NULL,NULL,'2026-06-11T10:10:00',1,'{}','trace1');
        CREATE TABLE results(id TEXT, owner_kind TEXT, owner_id TEXT, event_id TEXT, action_id TEXT, result_type TEXT, summary TEXT, progress_after INTEGER, state_changes_json TEXT, memory_ids_json TEXT, created_at TEXT);
        INSERT INTO results VALUES('res1','agent','iris','event_work',NULL,'partial_success','补了两张符，节点仍需观察。',50,'[]','[]','2026-06-11T11:00:00');
        CREATE TABLE resource_ledger(id TEXT, owner_kind TEXT, owner_id TEXT, resource_key TEXT, delta REAL, unit TEXT, operation TEXT, event_id TEXT, action_id TEXT, result_id TEXT, schedule_block_id TEXT, reason TEXT, source TEXT, created_at TEXT);
        INSERT INTO resource_ledger VALUES('rl1','agent','iris','energy',-10,'points','consume','event_work',NULL,'res1','block_work','field work','execution','2026-06-11T11:00:00');
        CREATE TABLE life_journal(id TEXT, owner_kind TEXT, owner_id TEXT, transaction_id TEXT, op_id TEXT, entry_type TEXT, payload_json TEXT, source TEXT, canon_version INTEGER, prev_hash TEXT, entry_hash TEXT, created_at TEXT);
        INSERT INTO life_journal VALUES('j1','agent','iris','tx1',NULL,'event_started','{"event_id":"event_work"}','heartbeat',1,NULL,'h1','2026-06-11T10:10:00');
        CREATE TABLE life_transactions(id TEXT, owner_kind TEXT, owner_id TEXT, source TEXT, session_id TEXT, turn_id TEXT, trace_id TEXT, canon_version INTEGER, status TEXT, created_at TEXT, committed_at TEXT);
        INSERT INTO life_transactions VALUES('tx1','agent','iris','heartbeat',NULL,NULL,'trace1',1,'committed','2026-06-11T10:10:00','2026-06-11T10:10:01');
        CREATE TABLE life_ops(id TEXT, transaction_id TEXT, owner_kind TEXT, owner_id TEXT, op_type TEXT, payload_json TEXT, status TEXT, created_at TEXT);
        INSERT INTO life_ops VALUES('op1','tx1','agent','iris','UPDATE_EVENT_STATUS','{"event_id":"event_work"}','committed','2026-06-11T10:10:00');
        CREATE TABLE commit_receipts(id TEXT, transaction_id TEXT, owner_kind TEXT, owner_id TEXT, session_id TEXT, turn_id TEXT, trace_id TEXT, facts_json TEXT, summary_json TEXT, created_at TEXT);
        INSERT INTO commit_receipts VALUES('receipt1','tx1','agent','iris',NULL,NULL,'trace1','[{"claim":"event started"}]','{}','2026-06-11T10:10:01');
        CREATE TABLE resource_accounts(owner_kind TEXT, owner_id TEXT, resource_key TEXT, current_value REAL, unit TEXT, capacity REAL, state TEXT);
        INSERT INTO resource_accounts VALUES('agent','iris','energy',50,'points',100,'available');
        CREATE TABLE delayed_replies(id TEXT, owner_kind TEXT, owner_id TEXT, status TEXT, message_text TEXT, created_at TEXT);
        CREATE TABLE human_review_items(id TEXT, owner_kind TEXT, owner_id TEXT, item_type TEXT, severity TEXT, title TEXT, message TEXT, source_table TEXT, source_id TEXT, action_hint_json TEXT, status TEXT, created_at TEXT);
        CREATE TABLE dream_entries(id TEXT, owner_kind TEXT, owner_id TEXT, title TEXT, summary TEXT, content TEXT, share_text TEXT, truth_layer TEXT, emotional_tone TEXT, symbols_json TEXT, source_event_ids_json TEXT, created_at TEXT);
        INSERT INTO dream_entries VALUES('dream1','agent','iris','雨棚巷回潮','梦见旧节点回潮。','梦里我又检查了一遍雨棚巷。','醒来想说这个梦。','dream_symbolic','calm','["雨棚"]','["event_work"]','2026-06-12T07:30:00');
        CREATE TABLE proactive_intents(owner_kind TEXT, owner_id TEXT, summary TEXT, status TEXT, created_at TEXT);
        CREATE TABLE proactive_outbox(owner_kind TEXT, owner_id TEXT, draft_text TEXT, status TEXT, created_at TEXT);
        """
    )
    conn.close()
    return db


def test_webui_v0121_version():
    assert PLUGIN_VERSION == "0.12.1"


def test_reader_event_detail_and_trace_explain(tmp_path):
    db = _make_detail_db(tmp_path)
    reader = LifeEngineReader(str(db))
    detail = reader.event_detail("event_work")
    assert detail["found"] is True
    assert detail["event"]["event_category"] == "work"
    assert detail["transitions"][0]["to_status"] == "in_progress"
    assert detail["resource_ledger"][0]["resource_key"] == "energy"
    tx = reader.trace_explain("tx1")
    assert tx["kind"] == "transaction"
    assert tx["ops"][0]["op_type"] == "UPDATE_EVENT_STATUS"
    ev = reader.trace_explain("event_work")
    assert ev["kind"] == "event"


def test_server_detail_endpoints(tmp_path):
    db = _make_detail_db(tmp_path)
    client = TestClient(create_app(str(db)))
    assert client.get("/api/health").json()["webui_version"] == "0.12.1"
    event = client.get("/api/event/event_work").json()
    assert event["found"] is True
    dream = client.get("/api/dream/dream1").json()
    assert dream["dream"]["truth_layer"] == "dream_symbolic"
    trace = client.get("/api/trace/explain/tx1").json()
    assert trace["kind"] == "transaction"
