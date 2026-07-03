"""Venture (营生) — an engine-level, registerable and cancellable
occupation that the heartbeat materializes into concrete events on a cadence.

This is the durable, engine-enforced answer to "let her run a stall to earn
money": you register an activity once (title, cadence, time window, and the
per-occurrence resource_costs the agent judges — e.g. ``money.lingzhu: +N``,
``energy: -M``); the heartbeat then creates one scheduled event per due day
(idempotent), and income/cost settles through the normal event-completion
path. Cancelling flips status to ``cancelled`` so nothing further materializes.

The engine hardcodes nothing about any particular activity — 摆摊 is just a row
the agent registered. Character-agnostic by construction.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id

VALID_STATUSES = {"active", "paused", "cancelled"}
VALID_CADENCES = {"daily", "weekly"}
VALID_OPERATION_MODELS = {"active", "self_service", "staffed"}
VALID_TRIGGERS = {"scheduled", "opportunity", "manual"}
VALID_LOCATION_KINDS = {"fixed", "flexible"}


def _row(conn, owner_kind: str, owner_id: str, activity_id: str) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT * FROM ventures WHERE id=? AND owner_kind=? AND owner_id=?",
        (activity_id, owner_kind, owner_id),
    ).fetchone()
    return _decode(r) if r else None


def _decode(row) -> dict[str, Any]:
    d = dict(row)
    d["weekdays"] = loads(d.pop("weekdays_json", None) or "[]", [])
    d["resource_costs"] = loads(d.pop("resource_costs_json", None) or "{}", {})
    d["supply_chain"] = loads(d.pop("supply_chain_json", None) or "null", None)
    d["arrival"] = loads(d.pop("arrival_json", None) or "null", None)
    d["tags"] = loads(d.pop("tags_json", None) or "[]", [])
    return d


def create_venture(
    conn, owner_kind: str, owner_id: str, *, title: str,
    description: str | None = None, activity_type: str = "work",
    event_category: str | None = None, activity_domain: str | None = None,
    cadence_kind: str = "daily", weekdays: list[int] | None = None,
    start_time: str | None = None, end_time: str | None = None, timezone: str = "UTC",
    resource_costs: dict[str, Any] | None = None, importance: int = 55, priority: int = 55,
    start_date: str | None = None, end_date: str | None = None,
    operation_model: str = "active", trigger_kind: str = "scheduled",
    location_kind: str = "fixed", location: str | None = None,
    supply_chain: dict[str, Any] | None = None,
    arrival: dict[str, Any] | None = None,
    wage_per_occurrence: float = 0,
    tags: list[Any] | None = None, source: str = "life_activity",
    canon_version: int | None = None, **_ignored: Any,
) -> dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("venture title is required")
    cadence_kind = cadence_kind if cadence_kind in VALID_CADENCES else "daily"
    operation_model = operation_model if operation_model in VALID_OPERATION_MODELS else "active"
    trigger_kind = trigger_kind if trigger_kind in VALID_TRIGGERS else "scheduled"
    location_kind = location_kind if location_kind in VALID_LOCATION_KINDS else "fixed"
    aid = new_id("recact")
    conn.execute(
        """INSERT INTO ventures(
             id, owner_kind, owner_id, title, description, activity_type, event_category,
             activity_domain, cadence_kind, weekdays_json, start_time, end_time, timezone,
             resource_costs_json, importance, priority, status, start_date, end_date,
             operation_model, trigger_kind, location_kind, location, supply_chain_json, arrival_json, wage_per_occurrence, tags_json, source)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (aid, owner_kind, owner_id, title.strip(), description, activity_type, event_category,
         activity_domain, cadence_kind, dumps(weekdays or []), start_time, end_time, timezone,
         dumps(resource_costs or {}), int(importance), int(priority), "active", start_date, end_date,
         operation_model, trigger_kind, location_kind, location,
         dumps(supply_chain) if supply_chain else None, dumps(arrival) if arrival else None,
         float(wage_per_occurrence or 0), dumps(tags or []), source),
    )
    append_journal(conn, owner_kind, owner_id, "venture_created",
                   {"activity_id": aid, "title": title, "cadence": cadence_kind}, source, canon_version=canon_version)
    return _row(conn, owner_kind, owner_id, aid)


