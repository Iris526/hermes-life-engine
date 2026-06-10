"""User Life confirmation flow.

User Life uses the same LifeOps machinery as Agent Life, but the epistemic
policy is stricter: uncertain or proposed user facts should be parked here until
explicitly confirmed by the user.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_audit, append_journal, new_id


class ConfirmationError(ValueError):
    pass


def propose_confirmation(conn, owner_kind: str, owner_id: str, ops: list[dict[str, Any]], reason: str,
                         session_id: str | None = None, turn_id: str | None = None,
                         source: str = "confirmation_proposed") -> dict[str, Any]:
    if owner_kind != "user":
        raise ConfirmationError("confirmation flow is intended for user life workspace")
    if not isinstance(ops, list) or not ops:
        raise ConfirmationError("proposed ops must be a non-empty list")
    confirmation_id = new_id("confirm")
    conn.execute(
        """INSERT INTO user_confirmations(id, owner_kind, owner_id, proposed_ops_json, reason, session_id, turn_id)
              VALUES(?,?,?,?,?,?,?)""",
        (confirmation_id, owner_kind, owner_id, dumps(ops), reason or "requires user confirmation", session_id, turn_id),
    )
    append_audit(conn, owner_kind, owner_id, "user_confirmation_required", "info", reason or "requires user confirmation", {"confirmation_id": confirmation_id})
    append_journal(conn, owner_kind, owner_id, "user_confirmation_proposed", {"confirmation_id": confirmation_id, "ops": ops, "reason": reason}, source)
    return get_confirmation(conn, owner_kind, owner_id, confirmation_id)


def get_confirmation(conn, owner_kind: str, owner_id: str, confirmation_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM user_confirmations WHERE id=? AND owner_kind=? AND owner_id=?", (confirmation_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise ConfirmationError(f"confirmation not found: {confirmation_id}")
    d = dict(row)
    d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
    d["resolution"] = loads(d.pop("resolution_json", None), {})
    return d


def list_confirmations(conn, owner_kind: str, owner_id: str, *, status: str | None = "pending", limit: int = 20) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute("SELECT * FROM user_confirmations WHERE owner_kind=? AND owner_id=? AND status=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, status, int(limit))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM user_confirmations WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
        d["resolution"] = loads(d.pop("resolution_json", None), {})
        out.append(d)
    return out


def mark_confirmation(conn, owner_kind: str, owner_id: str, confirmation_id: str, status: str,
                      *, resolved_by: str | None = None, note: str | None = None,
                      result_transaction_id: str | None = None) -> dict[str, Any]:
    if status not in {"confirmed", "rejected", "expired", "cancelled"}:
        raise ConfirmationError(f"invalid confirmation resolution status: {status}")
    current = get_confirmation(conn, owner_kind, owner_id, confirmation_id)
    if current["status"] != "pending":
        raise ConfirmationError(f"confirmation {confirmation_id} is not pending: {current['status']}")
    resolution = {"status": status, "note": note, "result_transaction_id": result_transaction_id}
    conn.execute(
        """UPDATE user_confirmations SET status=?, resolved_at=datetime('now'), resolved_by=?,
               resolution_json=?, result_transaction_id=? WHERE id=? AND owner_kind=? AND owner_id=?""",
        (status, resolved_by, dumps(resolution), result_transaction_id, confirmation_id, owner_kind, owner_id),
    )
    append_journal(conn, owner_kind, owner_id, f"user_confirmation_{status}", {"confirmation_id": confirmation_id, "resolution": resolution}, "confirmation")
    return get_confirmation(conn, owner_kind, owner_id, confirmation_id)


def confirmed_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare proposed ops for durable user-life commit after explicit consent."""
    out: list[dict[str, Any]] = []
    for op in ops:
        op2 = dict(op)
        payload = dict(op2.get("payload") or {k: v for k, v in op2.items() if k not in {"type", "op_type"}})
        payload["source"] = "user_confirmed"
        payload["confirmed_by_user"] = True
        op2["payload"] = payload
        if "type" not in op2 and "op_type" in op2:
            op2["type"] = op2["op_type"]
        out.append({"type": op2.get("type") or op2.get("op_type"), "payload": payload})
    return out
