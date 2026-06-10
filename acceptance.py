"""v0.99 acceptance scenario runner and report generator.

The acceptance suite is intentionally synthetic.  It exercises the full
LifeEngine closure on generated owners so a release candidate can be validated
without mutating the operator's real Agent/User life state.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .constants import PLUGIN_VERSION
from .db import _SCHEMA_VERSION
from .jsonutil import dumps
from .paths import exports_dir
from .trace import append_audit, new_id


def _now_ms() -> int:
    return int(time.perf_counter() * 1000)


def _check(name: str, ok: bool, message: str = "", **evidence: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "status": "ok" if ok else "failed", "message": message, **evidence}


def _status(checks: list[dict[str, Any]]) -> str:
    return "passed" if all(bool(c.get("ok")) for c in checks) else "failed"


def _owner(base_owner_id: str, run_id: str, suffix: str) -> str:
    short = run_id.replace("acceptance_", "")[:10]
    return f"{base_owner_id}__acceptance_{suffix}_{short}"


def _scenario_record(conn, owner_kind: str, owner_id: str, run_id: str, key: str, title: str,
                     checks: list[dict[str, Any]], output: dict[str, Any], duration_ms: int) -> dict[str, Any]:
    status = _status(checks)
    row_id = new_id("scenario")
    conn.execute(
        """INSERT INTO acceptance_scenario_runs(
               id, owner_kind, owner_id, acceptance_run_id, scenario_key, scenario_title,
               status, duration_ms, checks_json, output_json
             ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (row_id, owner_kind, owner_id, run_id, key, title, status, int(duration_ms), dumps(checks), dumps(output)),
    )
    return {"id": row_id, "key": key, "title": title, "status": status, "duration_ms": int(duration_ms), "checks": checks, "output": output}


def _run_scenario(conn, owner_kind: str, owner_id: str, run_id: str, key: str, title: str,
                  fn: Callable[[], tuple[list[dict[str, Any]], dict[str, Any]]]) -> dict[str, Any]:
    start = _now_ms()
    try:
        checks, output = fn()
    except Exception as exc:
        checks = [_check("scenario_exception", False, f"{type(exc).__name__}: {exc}")]
        output = {"error": f"{type(exc).__name__}: {exc}"}
    return _scenario_record(conn, owner_kind, owner_id, run_id, key, title, checks, output, _now_ms() - start)


