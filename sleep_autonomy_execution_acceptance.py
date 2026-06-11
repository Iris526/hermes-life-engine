"""Sleep / Autonomy / Execution v0.11.8 end-to-end acceptance.

The runner uses an isolated synthetic owner under the same embedded SQLite DB.
It does not mutate the caller's real Agent life; it records acceptance metadata
under the caller owner and puts all scenario life state under a generated owner
ID so traces remain inspectable.
"""
from __future__ import annotations

from typing import Any, Callable
from datetime import datetime, timedelta

from .constants import PLUGIN_VERSION

def _version_at_least(current: str, minimum: str) -> bool:
    def parts(v: str):
        return tuple(int(x) for x in str(v).split(".") if x.isdigit())
    return parts(current) >= parts(minimum)
from .db import _SCHEMA_VERSION, transaction
from .jsonutil import dumps, loads
from .trace import append_audit, new_id

SCENARIOS: list[tuple[str, str]] = [
    ("SAE01_ALL_NIGHTER_AUTONOMY_RECOVERY", "All-nighter SleepDayState makes Autonomy create a recovery sleep plan"),
    ("SAE02_RECOVERY_EXISTS_GOAL_DOWNSHIFT", "Existing recovery sleep prevents duplicate nap and downshifts high-intensity goal work"),
    ("SAE03_SHORT_SLEEP_IMPORTANT_EVENT_PARTIAL", "Short sleep makes an important scheduled work event execute only partially"),
    ("SAE04_ALL_NIGHTER_LOW_IMPORTANCE_POSTPONED", "All-nighter makes a low-importance scheduled work event postpone/reschedule"),
    ("SAE05_CALL_INTERRUPTS_SLEEP_AND_RELEASES", "life_call wakes active sleep and releases delayed replies"),
    ("SAE06_TRACE_COVERS_SLEEP_EXECUTION_CHAIN", "Trace explain covers sleep-aware execution adjustments for affected events"),
]

_REQUIRED_TABLES = [
    "sleep_day_states", "sleep_recovery_plans", "autonomy_sleep_adjustments",
    "execution_sleep_adjustments", "sleep_sessions", "sleep_interruptions",
    "reply_gate_decisions", "delayed_replies", "call_overrides",
    "events", "schedule_blocks", "life_transactions", "commit_receipts",
]


