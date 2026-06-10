"""Event / Action / Result / Schedule / WakeJob operations."""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .resources import apply_delta
from .time_utils import normalize_range, normalized_iso, to_epoch, now_iso
from .trace import append_journal, new_id
from .lifecycle import assert_event_transition, assert_event_completable, assert_event_schedulable, assert_schedule_transition


def create_event(conn, owner_kind: str, owner_id: str, title: str,
                 description: str | None = None, event_type: str = "other",
                 source: str = "life_commit", status: str = "planned",
                 planned_start: str | None = None, planned_end: str | None = None,
                 priority: int = 50, importance: int = 50, progress: int = 0,
                 resource_costs: dict[str, float] | None = None,
                 visibility: str = "agent_private", confidence: float = 1.0,
                 parent_event_id: str | None = None, goal_id: str | None = None,
                 dependency_ids: list[str] | None = None,
                 canon_version: int | None = None, **_ignored: Any) -> dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("event title is required")
    planned_start_iso, planned_end_iso, planned_start_ts, planned_end_ts = normalize_range(planned_start, planned_end)
    event_id = new_id("event")
    conn.execute(
        """INSERT INTO events(id, owner_kind, owner_id, title, description, event_type, source, status,
               parent_event_id, goal_id, planned_start, planned_end, planned_start_ts, planned_end_ts, priority, importance, progress,
               resource_costs_json, dependency_ids_json, visibility, confidence, canon_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (event_id, owner_kind, owner_id, title.strip(), description, event_type, source, status,
         parent_event_id, goal_id, planned_start_iso, planned_end_iso, planned_start_ts, planned_end_ts, int(priority), int(importance),
         int(progress), dumps(resource_costs or {}), dumps(dependency_ids or []), visibility, float(confidence), canon_version),
    )
    append_journal(conn, owner_kind, owner_id, "event_created", {"event_id": event_id, "title": title, "status": status}, source, canon_version=canon_version)
    return get_event(conn, event_id)


def get_event(conn, event_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        raise ValueError(f"Event not found: {event_id}")
    d = dict(row)
    d["resource_costs"] = loads(d.pop("resource_costs_json"), {})
    d["schedule_block_ids"] = loads(d.pop("schedule_block_ids_json"), [])
    if "dependency_ids_json" in d:
        d["dependency_ids"] = loads(d.pop("dependency_ids_json"), [])
    return d


def list_events(conn, owner_kind: str, owner_id: str, status: str | None = None,
                limit: int = 20) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM events WHERE owner_kind=? AND owner_id=? AND status=? ORDER BY COALESCE(planned_start_ts, actual_start_ts, unixepoch(created_at)) DESC LIMIT ?",
            (owner_kind, owner_id, status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events WHERE owner_kind=? AND owner_id=? ORDER BY COALESCE(planned_start_ts, actual_start_ts, unixepoch(created_at)) DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["resource_costs"] = loads(d.pop("resource_costs_json"), {})
        d["schedule_block_ids"] = loads(d.pop("schedule_block_ids_json"), [])
        if "dependency_ids_json" in d:
            d["dependency_ids"] = loads(d.pop("dependency_ids_json"), [])
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
    append_journal(conn, owner_kind, owner_id, "event_status_changed", {"event_id": event_id, "old": old, "new": new_status, "reason": reason}, source)
    return get_event(conn, event_id)


def create_schedule_block(conn, owner_kind: str, owner_id: str, start: str, end: str,
                          event_id: str | None = None, action_id: str | None = None,
                          block_type: str = "planned_event", timezone_name: str = "UTC",
                          status: str = "planned", lock_strength: str = "soft", **_ignored: Any) -> dict[str, Any]:
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
    conn.execute(
        """INSERT INTO schedule_blocks(id, owner_kind, owner_id, event_id, action_id, block_type,
               start, end, start_ts, end_ts, timezone, status, lock_strength, idempotency_key)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (block_id, owner_kind, owner_id, event_id, action_id, block_type, start_iso, end_iso, start_ts, end_ts,
         timezone_name, status, lock_strength, idem),
    )
    if event_id:
        event = get_event(conn, event_id)
        assert_event_schedulable(event.get("status"))
        ids = event.get("schedule_block_ids", [])
        if block_id not in ids:
            ids.append(block_id)
        conn.execute("UPDATE events SET schedule_block_ids_json=?, status=?, updated_at=datetime('now') WHERE id=?", (dumps(ids), "scheduled", event_id))
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
    conn.execute(
        """INSERT INTO actions(id, owner_kind, owner_id, event_id, action_type, verb, status,
               actual_start, actual_end, resource_deltas_json, result_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (action_id, owner_kind, owner_id, event_id, event.get("event_type") or "other", "execute", "completed",
         event.get("planned_start"), event.get("planned_end"), dumps(resource_deltas or event.get("resource_costs") or {}), result_id),
    )
    conn.execute(
        """INSERT INTO results(id, owner_kind, owner_id, event_id, action_id, result_type, summary,
               progress_after, state_changes_json) VALUES(?,?,?,?,?,?,?,?,?)""",
        (result_id, owner_kind, owner_id, event_id, action_id, "success", summary, 100, dumps([])),
    )
    deltas = resource_deltas if resource_deltas is not None else (event.get("resource_costs") or {})
    for key, delta in deltas.items():
        apply_delta(conn, owner_kind, owner_id, key, float(delta), "consume" if float(delta) < 0 else "produce",
                    f"event completed: {event['title']}", source, event_id=event_id, action_id=action_id, result_id=result_id)
    actual_start_iso = event.get("planned_start")
    actual_end_iso = event.get("planned_end")
    actual_start_ts = event.get("planned_start_ts")
    actual_end_ts = event.get("planned_end_ts")
    conn.execute(
        """UPDATE events SET status='completed', progress=100, actual_start=COALESCE(actual_start, ?),
                  actual_end=COALESCE(actual_end, ?), actual_start_ts=COALESCE(actual_start_ts, ?),
                  actual_end_ts=COALESCE(actual_end_ts, ?), updated_at=datetime('now'), closed_at=datetime('now') WHERE id=?""",
        (actual_start_iso, actual_end_iso, actual_start_ts, actual_end_ts, event_id),
    )
    conn.execute("UPDATE schedule_blocks SET status='completed', completed_at=datetime('now'), updated_at=datetime('now') WHERE event_id=?", (event_id,))
    append_journal(conn, owner_kind, owner_id, "event_completed", {"event_id": event_id, "result_id": result_id, "summary": summary}, source)
    return {"event": get_event(conn, event_id), "action_id": action_id, "result_id": result_id}


def update_schedule_block_status(conn, owner_kind: str, owner_id: str, schedule_block_id: str, status: str,
                                 reason: str | None = None, source: str = "life_commit") -> dict[str, Any]:
    row = conn.execute("SELECT * FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?", (schedule_block_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise ValueError(f"schedule block not found: {schedule_block_id}")
    old = row["status"]
    assert_schedule_transition(old, status)
    completed_sql = ", completed_at=datetime('now')" if status in {"completed", "missed", "cancelled", "rescheduled", "skipped"} else ""
    conn.execute(
        f"UPDATE schedule_blocks SET status=?, updated_at=datetime('now'){completed_sql} WHERE id=? AND owner_kind=? AND owner_id=?",
        (status, schedule_block_id, owner_kind, owner_id),
    )
    append_journal(conn, owner_kind, owner_id, "schedule_block_status_changed", {"block_id": schedule_block_id, "old": old, "new": status, "reason": reason}, source)
    updated = conn.execute("SELECT * FROM schedule_blocks WHERE id=?", (schedule_block_id,)).fetchone()
    return dict(updated)


def due_schedule_blocks(conn, owner_kind: str, owner_id: str, now: str) -> list[dict[str, Any]]:
    # Legacy helper retained for callers, but heartbeat should use due_wake_jobs.
    now_ts = to_epoch(now)
    return [dict(r) for r in conn.execute(
        """SELECT * FROM schedule_blocks WHERE owner_kind=? AND owner_id=?
              AND status IN ('planned','locked','ready') AND end_ts IS NOT NULL AND end_ts <= ? ORDER BY end_ts ASC""",
        (owner_kind, owner_id, now_ts),
    ).fetchall()]


def complete_wake_job(conn, wake_job_id: str, status: str = "done", output: dict[str, Any] | None = None, error: str | None = None) -> None:
    """Backward-compatible helper used by runtime v0.3.

    Prefer finish_wake_job(owner_kind, owner_id, ...) when owner context is at
    hand; this version resolves owner from the row.
    """
    row = conn.execute("SELECT owner_kind, owner_id FROM wake_jobs WHERE id=?", (wake_job_id,)).fetchone()
    if not row:
        return
    finish_wake_job(conn, row["owner_kind"], row["owner_id"], wake_job_id, status, error)
