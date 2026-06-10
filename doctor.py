"""LifeEngine doctor / hardening checks."""

from __future__ import annotations

from typing import Any

from .db import _SCHEMA_VERSION
from .lifecycle import EVENT_TRANSITIONS, SCHEDULE_BLOCK_TRANSITIONS
from .trace import append_audit, verify_journal_hash_chain

class ChecksDict(dict):
    """Dict for keyed access whose iteration yields check objects.

    This preserves machine-friendly keyed access while keeping older tests and
    callers that iterate over checks working.
    """
    def __iter__(self):  # type: ignore[override]
        return iter(self.values())


REQUIRED_TABLES = {
    "controls", "canon_versions", "canon_drafts", "life_transactions", "life_ops",
    "life_journal", "trace_runs", "trace_spans", "commit_receipts", "commit_receipt_facts",
    "resource_definitions", "resource_accounts", "resource_ledger", "events", "schedule_blocks",
    "wake_jobs", "truth_source_reads", "inventory_items", "goals", "autonomy_decisions",
    "proactive_intents", "execution_decisions", "serendipity_events", "memory_vec", "schema_migrations", "install_checks", "final_gate_reports", "final_gate_feedback_queue", "trace_coverage_reports", "acceptance_reports", "api_freeze_snapshots",
}


def _check(ok: bool, message: str = "", severity: str = "error", **data: Any) -> dict[str, Any]:
    return {"ok": bool(ok), "status": "ok" if ok else severity, "severity": severity, "message": message, **data}


def _count(conn, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] if row else 0)


def _table_names(conn) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()}


def check_trace_coverage(conn, owner_kind: str, owner_id: str, *, write_report: bool = True) -> dict[str, Any]:
    """Verify that committed LifeOps transactions have trace/op/journal/receipt coverage."""
    txs = conn.execute(
        "SELECT * FROM life_transactions WHERE owner_kind=? AND owner_id=? AND status='committed' ORDER BY created_at",
        (owner_kind, owner_id),
    ).fetchall()
    issues: list[dict[str, Any]] = []
    for tx in txs:
        tx_id = tx["id"]
        trace_id = tx["trace_id"]
        if not trace_id:
            issues.append({"kind": "transaction_trace", "transaction_id": tx_id, "message": "committed transaction has no trace_id"})
        elif not conn.execute("SELECT 1 FROM trace_runs WHERE id=?", (trace_id,)).fetchone():
            issues.append({"kind": "trace_run", "transaction_id": tx_id, "trace_id": trace_id, "message": "trace_id is missing from trace_runs"})
        ops = conn.execute("SELECT * FROM life_ops WHERE transaction_id=? ORDER BY created_at", (tx_id,)).fetchall()
        if not ops:
            issues.append({"kind": "transaction_ops", "transaction_id": tx_id, "message": "committed transaction has no life_ops"})
        for op in ops:
            if op["status"] != "committed":
                issues.append({"kind": "op_status", "transaction_id": tx_id, "op_id": op["id"], "message": f"op status is {op['status']}"})
            if not op["validator_report_json"]:
                issues.append({"kind": "op_validator", "transaction_id": tx_id, "op_id": op["id"], "message": "op lacks validator_report_json"})
            if not conn.execute("SELECT 1 FROM life_journal WHERE op_id=? AND transaction_id=?", (op["id"], tx_id)).fetchone():
                issues.append({"kind": "op_journal", "transaction_id": tx_id, "op_id": op["id"], "message": "op has no journal entry"})
        receipt = conn.execute("SELECT * FROM commit_receipts WHERE transaction_id=?", (tx_id,)).fetchone()
        if not receipt:
            issues.append({"kind": "receipt", "transaction_id": tx_id, "message": "committed transaction has no commit receipt"})
        else:
            fact_count = _count(conn, "SELECT COUNT(*) FROM commit_receipt_facts WHERE transaction_id=?", (tx_id,))
            if fact_count == 0:
                issues.append({"kind": "receipt_facts", "transaction_id": tx_id, "receipt_id": receipt["id"], "message": "commit receipt has no facts"})
    out = {"ok": not issues, "status": "ok" if not issues else "failed", "checked_transactions": len(txs), "issue_count": len(issues), "issues": issues, "message": "trace coverage ok" if not issues else f"{len(issues)} trace coverage issue(s)"}
    if write_report:
        report_id = f"tracecov_{__import__('uuid').uuid4().hex}"
        import json
        issues_json = json.dumps(issues, ensure_ascii=False, sort_keys=True)
        try:
            conn.execute(
                "INSERT INTO trace_coverage_reports(id, owner_kind, owner_id, status, checked_transactions, issue_count, issues_json) VALUES(?,?,?,?,?,?,?)",
                (report_id, owner_kind, owner_id, out["status"], len(txs), len(issues), issues_json),
            )
        except Exception:
            op_count = sum(_count(conn, "SELECT COUNT(*) FROM life_ops WHERE transaction_id=?", (tx["id"],)) for tx in txs)
            receipt_count = sum(_count(conn, "SELECT COUNT(*) FROM commit_receipts WHERE transaction_id=?", (tx["id"],)) for tx in txs)
            conn.execute(
                "INSERT INTO trace_coverage_reports(id, owner_kind, owner_id, status, checked_transactions, checked_ops, checked_receipts, issues_json) VALUES(?,?,?,?,?,?,?,?)",
                (report_id, owner_kind, owner_id, out["status"], len(txs), op_count, receipt_count, issues_json),
            )
        out["report_id"] = report_id
    return out