def run_acceptance_suite(rt: Any, owner_kind: str = "agent", owner_id: str = "default-agent", *,
                         report_path: str | None = None, include_details: bool = True) -> dict[str, Any]:
    """Run v1.0-rc acceptance scenarios and persist a Markdown report."""
    run_id = new_id("acceptance")
    scenarios: list[dict[str, Any]] = []

    scenarios.append(_run_scenario(
        rt.conn, owner_kind, owner_id, run_id,
        "S01_SETUP_CANON_PAUSE_GATING",
        "Setup / Canon commit / pause-state mutation gating",
        lambda: _scenario_setup_canon_pause(rt, owner_id, run_id),
    ))
    scenarios.append(_run_scenario(
        rt.conn, owner_kind, owner_id, run_id,
        "S02_AGENT_GOAL_HEARTBEAT_EXECUTION",
        "Agent goal, resource, schedule, heartbeat execution, memory, diary, proactive",
        lambda: _scenario_agent_goal_heartbeat(rt, owner_id, run_id),
    ))
    scenarios.append(_run_scenario(
        rt.conn, owner_kind, owner_id, run_id,
        "S03_TRUTH_WEATHER_POSTPONE",
        "Canon TruthSource weather observation postpones an outdoor plan",
        lambda: _scenario_truth_weather(rt, owner_id, run_id),
    ))
    scenarios.append(_run_scenario(
        rt.conn, owner_kind, owner_id, run_id,
        "S04_USER_CONFIRMATION_POLICY",
        "User Life confirmation prevents narrative pollution",
        lambda: _scenario_user_confirmation(rt, owner_id, run_id),
    ))
    scenarios.append(_run_scenario(
        rt.conn, owner_kind, owner_id, run_id,
        "S05_RELEASE_READINESS_TRACE",
        "Doctor, trace verification, integration surface, API freeze, release readiness",
        lambda: _scenario_release_readiness(rt, owner_id, run_id),
    ))

    status = "passed" if all(s["status"] == "passed" for s in scenarios) else "failed"
    summary = {
        "run_id": run_id,
        "status": status,
        "plugin_version": PLUGIN_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "scenario_count": len(scenarios),
        "scenarios": len(scenarios),
        "passed": sum(1 for s in scenarios if s["status"] == "passed"),
        "failed": sum(1 for s in scenarios if s["status"] != "passed"),
    }
    checklist = build_v1_rc_checklist(status, scenarios)
    markdown = render_acceptance_report(summary, scenarios, checklist, include_details=include_details)
    path = Path(report_path).expanduser() if report_path else exports_dir() / "reports" / f"lifeengine_acceptance_{run_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")

    report_id = new_id("acceptreport")
    rt.conn.execute(
        """INSERT INTO acceptance_reports(
               id, owner_kind, owner_id, plugin_version, schema_version, acceptance_run_id,
               status, summary_json, report_markdown, report_path
             ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (report_id, owner_kind, owner_id, PLUGIN_VERSION, _SCHEMA_VERSION, run_id, status, dumps(summary), markdown, str(path)),
    )
    checklist_id = new_id("v1rc")
    rt.conn.execute(
        """INSERT INTO v1_rc_checklists(
               id, owner_kind, owner_id, plugin_version, schema_version, acceptance_report_id,
               status, checklist_json
             ) VALUES(?,?,?,?,?,?,?,?)""",
        (checklist_id, owner_kind, owner_id, PLUGIN_VERSION, _SCHEMA_VERSION, report_id, status, dumps(checklist)),
    )
    out = {
        "ok": status == "passed",
        "status": status,
        "acceptance_run_id": run_id,
        "acceptance_report_id": report_id,
        "v1_rc_checklist_id": checklist_id,
        "report_path": str(path),
        "summary": summary,
        "scenarios": scenarios,
        "checklist": checklist,
    }
    append_audit(rt.conn, owner_kind, owner_id, "life_acceptance_suite", "info" if out["ok"] else "error", f"LifeEngine acceptance suite {status}", out)
    return out


def list_acceptance_reports(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, owner_kind, owner_id, plugin_version, schema_version, acceptance_run_id, status, summary_json, report_path, created_at FROM acceptance_reports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "reports": [dict(r) for r in rows]}


def get_acceptance_report(conn, report_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM acceptance_reports WHERE id=?", (report_id,)).fetchone()
    return {"ok": bool(row), "report": dict(row) if row else None}


def list_acceptance_scenarios(conn, owner_kind: str, owner_id: str, *, acceptance_run_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    if acceptance_run_id:
        rows = conn.execute(
            "SELECT * FROM acceptance_scenario_runs WHERE owner_kind=? AND owner_id=? AND acceptance_run_id=? ORDER BY created_at, scenario_key LIMIT ?",
            (owner_kind, owner_id, acceptance_run_id, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM acceptance_scenario_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, int(limit)),
        ).fetchall()
    return {"ok": True, "scenarios": [dict(r) for r in rows]}


def latest_v1_rc_checklists(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM v1_rc_checklists WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "checklists": [dict(r) for r in rows]}


def _activate_agent(rt: Any, agent_id: str, statement: str) -> None:
    rt.setup(statement, "agent", agent_id)
    rt.commit_canon("agent", agent_id)
    rt.control("resume", "agent", agent_id)


def _activate_user(rt: Any, user_id: str, statement: str) -> None:
    rt.setup(statement, "user", user_id)
    rt.commit_canon("user", user_id)
    rt.control("resume", "user", user_id)


def _scenario_setup_canon_pause(rt: Any, base_owner_id: str, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    agent_id = _owner(base_owner_id, run_id, "setup")
    rt.setup("验收 Agent：setup 阶段只允许写 CanonDraft，不允许生活流水。", "agent", agent_id)
    blocked_error = ""
    try:
        rt.event_tool("create", "agent", agent_id, title="不该在 setup 期间发生的生活事件")
    except Exception as exc:
        blocked_error = str(exc)
    canon = rt.commit_canon("agent", agent_id)
    resumed = rt.control("resume", "agent", agent_id)
    paused = rt.control("pause", "agent", agent_id, reason="acceptance pause gate")
    pause_blocked = ""
    try:
        rt.resources("define", "agent", agent_id, key="energy", display_name="Energy", initial=50)
    except Exception as exc:
        pause_blocked = str(exc)
    checks = [
        _check("setup_mutation_blocked", bool(blocked_error), "LifeOps are blocked before active Canon", error=blocked_error),
        _check("canon_committed", bool(canon.get("ok") and canon.get("canon", {}).get("version")), "CanonDraft committed into a CanonVersion", version=(canon.get("canon") or {}).get("version")),
        _check("resume_active", (resumed.get("control") or {}).get("engine_state") == "active", "LifeEngine resumed after Canon commit", state=(resumed.get("control") or {}).get("engine_state")),
        _check("pause_mutation_blocked", bool(pause_blocked), "Paused state blocks durable LifeOps", error=pause_blocked),
        _check("paused_state", (paused.get("control") or {}).get("engine_state") == "paused", "Pause state persisted", state=(paused.get("control") or {}).get("engine_state")),
    ]
    return checks, {"agent_id": agent_id, "canon_version": (canon.get("canon") or {}).get("version"), "blocked_error": blocked_error, "pause_blocked_error": pause_blocked}


def _scenario_agent_goal_heartbeat(rt: Any, base_owner_id: str, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    agent_id = _owner(base_owner_id, run_id, "goal")
    _activate_agent(rt, agent_id, "验收 Agent：有目标、资源、日程、叙事执行、小发现、日记和主动意图。")
    rt.control("module", "agent", agent_id, key="autonomy", value="low_spontaneity")
    rt.control("module", "agent", agent_id, key="proactive", value="pending_only")
    rt.resources("define", "agent", agent_id, key="energy", display_name="Energy", resource_class="capacity", unit="points", initial=60)
    goal = rt.goals("create", "agent", agent_id, title="准备七月考试", goal_type="study", priority=85, session_id="accept", turn_id="goal")
    goal_id = goal["results"][0]["result"]["id"]
    event = rt.event_tool("create", "agent", agent_id, title="完成第一章复习", event_type="study", importance=85, source="agent_prediction", resource_costs={"energy": -5}, goal_id=goal_id, session_id="accept", turn_id="event")
    event_id = event["results"][0]["result"]["id"]
    block = rt.event_tool("schedule", "agent", agent_id, event_id=event_id, start="2026-06-07T10:00:00+00:00", end="2026-06-07T11:00:00+00:00", timezone_name="UTC", session_id="accept", turn_id="schedule")
    tick = rt.tick("agent", agent_id, now="2026-06-07T11:01:00+00:00", manual=True)
    completed_events = rt.event_tool("list", "agent", agent_id, status="completed")["events"]
    serendipity = rt.execution("serendipity", "agent", agent_id)["serendipity"]
    diary = rt.diary("write", "agent", agent_id, content="验收日记：我完成了第一章复习，也记录了复习时的小发现。", source_event_ids=[event_id], session_id="accept", turn_id="diary")
    proactive = rt.proactive("create", "agent", agent_id, summary="我完成了第一章复习，想告诉用户。", intent_type="report_progress", target_type="user", target_id="acceptance-user", privacy_level="safe_to_share", importance=80, relationship_relevance=80, session_id="accept", turn_id="pro")
    eval_out = rt.proactive("evaluate", "agent", agent_id, target_user_id="acceptance-user", manual=True, session_id="accept", turn_id="proeval")
    gate = rt.final_gate("check", "agent", agent_id, response_text="我完成了第一章复习，也记录了一个复习时发现的小点。", mode="strict", session_id="accept", turn_id="final")
    progress = rt.goals("progress", "agent", agent_id, goal_id=goal_id)
    tx_id = tick["completed"][0]["commit"]["transaction_id"] if tick.get("completed") else None
    explained = rt.traces("explain", "agent", agent_id, transaction_id=tx_id) if tx_id else {"facts": []}
    checks = [
        _check("goal_created", bool(goal_id), "Goal created through LifeOps", goal_id=goal_id),
        _check("scheduled_wake_job_completed", bool(tick.get("completed")), "Heartbeat processed due wake job", completed=len(tick.get("completed") or [])),
        _check("event_completed", any(e["id"] == event_id for e in completed_events), "Event completed by execution simulator", event_id=event_id),
        _check("serendipity_recorded", bool(serendipity), "Serendipity event recorded for narrative execution", count=len(serendipity)),
        _check("diary_committed", bool(diary.get("ok") and diary.get("receipt")), "Diary entry committed with receipt", transaction_id=diary.get("transaction_id")),
        _check("proactive_pending", bool(eval_out.get("ok") and (eval_out.get("receipt") or eval_out.get("results"))), "Proactive intent evaluated under pending_only policy", proactive_tx=eval_out.get("transaction_id")),
        _check("final_gate_supported", bool(gate.get("report", {}).get("ok")), "FinalGate supports committed life claim", report_id=(gate.get("report") or {}).get("report_id")),
        _check("trace_explain_has_facts", bool(explained.get("facts")), "Trace explain returns receipt facts for heartbeat transaction", transaction_id=tx_id),
        _check("goal_progress_query", bool(progress.get("ok")), "Goal progress remains queryable", progress=progress.get("progress")),
    ]
    return checks, {"agent_id": agent_id, "goal_id": goal_id, "event_id": event_id, "schedule_block_id": block["results"][0]["result"]["id"], "tick": _compact(tick), "final_gate_report": gate.get("report"), "trace_transaction_id": tx_id}


def _scenario_truth_weather(rt: Any, base_owner_id: str, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    agent_id = _owner(base_owner_id, run_id, "weather")
    _activate_agent(rt, agent_id, "验收 Agent：天气通过 TruthSource 影响外出计划。")
    observed = rt.truth("observe", "agent", agent_id, domain="weather", authority="external_tool", result={"condition": "rain", "summary": "afternoon rain"}, ttl_minutes=120)
    resolved = rt.truth("resolve", "agent", agent_id, domain="weather")
    event = rt.event_tool("create", "agent", agent_id, title="下午出门买裙子", event_type="purchase", importance=60, source="agent_prediction")
    event_id = event["results"][0]["result"]["id"]
    rt.event_tool("schedule", "agent", agent_id, event_id=event_id, start="2026-06-07T10:00:00+00:00", end="2026-06-07T11:00:00+00:00", timezone_name="UTC")
    tick = rt.tick("agent", agent_id, now="2026-06-07T11:02:00+00:00", manual=True)
    decision = (tick.get("completed") or [{}])[0].get("execution_decision") or {}
    events = rt.event_tool("list", "agent", agent_id)["events"]
    checks = [
        _check("weather_observed", bool(observed.get("ok")), "Weather observation recorded", truth_read=(observed.get("truth_read") or {}).get("id")),
        _check("weather_resolved_from_cache", bool(resolved.get("truth_read")), "Weather TruthSource resolves from cache", status=(resolved.get("truth_read") or {}).get("status")),
        _check("outdoor_plan_postponed", decision.get("decision_type") == "postponed", "Execution simulator postponed outdoor purchase because of bad weather", decision=decision),
        _check("event_rescheduled", any(e["id"] == event_id and e["status"] in {"scheduled", "rescheduled", "postponed"} for e in events), "Event remains tracked after weather-based reschedule", event_id=event_id),
    ]
    return checks, {"agent_id": agent_id, "event_id": event_id, "truth_read": observed.get("truth_read"), "decision": decision}


def _scenario_user_confirmation(rt: Any, base_owner_id: str, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    user_id = _owner(base_owner_id, run_id, "user")
    _activate_user(rt, user_id, "验收用户生活：事实必须由用户确认，不允许 Agent 编造。")
    blocked_error = ""
    try:
        rt.event_tool("create", "user", user_id, title="用户今天中午吃了咖喱饭", source="agent_retro_assertion")
    except Exception as exc:
        blocked_error = str(exc)
    proposed_ops = [{"type": "CREATE_EVENT", "payload": {"title": "用户明天晚上健身", "status": "planned", "source": "user_confirmed"}}]
    proposed = rt.confirmation("propose", "user", user_id, ops=proposed_ops, reason="验收：用户计划待确认")
    confirmed = rt.confirmation("confirm", "user", user_id, confirmation_id=proposed["confirmation"]["id"], note="用户确认")
    rejected = rt.confirmation("propose", "user", user_id, ops=[{"type": "CREATE_MEMORY", "payload": {"content": "用户喜欢凌晨跑步", "source": "user_confirmed"}}], reason="验收：待拒绝")
    rejected_out = rt.confirmation("reject", "user", user_id, confirmation_id=rejected["confirmation"]["id"], note="不是事实")
    events = rt.event_tool("list", "user", user_id)["events"]
    memories = rt.memory("search", "user", user_id, query="凌晨跑步")["memories"]
    checks = [
        _check("narrative_user_fact_blocked", bool(blocked_error), "Agent narrative source is rejected for User Life", error=blocked_error),
        _check("confirmation_confirmed", confirmed.get("confirmation", {}).get("status") == "confirmed" and bool(confirmed.get("commit")), "Confirmed user fact becomes LifeOps commit", confirmation_id=proposed["confirmation"]["id"]),
        _check("confirmed_event_exists", any(e["title"] == "用户明天晚上健身" for e in events), "Confirmed User Life event exists", event_count=len(events)),
        _check("rejection_does_not_commit", rejected_out.get("confirmation", {}).get("status") == "rejected" and not memories, "Rejected user memory did not enter User Life memory", memory_count=len(memories)),
    ]
    return checks, {"user_id": user_id, "confirmed_confirmation_id": proposed["confirmation"]["id"], "rejected_confirmation_id": rejected["confirmation"]["id"], "blocked_error": blocked_error}


def _scenario_release_readiness(rt: Any, base_owner_id: str, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    agent_id = _owner(base_owner_id, run_id, "release")
    _activate_agent(rt, agent_id, "验收 Agent：用于 doctor、trace、integration、API freeze 与 release readiness。")
    rt.memory("remember", "agent", agent_id, content="验收记忆：LifeEngine 可以被 sqlite-vec 和 FTS5 索引。", memory_type="episodic", source="system")
    doctor = rt.doctor("agent", agent_id, include_samples=True)
    verify = rt.traces("verify", "agent", agent_id)
    upgrade = rt.upgrade("check", "agent", agent_id, include_details=True)
    integration = rt.upgrade("integration_check", "agent", agent_id, include_details=True)
    freeze = rt.upgrade("api_freeze", "agent", agent_id)
    readiness = rt.upgrade("release_readiness", "agent", agent_id)
    checks = [
        _check("doctor_no_errors", bool(doctor.get("ok")), "Doctor completed without error-level failures", status=doctor.get("status")),
        _check("journal_hash_chain_ok", bool(verify.get("ok")), "Journal hash chain verifies", verify_message=verify.get("message")),
        _check("upgrade_check_ok", bool(upgrade.get("ok")), "Install/upgrade check passes", status=upgrade.get("status")),
        _check("integration_check_ok", bool(integration.get("ok")), "Hermes fake-context integration surface passes", integration_run_id=integration.get("integration_test_run_id")),
        _check("api_freeze_snapshot", bool(freeze.get("snapshot_id") and freeze.get("snapshot_sha256")), "API freeze snapshot recorded", snapshot_id=freeze.get("snapshot_id")),
        _check("release_readiness_ok", bool(readiness.get("ok")), "Release readiness aggregation passes", api_freeze_snapshot_id=readiness.get("api_freeze_snapshot_id")),
    ]
    return checks, {"agent_id": agent_id, "doctor_status": doctor.get("status"), "verify": verify, "upgrade_run_id": upgrade.get("upgrade_run_id"), "integration_test_run_id": integration.get("integration_test_run_id"), "freeze_snapshot_id": freeze.get("snapshot_id"), "release_readiness": {k: readiness.get(k) for k in ["status", "integration_test_run_id", "api_freeze_snapshot_id", "core_patch_id", "core_patch_path"]}}


def _compact(obj: Any, max_items: int = 5) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in {"checks", "scenarios", "report_markdown"}:
                continue
            out[k] = _compact(v, max_items)
        return out
    if isinstance(obj, list):
        return [_compact(v, max_items) for v in obj[:max_items]]
    return obj


def build_v1_rc_checklist(status: str, scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenario_map = {s["key"]: s for s in scenarios}
    return [
        {"item": "setup_state_blocks_mutations", "status": "passed" if scenario_map.get("S01_SETUP_CANON_PAUSE_GATING", {}).get("status") == "passed" else "failed", "evidence": "S01"},
        {"item": "agent_self_life_full_loop", "status": "passed" if scenario_map.get("S02_AGENT_GOAL_HEARTBEAT_EXECUTION", {}).get("status") == "passed" else "failed", "evidence": "S02"},
        {"item": "truth_source_affects_execution", "status": "passed" if scenario_map.get("S03_TRUTH_WEATHER_POSTPONE", {}).get("status") == "passed" else "failed", "evidence": "S03"},
        {"item": "user_life_confirmation_policy", "status": "passed" if scenario_map.get("S04_USER_CONFIRMATION_POLICY", {}).get("status") == "passed" else "failed", "evidence": "S04"},
        {"item": "release_readiness_surfaces", "status": "passed" if scenario_map.get("S05_RELEASE_READINESS_TRACE", {}).get("status") == "passed" else "failed", "evidence": "S05"},
        {"item": "overall_acceptance", "status": status, "evidence": "all scenarios"},
    ]


def render_acceptance_report(summary: dict[str, Any], scenarios: list[dict[str, Any]], checklist: list[dict[str, Any]], *, include_details: bool = True) -> str:
    lines: list[str] = []
    lines.append(f"# LifeEngine v{summary['plugin_version']} 验收报告")
    lines.append("")
    lines.append(f"- Acceptance run: `{summary['run_id']}`")
    lines.append(f"- Plugin version: `{summary['plugin_version']}`")
    lines.append(f"- Schema version: `{summary['schema_version']}`")
    lines.append(f"- Status: **{summary['status']}**")
    lines.append(f"- Scenarios: {summary['passed']}/{summary['scenario_count']} passed")
    lines.append("")
    lines.append("## v1.0-rc Checklist")
    lines.append("")
    lines.append("| Item | Status | Evidence |")
    lines.append("|---|---:|---|")
    for item in checklist:
        lines.append(f"| `{item['item']}` | **{item['status']}** | {item.get('evidence','')} |")
    lines.append("")
    lines.append("## Scenario Results")
    lines.append("")
    for s in scenarios:
        lines.append(f"### {s['key']} — {s['title']}")
        lines.append("")
        lines.append(f"Status: **{s['status']}**  ")
        lines.append(f"Duration: `{s['duration_ms']} ms`")
        lines.append("")
        lines.append("| Check | Status | Message |")
        lines.append("|---|---:|---|")
        for c in s["checks"]:
            msg = str(c.get("message", "")).replace("|", "\\|")
            lines.append(f"| `{c['name']}` | {'passed' if c.get('ok') else 'failed'} | {msg} |")
        if include_details:
            lines.append("")
            lines.append("<details><summary>Scenario output</summary>")
            lines.append("")
            lines.append("```json")
            lines.append(dumps(_compact(s.get("output"), 8)))
            lines.append("```")
            lines.append("")
            lines.append("</details>")
        lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    if summary["status"] == "passed":
        lines.append(f"LifeEngine v{PLUGIN_VERSION} 通过 v1.0-rc 验收场景。当前建议进入 v1.0-rc 冻结阶段，只接受阻断性 bug fix。")
    else:
        lines.append(f"LifeEngine v{PLUGIN_VERSION} 未完全通过验收。进入 v1.0-rc 前应先修复失败场景。")
    lines.append("")
    return "\n".join(lines)
