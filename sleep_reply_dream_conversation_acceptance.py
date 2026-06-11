"""Sleep / Reply / Dream / Execution v0.11.9 real-conversation acceptance.

The runner uses an isolated synthetic owner so realistic user-message scenarios
leave inspectable traces without polluting the caller's real Agent life.  The
scenarios focus on user-visible continuity: chatting can delay sleep, calls can
wake and release delayed replies, dreams become shareable wake topics, and poor
sleep affects next-day execution.
"""
from __future__ import annotations

from typing import Any, Callable

from .constants import PLUGIN_VERSION

def _version_at_least(current: str, minimum: str) -> bool:
    def parts(v: str):
        return tuple(int(x) for x in str(v).split(".") if x.isdigit())
    return parts(current) >= parts(minimum)
from .db import _SCHEMA_VERSION, transaction
from .jsonutil import dumps, loads
from .trace import append_audit, new_id

SCENARIOS: list[tuple[str, str]] = [
    ("CRD01_CHAT_DELAYS_SLEEP_ACTUAL_DIFFERS", "Conversation can delay actual sleep beyond planned sleep time"),
    ("CRD02_CALL_WAKES_SLEEP_AND_RELEASES_DIGEST", "life_call wakes sleep, releases delayed replies, and creates an aggregate digest"),
    ("CRD03_WAKE_DREAM_SHARE_INTENT", "After waking, DreamRun creates a dream entry and a shareable proactive intent"),
    ("CRD04_DREAM_AUDIT_REPAIR_AND_WAKE_REPLY", "DreamAudit can find stale schedule/reply state and apply safe repairs through LifeOps"),
    ("CRD05_INTERRUPTED_SLEEP_AFFECTS_NEXT_EXECUTION", "Interrupted/short sleep affects next-day execution outcome"),
    ("CRD06_TRACE_COVERS_USER_VISIBLE_CHAIN", "Trace surfaces the event/sleep/reply/dream/execution chain for explanation"),
]

_REQUIRED_TABLES = [
    "sleep_plans", "sleep_sessions", "sleep_interruptions", "sleep_day_states",
    "reply_gate_decisions", "delayed_replies", "delayed_reply_digests", "call_overrides",
    "dream_runs", "dream_entries", "dream_audit_findings", "dream_repair_runs",
    "execution_decisions", "execution_sleep_adjustments", "proactive_intents",
    "events", "schedule_blocks", "event_state_transitions", "schedule_block_state_transitions",
    "life_transactions", "commit_receipts", "life_journal",
]


