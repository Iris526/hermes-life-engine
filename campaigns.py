"""Campaigns / 资料片 (v0.18.0 P3) — cross-week themed arcs.

The audit's sharpest complaint: the agent's life was only ever one or two
scattered tasks a day, with no big thing it was working toward — no "这个月
在筹备庙会" arc with rising stakes. A **campaign** is that arc: a theme plus a
list of phases (预兆 → 升温 → 高潮 → 收尾), each phase carrying its own daily
spawn density and one-time beats. The heartbeat materializes the current
phase's themed events into the schedule day by day (idempotent, conflict-free
like recurring activities), auto-advances phases by elapsed time, escalates via
the per-phase data, and resolves at the end.

This module is the pure data/phase layer — table CRUD, the phase-locating
function, and ``plan_today`` (what to spawn for a given day). The runtime owns
the actual event creation (so campaign events go through the same
CREATE_EVENT / schedule-block / apply_delta machinery as everything else).

Character-agnostic: the theme and phases are data the agent/host registers (or
LifeAuthor seeds via ``campaign_seed``) — nothing about any specific story is
hardcoded here.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .jsonutil import dumps, loads
from .time_utils import now_iso, parse_datetime
from .trace import append_journal, new_id

_TERMINAL = {"resolved", "cancelled"}

# JSON schema for LifeAuthor to seed a campaign blueprint (kind="campaign_seed").
_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "event_type": {"type": "string"},
        "importance": {"type": "integer"},
        "duration_minutes": {"type": "integer"},
    },
    "required": ["title"],
}
_PHASE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "kind": {"type": "string"},
        "duration_days": {"type": "integer"},
        "daily_spawns": {"type": "integer"},
        "spawn_template": _EVENT_SCHEMA,
        "one_time_events": {"type": "array", "items": _EVENT_SCHEMA},
    },
    "required": ["title", "duration_days"],
}
SEED_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "importance": {"type": "integer"},
        "phases": {"type": "array", "items": _PHASE_SCHEMA},
    },
    "required": ["title", "phases"],
}


def _local_date_key(now: str | None, timezone: str = "UTC") -> str:
    dt = parse_datetime(now or now_iso())
    if dt is None:
        return (now or now_iso())[:10]
    try:
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo(timezone))
    except Exception:
        pass
    return dt.date().isoformat()


def _total_days(phases: list[dict[str, Any]]) -> int:
    return sum(max(1, int((p or {}).get("duration_days", 1))) for p in (phases or [])) or 1


def locate_phase(phases: list[dict[str, Any]], elapsed_days: int) -> tuple[int | None, int, int]:
    """Return (phase_index, day_in_phase, total_days). phase_index is None when
    elapsed_days is past the whole arc (time to resolve)."""
    total = _total_days(phases)
    if elapsed_days < 0:
        return 0, 0, total
    acc = 0
    for idx, p in enumerate(phases or []):
        dur = max(1, int((p or {}).get("duration_days", 1)))
        if elapsed_days < acc + dur:
            return idx, elapsed_days - acc, total
        acc += dur
    return None, 0, total


def _progress(elapsed_days: int, total_days: int) -> float:
    return max(0.0, min(1.0, round((int(elapsed_days) + 1) / max(1, int(total_days)), 4)))


def _decode(row) -> dict[str, Any]:
    if not row:
        return {}
    d = dict(row)
    d["phases"] = loads(d.get("phases_json"), [])
    d["theme"] = loads(d.get("theme_json"), {})
    return d


def create_campaign(conn, owner_kind: str, owner_id: str, *, title: str, phases: list[dict[str, Any]],
                    description: str | None = None, theme: dict[str, Any] | None = None,
                    goal_id: str | None = None, arc_id: str | None = None, importance: int = 60,
                    timezone: str = "UTC", start_date: str | None = None, now: str | None = None,
                    source: str = "life_campaign") -> dict[str, Any]:
    if not title or not str(title).strip():
        raise ValueError("campaign title is required")
    if not phases or not isinstance(phases, list):
        raise ValueError("campaign requires a non-empty phases list")
    camp_id = new_id("campaign")
    sd = start_date or _local_date_key(now, timezone)
    total = _total_days(phases)
    try:
        ed = (date.fromisoformat(sd).toordinal() + total)
        expected_end = date.fromordinal(ed).isoformat()
    except Exception:
        expected_end = None
    conn.execute(
        """INSERT INTO campaigns(
             id, owner_kind, owner_id, title, description, theme_json, status, phases_json,
             current_phase, progress, goal_id, arc_id, importance, timezone, start_date, expected_end_date, source
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (camp_id, owner_kind, owner_id, str(title).strip(), description, dumps(theme or {}), "active",
         dumps(phases), 0, 0.0, goal_id, arc_id, int(importance), timezone, sd, expected_end, source),
    )
    append_journal(conn, owner_kind, owner_id, "campaign_created",
                   {"campaign_id": camp_id, "title": title, "phases": len(phases), "total_days": total}, source)
    return get_campaign(conn, camp_id)


def get_campaign(conn, campaign_id: str) -> dict[str, Any]:
    return _decode(conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone())