def update_venture(
    conn, owner_kind: str, owner_id: str, activity_id: str, *,
    status: str | None = None, source: str = "life_activity",
    canon_version: int | None = None, **fields: Any,
) -> dict[str, Any]:
    existing = _row(conn, owner_kind, owner_id, activity_id)
    if not existing:
        raise ValueError(f"venture not found: {activity_id}")
    sets: list[str] = []
    params: list[Any] = []
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        sets.append("status=?"); params.append(status)
    _COL = {
        "title": "title", "description": "description", "activity_type": "activity_type",
        "event_category": "event_category", "activity_domain": "activity_domain",
        "cadence_kind": "cadence_kind", "start_time": "start_time", "end_time": "end_time",
        "timezone": "timezone", "importance": "importance", "priority": "priority",
        "start_date": "start_date", "end_date": "end_date",
        "operation_model": "operation_model", "trigger_kind": "trigger_kind",
        "location_kind": "location_kind", "location": "location",
        "wage_per_occurrence": "wage_per_occurrence",
    }
    for k, col in _COL.items():
        if k in fields and fields[k] is not None:
            sets.append(f"{col}=?"); params.append(fields[k])
    if "weekdays" in fields and fields["weekdays"] is not None:
        sets.append("weekdays_json=?"); params.append(dumps(fields["weekdays"]))
    if "resource_costs" in fields and fields["resource_costs"] is not None:
        sets.append("resource_costs_json=?"); params.append(dumps(fields["resource_costs"]))
    if "supply_chain" in fields and fields["supply_chain"] is not None:
        sets.append("supply_chain_json=?"); params.append(dumps(fields["supply_chain"]))
    if "arrival" in fields and fields["arrival"] is not None:
        sets.append("arrival_json=?"); params.append(dumps(fields["arrival"]))
    if "tags" in fields and fields["tags"] is not None:
        sets.append("tags_json=?"); params.append(dumps(fields["tags"]))
    if not sets:
        return existing
    sets.append("updated_at=datetime('now')")
    params.extend([activity_id, owner_kind, owner_id])
    conn.execute(
        f"UPDATE ventures SET {', '.join(sets)} WHERE id=? AND owner_kind=? AND owner_id=?",
        tuple(params),
    )
    append_journal(conn, owner_kind, owner_id, "venture_updated",
                   {"activity_id": activity_id, "status": status, "fields": list(fields.keys())}, source, canon_version=canon_version)
    return _row(conn, owner_kind, owner_id, activity_id)


def list_ventures(conn, owner_kind: str, owner_id: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM ventures WHERE owner_kind=? AND owner_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, status, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ventures WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, int(limit)),
        ).fetchall()
    return [_decode(r) for r in rows]


def get_venture(conn, owner_kind: str, owner_id: str, activity_id: str) -> dict[str, Any] | None:
    return _row(conn, owner_kind, owner_id, activity_id)


def _matches_cadence(activity: dict[str, Any], date_key: str, weekday: int) -> bool:
    """Is this activity due on the given local date? (status/bounds checked separately.)"""
    if (activity.get("start_date") or "") and date_key < activity["start_date"]:
        return False
    if (activity.get("end_date") or "") and date_key > activity["end_date"]:
        return False
    cadence = activity.get("cadence_kind") or "daily"
    if cadence == "weekly":
        wd = activity.get("weekdays") or []
        return int(weekday) in {int(x) for x in wd}
    return True  # daily


def due_activities(conn, owner_kind: str, owner_id: str, date_key: str, weekday: int) -> list[dict[str, Any]]:
    """Active activities due on date_key that have NOT yet been materialized that day."""
    out: list[dict[str, Any]] = []
    for act in list_ventures(conn, owner_kind, owner_id, status="active"):
        if not _matches_cadence(act, date_key, weekday):
            continue
        seen = conn.execute(
            "SELECT 1 FROM venture_occurrences WHERE activity_id=? AND date_key=?",
            (act["id"], date_key),
        ).fetchone()
        if seen:
            continue
        out.append(act)
    return out


def _hash01(seed: str) -> float:
    """Deterministic float in [0,1) from a string seed (reproducible, no RNG)."""
    import hashlib
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def opportunity_target(activity: dict[str, Any], date_key: str) -> int:
    """How many opportunities (委托/客人) should land for this venture on date_key.

    Deterministic per (venture, day): the integer part of per_day always lands,
    and the fractional part lands on days whose hash falls under it — so a
    per_day of 1.5 averages out while staying reproducible and testable.
    """
    arrival = activity.get("arrival") if isinstance(activity.get("arrival"), dict) else {}
    per_day = float(arrival.get("per_day", 1) or 0)
    if per_day <= 0:
        return 0
    base = int(per_day)
    frac = per_day - base
    bonus = 1 if (frac > 0 and _hash01(f"{activity['id']}:{date_key}") < frac) else 0
    return base + bonus


def count_arrivals(conn, owner_kind: str, owner_id: str, activity_id: str, date_key: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM venture_opportunity_arrivals WHERE owner_kind=? AND owner_id=? AND activity_id=? AND date_key=?",
        (owner_kind, owner_id, activity_id, date_key),
    ).fetchone()
    return int(row["c"]) if row else 0


def record_arrival(conn, owner_kind: str, owner_id: str, activity_id: str, date_key: str, event_id: str | None) -> None:
    conn.execute(
        "INSERT INTO venture_opportunity_arrivals(id, owner_kind, owner_id, activity_id, date_key, event_id) VALUES(?,?,?,?,?,?)",
        (new_id("opparr"), owner_kind, owner_id, activity_id, date_key, event_id),
    )


def record_occurrence(conn, owner_kind: str, owner_id: str, activity_id: str, date_key: str,
                      event_id: str | None, schedule_block_id: str | None) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO venture_occurrences(
             id, activity_id, owner_kind, owner_id, date_key, event_id, schedule_block_id)
           VALUES(?,?,?,?,?,?,?)""",
        (new_id("recocc"), activity_id, owner_kind, owner_id, date_key, event_id, schedule_block_id),
    )
    conn.execute(
        "UPDATE ventures SET last_materialized_date=?, updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?",
        (date_key, activity_id, owner_kind, owner_id),
    )
