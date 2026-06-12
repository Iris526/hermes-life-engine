"""Entity-resource inventory and meal records for LifeEngine.

Scalar resources (energy, money, inspiration) live in resource_ledger. Entity
resources (clothes, books, supplies, meals) live here so the agent can answer
"what do I own / what did I eat" without flattening everything into counters.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id
from .resources import apply_delta


class InventoryError(ValueError):
    pass


def _row(conn, table: str, row_id: str) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    return dict(r) if r else None


def create_inventory_item(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    name: str,
    category: str = "other",
    subcategory: str | None = None,
    quantity: float = 1,
    unit: str | None = None,
    attributes: dict[str, Any] | None = None,
    acquired_at: str | None = None,
    acquired_by_event_id: str | None = None,
    acquired_by_transaction_id: str | None = None,
    condition: str = "good",
    location: str | None = None,
    emotional_value: int = 0,
    notes: str | None = None,
    status: str = "active",
    canon_version: int | None = None,
    source: str = "life_inventory",
    reason: str = "inventory item created",
) -> dict[str, Any]:
    if not name or not str(name).strip():
        raise InventoryError("inventory item name is required")
    quantity = float(quantity)
    if quantity < 0:
        raise InventoryError("inventory quantity cannot be negative")
    item_id = new_id("item")
    conn.execute(
        """INSERT INTO inventory_items(id, owner_kind, owner_id, name, category, subcategory, quantity,
               unit, attributes_json, acquired_at, acquired_by_event_id, acquired_by_transaction_id,
               condition, location, emotional_value, status, notes, canon_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item_id,
            owner_kind,
            owner_id,
            str(name).strip(),
            category or "other",
            subcategory,
            quantity,
            unit,
            dumps(attributes or {}),
            acquired_at,
            acquired_by_event_id,
            acquired_by_transaction_id,
            condition or "good",
            location,
            int(emotional_value or 0),
            status or "active",
            notes,
            canon_version,
        ),
    )
    if quantity:
        _record_movement(
            conn,
            owner_kind,
            owner_id,
            item_id=item_id,
            operation="acquire",
            quantity_delta=quantity,
            unit=unit,
            to_location=location,
            event_id=acquired_by_event_id,
            transaction_id=acquired_by_transaction_id,
            reason=reason,
            source=source,
        )
    append_journal(conn, owner_kind, owner_id, "inventory_item_created", {"item_id": item_id, "name": name, "category": category, "quantity": quantity}, source, canon_version=canon_version)
    return get_inventory_item(conn, owner_kind, owner_id, item_id)