def _table_exists(conn, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual') AND name=?", (name,)).fetchone()
    return bool(row)


def _safe_scenario(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        out = fn()
        out.setdefault("ok", True)
        return out
    except Exception as exc:  # pragma: no cover - exercised by failure diagnostics
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _activate_synthetic(rt: Any, owner_id: str) -> None:
    rt.setup("v0.11.8 sleep-autonomy-execution acceptance synthetic agent", owner_id=owner_id)
    rt.commit_canon(owner_id=owner_id)
    rt.control("resume", owner_id=owner_id)
    rt.control("module", owner_id=owner_id, key="autonomy", value="full")
    rt.control("module", owner_id=owner_id, key="execution", value="auto")
    rt.control("module", owner_id=owner_id, key="reply_gate", value="auto")
    for key, initial in [("energy", 70), ("focus", 70), ("mood", 60), ("fatigue", 0)]:
        rt.resources("define", owner_id=owner_id, key=key, display_name=key, initial=initial)


def _sleep_plan_id(out: dict[str, Any]) -> str:
    for fact in (out.get("receipt") or {}).get("facts", []):
        ev = fact.get("evidence") or {}
        if ev.get("sleep_plan_id"):
            return ev["sleep_plan_id"]
    raise AssertionError("sleep_plan_id not found in receipt facts")


def _event_id(out: dict[str, Any]) -> str:
    for fact in (out.get("receipt") or {}).get("facts", []):
        ev = fact.get("evidence") or {}
        if ev.get("event_id"):
            return ev["event_id"]
    raise AssertionError("event_id not found in receipt facts")


def _schedule_block_id(out: dict[str, Any]) -> str:
    for fact in (out.get("receipt") or {}).get("facts", []):
        ev = fact.get("evidence") or {}
        if ev.get("schedule_block_id"):
            return ev["schedule_block_id"]
    raise AssertionError("schedule_block_id not found in receipt facts")


def _create_work_event(rt: Any, owner_id: str, *, title: str, importance: int, start: str) -> tuple[str, str]:
    event_out = rt.event_tool(
        "create",
        owner_id=owner_id,
        title=title,
        event_type="work",
        event_category="work",
        status="planned",
        importance=importance,
        priority=importance,
        resource_costs={"energy": -12, "focus": -10},
    )
    event_id = _event_id(event_out)
    # Keep the end one hour after the given start; tests use different dates.
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end = (start_dt + timedelta(hours=1)).isoformat()
    schedule_out = rt.event_tool(
        "schedule",
        owner_id=owner_id,
        event_id=event_id,
        start=start,
        end=end,
        timezone_name="UTC",
    )
    return event_id, _schedule_block_id(schedule_out)


def run_sleep_autonomy_execution_acceptance(rt: Any, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """Run concrete sleep/autonomy/execution acceptance scenarios.

    The synthetic owner is an Agent owner so narrative/autonomy/execution rules
    apply, but it is separate from the real caller owner.  Scenario records are
    written under the real caller owner for discoverability.
    """
    run_id = new_id("saeacc")
    synthetic_owner_id = f"{owner_id}-sae-{run_id}"
    _activate_synthetic(rt, synthetic_owner_id)

    base_checks = [
        {"name": f"table:{table}", "ok": _table_exists(rt.conn, table)} for table in _REQUIRED_TABLES
    ] + [
        {"name": "schema_version", "ok": _SCHEMA_VERSION >= 28, "value": _SCHEMA_VERSION},
        {"name": "plugin_version", "ok": _version_at_least(PLUGIN_VERSION, "0.11.8"), "value": PLUGIN_VERSION},
    ]

    context: dict[str, Any] = {"synthetic_owner_id": synthetic_owner_id}

    def s1_all_nighter_autonomy_recovery() -> dict[str, Any]:
        plan = rt.sleep_tool(
            "plan",
            owner_id=synthetic_owner_id,
            planned_sleep_at="2026-06-10T23:00:00+00:00",
            planned_wake_at="2026-06-11T07:00:00+00:00",
            timezone_name="UTC",
        )
        plan_id = _sleep_plan_id(plan)
        day = rt.sleep_tool("all_nighter", owner_id=synthetic_owner_id, sleep_plan_id=plan_id)["sleep_day_state"]
        rt.goals("create", owner_id=synthetic_owner_id, title="准备考试", goal_type="study", priority=90)
        decision = rt.autonomy("run", owner_id=synthetic_owner_id, now="2026-06-11T09:00:00+00:00")
        plans = rt.sleep_tool("plans", owner_id=synthetic_owner_id)["sleep_plans"]
        recovery = [p for p in plans if p.get("plan_type") == "recovery_sleep"]
        assert day["all_nighter"] is True
        assert recovery
        context["all_nighter_day_state_id"] = day["id"]
        context["recovery_plan_id"] = recovery[0]["id"]
        return {"ok": True, "sleep_day_state_id": day["id"], "recovery_plan_id": recovery[0]["id"], "autonomy_decision_id": decision["decision"]["id"]}

    def s2_recovery_exists_downshift() -> dict[str, Any]:
        rt.goals("create", owner_id=synthetic_owner_id, title="写完创作项目", goal_type="creative", priority=85)
        out = rt.autonomy("run", owner_id=synthetic_owner_id, now="2026-06-11T10:00:00+00:00")
        events = rt.event_tool("list", owner_id=synthetic_owner_id, limit=50)["events"]
        light = [
            e for e in events
            if str(e.get("title") or "").startswith("轻量推进目标：")
            or "sleep_adjusted" in (e.get("tags") or [])
            or "低强度恢复" in str(e.get("title") or "")
        ]
        assert light, {"events": events, "decision": out}
        context["downshift_event_id"] = light[0]["id"]
        return {"ok": True, "autonomy_decision_id": out["decision"]["id"], "downshift_event_id": light[0]["id"], "event_title": light[0].get("title")}

    def s3_short_sleep_important_partial() -> dict[str, Any]:
        plan = rt.sleep_tool(
            "plan",
            owner_id=synthetic_owner_id,
            planned_sleep_at="2026-06-11T23:00:00+00:00",
            planned_wake_at="2026-06-12T07:00:00+00:00",
            timezone_name="UTC",
        )
        plan_id = _sleep_plan_id(plan)
        rt.sleep_tool("start", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-12T03:00:00+00:00")
        wake = rt.sleep_tool("wake", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-12T05:00:00+00:00")
        event_id, block_id = _create_work_event(rt, synthetic_owner_id, title="写重要报告", importance=88, start="2026-06-12T10:00:00+00:00")
        out = rt.execution("run", owner_id=synthetic_owner_id, schedule_block_id=block_id)
        assert out["decision"]["decision_type"] == "partial"
        context["partial_event_id"] = event_id
        return {"ok": True, "event_id": event_id, "schedule_block_id": block_id, "decision_id": out["decision"]["id"], "sleep_day_state_id": wake["results"][0]["result"]["sleep_day_state"]["id"]}

    def s4_low_importance_postponed() -> dict[str, Any]:
        # Reuse latest all-nighter/short-sleep pressure; lower importance should postpone.
        event_id, block_id = _create_work_event(rt, synthetic_owner_id, title="整理低优先级资料", importance=35, start="2026-06-12T10:00:00+00:00")
        out = rt.execution("run", owner_id=synthetic_owner_id, schedule_block_id=block_id)
        assert out["decision"]["decision_type"] == "postponed"
        context["postponed_event_id"] = event_id
        return {"ok": True, "event_id": event_id, "schedule_block_id": block_id, "decision_id": out["decision"]["id"]}

    def s5_call_interrupts_sleep() -> dict[str, Any]:
        plan = rt.sleep_tool(
            "plan",
            owner_id=synthetic_owner_id,
            planned_sleep_at="2026-06-12T23:00:00+00:00",
            planned_wake_at="2026-06-13T07:00:00+00:00",
            timezone_name="UTC",
        )
        plan_id = _sleep_plan_id(plan)
        start = rt.sleep_tool("start", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-12T23:30:00+00:00")
        session_id = (start.get("receipt") or {}).get("facts", [{}])[0].get("evidence", {}).get("sleep_session_id")
        rt.reply("defer", owner_id=synthetic_owner_id, message_text="你醒着吗？", user_id="acceptance-user", reason="sleeping")
        call = rt.call(owner_id=synthetic_owner_id, reason="acceptance call override", message_text="call")
        delayed = rt.reply("delayed", owner_id=synthetic_owner_id)["delayed_replies"]
        calls = rt.reply("calls", owner_id=synthetic_owner_id)["call_overrides"]
        assert calls
        assert all(d.get("status") in {"released", "cancelled"} for d in delayed) or not delayed
        return {"ok": True, "sleep_plan_id": plan_id, "sleep_session_id": session_id, "call_transaction_id": call.get("transaction_id"), "call_overrides": len(calls)}

    def s6_trace_coverage() -> dict[str, Any]:
        event_id = context.get("partial_event_id") or context.get("postponed_event_id")
        explained = rt.traces("explain", owner_id=synthetic_owner_id, event_id=event_id)
        assert explained.get("execution_sleep_adjustments") is not None
        assert explained.get("event_state_transitions") is not None
        return {"ok": True, "event_id": event_id, "has_execution_sleep_adjustments": bool(explained.get("execution_sleep_adjustments")), "has_event_state_transitions": bool(explained.get("event_state_transitions"))}

    scenario_fns: list[tuple[str, str, Callable[[], dict[str, Any]]]] = [
        (*SCENARIOS[0], s1_all_nighter_autonomy_recovery),
        (*SCENARIOS[1], s2_recovery_exists_downshift),
        (*SCENARIOS[2], s3_short_sleep_important_partial),
        (*SCENARIOS[3], s4_low_importance_postponed),
        (*SCENARIOS[4], s5_call_interrupts_sleep),
        (*SCENARIOS[5], s6_trace_coverage),
    ]

    scenario_rows = []
    for key, title, fn in scenario_fns:
        sid = new_id("saescenario")
        out = _safe_scenario(fn)
        status = "passed" if out.get("ok") else "failed"
        checks = list(base_checks) + [{"name": "scenario_result", "ok": bool(out.get("ok")), "message": out.get("error") or "ok"}]
        scenario_rows.append({"id": sid, "key": key, "title": title, "status": status, "checks": checks, "output": out})

    passed = len([s for s in scenario_rows if s["status"] == "passed"])
    failed = len(scenario_rows) - passed
    summary = {"scenarios": len(scenario_rows), "passed": passed, "failed": failed, "status": "passed" if failed == 0 else "failed", "synthetic_owner_id": synthetic_owner_id}
    report_lines = [
        "# LifeEngine v0.11.8 Sleep / Autonomy / Execution Acceptance",
        "",
        f"- Run: `{run_id}`",
        f"- Plugin version: `{PLUGIN_VERSION}`",
        f"- Schema version: `{_SCHEMA_VERSION}`",
        f"- Synthetic owner: `{synthetic_owner_id}`",
        f"- Status: **{summary['status']}**",
        "",
    ] + [f"- {s['key']}: **{s['status']}** — {s['title']}" for s in scenario_rows]
    report = "\n".join(report_lines) + "\n"

    with transaction(rt.conn):
        for s in scenario_rows:
            rt.conn.execute(
                """INSERT INTO sleep_autonomy_execution_acceptance_scenarios(
                     id, owner_kind, owner_id, acceptance_run_id, scenario_key, title, status,
                     checks_json, output_json
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (s["id"], owner_kind, owner_id, run_id, s["key"], s["title"], s["status"], dumps(s["checks"]), dumps(s["output"])),
            )
        rt.conn.execute(
            """INSERT INTO sleep_autonomy_execution_acceptance_runs(
                 id, owner_kind, owner_id, status, synthetic_owner_id, scenario_count, passed_count, failed_count,
                 summary_json, report_markdown, completed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (run_id, owner_kind, owner_id, summary["status"], synthetic_owner_id, summary["scenarios"], passed, failed, dumps(summary), report),
        )
        out = {"ok": failed == 0, "status": summary["status"], "acceptance_run_id": run_id, "synthetic_owner_id": synthetic_owner_id, "summary": summary, "scenarios": scenario_rows, "report_markdown": report}
        append_audit(rt.conn, owner_kind, owner_id, "sleep_autonomy_execution_acceptance", "info" if out["ok"] else "error", f"Sleep/Autonomy/Execution acceptance {summary['status']}", out)
    return out


def list_sleep_autonomy_execution_acceptance(conn, owner_kind: str, owner_id: str, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM sleep_autonomy_execution_acceptance_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = loads(d.pop("summary_json", "{}"), {})
        out.append(d)
    return {"ok": True, "runs": out}


def get_sleep_autonomy_execution_acceptance(conn, acceptance_run_id: str) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM sleep_autonomy_execution_acceptance_runs WHERE id=?", (acceptance_run_id,)).fetchone()
    if not run:
        return {"ok": False, "error": "acceptance run not found"}
    d = dict(run)
    d["summary"] = loads(d.pop("summary_json", "{}"), {})
    scenarios = []
    for r in conn.execute("SELECT * FROM sleep_autonomy_execution_acceptance_scenarios WHERE acceptance_run_id=? ORDER BY scenario_key", (acceptance_run_id,)).fetchall():
        s = dict(r)
        s["checks"] = loads(s.pop("checks_json", "[]"), [])
        s["output"] = loads(s.pop("output_json", "{}"), {})
        scenarios.append(s)
    d["scenarios"] = scenarios
    return {"ok": True, "run": d}
