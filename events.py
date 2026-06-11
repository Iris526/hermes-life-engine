"""Event / Action / Result / Schedule / WakeJob operations.

v0.11.0 adds Event V2 semantics: richer event attributes, explicit state-
transition tables, schedule transition history, and realtime-state anchors.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .resources import apply_delta
from .time_utils import normalize_range, normalized_iso, to_epoch, now_iso
from .trace import append_journal, new_id
from .lifecycle import (
    assert_event_transition,
    assert_event_completable,
    assert_event_schedulable,
    assert_schedule_transition,
)

_EVENT_JSON_COLUMNS = {
    "resource_costs_json": ("resource_costs", {}),
    "schedule_block_ids_json": ("schedule_block_ids", []),
    "dependency_ids_json": ("dependency_ids", []),
    "tags_json": ("tags", []),
    "attributes_json": ("attributes", {}),
    "location_json": ("location", {}),
    "participants_json": ("participants", []),
    "interruptibility_json": ("interruptibility", {}),
    "state_effects_json": ("state_effects", {}),
}


def _decode_event_row(row) -> dict[str, Any]:
    d = dict(row)
    for raw_key, (public_key, default) in _EVENT_JSON_COLUMNS.items():
        if raw_key in d:
            d[public_key] = loads(d.pop(raw_key), default)
    return d


def _transition_ts() -> int | None:
    return to_epoch(now_iso())


def _record_event_transition(conn, owner_kind: str, owner_id: str, event_id: str,
                             from_status: str | None, to_status: str, *,
                             reason: str | None = None, source: str = "life_commit",
                             schedule_block_id: str | None = None, action_id: str | None = None,
                             result_id: str | None = None, transaction_id: str | None = None,
                             op_id: str | None = None, receipt_id: str | None = None,
                             trace_id: str | None = None, metadata: dict[str, Any] | None = None) -> str:
    tid = new_id("evtr")
    occurred = now_iso()
    conn.execute(
        """INSERT INTO event_state_transitions(
               id, owner_kind, owner_id, event_id, from_status, to_status, reason, source,
               transaction_id, op_id, receipt_id, schedule_block_id, action_id, result_id,
               occurred_at, occurred_at_ts, metadata_json, trace_id
             ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, owner_kind, owner_id, event_id, from_status, to_status, reason, source,
         transaction_id, op_id, receipt_id, schedule_block_id, action_id, result_id,
         occurred, to_epoch(occurred), dumps(metadata or {}), trace_id),
    )
    conn.execute("UPDATE events SET last_transition_id=? WHERE id=?", (tid, event_id))
    return tid


def _record_schedule_transition(conn, owner_kind: str, owner_id: str, block_id: str,
                                from_status: str | None, to_status: str, *,
                                event_id: str | None = None, reason: str | None = None,
                                source: str = "life_commit", transaction_id: str | None = None,
                                op_id: str | None = None, receipt_id: str | None = None,
                                trace_id: str | None = None, metadata: dict[str, Any] | None = None) -> str:
    tid = new_id("sbtr")
    occurred = now_iso()
    conn.execute(
        """INSERT INTO schedule_block_state_transitions(
               id, owner_kind, owner_id, schedule_block_id, event_id, from_status, to_status,
               reason, source, transaction_id, op_id, receipt_id, occurred_at, occurred_at_ts,
               metadata_json, trace_id
             ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, owner_kind, owner_id, block_id, event_id, from_status, to_status, reason, source,
         transaction_id, op_id, receipt_id, occurred, to_epoch(occurred), dumps(metadata or {}), trace_id),
    )
    conn.execute("UPDATE schedule_blocks SET last_transition_id=?, transition_reason=? WHERE id=?", (tid, reason, block_id))
    return tid


def _record_action_transition(conn, owner_kind: str, owner_id: str, action_id: str,
                              from_status: str | None, to_status: str, *, event_id: str | None = None,
                              reason: str | None = None, source: str = "life_commit",
                              transaction_id: str | None = None, op_id: str | None = None,
                              receipt_id: str | None = None, trace_id: str | None = None,
                              metadata: dict[str, Any] | None = None) -> str:
    tid = new_id("acttr")
    occurred = now_iso()
    conn.execute(
        """INSERT INTO action_state_transitions(
               id, owner_kind, owner_id, action_id, event_id, from_status, to_status, reason,
               source, transaction_id, op_id, receipt_id, occurred_at, occurred_at_ts,
               metadata_json, trace_id
             ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, owner_kind, owner_id, action_id, event_id, from_status, to_status, reason, source,
         transaction_id, op_id, receipt_id, occurred, to_epoch(occurred), dumps(metadata or {}), trace_id),
    )
    conn.execute("UPDATE actions SET last_transition_id=? WHERE id=?", (tid, action_id))
    return tid


