"""Meal records for LifeEngine.

Meal records track what the agent ate, when, where, and how much it cost.
They are separate from collection_items (wearable/useable props) because meals
are ephemeral consumption events, not durable inventory.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id
from .resources import apply_delta


class MealError(ValueError):
    pass


def create_meal_record(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    meal_type: str,
    eaten_at: str | None = None,
    food_items: list[str] | str | None = None,
    location: str | None = None,
    cost: dict[str, Any] | None = None,
    cost_resource_key: str | None = None,
    cost_amount: float | None = None,
    event_id: str | None = None,
    satisfaction: int | None = None,
    notes: str | None = None,
    status: str = "eaten",
    skip_reason: str | None = None,
    meal_date: str | None = None,
    planned_for: str | None = None,
    skip_penalty: dict[str, float] | None = None,
    source: str = "life_meal",
    canon_version: int | None = None,
) -> dict[str, Any]:
    if not meal_type:
        raise MealError("meal_type is required")
    status = status or "eaten"
    if food_items is None:
        foods: list[str] = []
    elif isinstance(food_items, str):
        foods = [food_items]
    else:
        foods = [str(x) for x in food_items]
    meal_id = new_id("meal")
    cost_payload = dict(cost or {})
    ledger_id = None
    if cost_resource_key and cost_amount is not None and float(cost_amount) != 0:
        ledger = apply_delta(
            conn, owner_kind, owner_id, cost_resource_key, -abs(float(cost_amount)),
            operation="consume", reason=f"{meal_type} meal", source=source, event_id=event_id, meal_id=meal_id,
        )
        ledger_id = ledger.get("ledger_id")
        cost_payload.update({"resource_key": cost_resource_key, "amount": float(cost_amount), "ledger_id": ledger_id})
    # A skipped meal still costs the body: apply the configured penalty to vitals
    # that exist, so "not eating" has consequences and is recorded.
    penalty_applied = {}
    if status == "skipped" and skip_penalty:
        for rk, delta in skip_penalty.items():
            try:
                res = apply_delta(conn, owner_kind, owner_id, rk, float(delta), operation="consume",
                                  reason=f"skipped {meal_type}", source=source, meal_id=meal_id)
                penalty_applied[rk] = res.get("delta")
            except Exception:
                pass
    conn.execute(
        """INSERT INTO meal_records(id, owner_kind, owner_id, meal_type, eaten_at, food_items_json,
               location, cost_json, event_id, satisfaction, notes, source, canon_version,
               status, skip_reason, meal_date, planned_for)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (meal_id, owner_kind, owner_id, meal_type, eaten_at or _sqlite_now(conn), dumps(foods), location, dumps(cost_payload), event_id, satisfaction, notes, source, canon_version,
         status, skip_reason, meal_date, planned_for),
    )
    append_journal(conn, owner_kind, owner_id, "meal_record_created",
                   {"meal_id": meal_id, "meal_type": meal_type, "status": status, "skip_reason": skip_reason,
                    "food_items": foods, "cost": cost_payload, "penalty": penalty_applied}, source, canon_version=canon_version)
    out = get_meal_record(conn, owner_kind, owner_id, meal_id)
    out["ledger_id"] = ledger_id
    out["penalty"] = penalty_applied
    return out


def _sqlite_now(conn) -> str:
    return str(conn.execute("SELECT datetime('now')").fetchone()[0])


def get_meal_record(conn, owner_kind: str, owner_id: str, meal_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM meal_records WHERE id=? AND owner_kind=? AND owner_id=?", (meal_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise MealError(f"meal record not found: {meal_id}")
    d = dict(row)
    d["food_items"] = loads(d.pop("food_items_json"), [])
    d["cost"] = loads(d.pop("cost_json"), {})
    return d


def list_meals(conn, owner_kind: str, owner_id: str, *, meal_type: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    if meal_type:
        rows = conn.execute("SELECT * FROM meal_records WHERE owner_kind=? AND owner_id=? AND meal_type=? ORDER BY eaten_at DESC LIMIT ?", (owner_kind, owner_id, meal_type, int(limit))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM meal_records WHERE owner_kind=? AND owner_id=? ORDER BY eaten_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["food_items"] = loads(d.pop("food_items_json"), [])
        d["cost"] = loads(d.pop("cost_json"), {})
        out.append(d)
    return out


def _meal_config(canon: dict[str, Any] | None) -> dict[str, Any]:
    meals = ((canon or {}).get("meals") or {})
    return {
        "enabled": meals.get("enabled", True) and meals.get("needs_food", True),
        "times": meals.get("times") or {"breakfast": "07:30", "lunch": "12:30", "dinner": "19:00"},
        "window_minutes": int(meals.get("window_minutes", 150)),
        "skip_penalty": meals.get("skip_penalty") or {"energy": -6, "mood": -4},
        "default_skip_reason": meals.get("default_skip_reason") or "没能按时吃饭",
    }


def meal_status_for_date(conn, owner_kind: str, owner_id: str, date_key: str, canon: dict[str, Any] | None) -> dict[str, Any]:
    """Per-meal status for a day: eaten / skipped / pending."""
    cfg = _meal_config(canon)
    rows = conn.execute(
        "SELECT meal_type, status, skip_reason FROM meal_records WHERE owner_kind=? AND owner_id=? AND meal_date=?",
        (owner_kind, owner_id, date_key),
    ).fetchall()
    by_type = {r["meal_type"]: {"status": r["status"] or "eaten", "skip_reason": r["skip_reason"]} for r in rows}
    out = {}
    for meal_type in cfg["times"]:
        out[meal_type] = by_type.get(meal_type, {"status": "pending", "skip_reason": None})
    return {"date": date_key, "meals": out}


def plan_meal_settlement(conn, owner_kind: str, owner_id: str, *, now: str, canon: dict[str, Any] | None,
                         tz_offset: str = "+09:00") -> list[dict[str, Any]]:
    """Return CREATE_MEAL_RECORD ops (status='skipped') for any of today's meals
    whose eating window has fully passed without a recorded meal. Every planned
    meal thus ends the day accounted for: eaten, or skipped with a reason.

    Pure planner — no writes. ``now`` is the agent-local ISO time.
    """
    cfg = _meal_config(canon)
    if not cfg["enabled"]:
        return []
    from .time_utils import to_epoch, parse_datetime
    now_dt = parse_datetime(now)
    if now_dt is None:
        return []
    date_key = now_dt.date().isoformat()
    now_ts = to_epoch(now)
    existing = {
        r["meal_type"]
        for r in conn.execute(
            "SELECT meal_type FROM meal_records WHERE owner_kind=? AND owner_id=? AND meal_date=?",
            (owner_kind, owner_id, date_key),
        ).fetchall()
    }
    ops = []
    for meal_type, hhmm in cfg["times"].items():
        if meal_type in existing:
            continue
        planned_iso = f"{date_key}T{hhmm}:00{tz_offset}"
        planned_ts = to_epoch(planned_iso)
        if planned_ts is None:
            continue
        window_end = planned_ts + cfg["window_minutes"] * 60
        if now_ts is not None and now_ts >= window_end:
            ops.append({"type": "CREATE_MEAL_RECORD", "payload": {
                "meal_type": meal_type,
                "status": "skipped",
                "skip_reason": cfg["default_skip_reason"],
                "meal_date": date_key,
                "planned_for": planned_iso,
                "eaten_at": planned_iso,
                "skip_penalty": cfg["skip_penalty"],
                "source": "heartbeat_meals",
            }})
    return ops
