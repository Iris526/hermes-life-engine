"""Trace and audit primitives for LifeEngine."""

from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .jsonutil import dumps


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def hash_entry(prev_hash: str | None, payload: str) -> str:
    h = hashlib.sha256()
    h.update((prev_hash or "").encode("utf-8"))
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


class Trace:
    def __init__(self, conn, owner_kind: str, owner_id: str, trace_type: str,
                 session_id: str | None = None, turn_id: str | None = None,
                 tick_id: str | None = None, engine_state: str | None = None,
                 canon_version: int | None = None, input_obj: Any = None):
        self.conn = conn
        self.id = new_id("trace")
        self.owner_kind = owner_kind
        self.owner_id = owner_id
        self.trace_type = trace_type
        self.session_id = session_id
        self.turn_id = turn_id
        self.tick_id = tick_id
        self.engine_state = engine_state
        self.canon_version = canon_version
        self.input_obj = input_obj

    def start(self) -> "Trace":
        self.conn.execute(
            """INSERT INTO trace_runs(id, owner_kind, owner_id, trace_type, session_id, turn_id, tick_id,
                       engine_state, canon_version, status, input_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (self.id, self.owner_kind, self.owner_id, self.trace_type, self.session_id,
             self.turn_id, self.tick_id, self.engine_state, self.canon_version, "running",
             dumps(self.input_obj) if self.input_obj is not None else None),
        )
        return self

    def end(self, status: str = "ok", output_obj: Any = None, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE trace_runs SET status=?, ended_at=datetime('now'), output_json=?, error=? WHERE id=?",
            (status, dumps(output_obj) if output_obj is not None else None, error, self.id),
        )

    @contextmanager
    def span(self, name: str, input_obj: Any = None, parent_span_id: str | None = None) -> Iterator[str]:
        span_id = new_id("span")
        self.conn.execute(
            """INSERT INTO trace_spans(id, trace_id, parent_span_id, name, status, input_json)
                   VALUES(?,?,?,?,?,?)""",
            (span_id, self.id, parent_span_id, name, "running", dumps(input_obj) if input_obj is not None else None),
        )
        try:
            yield span_id
        except Exception as exc:
            self.conn.execute(
                "UPDATE trace_spans SET status='error', ended_at=datetime('now'), error=? WHERE id=?",
                (f"{type(exc).__name__}: {exc}", span_id),
            )
            raise
        else:
            self.conn.execute(
                "UPDATE trace_spans SET status='ok', ended_at=datetime('now') WHERE id=?",
                (span_id,),
            )


def append_audit(conn, owner_kind: str, owner_id: str, audit_type: str, severity: str,
                 message: str, payload: Any = None, trace_id: str | None = None) -> str:
    audit_id = new_id("audit")
    conn.execute(
        """INSERT INTO audit_log(id, owner_kind, owner_id, audit_type, severity, message, payload_json, trace_id)
               VALUES(?,?,?,?,?,?,?,?)""",
        (audit_id, owner_kind, owner_id, audit_type, severity, message,
         dumps(payload) if payload is not None else None, trace_id),
    )
    return audit_id


def append_journal(conn, owner_kind: str, owner_id: str, entry_type: str, payload: Any,
                   source: str, transaction_id: str | None = None, op_id: str | None = None,
                   canon_version: int | None = None) -> str:
    row = conn.execute(
        "SELECT entry_hash FROM life_journal WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (owner_kind, owner_id),
    ).fetchone()
    prev_hash = row[0] if row else None
    payload_json = dumps(payload)
    entry_hash = hash_entry(prev_hash, f"{entry_type}|{source}|{payload_json}")
    journal_id = new_id("journal")
    conn.execute(
        """INSERT INTO life_journal(id, owner_kind, owner_id, transaction_id, op_id, entry_type,
               payload_json, source, canon_version, prev_hash, entry_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (journal_id, owner_kind, owner_id, transaction_id, op_id, entry_type,
         payload_json, source, canon_version, prev_hash, entry_hash),
    )
    return journal_id


def verify_journal_hash_chain(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """Verify the append-only life_journal hash chain for one owner."""
    rows = conn.execute(
        "SELECT id, entry_type, payload_json, source, prev_hash, entry_hash FROM life_journal "
        "WHERE owner_kind=? AND owner_id=? ORDER BY created_at ASC, rowid ASC",
        (owner_kind, owner_id),
    ).fetchall()
    prev = None
    checked = 0
    for r in rows:
        expected_prev = prev
        if r["prev_hash"] != expected_prev:
            return {
                "ok": False,
                "checked_entries": checked,
                "first_bad_journal_id": r["id"],
                "message": f"prev_hash mismatch: expected {expected_prev}, got {r['prev_hash']}",
            }
        expected_hash = hash_entry(prev, f"{r['entry_type']}|{r['source']}|{r['payload_json']}")
        if r["entry_hash"] != expected_hash:
            return {
                "ok": False,
                "checked_entries": checked,
                "first_bad_journal_id": r["id"],
                "message": "entry_hash mismatch",
            }
        prev = r["entry_hash"]
        checked += 1
    return {"ok": True, "checked_entries": checked, "first_bad_journal_id": None, "message": "hash chain valid"}