def ensure_realtime_state(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    conn.execute(
        """INSERT OR IGNORE INTO agent_realtime_state(owner_kind, owner_id, mode, body_state_json, mind_state_json, environment_state_json)
              VALUES(?,?,?,?,?,?)""",
        (owner_kind, owner_id, "idle", "{}", "{}", "{}"),
    )
    return get_realtime_state(conn, owner_kind, owner_id)


def get_realtime_state(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_realtime_state WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)).fetchone()
    if not row:
        conn.execute(
            """INSERT INTO agent_realtime_state(owner_kind, owner_id, mode, body_state_json, mind_state_json, environment_state_json)
                  VALUES(?,?,?,?,?,?)""",
            (owner_kind, owner_id, "idle", "{}", "{}", "{}"),
        )
        row = conn.execute("SELECT * FROM agent_realtime_state WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)).fetchone()
    d = dict(row)
    d["body_state"] = loads(d.pop("body_state_json"), {})
    d["mind_state"] = loads(d.pop("mind_state_json"), {})
    d["environment_state"] = loads(d.pop("environment_state_json"), {})
    return d


def set_realtime_state(conn, owner_kind: str, owner_id: str, *, mode: str | None = None,
                       active_event_id: str | None = None, active_action_id: str | None = None,
                       active_schedule_block_id: str | None = None, active_sleep_session_id: str | None = None,
                       interruptibility_level: str | None = None, reply_mode: str | None = None,
                       lease_expires_at: str | None = None, body_state: dict[str, Any] | None = None,
                       mind_state: dict[str, Any] | None = None, environment_state: dict[str, Any] | None = None,
                       source: str = "life_commit", reason: str | None = None,
                       trace_id: str | None = None) -> dict[str, Any]:
    current = ensure_realtime_state(conn, owner_kind, owner_id)
    new_body = current.get("body_state") or {}
    new_mind = current.get("mind_state") or {}
    new_env = current.get("environment_state") or {}
    if body_state:
        new_body.update(body_state)
    if mind_state:
        new_mind.update(mind_state)
    if environment_state:
        new_env.update(environment_state)
    lease_iso = normalized_iso(lease_expires_at) if lease_expires_at else current.get("lease_expires_at")
    lease_ts = to_epoch(lease_iso) if lease_iso else None
    conn.execute(
        """UPDATE agent_realtime_state SET
               mode=COALESCE(?, mode), active_event_id=?, active_action_id=?, active_schedule_block_id=?, active_sleep_session_id=?,
               interruptibility_level=COALESCE(?, interruptibility_level), reply_mode=COALESCE(?, reply_mode),
               lease_expires_at=?, lease_expires_at_ts=?, body_state_json=?, mind_state_json=?, environment_state_json=?,
               updated_at=datetime('now')
             WHERE owner_kind=? AND owner_id=?""",
        (mode, active_event_id, active_action_id, active_schedule_block_id, active_sleep_session_id,
         interruptibility_level, reply_mode, lease_iso, lease_ts, dumps(new_body), dumps(new_mind), dumps(new_env),
         owner_kind, owner_id),
    )
    updated = get_realtime_state(conn, owner_kind, owner_id)
    conn.execute(
        """INSERT INTO agent_state_snapshots(
               id, owner_kind, owner_id, mode, active_event_id, active_action_id, active_schedule_block_id, active_sleep_session_id,
               interruptibility_level, reply_mode, body_state_json, mind_state_json, source, reason, event_id,
               schedule_block_id, trace_id
             ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (new_id("statesnap"), owner_kind, owner_id, updated.get("mode"), updated.get("active_event_id"),
         updated.get("active_action_id"), updated.get("active_schedule_block_id"), updated.get("active_sleep_session_id"), updated.get("interruptibility_level"),
         updated.get("reply_mode"), dumps(updated.get("body_state") or {}), dumps(updated.get("mind_state") or {}),
         source, reason, active_event_id or updated.get("active_event_id"), active_schedule_block_id or updated.get("active_schedule_block_id"), trace_id),
    )
    append_journal(conn, owner_kind, owner_id, "realtime_state_updated", {"state": updated, "reason": reason}, source)
    return updated


def create_event(conn, owner_kind: str, owner_id: str, title: str,
                 description: str | None = None, event_type: str = "other",
                 source: str = "life_commit", status: str = "planned",
                 planned_start: str | None = None, planned_end: str | None = None,
                 priority: int = 50, importance: int = 50, progress: int = 0,
                 resource_costs: dict[str, float] | None = None,
                 visibility: str = "agent_private", confidence: float = 1.0,
                 parent_event_id: str | None = None, goal_id: str | None = None,
                 dependency_ids: list[str] | None = None,
                 canon_version: int | None = None,
                 event_category: str | None = None, activity_domain: str | None = None,
                 subtype: str | None = None, tags: list[str] | None = None,
                 attributes: dict[str, Any] | None = None, location: dict[str, Any] | None = None,
                 participants: list[dict[str, Any]] | list[str] | None = None,
                 interruptibility: dict[str, Any] | None = None,
                 state_effects: dict[str, Any] | None = None, **_ignored: Any) -> dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("event title is required")
    planned_start_iso, planned_end_iso, planned_start_ts, planned_end_ts = normalize_range(planned_start, planned_end)
    event_id = new_id("event")
    category = event_category or event_type or "other"
    conn.execute(
        """INSERT INTO events(id, owner_kind, owner_id, title, description, event_type, event_category,
               activity_domain, subtype, source, status, parent_event_id, goal_id, planned_start, planned_end,
               planned_start_ts, planned_end_ts, priority, importance, progress, resource_costs_json,
               dependency_ids_json, tags_json, attributes_json, location_json, participants_json, interruptibility_json,
               state_effects_json, visibility, confidence, canon_version, lifecycle_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (event_id, owner_kind, owner_id, title.strip(), description, event_type, category,
         activity_domain, subtype, source, status, parent_event_id, goal_id, planned_start_iso, planned_end_iso,
         planned_start_ts, planned_end_ts, int(priority), int(importance), int(progress), dumps(resource_costs or {}),
         dumps(dependency_ids or []), dumps(tags or []), dumps(attributes or {}), dumps(location or {}),
         dumps(participants or []), dumps(interruptibility or {}), dumps(state_effects or {}), visibility,
         float(confidence), canon_version, 2),
    )
    _record_event_transition(conn, owner_kind, owner_id, event_id, None, status, reason="event created", source=source, metadata={"title": title, "category": category})
    append_journal(conn, owner_kind, owner_id, "event_created", {"event_id": event_id, "title": title, "status": status, "event_category": category}, source, canon_version=canon_version)
    return get_event(conn, event_id)


def get_event(conn, event_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        raise ValueError(f"Event not found: {event_id}")
    return _decode_event_row(row)


def list_events(conn, owner_kind: str, owner_id: str, status: str | None = None,
                limit: int = 20, event_category: str | None = None) -> list[dict[str, Any]]:
    clauses = ["owner_kind=?", "owner_id=?"]
    params: list[Any] = [owner_kind, owner_id]
    if status:
        clauses.append("status=?")
        params.append(status)
    if event_category:
        clauses.append("event_category=?")
        params.append(event_category)
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY COALESCE(planned_start_ts, actual_start_ts, unixepoch(created_at)) DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    return [_decode_event_row(r) for r in rows]


def event_transitions(conn, owner_kind: str, owner_id: str, event_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM event_state_transitions WHERE owner_kind=? AND owner_id=? AND event_id=? ORDER BY occurred_at_ts, occurred_at",
        (owner_kind, owner_id, event_id),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = loads(d.pop("metadata_json"), {})
        out.append(d)
    return out


def schedule_transitions(conn, owner_kind: str, owner_id: str, schedule_block_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM schedule_block_state_transitions WHERE owner_kind=? AND owner_id=? AND schedule_block_id=? ORDER BY occurred_at_ts, occurred_at",
        (owner_kind, owner_id, schedule_block_id),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = loads(d.pop("metadata_json"), {})
        out.append(d)
    return out


def transition_event(conn, owner_kind: str, owner_id: str, event_id: str, new_status: str,
                     reason: str | None = None, source: str = "life_commit") -> dict[str, Any]:
    event = get_event(conn, event_id)
    if event["owner_kind"] != owner_kind or event["owner_id"] != owner_id:
        raise ValueError("event owner mismatch")
    old = event["status"]
    assert_event_transition(old, new_status)
    closed_at = "datetime('now')" if new_status in {"completed", "cancelled", "failed", "abandoned", "archived"} else "closed_at"
    conn.execute(f"UPDATE events SET status=?, updated_at=datetime('now'), closed_at={closed_at} WHERE id=?", (new_status, event_id))
    _record_event_transition(conn, owner_kind, owner_id, event_id, old, new_status, reason=reason, source=source)
    append_journal(conn, owner_kind, owner_id, "event_status_changed", {"event_id": event_id, "old": old, "new": new_status, "reason": reason}, source)
    return get_event(conn, event_id)


def create_schedule_block(conn, owner_kind: str, owner_id: str, start: str, end: str,
                          event_id: str | None = None, action_id: str | None = None,
                          block_type: str = "planned_event", timezone_name: str = "UTC",
                          status: str = "planned", lock_strength: str = "soft",
                          interruptibility: dict[str, Any] | None = None, **_ignored: Any) -> dict[str, Any]:
    start_iso, end_iso, start_ts, end_ts = normalize_range(start, end, default_tz=timezone_name or "UTC")
    overlap = conn.execute(
        """SELECT id,start,end FROM schedule_blocks
              WHERE owner_kind=? AND owner_id=? AND status IN ('planned','locked','ready','in_progress')
                AND start_ts IS NOT NULL AND end_ts IS NOT NULL
                AND NOT(end_ts <= ? OR start_ts >= ?) LIMIT 1""",
        (owner_kind, owner_id, start_ts, end_ts),
    ).fetchone()
    if overlap:
        raise ValueError(f"schedule overlap with {overlap['id']} ({overlap['start']} - {overlap['end']})")
    block_id = new_id("block")
    idem = f"{owner_kind}:{owner_id}:schedule:{event_id or 'none'}:{start_ts}:{end_ts}"
    planned_minutes = int((end_ts - start_ts) / 60) if start_ts is not None and end_ts is not None and end_ts >= start_ts else None
    conn.execute(
        """INSERT INTO schedule_blocks(id, owner_kind, owner_id, event_id, action_id, block_type,
               start, end, start_ts, end_ts, timezone, status, lock_strength, idempotency_key,
               planned_duration_minutes, interruptibility_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (block_id, owner_kind, owner_id, event_id, action_id, block_type, start_iso, end_iso, start_ts, end_ts,
         timezone_name, status, lock_strength, idem, planned_minutes, dumps(interruptibility or {})),
    )
    _record_schedule_transition(conn, owner_kind, owner_id, block_id, None, status, event_id=event_id, reason="schedule block created", source="schedule")
    if event_id:
        event = get_event(conn, event_id)
        assert_event_schedulable(event.get("status"))
        ids = event.get("schedule_block_ids", [])
        if block_id not in ids:
            ids.append(block_id)
        old_status = event.get("status")
        conn.execute("""UPDATE events SET schedule_block_ids_json=?, status=?, current_schedule_block_id=?,
                      planned_start=COALESCE(planned_start, ?), planned_end=COALESCE(planned_end, ?),
                      planned_start_ts=COALESCE(planned_start_ts, ?), planned_end_ts=COALESCE(planned_end_ts, ?),
                      updated_at=datetime('now') WHERE id=?""", (dumps(ids), "scheduled", block_id, start_iso, end_iso, start_ts, end_ts, event_id))
        if old_status != "scheduled":
            _record_event_transition(conn, owner_kind, owner_id, event_id, old_status, "scheduled", reason="scheduled block created", source="schedule", schedule_block_id=block_id)
    _create_wake_job(conn, owner_kind, owner_id, end_iso or start_iso, "schedule_block_end", block_id)
    append_journal(conn, owner_kind, owner_id, "schedule_block_created", {"block_id": block_id, "event_id": event_id, "start": start_iso, "end": end_iso}, "schedule")
    return dict(conn.execute("SELECT * FROM schedule_blocks WHERE id=?", (block_id,)).fetchone())


def _create_wake_job(conn, owner_kind: str, owner_id: str, wake_at: str | None, reason: str, target_id: str | None) -> dict[str, Any] | None:
    if not wake_at:
        return None
    wake_at_iso = normalized_iso(wake_at)
    wake_at_ts = to_epoch(wake_at_iso)
    idem = f"{owner_kind}:{owner_id}:{reason}:{target_id}:{wake_at_ts}"
    job_id = new_id("wake")
    conn.execute(
        """INSERT OR IGNORE INTO wake_jobs(id, owner_kind, owner_id, wake_at, wake_at_ts, reason, target_id, status, idempotency_key)
              VALUES(?,?,?,?,?,?,?,?,?)""",
        (job_id, owner_kind, owner_id, wake_at_iso, wake_at_ts, reason, target_id, "pending", idem),
    )
    row = conn.execute("SELECT * FROM wake_jobs WHERE idempotency_key=?", (idem,)).fetchone()
    return dict(row) if row else None


def due_wake_jobs(conn, owner_kind: str, owner_id: str, now: str) -> list[dict[str, Any]]:
    now_ts = to_epoch(now)
    return [dict(r) for r in conn.execute(
        """SELECT * FROM wake_jobs WHERE owner_kind=? AND owner_id=?
              AND status='pending' AND wake_at_ts IS NOT NULL AND wake_at_ts <= ? ORDER BY wake_at_ts ASC""",
        (owner_kind, owner_id, now_ts),
    ).fetchall()]


def claim_wake_job(conn, owner_kind: str, owner_id: str, wake_job_id: str, claimed_by: str) -> dict[str, Any] | None:
    conn.execute(
        """UPDATE wake_jobs SET status='running', running_at=datetime('now'), claimed_by=?
              WHERE id=? AND owner_kind=? AND owner_id=? AND status='pending'""",
        (claimed_by, wake_job_id, owner_kind, owner_id),
    )
    row = conn.execute("SELECT * FROM wake_jobs WHERE id=? AND owner_kind=? AND owner_id=?", (wake_job_id, owner_kind, owner_id)).fetchone()
    if not row or row["status"] != "running":
        return None
    return dict(row)


def finish_wake_job(conn, owner_kind: str, owner_id: str, wake_job_id: str, status: str = "done", error: str | None = None) -> None:
    conn.execute(
        """UPDATE wake_jobs SET status=?, completed_at=datetime('now'), error=?
              WHERE id=? AND owner_kind=? AND owner_id=?""",
        (status, error, wake_job_id, owner_kind, owner_id),
    )


def complete_event(conn, owner_kind: str, owner_id: str, event_id: str, summary: str,
                   resource_deltas: dict[str, float] | None = None,
                   source: str = "heartbeat") -> dict[str, Any]:
    event = get_event(conn, event_id)
    assert_event_completable(event["status"])
    action_id = new_id("action")
    result_id = new_id("result")
    primary_block = None
    if not event.get("planned_start"):
        primary_block = conn.execute("SELECT * FROM schedule_blocks WHERE event_id=? ORDER BY start_ts, created_at LIMIT 1", (event_id,)).fetchone()
    actual_start_iso = event.get("planned_start") or (primary_block["start"] if primary_block else None)
    actual_end_iso = event.get("planned_end") or (primary_block["end"] if primary_block else None) or now_iso()
    actual_start_ts = event.get("planned_start_ts") or (primary_block["start_ts"] if primary_block else None)
    actual_end_ts = event.get("planned_end_ts") or (primary_block["end_ts"] if primary_block else None) or to_epoch(actual_end_iso)
    duration = int((actual_end_ts - actual_start_ts) / 60) if actual_start_ts is not None and actual_end_ts is not None and actual_end_ts >= actual_start_ts else None
    conn.execute(
        """INSERT INTO actions(id, owner_kind, owner_id, event_id, action_type, verb, status,
               actual_start, actual_end, actual_start_ts, actual_end_ts, duration_minutes, resource_deltas_json, result_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (action_id, owner_kind, owner_id, event_id, event.get("event_type") or "other", "execute", "completed",
         actual_start_iso, actual_end_iso, actual_start_ts, actual_end_ts, duration, dumps(resource_deltas or event.get("resource_costs") or {}), result_id),
    )
    _record_action_transition(conn, owner_kind, owner_id, action_id, None, "completed", event_id=event_id, reason="event completed", source=source)
    conn.execute(
        """INSERT INTO results(id, owner_kind, owner_id, event_id, action_id, result_type, summary,
               progress_after, state_changes_json) VALUES(?,?,?,?,?,?,?,?,?)""",
        (result_id, owner_kind, owner_id, event_id, action_id, "success", summary, 100, dumps([])),
    )
    deltas = resource_deltas if resource_deltas is not None else (event.get("resource_costs") or {})
    for key, delta in deltas.items():
        apply_delta(conn, owner_kind, owner_id, key, float(delta), "consume" if float(delta) < 0 else "produce",
                    f"event completed: {event['title']}", source, event_id=event_id, action_id=action_id, result_id=result_id)
    old_status = event.get("status")
    conn.execute(
        """UPDATE events SET status='completed', progress=100, actual_start=COALESCE(actual_start, ?),
                  actual_end=COALESCE(actual_end, ?), actual_start_ts=COALESCE(actual_start_ts, ?),
                  actual_end_ts=COALESCE(actual_end_ts, ?), actual_duration_minutes=COALESCE(actual_duration_minutes, ?),
                  updated_at=datetime('now'), closed_at=datetime('now') WHERE id=?""",
        (actual_start_iso, actual_end_iso, actual_start_ts, actual_end_ts, duration, event_id),
    )
    _record_event_transition(conn, owner_kind, owner_id, event_id, old_status, "completed", reason=summary, source=source, action_id=action_id, result_id=result_id)
    blocks = conn.execute("SELECT * FROM schedule_blocks WHERE event_id=? AND status NOT IN ('completed','skipped','cancelled','rescheduled','missed')", (event_id,)).fetchall()
    for b in blocks:
        old = b["status"]
        conn.execute(
            """UPDATE schedule_blocks SET status='completed', actual_start=COALESCE(actual_start, start), actual_end=COALESCE(actual_end, end),
                      actual_start_ts=COALESCE(actual_start_ts, start_ts), actual_end_ts=COALESCE(actual_end_ts, end_ts),
                      actual_duration_minutes=COALESCE(actual_duration_minutes, planned_duration_minutes),
                      completed_at=datetime('now'), updated_at=datetime('now') WHERE id=?""",
            (b["id"],),
        )
        _record_schedule_transition(conn, owner_kind, owner_id, b["id"], old, "completed", event_id=event_id, reason="event completed", source=source)
    set_realtime_state(conn, owner_kind, owner_id, mode="idle", active_event_id=None, active_action_id=None, active_schedule_block_id=None, source=source, reason="event completed")
    append_journal(conn, owner_kind, owner_id, "event_completed", {"event_id": event_id, "result_id": result_id, "summary": summary}, source)
    return {"event": get_event(conn, event_id), "action_id": action_id, "result_id": result_id}


def update_schedule_block_status(conn, owner_kind: str, owner_id: str, schedule_block_id: str, status: str,
                                 reason: str | None = None, source: str = "life_commit") -> dict[str, Any]:
    row = conn.execute("SELECT * FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?", (schedule_block_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise ValueError(f"schedule block not found: {schedule_block_id}")
    old = row["status"]
    assert_schedule_transition(old, status)
    completed_sql = ", completed_at=datetime('now'), actual_end=COALESCE(actual_end, datetime('now')), actual_end_ts=COALESCE(actual_end_ts, unixepoch('now'))" if status in {"completed", "missed", "cancelled", "rescheduled", "skipped"} else ""
    conn.execute(
        f"UPDATE schedule_blocks SET status=?, updated_at=datetime('now'){completed_sql} WHERE id=? AND owner_kind=? AND owner_id=?",
        (status, schedule_block_id, owner_kind, owner_id),
    )
    _record_schedule_transition(conn, owner_kind, owner_id, schedule_block_id, old, status, event_id=row["event_id"], reason=reason, source=source)
    append_journal(conn, owner_kind, owner_id, "schedule_block_status_changed", {"block_id": schedule_block_id, "old": old, "new": status, "reason": reason}, source)
    updated = conn.execute("SELECT * FROM schedule_blocks WHERE id=?", (schedule_block_id,)).fetchone()
    return dict(updated)


def due_schedule_blocks(conn, owner_kind: str, owner_id: str, now: str) -> list[dict[str, Any]]:
    now_ts = to_epoch(now)
    return [dict(r) for r in conn.execute(
        """SELECT * FROM schedule_blocks WHERE owner_kind=? AND owner_id=?
              AND status IN ('planned','locked','ready') AND end_ts IS NOT NULL AND end_ts <= ? ORDER BY end_ts ASC""",
        (owner_kind, owner_id, now_ts),
    ).fetchall()]


def complete_wake_job(conn, wake_job_id: str, status: str = "done", output: dict[str, Any] | None = None, error: str | None = None) -> None:
    row = conn.execute("SELECT owner_kind, owner_id FROM wake_jobs WHERE id=?", (wake_job_id,)).fetchone()
    if not row:
        return
    finish_wake_job(conn, row["owner_kind"], row["owner_id"], wake_job_id, status, error)
