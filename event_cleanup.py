"""Stale event/schedule cleanup helpers."""
from __future__ import annotations
from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id
from .time_utils import now_iso, to_epoch
from .events import transition_event, update_schedule_block_status

ACTIVE_BLOCK_STATUSES = {"planned", "locked", "ready", "in_progress"}


def cleanup_stale_events(conn, owner_kind: str, owner_id: str, *, cutoff_ts: int | None = None, mode: str = "safe", limit: int = 100, source: str = "stale_cleanup") -> dict[str, Any]:
    cutoff_ts = int(cutoff_ts or to_epoch(now_iso()) or 0)
    affected_blocks: list[dict[str, Any]] = []
    affected_events: list[dict[str, Any]] = []
    rows = conn.execute(
        """SELECT * FROM schedule_blocks WHERE owner_kind=? AND owner_id=?
             AND status IN ('planned','locked','ready','in_progress') AND end_ts IS NOT NULL AND end_ts < ?
             ORDER BY end_ts LIMIT ?""",
        (owner_kind, owner_id, cutoff_ts, int(limit)),
    ).fetchall()
    for r in rows:
        block = dict(r)
        try:
            update_schedule_block_status(conn, owner_kind, owner_id, block["id"], "missed", reason="stale cleanup: schedule block ended without execution", source=source)
            affected_blocks.append({"schedule_block_id": block["id"], "old_status": block.get("status"), "new_status": "missed", "event_id": block.get("event_id")})
            if block.get("event_id"):
                ev = conn.execute("SELECT * FROM events WHERE id=? AND owner_kind=? AND owner_id=?", (block["event_id"], owner_kind, owner_id)).fetchone()
                if ev and ev["status"] in {"scheduled", "ready", "in_progress"}:
                    new_status = "missed" if ev["status"] == "scheduled" else "partial"
                    try:
                        transition_event(conn, owner_kind, owner_id, block["event_id"], new_status, reason="stale cleanup after missed schedule block", source=source, schedule_block_id=block["id"])
                        affected_events.append({"event_id": block["event_id"], "old_status": ev["status"], "new_status": new_status})
                    except Exception:
                        pass
        except Exception as exc:
            affected_blocks.append({"schedule_block_id": block["id"], "error": str(exc)})

    # Planned events with explicit old planned_end but no active block should not block autonomy forever.
    evs = conn.execute(
        """SELECT * FROM events e WHERE e.owner_kind=? AND e.owner_id=? AND e.status='planned'
             AND e.planned_end_ts IS NOT NULL AND e.planned_end_ts < ?
             ORDER BY e.planned_end_ts LIMIT ?""",
        (owner_kind, owner_id, cutoff_ts, int(limit)),
    ).fetchall()
    for ev in evs:
        active = conn.execute("SELECT COUNT(*) FROM schedule_blocks WHERE owner_kind=? AND owner_id=? AND event_id=? AND status IN ('planned','locked','ready','in_progress')", (owner_kind, owner_id, ev["id"])).fetchone()[0]
        if active:
            continue
        title = ev["title"] or ""
        new_status = "abandoned" if ("推进目标" in title or str(ev["source"]).startswith("autonomy")) else "postponed"
        try:
            transition_event(conn, owner_kind, owner_id, ev["id"], new_status, reason="stale cleanup: planned event past planned_end without active schedule", source=source)
            affected_events.append({"event_id": ev["id"], "old_status": "planned", "new_status": new_status, "title": title})
        except Exception as exc:
            affected_events.append({"event_id": ev["id"], "error": str(exc), "title": title})

    run_id = new_id("stalecln")
    status = "clean" if not affected_blocks and not affected_events else "changed"
    conn.execute(
        """INSERT INTO stale_event_cleanup_runs(id, owner_kind, owner_id, mode, status, cutoff_ts, affected_events_json, affected_blocks_json, result_json)
             VALUES(?,?,?,?,?,?,?,?,?)""",
        (run_id, owner_kind, owner_id, mode, status, cutoff_ts, dumps(affected_events), dumps(affected_blocks), dumps({"events": len(affected_events), "blocks": len(affected_blocks)})),
    )
    append_journal(conn, owner_kind, owner_id, "stale_event_cleanup", {"run_id": run_id, "status": status, "events": affected_events, "blocks": affected_blocks}, source)
    rendered = "过期计划清理\n============\n" + (f"处理时间块 {len(affected_blocks)} 个，事件 {len(affected_events)} 个。" if status != "clean" else "没有发现需要清理的过期计划。")
    return {"ok": True, "run_id": run_id, "status": status, "affected_events": affected_events, "affected_blocks": affected_blocks, "rendered": rendered}