def list_campaigns(conn, owner_kind: str, owner_id: str, *, status: str | None = "active",
                   limit: int = 50) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE owner_kind=? AND owner_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, status, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, int(limit)),
        ).fetchall()
    return [_decode(r) for r in rows]


def cancel_campaign(conn, owner_kind: str, owner_id: str, campaign_id: str) -> dict[str, Any]:
    conn.execute(
        "UPDATE campaigns SET status='cancelled', updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?",
        (campaign_id, owner_kind, owner_id),
    )
    append_journal(conn, owner_kind, owner_id, "campaign_cancelled", {"campaign_id": campaign_id}, "life_campaign")
    return get_campaign(conn, campaign_id)


def resolve_campaign(conn, campaign_id: str, *, now: str | None = None) -> None:
    conn.execute(
        "UPDATE campaigns SET status='resolved', progress=1.0, resolved_at=?, updated_at=datetime('now') WHERE id=? AND status='active'",
        (now or now_iso(), campaign_id),
    )


def update_phase_progress(conn, campaign_id: str, phase_idx: int, progress: float) -> None:
    conn.execute(
        "UPDATE campaigns SET current_phase=?, progress=?, updated_at=datetime('now') WHERE id=?",
        (int(phase_idx), float(progress), campaign_id),
    )


def occurrence_exists(conn, campaign_id: str, phase_idx: int, date_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM campaign_phase_occurrences WHERE campaign_id=? AND phase=? AND date_key=? LIMIT 1",
        (campaign_id, int(phase_idx), date_key),
    ).fetchone()
    return row is not None


def phase_ever_materialized(conn, campaign_id: str, phase_idx: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM campaign_phase_occurrences WHERE campaign_id=? AND phase=? LIMIT 1",
        (campaign_id, int(phase_idx)),
    ).fetchone()
    return row is not None


def record_phase_occurrence(conn, campaign_id: str, owner_kind: str, owner_id: str, phase_idx: int,
                            date_key: str, spawned_event_ids: list[str]) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO campaign_phase_occurrences(
             id, campaign_id, owner_kind, owner_id, phase, date_key, spawned_event_ids_json
           ) VALUES(?,?,?,?,?,?,?)""",
        (new_id("campocc"), campaign_id, owner_kind, owner_id, int(phase_idx), date_key, dumps(spawned_event_ids or [])),
    )


def _normalize_event(t: dict[str, Any] | None, campaign: dict[str, Any], idx: int,
                     phase: dict[str, Any], kind: str) -> dict[str, Any]:
    t = t or {}
    phase_title = phase.get("title") or f"第{idx + 1}阶段"
    return {
        "title": str(t.get("title") or campaign.get("title") or "事件"),
        "description": t.get("description") or f"{campaign.get('title')} · {phase_title}",
        "event_type": t.get("event_type") or "personal",
        "event_category": t.get("event_category") or t.get("event_type") or "personal",
        "importance": int(t.get("importance") or campaign.get("importance") or 60),
        "priority": int(t.get("priority") or t.get("importance") or campaign.get("importance") or 60),
        "resource_costs": dict(t.get("resource_costs") or {}),
        "duration_minutes": int(t.get("duration_minutes") or 60),
        "tags": ["campaign", str(campaign.get("id")), f"phase:{idx}", kind],
        "attributes": {"campaign_id": campaign.get("id"), "campaign_phase": idx,
                       "phase_title": phase_title, "generated_by": "campaign"},
    }


def plan_today(conn, campaign: dict[str, Any], date_key: str) -> dict[str, Any]:
    """Decide what (if anything) this campaign spawns on ``date_key``.

    Returns {resolve, already_done, phase_idx, progress, spawn:[event_payload,...]}.
    Pure read — the runtime executes the spawn and records the occurrence.
    """
    phases = campaign.get("phases") or []
    start_date = campaign.get("start_date") or date_key
    try:
        elapsed = (date.fromisoformat(date_key) - date.fromisoformat(start_date)).days
    except Exception:
        elapsed = 0
    idx, _day_in_phase, total = locate_phase(phases, elapsed)
    if idx is None:
        return {"resolve": True, "already_done": False, "phase_idx": None, "progress": 1.0, "spawn": []}
    progress = _progress(elapsed, total)
    if occurrence_exists(conn, campaign["id"], idx, date_key):
        return {"resolve": False, "already_done": True, "phase_idx": idx, "progress": progress, "spawn": []}
    phase = phases[idx] or {}
    spawn: list[dict[str, Any]] = []
    if not phase_ever_materialized(conn, campaign["id"], idx):
        for oe in (phase.get("one_time_events") or []):
            spawn.append(_normalize_event(oe, campaign, idx, phase, "beat"))
    tmpl = phase.get("spawn_template")
    n = int(phase.get("daily_spawns") or 0)
    if tmpl and n > 0:
        for _ in range(max(0, n)):
            spawn.append(_normalize_event(tmpl, campaign, idx, phase, "daily"))
    return {"resolve": False, "already_done": False, "phase_idx": idx, "progress": progress, "spawn": spawn}
