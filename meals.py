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
    source: str = "life_meal",
    canon_version: int | None = None,
) -> dict[str, Any]:
    if not meal_type:
        raise MealError("meal_type is required")
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
    conn.execute(
        """INSERT INTO meal_records(id, owner_kind, owner_id, meal_type, eaten_at, food_items_json,
               location, cost_json, event_id, satisfaction, notes, source, canon_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (meal_id, owner_kind, owner_id, meal_type, eaten_at or _sqlite_now(conn), dumps(foods), location, dumps(cost_payload), event_id, satisfaction, notes, source, canon_version),
    )
    append_journal(conn, owner_kind, owner_id, "meal_record_created", {"meal_id": meal_id, "meal_type": meal_type, "food_items": foods, "cost": cost_payload}, source, canon_version=canon_version)
    out = get_meal_record(conn, owner_kind, owner_id, meal_id)
    out["ledger_id"] = ledger_id
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
