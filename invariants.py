"""LifeEngine hardening / doctor checks.

The doctor is intentionally read-mostly: it does not repair state.  It records
an auditable invariant-check row so operators can see when and why a profile was
considered healthy or unhealthy.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .resources import reconcile_resources
from .trace import new_id, verify_journal_hash_chain, append_journal
from .validators import EVENT_STATUSES

_TERMINAL_EVENT_STATUSES = {"completed", "cancelled", "failed", "abandoned", "archived", "discarded"}
_ACTIVE_SCHEDULE_STATUSES = {"planned", "locked", "ready", "in_progress"}


def _issue(kind: str, severity: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "message": message, "evidence": evidence}


def check_event_lifecycle(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """Check durable event/schedule lifecycle invariants.

    Runtime transition functions still enforce state-machine rules at mutation
    time.  This doctor pass catches drift caused by old versions, manual DB
    edits, failed migrations, or code paths that existed before LifeOps became
    mandatory.
    """
    issues: list[dict[str, Any]] = []

    rows = conn.execute(
        "SELECT id,title,status,progress FROM events WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchall()
    for row in rows:
        status = row["status"]
        if status not in EVENT_STATUSES:
            issues.append(_issue("event_status", "error", f"event has unknown status {status!r}", event_id=row["id"], title=row["title"]))
        if status == "completed" and float(row["progress"] or 0) < 100:
            issues.append(_issue("event_progress", "error", "completed event has progress < 100", event_id=row["id"], progress=row["progress"]))
        if status in _TERMINAL_EVENT_STATUSES:
            active_blocks = conn.execute(
                """SELECT id,status,start,end FROM schedule_blocks
                     WHERE owner_kind=? AND owner_id=? AND event_id=?
                       AND status IN ('planned','locked','ready','in_progress')""",
                (owner_kind, owner_id, row["id"]),
            ).fetchall()
            for block in active_blocks:
                issues.append(_issue(
                    "terminal_event_active_schedule",
                    "error",
                    "terminal event still has an active schedule block",
                    event_id=row["id"],
                    event_status=status,
                    schedule_block_id=block["id"],
                    schedule_status=block["status"],
                    start=block["start"],
                    end=block["end"],
                ))

    blocks = conn.execute(
        """SELECT b.id,b.status,b.event_id,b.start,b.end,e.status AS event_status
             FROM schedule_blocks b LEFT JOIN events e ON e.id=b.event_id
             WHERE b.owner_kind=? AND b.owner_id=? AND b.event_id IS NOT NULL""",
        (owner_kind, owner_id),
    ).fetchall()
    for block in blocks:
        if block["event_status"] is None:
            issues.append(_issue("schedule_orphan", "error", "schedule block references missing event", schedule_block_id=block["id"], event_id=block["event_id"]))
        elif block["status"] in _ACTIVE_SCHEDULE_STATUSES and block["event_status"] in _TERMINAL_EVENT_STATUSES:
            issues.append(_issue(
                "active_schedule_terminal_event",
                "error",
                "active schedule block references terminal event",
                schedule_block_id=block["id"],
                schedule_status=block["status"],
                event_id=block["event_id"],
                event_status=block["event_status"],
            ))
        elif block["status"] == "completed" and block["event_status"] not in {"completed", "partial", "in_progress"}:
            issues.append(_issue(
                "completed_schedule_open_event",
                "warning",
                "completed schedule block references an event that is not completed/partial/in_progress",
                schedule_block_id=block["id"],
                event_id=block["event_id"],
                event_status=block["event_status"],
            ))

    deps = conn.execute(
        "SELECT id,event_id,depends_on_event_id FROM event_dependencies WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchall()
    for dep in deps:
        if dep["event_id"] == dep["depends_on_event_id"]:
            issues.append(_issue("event_dependency", "error", "event depends on itself", dependency_id=dep["id"], event_id=dep["event_id"]))

    status = "ok" if not any(i["severity"] == "error" for i in issues) else "failed"
    return {"ok": status == "ok", "status": status, "issues": issues, "checked_events": len(rows), "checked_schedule_blocks": len(blocks)}


def check_wake_jobs(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    rows = conn.execute(
        "SELECT * FROM wake_jobs WHERE owner_kind=? AND owner_id=? ORDER BY wake_at_ts DESC LIMIT 1000",
        (owner_kind, owner_id),
    ).fetchall()
    for row in rows:
        if row["reason"] == "schedule_block_end" and row["target_id"]:
            block = conn.execute(
                "SELECT id FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?",
                (row["target_id"], owner_kind, owner_id),
            ).fetchone()
            if not block:
                issues.append(_issue("wake_orphan", "error", "wake job references missing schedule block", wake_job_id=row["id"], target_id=row["target_id"]))
        if row["status"] == "running":
            issues.append(_issue("wake_running", "warning", "wake job is still marked running", wake_job_id=row["id"], running_at=row["running_at"]))
    status = "ok" if not any(i["severity"] == "error" for i in issues) else "failed"
    return {"ok": status == "ok", "status": status, "issues": issues, "checked_wake_jobs": len(rows)}


def run_doctor(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    journal = verify_journal_hash_chain(conn, owner_kind, owner_id)
    resources = reconcile_resources(conn, owner_kind, owner_id)
    lifecycle = check_event_lifecycle(conn, owner_kind, owner_id)
    wake_jobs = check_wake_jobs(conn, owner_kind, owner_id)
    checks = {
        "journal_hash_chain": journal,
        "resources": resources,
        "event_lifecycle": lifecycle,
        "wake_jobs": wake_jobs,
    }
    ok = all(bool(v.get("ok")) for v in checks.values())
    issues: list[dict[str, Any]] = []
    for check_name, report in checks.items():
        for issue in report.get("issues", []) or []:
            issues.append({"check": check_name, **issue})
        for mismatch in report.get("mismatches", []) or []:
            issues.append({"check": check_name, "kind": "resource_mismatch", "severity": "error", "message": "resource account drift", "evidence": mismatch})
    check_id = new_id("doctor")
    conn.execute(
        """INSERT INTO life_invariant_checks(id, owner_kind, owner_id, status, checks_json, issues_json)
              VALUES(?,?,?,?,?,?)""",
        (check_id, owner_kind, owner_id, "ok" if ok else "failed", dumps(checks), dumps(issues)),
    )
    append_journal(conn, owner_kind, owner_id, "life_doctor", {"check_id": check_id, "status": "ok" if ok else "failed", "issue_count": len(issues)}, "doctor")
    return {"ok": ok, "status": "ok" if ok else "failed", "check_id": check_id, "checks": checks, "issues": issues}