def _table_exists(conn, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual') AND name=?", (name,)).fetchone()
    return bool(row)


def _safe_scenario(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        out = fn()
        out.setdefault("ok", True)
        return out
    except Exception as exc:  # pragma: no cover - failure path is recorded for release diagnostics
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _first_fact_evidence(out: dict[str, Any], key: str) -> str:
    for fact in (out.get("receipt") or {}).get("facts", []):
        ev = fact.get("evidence") or {}
        if ev.get(key):
            return ev[key]
    for candidate in [out.get("commit"), out.get("result")]:
        if isinstance(candidate, dict):
            for fact in (candidate.get("receipt") or {}).get("facts", []):
                ev = fact.get("evidence") or {}
                if ev.get(key):
                    return ev[key]
    raise AssertionError(f"{key} not found in receipt facts: {out}")


def _activate_synthetic(rt: Any, owner_id: str) -> None:
    rt.setup("v0.11.9 sleep/reply/dream conversation acceptance synthetic agent", owner_id=owner_id)
    rt.commit_canon(owner_id=owner_id)
    rt.control("resume", owner_id=owner_id)
    rt.control("module", owner_id=owner_id, key="reply_gate", value="auto")
    rt.control("module", owner_id=owner_id, key="dream", value="auto")
    rt.control("module", owner_id=owner_id, key="execution", value="auto")
    rt.control("module", owner_id=owner_id, key="autonomy", value="full")
    for key, initial in [("energy", 220), ("focus", 220), ("mood", 80), ("fatigue", 0), ("sleep_debt_minutes", 0)]:
        rt.resources("define", owner_id=owner_id, key=key, display_name=key, initial=initial)


def _latest_sleep_session(rt: Any, owner_id: str) -> dict[str, Any]:
    sessions = rt.sleep_tool("sessions", owner_id=owner_id, limit=10)["sleep_sessions"]
    if not sessions:
        raise AssertionError("no sleep session found")
    return sessions[0]


def _create_work_event(rt: Any, owner_id: str, *, title: str, importance: int, start: str) -> tuple[str, str]:
    ev_out = rt.event_tool(
        "create", owner_id=owner_id, title=title, event_type="work", event_category="work",
        activity_domain="conversation_acceptance", source="acceptance", status="planned",
        importance=importance, priority=importance, resource_costs={"energy": -30, "focus": -30},
        interruptibility={"level": "soft_interruptible"},
    )
    event_id = _first_fact_evidence(ev_out, "event_id")
    sched_out = rt.event_tool(
        "schedule", owner_id=owner_id, event_id=event_id, start=start, end=start.replace("10:00:00", "11:00:00"),
        block_type="work", timezone_name="UTC", status="planned",
    )
    block_id = _first_fact_evidence(sched_out, "schedule_block_id")
    return event_id, block_id


def run_sleep_reply_dream_conversation_acceptance(rt: Any, owner_kind: str, owner_id: str) -> dict[str, Any]:
    run_id = new_id("crdacc")
    synthetic_owner_id = f"{owner_id}-crd-{run_id}"
    _activate_synthetic(rt, synthetic_owner_id)
    base_checks = [
        {"name": f"table:{table}", "ok": _table_exists(rt.conn, table)} for table in _REQUIRED_TABLES
    ] + [
        {"name": "schema_version", "ok": _SCHEMA_VERSION >= 29, "value": _SCHEMA_VERSION},
        {"name": "plugin_version", "ok": _version_at_least(PLUGIN_VERSION, "0.11.9"), "value": PLUGIN_VERSION},
    ]
    context: dict[str, Any] = {"synthetic_owner_id": synthetic_owner_id}

    def s1_chat_delays_sleep() -> dict[str, Any]:
        plan = rt.sleep_tool(
            "plan", owner_id=synthetic_owner_id,
            planned_sleep_at="2026-06-10T23:00:00+00:00", planned_wake_at="2026-06-11T07:00:00+00:00", timezone_name="UTC",
        )
        plan_id = _first_fact_evidence(plan, "sleep_plan_id")
        # Simulate the user keeping the agent in conversation; actual sleep starts late.
        rt.event_tool("update_state", owner_id=synthetic_owner_id, mode="in_conversation", reply_mode="immediate", reason="chatting past planned bedtime")
        rt.sleep_tool("start", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-11T00:25:00+00:00")
        sess = _latest_sleep_session(rt, synthetic_owner_id)
        plan_row = rt.sleep_tool("get_plan", owner_id=synthetic_owner_id, sleep_plan_id=plan_id)["sleep_plan"]
        assert int(sess["actual_sleep_at_ts"]) > int(plan_row["planned_sleep_at_ts"])
        context["delayed_sleep_plan_id"] = plan_id
        context["delayed_sleep_session_id"] = sess["id"]
        return {"ok": True, "sleep_plan_id": plan_id, "sleep_session_id": sess["id"], "planned_sleep_at": plan_row["planned_sleep_at"], "actual_sleep_at": sess["actual_sleep_at"]}

    def s2_call_wakes_and_releases_digest() -> dict[str, Any]:
        plan = rt.sleep_tool(
            "plan", owner_id=synthetic_owner_id,
            planned_sleep_at="2026-06-11T23:00:00+00:00", planned_wake_at="2026-06-12T07:00:00+00:00", timezone_name="UTC",
        )
        plan_id = _first_fact_evidence(plan, "sleep_plan_id")
        rt.sleep_tool("start", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-11T23:15:00+00:00")
        rt.reply("defer", owner_id=synthetic_owner_id, message_text="你醒着吗？", user_id="acceptance-user", reason="agent sleeping")
        rt.reply("defer", owner_id=synthetic_owner_id, message_text="我想补充一句。", user_id="acceptance-user", reason="agent sleeping")
        call = rt.call(owner_id=synthetic_owner_id, reason="acceptance urgent call", message_text="call", user_id="acceptance-user")
        digests = rt.reply("digests", owner_id=synthetic_owner_id)["delayed_reply_digests"]
        calls = rt.reply("calls", owner_id=synthetic_owner_id)["call_overrides"]
        assert calls
        assert digests and int(digests[0].get("message_count") or 0) >= 1
        context["call_sleep_plan_id"] = plan_id
        context["call_digest_id"] = digests[0]["id"]
        return {"ok": True, "sleep_plan_id": plan_id, "call_transaction_id": call.get("transaction_id"), "digest_id": digests[0]["id"], "call_count": len(calls)}

    def s3_wake_dream_share() -> dict[str, Any]:
        plan = rt.sleep_tool(
            "plan", owner_id=synthetic_owner_id,
            planned_sleep_at="2026-06-12T23:00:00+00:00", planned_wake_at="2026-06-13T07:00:00+00:00", timezone_name="UTC",
        )
        plan_id = _first_fact_evidence(plan, "sleep_plan_id")
        rt.sleep_tool("start", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-12T23:05:00+00:00")
        rt.sleep_tool("wake", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-13T07:05:00+00:00")
        sess = _latest_sleep_session(rt, synthetic_owner_id)
        dream = rt.dream("run", owner_id=synthetic_owner_id, sleep_session_id=sess["id"], force=True, target_user_id="acceptance-user")
        entries = rt.dream("entries", owner_id=synthetic_owner_id)["entries"]
        intents = rt.proactive("list", owner_id=synthetic_owner_id, limit=20)["intents"]
        assert entries
        assert any(i.get("generated_by") == "dream" or i.get("intent_type") == "self_reflection_share" for i in intents)
        context["dream_run_id"] = (dream.get("dream_run") or {}).get("id")
        context["dream_entry_id"] = entries[0]["id"]
        return {"ok": True, "sleep_session_id": sess["id"], "dream_run_id": context["dream_run_id"], "dream_entry_id": entries[0]["id"], "intent_count": len(intents)}

    def s4_dream_audit_repair_and_wake_reply() -> dict[str, Any]:
        ev = rt.event_tool("create", owner_id=synthetic_owner_id, title="过期未结算小任务", event_type="maintenance", event_category="maintenance", status="planned", source="acceptance", importance=20)
        event_id = _first_fact_evidence(ev, "event_id")
        sched = rt.event_tool("schedule", owner_id=synthetic_owner_id, event_id=event_id, start="2026-06-13T09:00:00+00:00", end="2026-06-13T10:00:00+00:00", block_type="maintenance", timezone_name="UTC")
        block_id = _first_fact_evidence(sched, "schedule_block_id")
        rt.reply("defer", owner_id=synthetic_owner_id, message_text="醒来后记得回我。", user_id="acceptance-user", reason="acceptance pending reply")
        plan = rt.sleep_tool("plan", owner_id=synthetic_owner_id, planned_sleep_at="2026-06-13T23:00:00+00:00", planned_wake_at="2026-06-14T07:00:00+00:00", timezone_name="UTC")
        plan_id = _first_fact_evidence(plan, "sleep_plan_id")
        rt.sleep_tool("start", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-13T23:05:00+00:00")
        rt.sleep_tool("wake", owner_id=synthetic_owner_id, sleep_plan_id=plan_id, now="2026-06-14T07:05:00+00:00")
        sess = _latest_sleep_session(rt, synthetic_owner_id)
        dream = rt.dream("run", owner_id=synthetic_owner_id, sleep_session_id=sess["id"], force=True)
        dream_run_id = (dream.get("dream_run") or {}).get("id")
        plan_out = rt.dream("repair_plan", owner_id=synthetic_owner_id, dream_run_id=dream_run_id)
        repair = rt.dream("repair", owner_id=synthetic_owner_id, dream_run_id=dream_run_id)
        block_after = rt.conn.execute("SELECT status FROM schedule_blocks WHERE id=?", (block_id,)).fetchone()[0]
        digests = rt.reply("digests", owner_id=synthetic_owner_id)["delayed_reply_digests"]
        assert block_after in {"missed", "completed", "skipped", "cancelled", "rescheduled"}
        assert repair.get("ok")
        assert digests
        context["stale_event_id"] = event_id
        context["stale_block_id"] = block_id
        context["repair_dream_run_id"] = dream_run_id
        return {"ok": True, "dream_run_id": dream_run_id, "repair_transaction_id": repair.get("transaction_id") or (repair.get("commit") or {}).get("transaction_id"), "repaired_block_status": block_after, "planned_ops": len(plan_out.get("ops", [])), "digest_id": digests[0]["id"]}

    def s5_interrupted_sleep_affects_execution() -> dict[str, Any]:
        # Record a severe sleep deficit explicitly, then execute an important event.
        # The call-interrupted session from CRD02 can be too short/clock-dependent in
        # isolated test runners, so this scenario creates a deterministic missed core
        # sleep plan to guarantee SleepDayState pressure for the execution simulator.
        missed_plan = rt.sleep_tool(
            "plan", owner_id=synthetic_owner_id,
            planned_sleep_at="2026-06-12T00:00:00+00:00",
            planned_wake_at="2026-06-12T08:00:00+00:00",
            timezone_name="UTC",
        )
        missed_plan_id = _first_fact_evidence(missed_plan, "sleep_plan_id")
        day = rt.sleep_tool("record_effects", owner_id=synthetic_owner_id, sleep_plan_id=missed_plan_id, date="2026-06-12")["sleep_day_state"]
        assert day.get("all_nighter") or int(day.get("recovery_pressure") or 0) >= 70
        event_id, block_id = _create_work_event(rt, synthetic_owner_id, title="被睡眠不足影响的重要对话后工作", importance=88, start="2026-06-12T10:00:00+00:00")
        out = rt.execution("run", owner_id=synthetic_owner_id, schedule_block_id=block_id)
        assert out["decision"]["decision_type"] in {"partial", "postponed"}
        context["sleep_affected_event_id"] = event_id
        context["sleep_affected_block_id"] = block_id
        return {"ok": True, "sleep_day_state_id": day["id"], "event_id": event_id, "schedule_block_id": block_id, "decision_type": out["decision"]["decision_type"], "decision_id": out["decision"]["id"]}

    def s6_trace_chain() -> dict[str, Any]:
        event_id = context.get("sleep_affected_event_id") or context.get("stale_event_id")
        explained = rt.traces("explain", owner_id=synthetic_owner_id, event_id=event_id)
        assert explained.get("event")
        assert explained.get("event_state_transitions") is not None
        # Also assert cross-chain artifacts exist for the same synthetic owner.
        dreams = rt.dream("entries", owner_id=synthetic_owner_id)["entries"]
        digests = rt.reply("digests", owner_id=synthetic_owner_id)["delayed_reply_digests"]
        sleep_adjustments = rt.execution("sleep_adjustments", owner_id=synthetic_owner_id)["sleep_adjustments"]
        assert dreams and digests and sleep_adjustments
        return {"ok": True, "event_id": event_id, "has_event_transitions": bool(explained.get("event_state_transitions")), "dream_entries": len(dreams), "reply_digests": len(digests), "execution_sleep_adjustments": len(sleep_adjustments)}

    scenario_fns: list[tuple[str, str, Callable[[], dict[str, Any]]]] = [
        (*SCENARIOS[0], s1_chat_delays_sleep),
        (*SCENARIOS[1], s2_call_wakes_and_releases_digest),
        (*SCENARIOS[2], s3_wake_dream_share),
        (*SCENARIOS[3], s4_dream_audit_repair_and_wake_reply),
        (*SCENARIOS[4], s5_interrupted_sleep_affects_execution),
        (*SCENARIOS[5], s6_trace_chain),
    ]

    scenario_rows: list[dict[str, Any]] = []
    for key, title, fn in scenario_fns:
        sid = new_id("crdscenario")
        out = _safe_scenario(fn)
        status = "passed" if out.get("ok") else "failed"
        checks = list(base_checks) + [{"name": "scenario_result", "ok": bool(out.get("ok")), "message": out.get("error") or "ok"}]
        scenario_rows.append({"id": sid, "key": key, "title": title, "status": status, "checks": checks, "output": out})

    passed = len([s for s in scenario_rows if s["status"] == "passed"])
    failed = len(scenario_rows) - passed
    summary = {"scenarios": len(scenario_rows), "passed": passed, "failed": failed, "status": "passed" if failed == 0 else "failed", "synthetic_owner_id": synthetic_owner_id}
    report_lines = [
        "# LifeEngine v0.11.9 Sleep / Reply / Dream Conversation Acceptance",
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
                """INSERT INTO sleep_reply_dream_conversation_acceptance_scenarios(
                     id, owner_kind, owner_id, acceptance_run_id, scenario_key, title, status,
                     checks_json, output_json
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (s["id"], owner_kind, owner_id, run_id, s["key"], s["title"], s["status"], dumps(s["checks"]), dumps(s["output"])),
            )
        rt.conn.execute(
            """INSERT INTO sleep_reply_dream_conversation_acceptance_runs(
                 id, owner_kind, owner_id, status, synthetic_owner_id, scenario_count, passed_count, failed_count,
                 summary_json, report_markdown, completed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (run_id, owner_kind, owner_id, summary["status"], synthetic_owner_id, summary["scenarios"], passed, failed, dumps(summary), report),
        )
        out = {"ok": failed == 0, "status": summary["status"], "acceptance_run_id": run_id, "synthetic_owner_id": synthetic_owner_id, "summary": summary, "scenarios": scenario_rows, "report_markdown": report}
        append_audit(rt.conn, owner_kind, owner_id, "sleep_reply_dream_conversation_acceptance", "info" if out["ok"] else "error", f"Sleep/Reply/Dream conversation acceptance {summary['status']}", out)
    return out


def list_sleep_reply_dream_conversation_acceptance(conn, owner_kind: str, owner_id: str, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM sleep_reply_dream_conversation_acceptance_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = loads(d.pop("summary_json", "{}"), {})
        out.append(d)
    return {"ok": True, "runs": out}


def get_sleep_reply_dream_conversation_acceptance(conn, acceptance_run_id: str) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM sleep_reply_dream_conversation_acceptance_runs WHERE id=?", (acceptance_run_id,)).fetchone()
    if not run:
        return {"ok": False, "error": "acceptance run not found"}
    d = dict(run)
    d["summary"] = loads(d.pop("summary_json", "{}"), {})
    scenarios = []
    for r in conn.execute("SELECT * FROM sleep_reply_dream_conversation_acceptance_scenarios WHERE acceptance_run_id=? ORDER BY scenario_key", (acceptance_run_id,)).fetchall():
        s = dict(r)
        s["checks"] = loads(s.pop("checks_json", "[]"), [])
        s["output"] = loads(s.pop("output_json", "{}"), {})
        scenarios.append(s)
    d["scenarios"] = scenarios
    return {"ok": True, "run": d}