def get_inventory_item(conn, owner_kind: str, owner_id: str, item_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM inventory_items WHERE id=? AND owner_kind=? AND owner_id=?", (item_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise InventoryError(f"inventory item not found: {item_id}")
    d = dict(row)
    d["attributes"] = loads(d.pop("attributes_json"), {})
    return d


def list_inventory(conn, owner_kind: str, owner_id: str, *, category: str | None = None, status: str | None = "active", limit: int = 50) -> list[dict[str, Any]]:
    sql = "SELECT * FROM inventory_items WHERE owner_kind=? AND owner_id=?"
    params: list[Any] = [owner_kind, owner_id]
    if category:
        sql += " AND category=?"
        params.append(category)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(int(limit))
    out = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        d["attributes"] = loads(d.pop("attributes_json"), {})
        out.append(d)
    return out


def update_inventory_item(conn, owner_kind: str, owner_id: str, *, item_id: str, source: str = "life_inventory", reason: str = "inventory item updated", **fields: Any) -> dict[str, Any]:
    current = get_inventory_item(conn, owner_kind, owner_id, item_id)
    allowed = {"name", "category", "subcategory", "unit", "condition", "location", "emotional_value", "status", "notes"}
    updates: dict[str, Any] = {}
    for k, v in fields.items():
        if k in allowed and v is not None:
            updates[k] = v
    if "attributes" in fields and fields["attributes"] is not None:
        updates["attributes_json"] = dumps(fields["attributes"])
    if not updates:
        return current
    sets = ", ".join([f"{k}=?" for k in updates] + ["updated_at=datetime('now')"])
    conn.execute(f"UPDATE inventory_items SET {sets} WHERE id=? AND owner_kind=? AND owner_id=?", tuple(updates.values()) + (item_id, owner_kind, owner_id))
    _record_movement(conn, owner_kind, owner_id, item_id=item_id, operation="update", quantity_delta=0, reason=reason, source=source)
    append_journal(conn, owner_kind, owner_id, "inventory_item_updated", {"item_id": item_id, "updates": updates}, source)
    return get_inventory_item(conn, owner_kind, owner_id, item_id)


def inventory_delta(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    item_id: str,
    quantity_delta: float,
    operation: str = "adjust",
    reason: str = "inventory change",
    unit: str | None = None,
    from_location: str | None = None,
    to_location: str | None = None,
    event_id: str | None = None,
    action_id: str | None = None,
    result_id: str | None = None,
    transaction_id: str | None = None,
    source: str = "life_inventory",
    condition: str | None = None,
    location: str | None = None,
    status: str | None = None,
    allow_negative: bool = False,
) -> dict[str, Any]:
    item = get_inventory_item(conn, owner_kind, owner_id, item_id)
    qd = float(quantity_delta)
    new_q = float(item["quantity"]) + qd
    if new_q < 0 and not allow_negative:
        raise InventoryError(f"inventory item {item_id} would go negative: {new_q}")
    final_status = status
    if final_status is None and new_q <= 0 and operation in {"consume", "discard", "dispose"}:
        final_status = "consumed" if operation == "consume" else "removed"
    final_location = location if location is not None else to_location
    conn.execute(
        """UPDATE inventory_items SET quantity=?, condition=COALESCE(?, condition), location=COALESCE(?, location),
               status=COALESCE(?, status), updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?""",
        (new_q, condition, final_location, final_status, item_id, owner_kind, owner_id),
    )
    movement = _record_movement(
        conn,
        owner_kind,
        owner_id,
        item_id=item_id,
        operation=operation,
        quantity_delta=qd,
        unit=unit or item.get("unit"),
        from_location=from_location,
        to_location=to_location,
        event_id=event_id,
        action_id=action_id,
        result_id=result_id,
        transaction_id=transaction_id,
        reason=reason,
        source=source,
    )
    append_journal(conn, owner_kind, owner_id, "inventory_delta", {"item_id": item_id, "quantity_delta": qd, "new_quantity": new_q, "movement_id": movement["id"]}, source)
    updated = get_inventory_item(conn, owner_kind, owner_id, item_id)
    return {"item": updated, "movement": movement}


def _record_movement(conn, owner_kind: str, owner_id: str, *, item_id: str, operation: str, quantity_delta: float,
                     unit: str | None = None, from_location: str | None = None, to_location: str | None = None,
                     event_id: str | None = None, action_id: str | None = None, result_id: str | None = None,
                     transaction_id: str | None = None, reason: str = "inventory change", source: str = "life_inventory") -> dict[str, Any]:
    movement_id = new_id("move")
    conn.execute(
        """INSERT INTO inventory_movements(id, owner_kind, owner_id, item_id, operation, quantity_delta, unit,
               from_location, to_location, event_id, action_id, result_id, transaction_id, reason, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (movement_id, owner_kind, owner_id, item_id, operation, float(quantity_delta), unit, from_location, to_location,
         event_id, action_id, result_id, transaction_id, reason, source),
    )
    return dict(conn.execute("SELECT * FROM inventory_movements WHERE id=?", (movement_id,)).fetchone())


def list_inventory_movements(conn, owner_kind: str, owner_id: str, *, item_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if item_id:
        rows = conn.execute("SELECT * FROM inventory_movements WHERE owner_kind=? AND owner_id=? AND item_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, item_id, int(limit))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM inventory_movements WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [dict(r) for r in rows]


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
        raise InventoryError("meal_type is required")
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
        raise InventoryError(f"meal record not found: {meal_id}")
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
