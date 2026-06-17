"""Impromptu activity capture + conflict resolution (v0.14.0).

The planned-first scheduler had no path for an activity that happens *now* out
of conversation — e.g. the user says "let's go shopping" and the agent goes.
Such an activity never landed in the schedule, never became a completed event,
and if the slot was occupied the overlap check just hard-errored.

``record_impromptu_activity`` closes that gap as a single orchestration:

1. The activity is real and happening now, so it always occupies ``[now, now+dur]``
   and is recorded as an event (optionally completed immediately).
2. Any planned blocks overlapping that window must yield the slot — they are
   rescheduled to the next free slot (their event goes ``rescheduled`` then
   ``scheduled`` again at the new time). Reality wins the present.
3. Priority does not decide *whether* to yield (the activity already happened);
   it decides whether the agent is handed a *notice* to surface to the user
   ("I moved your important X to <time>") vs. a silent reschedule. This matches
   "agent self-arbitrates: auto-postpone low priority, flag high priority".

Conflicts are rescheduled BEFORE the impromptu block is created, so the
existing overlap guard in ``create_schedule_block`` sees no active overlap and
no core invariant changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .events import (
    create_event,
    create_schedule_block,
    complete_event,
    get_event,
    transition_event,
    update_schedule_block_status,
)
from .lifecycle import TERMINAL_EVENT_STATUSES
from .time_utils import now_iso, parse_datetime, to_epoch

_ACTIVE_BLOCK_STATUSES = ("planned", "locked", "ready", "in_progress")


def _iso_from_epoch(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def _next_free_slot(conn, owner_kind: str, owner_id: str, *, after_ts: int, duration_s: int,
                    exclude_ids: set[str]) -> tuple[int, int]:
    """First gap of length ``duration_s`` at or after ``after_ts`` that does not
    overlap any active schedule block (excluding ``exclude_ids``)."""
    rows = conn.execute(
        f"""SELECT id, start_ts, end_ts FROM schedule_blocks
              WHERE owner_kind=? AND owner_id=? AND status IN {_ACTIVE_BLOCK_STATUSES}
                AND start_ts IS NOT NULL AND end_ts IS NOT NULL
              ORDER BY start_ts""",
        (owner_kind, owner_id),
    ).fetchall()
    busy = [(int(r["start_ts"]), int(r["end_ts"])) for r in rows if r["id"] not in exclude_ids]
    cursor = int(after_ts)
    for start, end in busy:
        if end <= cursor:
            continue
        if start - cursor >= duration_s:
            return cursor, cursor + duration_s
        cursor = max(cursor, end)
    return cursor, cursor + duration_s


def record_impromptu_activity(conn, owner_kind: str, owner_id: str, *, title: str,
                              duration_minutes: int = 60, now: str | None = None,
                              event_type: str = "leisure", event_category: str | None = None,
                              resource_costs: dict[str, float] | None = None,
                              importance: int = 50, priority: int = 50,
                              participants: list[Any] | None = None,
                              timezone_name: str = "UTC", complete: bool = True,
                              canon_version: int | None = None, source: str = "impromptu",
                              **_ignored: Any) -> dict[str, Any]:
    now_dt = parse_datetime(now) if now else parse_datetime(now_iso())
    start_iso = now_dt.isoformat()
    end_iso = (now_dt + timedelta(minutes=int(duration_minutes))).isoformat()
    start_ts = to_epoch(start_iso)
    end_ts = to_epoch(end_iso)

    # --- 1) detect conflicts in the now-window -----------------------------
    conflicts = conn.execute(
        f"""SELECT sb.id AS block_id, sb.start_ts AS b_start, sb.end_ts AS b_end,
                   sb.block_type AS block_type, sb.timezone AS block_tz, sb.event_id AS event_id
              FROM schedule_blocks sb
             WHERE sb.owner_kind=? AND sb.owner_id=? AND sb.status IN {_ACTIVE_BLOCK_STATUSES}
               AND sb.start_ts IS NOT NULL AND sb.end_ts IS NOT NULL
               AND NOT(sb.end_ts <= ? OR sb.start_ts >= ?)
             ORDER BY sb.start_ts""",
        (owner_kind, owner_id, start_ts, end_ts),
    ).fetchall()

    postponed: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    rescheduled_ids: set[str] = set()
    alloc_cursor = end_ts  # never reschedule into the impromptu window

    for c in conflicts:
        block_id = c["block_id"]
        event_id = c["event_id"]
        block_dur = max(60, int(c["b_end"]) - int(c["b_start"]))
        ev = get_event(conn, event_id) if event_id else None
        ev_importance = int(ev.get("importance") or 50) if ev else 0
        ev_title = ev.get("title") if ev else None
        ev_status = ev.get("status") if ev else None

        # Yield the slot: mark the displaced block rescheduled (excluded from
        # the overlap guard so the impromptu block can be created).
        update_schedule_block_status(conn, owner_kind, owner_id, block_id, "rescheduled",
                                     f"displaced by impromptu activity: {title}", source)
        rescheduled_ids.add(block_id)

        new_start = new_end = None
        if event_id and ev_status not in TERMINAL_EVENT_STATUSES:
            # Move the event to a fresh free slot after the impromptu window.
            ns, ne = _next_free_slot(conn, owner_kind, owner_id, after_ts=max(end_ts, alloc_cursor),
                                     duration_s=block_dur, exclude_ids=rescheduled_ids)
            alloc_cursor = ne
            new_start, new_end = _iso_from_epoch(ns), _iso_from_epoch(ne)
            try:
                transition_event(conn, owner_kind, owner_id, event_id, "rescheduled",
                                 reason=f"displaced by impromptu activity: {title}", source=source)
            except Exception:
                pass
            create_schedule_block(conn, owner_kind, owner_id, new_start, new_end, event_id=event_id,
                                  block_type=c["block_type"] or "planned_event",
                                  timezone_name=c["block_tz"] or "UTC")

        surfaced = ev_importance > int(importance)
        rec = {"block_id": block_id, "event_id": event_id, "title": ev_title,
               "importance": ev_importance, "new_start": new_start, "surfaced": surfaced}
        postponed.append(rec)
        if surfaced:
            notices.append(rec)

    # --- 2) record the impromptu activity itself ---------------------------
    event = create_event(
        conn, owner_kind, owner_id, title,
        event_type=event_type, event_category=event_category or event_type,
        status="planned", planned_start=start_iso, planned_end=end_iso,
        importance=int(importance), priority=int(priority),
        resource_costs=resource_costs, participants=participants,
        source=source, canon_version=canon_version,
    )
    event_id = event["id"]

    # Slot is now free (conflicts rescheduled) -> overlap guard passes.
    block = create_schedule_block(conn, owner_kind, owner_id, start_iso, end_iso, event_id=event_id,
                                  block_type="impromptu", timezone_name=timezone_name)

    completion = None
    if complete:
        # resource_deltas=None -> complete_event applies the event's own costs once.
        completion = complete_event(conn, owner_kind, owner_id, event_id,
                                    summary=f"即兴活动：{title}", resource_deltas=None, source=source)
    else:
        transition_event(conn, owner_kind, owner_id, event_id, "in_progress",
                         reason="impromptu activity in progress", source=source)

    return {
        "ok": True,
        "event": event,
        "schedule_block": block,
        "completed": completion is not None,
        "completion": completion,
        "window": {"start": start_iso, "end": end_iso, "duration_minutes": int(duration_minutes)},
        "postponed": postponed,
        "notices": notices,
        "conflict_count": len(postponed),
        # Agent guidance: surface the high-priority displacements to the user.
        "agent_notice": (
            "已记录这次活动。" + (
                "原本这个时段还有：" + "、".join(
                    f"《{n['title']}》(重要度{n['importance']})已改到 {n['new_start']}" for n in notices
                ) + "，记得跟用户说一声。" if notices else (
                    f"顺手把 {len(postponed)} 个原定任务挪到了之后的空档。" if postponed else ""
                )
            )
        ),
    }
