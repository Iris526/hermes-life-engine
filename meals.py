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
        "autonomy": bool(meals.get("autonomy", True)),
        "snack_tendency": float(meals.get("snack_tendency", 0.5)),
        "optional": meals.get("optional") or {},
        "allow_brunch": bool(meals.get("allow_brunch", True)),
        "brunch_window": meals.get("brunch_window") or ["10:00", "12:00"],
        "brunch_reason": meals.get("brunch_reason") or "早午饭并作一顿",
    }


# Meals that, when present, account for the staple meals they replace.
_BRUNCH_COVERS = {"breakfast", "lunch"}


def _records_for_date(conn, owner_kind, owner_id, date_key):
    return conn.execute(
        "SELECT meal_type, status, skip_reason, eaten_at FROM meal_records WHERE owner_kind=? AND owner_id=? AND meal_date=?",
        (owner_kind, owner_id, date_key),
    ).fetchall()


def meal_status_for_date(conn, owner_kind: str, owner_id: str, date_key: str, canon: dict[str, Any] | None) -> dict[str, Any]:
    """Per-meal status for a day. Base meals: eaten/skipped/covered/pending.
    Any autonomously-derived extras (brunch / 下午茶 / 夜宵 …) are listed too."""
    cfg = _meal_config(canon)
    rows = _records_for_date(conn, owner_kind, owner_id, date_key)
    by_type = {r["meal_type"]: {"status": r["status"] or "eaten", "skip_reason": r["skip_reason"]} for r in rows}
    brunch = "brunch" in by_type
    base = {}
    for meal_type in cfg["times"]:
        if meal_type in by_type:
            base[meal_type] = by_type[meal_type]
        elif brunch and meal_type in _BRUNCH_COVERS:
            base[meal_type] = {"status": "covered", "skip_reason": None}
        else:
            base[meal_type] = {"status": "pending", "skip_reason": None}
    extras = {mt: v for mt, v in by_type.items() if mt not in cfg["times"]}
    return {"date": date_key, "meals": base, "extras": extras}


def plan_meal_settlement(conn, owner_kind: str, owner_id: str, *, now: str, canon: dict[str, Any] | None,
                         tz_offset: str = "+09:00") -> list[dict[str, Any]]:
    """Return CREATE_MEAL_RECORD ops (status='skipped') for any base meal whose
    window has fully passed without being eaten OR covered by a brunch. Every
    base meal ends the day accounted for: eaten, brunch-covered, or skipped.
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
    existing = {r["meal_type"] for r in _records_for_date(conn, owner_kind, owner_id, date_key)}
    brunch_covered = _BRUNCH_COVERS if "brunch" in existing else set()
    ops = []
    for meal_type, hhmm in cfg["times"].items():
        if meal_type in existing or meal_type in brunch_covered:
            continue
        planned_iso = f"{date_key}T{hhmm}:00{tz_offset}"
        planned_ts = to_epoch(planned_iso)
        if planned_ts is None:
            continue
        window_end = planned_ts + cfg["window_minutes"] * 60
        if now_ts is not None and now_ts >= window_end:
            ops.append({"type": "CREATE_MEAL_RECORD", "payload": {
                "meal_type": meal_type, "status": "skipped", "skip_reason": cfg["default_skip_reason"],
                "meal_date": date_key, "planned_for": planned_iso, "eaten_at": planned_iso,
                "skip_penalty": cfg["skip_penalty"], "source": "heartbeat_meals",
            }})
    return ops


def _hhmm_to_ts(date_key: str, hhmm: str, tz_offset: str):
    from .time_utils import to_epoch
    # supports past-midnight like "25:00" -> next day 01:00
    h, m = (hhmm.split(":") + ["0"])[:2]
    h = int(h); m = int(m)
    extra_days, h = divmod(h, 24)
    base = to_epoch(f"{date_key}T{h:02d}:{m:02d}:00{tz_offset}")
    return None if base is None else base + extra_days * 86400


def plan_meal_autonomy(conn, owner_kind: str, owner_id: str, *, now: str, canon: dict[str, Any] | None,
                       persona: dict[str, Any] | None = None, tz_offset: str = "+09:00") -> list[dict[str, Any]]:
    """The agent autonomously DERIVES meals it wasn't told to eat — an
    afternoon tea / late-night snack when it's 嘴馋, or a brunch that merges a
    late breakfast+lunch. Driven by 性格(persona) + snack_tendency + mood/energy
    + time windows. Returns CREATE_MEAL_RECORD (+ small mood RESOURCE_DELTA) ops.
    """
    cfg = _meal_config(canon)
    if not cfg["enabled"] or not cfg["autonomy"]:
        return []
    from .time_utils import to_epoch, parse_datetime
    now_dt = parse_datetime(now)
    if now_dt is None:
        return []
    date_key = now_dt.date().isoformat()
    now_ts = to_epoch(now)
    existing = {r["meal_type"] for r in _records_for_date(conn, owner_kind, owner_id, date_key)}
    vit = {r["resource_key"]: float(r["current_value"] or 0) for r in conn.execute(
        "SELECT resource_key, current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key IN ('mood','energy')",
        (owner_kind, owner_id)).fetchall()}
    pt = persona or {}
    def trait(k):
        v = (pt.get(k) or {}).get("value") if isinstance(pt.get(k), dict) else pt.get(k)
        return float(v) if v is not None else 0.0
    enjoy = max(0.0, trait("expressiveness")) * 0.25 + max(0.0, trait("optimism")) * 0.2
    comfort = 0.2 if vit.get("mood", 0) < 0 else 0.0
    hungry = 0.2 if vit.get("energy", 100) < 35 else 0.0
    desire = cfg["snack_tendency"] + enjoy + comfort + hungry
    ops = []

    # Brunch: late morning, neither breakfast nor lunch yet (and no brunch).
    if cfg["allow_brunch"] and not ({"breakfast", "lunch", "brunch"} & existing):
        bw = cfg["brunch_window"]
        bs, be = _hhmm_to_ts(date_key, bw[0], tz_offset), _hhmm_to_ts(date_key, bw[1], tz_offset)
        if bs is not None and be is not None and now_ts is not None and bs <= now_ts <= be:
            ops.append({"type": "CREATE_MEAL_RECORD", "payload": {
                "meal_type": "brunch", "status": "eaten", "meal_date": date_key,
                "eaten_at": now, "notes": cfg["brunch_reason"], "source": "autonomy_meal"}})
            return ops  # one derivation per tick is plenty

    # Optional extras (afternoon tea / late-night snack) when in-window and 嘴馋.
    for meal_type, spec in cfg["optional"].items():
        if meal_type in existing:
            continue
        win = spec.get("window") or []
        if len(win) != 2:
            continue
        ws, we = _hhmm_to_ts(date_key, win[0], tz_offset), _hhmm_to_ts(date_key, win[1], tz_offset)
        if ws is None or we is None or now_ts is None or not (ws <= now_ts <= we):
            continue
        if desire >= 0.7:
            ops.append({"type": "CREATE_MEAL_RECORD", "payload": {
                "meal_type": meal_type, "status": "eaten", "meal_date": date_key,
                "eaten_at": now, "notes": spec.get("reason") or "嘴馋,加了一餐", "source": "autonomy_meal"}})
            mood_gain = float(spec.get("mood", 3))
            if mood_gain:
                ops.append({"type": "RESOURCE_DELTA", "payload": {
                    "resource_key": "mood", "delta": mood_gain, "operation": "produce",
                    "reason": f"{meal_type} 小确幸", "source": "autonomy_meal"}})
            break  # at most one extra per tick
    return ops
