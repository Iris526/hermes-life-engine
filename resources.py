"""Resource registry, account, reservation, and ledger operations."""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps
from .trace import append_journal, new_id


class ResourceError(ValueError):
    pass


def list_resources(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    defs = [dict(r) for r in conn.execute(
        "SELECT * FROM resource_definitions WHERE owner_kind=? AND owner_id=? ORDER BY key",
        (owner_kind, owner_id),
    ).fetchall()]
    accounts = [dict(r) for r in conn.execute(
        "SELECT * FROM resource_accounts WHERE owner_kind=? AND owner_id=? ORDER BY resource_key",
        (owner_kind, owner_id),
    ).fetchall()]
    return {"definitions": defs, "accounts": accounts}


def define_resource(conn, owner_kind: str, owner_id: str, key: str, display_name: str | None = None,
                    resource_class: str = "capacity", unit: str | None = "points",
                    min_value: float | None = 0, max_value: float | None = 100,
                    initial: float = 0, rules: dict[str, Any] | None = None,
                    canon_version: int | None = None) -> dict[str, Any]:
    if not key or not str(key).strip():
        raise ResourceError("resource key is required")
    key = str(key).strip()
    conn.execute(
        """INSERT INTO resource_definitions(id, owner_kind, owner_id, key, display_name, resource_class,
               unit, min_value, max_value, rules_json, canon_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(owner_kind, owner_id, key) DO UPDATE SET
                 display_name=excluded.display_name, resource_class=excluded.resource_class,
                 unit=excluded.unit, min_value=excluded.min_value, max_value=excluded.max_value,
                 rules_json=excluded.rules_json, canon_version=excluded.canon_version""",
        (new_id("resdef"), owner_kind, owner_id, key, display_name or key, resource_class,
         unit, min_value, max_value, dumps(rules or {}), canon_version),
    )
    row = conn.execute(
        "SELECT * FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if not row:
        conn.execute(
            """INSERT INTO resource_accounts(id, owner_kind, owner_id, resource_key, current_value, unit, capacity)
                   VALUES(?,?,?,?,?,?,?)""",
            (new_id("resacct"), owner_kind, owner_id, key, float(initial), unit, max_value),
        )
        conn.execute(
            """INSERT INTO resource_ledger(id, owner_kind, owner_id, resource_key, delta, unit, operation,
                   reason, source) VALUES(?,?,?,?,?,?,?,?,?)""",
            (new_id("reslog"), owner_kind, owner_id, key, float(initial), unit, "produce", "initial resource value", "resource_define"),
        )
    append_journal(conn, owner_kind, owner_id, "resource_defined", {"key": key, "display_name": display_name or key}, "resource")
    return get_resource(conn, owner_kind, owner_id, key)


def get_resource(conn, owner_kind: str, owner_id: str, key: str) -> dict[str, Any]:
    d = conn.execute(
        "SELECT * FROM resource_definitions WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    a = conn.execute(
        "SELECT * FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    return {"definition": dict(d) if d else None, "account": dict(a) if a else None}


def apply_delta(conn, owner_kind: str, owner_id: str, resource_key: str, delta: float,
                operation: str = "adjust", reason: str = "resource delta", source: str = "life_event",
                event_id: str | None = None, action_id: str | None = None,
                result_id: str | None = None, schedule_block_id: str | None = None,
                inventory_item_id: str | None = None, meal_id: str | None = None,
                allow_ad_hoc: bool = False) -> dict[str, Any]:
    definition = conn.execute(
        "SELECT * FROM resource_definitions WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, resource_key),
    ).fetchone()
    if not definition:
        if allow_ad_hoc:
            define_resource(conn, owner_kind, owner_id, resource_key, resource_key, "custom", None, None, None, 0)
            definition = conn.execute(
                "SELECT * FROM resource_definitions WHERE owner_kind=? AND owner_id=? AND key=?",
                (owner_kind, owner_id, resource_key),
            ).fetchone()
        else:
            raise ResourceError(f"undefined resource: {resource_key}. Define it through Life Canon or RESOURCE_DEFINE before using it.")
    account = conn.execute(
        "SELECT * FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
        (owner_kind, owner_id, resource_key),
    ).fetchone()
    if not account:
        conn.execute(
            "INSERT INTO resource_accounts(id, owner_kind, owner_id, resource_key, current_value, unit, capacity) VALUES(?,?,?,?,?,?,?)",
            (new_id("resacct"), owner_kind, owner_id, resource_key, 0, definition["unit"], definition["max_value"]),
        )
        account = conn.execute(
            "SELECT * FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
            (owner_kind, owner_id, resource_key),
        ).fetchone()
    current = float(account["current_value"])
    new_value = current + float(delta)
    minv = definition["min_value"]
    maxv = definition["max_value"]
    if minv is not None and new_value < float(minv):
        raise ResourceError(f"resource {resource_key} would go below min {minv}: {new_value}")
    if maxv is not None and new_value > float(maxv):
        new_value = float(maxv)
    unit = definition["unit"]
    ledger_id = new_id("reslog")
    conn.execute(
        """INSERT INTO resource_ledger(id, owner_kind, owner_id, resource_key, delta, unit, operation,
               event_id, action_id, result_id, schedule_block_id, inventory_item_id, meal_id, reason, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ledger_id, owner_kind, owner_id, resource_key, float(delta), unit, operation,
         event_id, action_id, result_id, schedule_block_id, inventory_item_id, meal_id, reason, source),
    )
    conn.execute(
        "UPDATE resource_accounts SET current_value=?, unit=?, capacity=?, updated_at=datetime('now') WHERE owner_kind=? AND owner_id=? AND resource_key=?",
        (new_value, unit, maxv, owner_kind, owner_id, resource_key),
    )
    append_journal(conn, owner_kind, owner_id, "resource_delta", {
        "resource_key": resource_key, "delta": delta, "operation": operation,
        "new_value": new_value, "ledger_id": ledger_id,
    }, source)
    return {"ledger_id": ledger_id, "resource_key": resource_key, "delta": delta, "new_value": new_value}


def reserve(conn, owner_kind: str, owner_id: str, resource_key: str, amount: float,
            reason: str = "reservation", event_id: str | None = None,
            schedule_block_id: str | None = None) -> dict[str, Any]:
    amount = float(amount)
    if amount <= 0:
        raise ResourceError("reservation amount must be positive")
    definition = conn.execute(
        "SELECT * FROM resource_definitions WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, resource_key),
    ).fetchone()
    if not definition:
        raise ResourceError(f"cannot reserve undefined resource: {resource_key}")
    account = conn.execute(
        "SELECT * FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
        (owner_kind, owner_id, resource_key),
    ).fetchone()
    if not account:
        raise ResourceError(f"cannot reserve resource without account: {resource_key}")
    reserved = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM resource_reservations WHERE owner_kind=? AND owner_id=? AND resource_key=? AND status='reserved'",
        (owner_kind, owner_id, resource_key),
    ).fetchone()[0]
    available = float(account["current_value"]) - float(reserved or 0)
    if amount > available:
        raise ResourceError(f"resource {resource_key} reservation exceeds available value: requested {amount}, available {available}")
    res_id = new_id("resv")
    conn.execute(
        """INSERT INTO resource_reservations(id, owner_kind, owner_id, resource_key, amount, unit, status,
               event_id, schedule_block_id, reason) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (res_id, owner_kind, owner_id, resource_key, amount, definition["unit"], "reserved", event_id, schedule_block_id, reason),
    )
    append_journal(conn, owner_kind, owner_id, "resource_reserved", {"reservation_id": res_id, "resource_key": resource_key, "amount": amount}, "resource")
    return {"reservation_id": res_id, "status": "reserved", "available_after_reservation": available - amount}


def release_reservation(conn, owner_kind: str, owner_id: str, reservation_id: str) -> dict[str, Any]:
    conn.execute(
        "UPDATE resource_reservations SET status='released', released_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?",
        (reservation_id, owner_kind, owner_id),
    )
    append_journal(conn, owner_kind, owner_id, "resource_reservation_released", {"reservation_id": reservation_id}, "resource")
    return {"reservation_id": reservation_id, "status": "released"}




def reconcile_resources(conn, owner_kind: str, owner_id: str, tolerance: float = 1e-6, *, record: bool = True) -> dict[str, Any]:
    """Verify materialized resource account values against ledger sums.

    ``resource_accounts.current_value`` is a fast materialized view.  The
    append-only ``resource_ledger`` is the auditable source of changes.  This
    checker never repairs balances silently; it records drift evidence when
    requested so operators can inspect and decide how to recover.
    """
    mismatches: list[dict[str, Any]] = []
    rows = conn.execute(
        "SELECT resource_key, current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? ORDER BY resource_key",
        (owner_kind, owner_id),
    ).fetchall()
    for row in rows:
        ledger_sum = conn.execute(
            "SELECT COALESCE(SUM(delta),0) FROM resource_ledger WHERE owner_kind=? AND owner_id=? AND resource_key=?",
            (owner_kind, owner_id, row["resource_key"]),
        ).fetchone()[0]
        current = float(row["current_value"] or 0)
        expected = float(ledger_sum or 0)
        if abs(current - expected) > tolerance:
            mismatches.append({
                "resource_key": row["resource_key"],
                "current_value": current,
                "ledger_sum": expected,
                "delta": current - expected,
            })
    status = "ok" if not mismatches else "mismatch"
    result = {"ok": not mismatches, "status": status, "checked": len(rows), "mismatches": mismatches}
    if record:
        check_id = new_id("reconcile")
        conn.execute(
            "INSERT INTO resource_reconcile_checks(id, owner_kind, owner_id, status, report_json) VALUES(?,?,?,?,?)",
            (check_id, owner_kind, owner_id, status, dumps({"mismatches": mismatches, "checked": len(rows)})),
        )
        append_journal(conn, owner_kind, owner_id, "resource_reconcile", {"check_id": check_id, **result}, "resource")
        result["check_id"] = check_id
    return result
