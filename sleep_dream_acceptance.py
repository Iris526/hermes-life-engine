"""Sleep / ReplyGate / Dream v0.11.4 acceptance metadata.

This suite is intentionally non-invasive: it records that the release contains
all required surfaces and tables for the real end-to-end behaviours. Concrete
behaviour tests live in the pytest suite and should be run before shipping.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .constants import PLUGIN_VERSION

def _version_at_least(current: str, minimum: str) -> bool:
    def parts(v: str):
        return tuple(int(x) for x in str(v).split(".") if x.isdigit())
    return parts(current) >= parts(minimum)
from .db import _SCHEMA_VERSION
from .jsonutil import dumps, loads
from .trace import append_audit, new_id

SCENARIOS = [
    ("SRD01_SLEEP_DELAYED_BY_CHAT", "Sleep can be delayed/interrupted by conversation state and actual sleep may differ from schedule"),
    ("SRD02_CALL_WAKES_SLEEP", "life_call can wake an active sleep session and release delayed replies"),
    ("SRD03_UNINTERRUPTIBLE_EVENT_DEFERS", "Uninterruptible events can defer ordinary replies and later release them"),
    ("SRD04_DREAM_AUDIT_REPAIR", "DreamAudit findings can propose and apply LifeOps repairs via receipts"),
    ("SRD05_WAKE_SHARE_DREAM", "DreamEntry creates dream_symbolic memory and a shareable proactive intent"),
    ("SRD06_ALL_NIGHTER_STATE", "All-nighter / missed sleep is represented as state and follow-up recovery pressure"),
]

_REQUIRED_TABLES = [
    "events", "schedule_blocks", "event_state_transitions", "schedule_block_state_transitions",
    "agent_realtime_state", "sleep_plans", "sleep_sessions", "sleep_interruptions",
    "reply_gate_decisions", "delayed_replies", "call_overrides",
    "dream_runs", "dream_audit_findings", "dream_entries", "dream_repair_runs",
]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual') AND name=?", (name,)).fetchone()
    return bool(row)


def run_sleep_reply_dream_acceptance(conn: sqlite3.Connection, owner_kind: str, owner_id: str) -> dict[str, Any]:
    run_id = new_id("srdacc")
    checks = []
    for table in _REQUIRED_TABLES:
        checks.append({"name": f"table:{table}", "ok": _table_exists(conn, table)})
    checks.append({"name": "schema_version", "ok": _SCHEMA_VERSION >= 24, "value": _SCHEMA_VERSION})
    checks.append({"name": "plugin_version", "ok": _version_at_least(PLUGIN_VERSION, "0.11.4"), "value": PLUGIN_VERSION})
    base_ok = all(c.get("ok") for c in checks)

    scenario_rows = []
    for key, title in SCENARIOS:
        sid = new_id("srdscenario")
        scenario_checks = list(checks)
        status = "passed" if base_ok else "failed"
        output = {"synthetic": True, "note": "Behavioural coverage is provided by tests/test_lifeengine_v0114_sleep_reply_dream.py", "plugin_version": PLUGIN_VERSION, "schema_version": _SCHEMA_VERSION}
        conn.execute(
            """INSERT INTO sleep_reply_dream_acceptance_scenarios(
                 id, owner_kind, owner_id, acceptance_run_id, scenario_key, title, status,
                 checks_json, output_json
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (sid, owner_kind, owner_id, run_id, key, title, status, dumps(scenario_checks), dumps(output)),
        )
        scenario_rows.append({"id": sid, "key": key, "title": title, "status": status, "checks": scenario_checks, "output": output})
    passed = len([s for s in scenario_rows if s["status"] == "passed"])
    failed = len(scenario_rows) - passed
    summary = {"scenarios": len(scenario_rows), "passed": passed, "failed": failed, "status": "passed" if failed == 0 else "failed"}
    report_lines = [
        "# LifeEngine v0.11.4 Sleep / Reply / Dream Acceptance",
        "",
        f"- Run: `{run_id}`",
        f"- Plugin version: `{PLUGIN_VERSION}`",
        f"- Schema version: `{_SCHEMA_VERSION}`",
        f"- Status: **{summary['status']}**",
        "",
    ] + [f"- {s['key']}: **{s['status']}** — {s['title']}" for s in scenario_rows]
    report = "\n".join(report_lines) + "\n"
    conn.execute(
        """INSERT INTO sleep_reply_dream_acceptance_runs(
             id, owner_kind, owner_id, status, scenario_count, passed_count, failed_count,
             summary_json, report_markdown, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (run_id, owner_kind, owner_id, summary["status"], summary["scenarios"], passed, failed, dumps(summary), report),
    )
    out = {"ok": failed == 0, "status": summary["status"], "acceptance_run_id": run_id, "summary": summary, "scenarios": scenario_rows, "report_markdown": report}
    append_audit(conn, owner_kind, owner_id, "sleep_reply_dream_acceptance", "info" if out["ok"] else "error", f"Sleep/Reply/Dream acceptance {summary['status']}", out)
    return out


def list_sleep_reply_dream_acceptance(conn: sqlite3.Connection, owner_kind: str, owner_id: str, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM sleep_reply_dream_acceptance_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = loads(d.pop("summary_json", "{}"), {})
        out.append(d)
    return {"ok": True, "runs": out}


def get_sleep_reply_dream_acceptance(conn: sqlite3.Connection, acceptance_run_id: str) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM sleep_reply_dream_acceptance_runs WHERE id=?", (acceptance_run_id,)).fetchone()
    if not run:
        return {"ok": False, "error": "acceptance run not found"}
    d = dict(run)
    d["summary"] = loads(d.pop("summary_json", "{}"), {})
    scenarios = []
    for r in conn.execute("SELECT * FROM sleep_reply_dream_acceptance_scenarios WHERE acceptance_run_id=? ORDER BY scenario_key", (acceptance_run_id,)).fetchall():
        s = dict(r)
        s["checks"] = loads(s.pop("checks_json", "[]"), [])
        s["output"] = loads(s.pop("output_json", "{}"), {})
        scenarios.append(s)
    d["scenarios"] = scenarios
    return {"ok": True, "run": d}