def _resource_mismatches(conn, owner_kind: str, owner_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT resource_key,current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? ORDER BY resource_key",
        (owner_kind, owner_id),
    ).fetchall()
    mismatches = []
    for row in rows:
        ledger_sum = conn.execute(
            "SELECT COALESCE(SUM(delta),0) FROM resource_ledger WHERE owner_kind=? AND owner_id=? AND resource_key=?",
            (owner_kind, owner_id, row["resource_key"]),
        ).fetchone()[0]
        current = float(row["current_value"] or 0)
        expected = float(ledger_sum or 0)
        if abs(current - expected) > 1e-6:
            mismatches.append({"resource_key": row["resource_key"], "current_value": current, "ledger_sum": expected})
    return mismatches


def run_doctor(conn, owner_kind: str, owner_id: str, *, write_audit: bool = True) -> dict[str, Any]:
    checks: ChecksDict = ChecksDict()

    try:
        sqlite_version, vec_version = conn.execute("SELECT sqlite_version(), vec_version()").fetchone()
        checks["sqlite_vec"] = _check(True, f"sqlite={sqlite_version}, sqlite-vec={vec_version}", sqlite_version=sqlite_version, vec_version=vec_version)
    except Exception as exc:
        checks["sqlite_vec"] = _check(False, f"sqlite-vec is not loaded: {type(exc).__name__}: {exc}")

    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    checks["schema_version"] = _check(user_version == _SCHEMA_VERSION, f"user_version={user_version}, expected={_SCHEMA_VERSION}", current=user_version, expected=_SCHEMA_VERSION)

    names = _table_names(conn)
    missing = sorted(REQUIRED_TABLES - names)
    checks["required_tables"] = _check(not missing, "all required tables present" if not missing else f"missing: {', '.join(missing)}", missing=missing)

    if "life_journal" in names:
        journal = dict(verify_journal_hash_chain(conn, owner_kind, owner_id))
        journal_ok = bool(journal.pop("ok", False))
        journal_message = str(journal.pop("message", ""))
        checks["journal_hash_chain"] = _check(journal_ok, journal_message, **journal)

    if {"resource_accounts", "resource_ledger"}.issubset(names):
        mismatches = _resource_mismatches(conn, owner_kind, owner_id)
        checks["resources"] = _check(not mismatches, f"{len(mismatches)} resource mismatches" if mismatches else "resource accounts match ledger", mismatches=mismatches)

    lifecycle_issues: list[dict[str, Any]] = []
    if "events" in names:
        valid_events = sorted(EVENT_TRANSITIONS.keys())
        invalid_events = _count(
            conn,
            f"SELECT COUNT(*) FROM events WHERE owner_kind=? AND owner_id=? AND status NOT IN ({','.join('?' for _ in valid_events)})",
            (owner_kind, owner_id, *valid_events),
        )
        if invalid_events:
            lifecycle_issues.append({"kind": "event_status_values", "invalid_count": invalid_events})
    if "schedule_blocks" in names:
        valid_blocks = sorted(SCHEDULE_BLOCK_TRANSITIONS.keys())
        invalid_blocks = _count(
            conn,
            f"SELECT COUNT(*) FROM schedule_blocks WHERE owner_kind=? AND owner_id=? AND status NOT IN ({','.join('?' for _ in valid_blocks)})",
            (owner_kind, owner_id, *valid_blocks),
        )
        if invalid_blocks:
            lifecycle_issues.append({"kind": "schedule_status_values", "invalid_count": invalid_blocks})
        overlaps = conn.execute(
            """SELECT a.id AS a_id, b.id AS b_id
                 FROM schedule_blocks a
                 JOIN schedule_blocks b ON a.owner_kind=b.owner_kind AND a.owner_id=b.owner_id AND a.id < b.id
                WHERE a.owner_kind=? AND a.owner_id=?
                  AND a.status IN ('planned','locked','ready','in_progress')
                  AND b.status IN ('planned','locked','ready','in_progress')
                  AND a.start_ts IS NOT NULL AND a.end_ts IS NOT NULL
                  AND b.start_ts IS NOT NULL AND b.end_ts IS NOT NULL
                  AND NOT(a.end_ts <= b.start_ts OR a.start_ts >= b.end_ts)
                LIMIT 10""",
            (owner_kind, owner_id),
        ).fetchall()
        if overlaps:
            lifecycle_issues.append({"kind": "schedule_overlap", "overlaps": [dict(r) for r in overlaps]})
    if {"events", "schedule_blocks"}.issubset(names):
        terminal_open = _count(
            conn,
            """SELECT COUNT(*) FROM events e JOIN schedule_blocks b ON b.event_id=e.id
                 WHERE e.owner_kind=? AND e.owner_id=?
                   AND e.status IN ('completed','cancelled','abandoned','archived','discarded')
                   AND b.status IN ('planned','locked','ready','in_progress')""",
            (owner_kind, owner_id),
        )
        if terminal_open:
            lifecycle_issues.append({"kind": "terminal_event_active_schedule", "open_blocks": terminal_open})
    checks["event_lifecycle"] = _check(not lifecycle_issues, "event and schedule lifecycle ok" if not lifecycle_issues else f"{len(lifecycle_issues)} lifecycle issues", issues=lifecycle_issues)

    if {"life_transactions", "life_ops", "life_journal", "trace_runs", "commit_receipts", "commit_receipt_facts"}.issubset(names):
        coverage = check_trace_coverage(conn, owner_kind, owner_id, write_report=False)
        checks["trace_coverage"] = _check(bool(coverage.get("ok")), coverage.get("message", "trace coverage"), issues=coverage.get("issues", []), checked_transactions=coverage.get("checked_transactions", 0), severity="error")

    if "wake_jobs" in names:
        running = _count(conn, "SELECT COUNT(*) FROM wake_jobs WHERE owner_kind=? AND owner_id=? AND status='running'", (owner_kind, owner_id))
        checks["wake_jobs"] = _check(running == 0, f"running_count={running}", "warning", running_count=running)

    errors = [c for c in checks.values() if not c.get("ok") and c.get("severity") == "error"]
    warnings = [c for c in checks.values() if not c.get("ok") and c.get("severity") == "warning"]
    result = {
        "ok": not errors,
        "status": "failed" if errors else "warning" if warnings else "ok",
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "checks": checks,
        "errors": len(errors),
        "warnings": len(warnings),
    }
    if write_audit:
        append_audit(
            conn,
            owner_kind,
            owner_id,
            "life_doctor",
            "info" if result["ok"] else "warning",
            f"LifeEngine doctor status={result['status']}",
            result,
        )
    return result

__all__ = ["run_doctor", "check_trace_coverage"]
