"""Human Review UX aggregation for LifeEngine v0.11.12.

This module is deliberately read-mostly and human-facing.  It gathers the many
advanced LifeEngine queues (sleep debt, delayed replies, dream share intents,
FinalGate advisory, proactive outbox, user confirmations, policy conflicts, and
Doctor warnings) into a single review page.  It does not create narrative life
facts and it does not advance the agent's life.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_audit, append_journal, new_id
from .time_utils import now_iso
from .sleep_effects import get_sleep_day_state
from .sleep_reply_dream_policy import get_policy, validate_policy, explain_policy


def _row_dict(row) -> dict[str, Any] | None:
    return dict(row) if row else None


def _count(conn, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] if row else 0)


def _severity_rank(sev: str) -> int:
    return {"error": 0, "warning": 1, "action": 2, "info": 3}.get(str(sev or "info"), 3)


def _item(item_type: str, severity: str, title: str, message: str, *, source_table: str | None = None,
          source_id: str | None = None, action_hint: dict[str, Any] | None = None, section: str | None = None) -> dict[str, Any]:
    return {
        "item_type": item_type,
        "severity": severity,
        "title": title,
        "message": message,
        "source_table": source_table,
        "source_id": source_id,
        "action_hint": action_hint or {},
        "section": section or item_type,
    }


def _latest_final_gate_reports(conn, owner_kind: str, owner_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM final_gate_reports
             WHERE owner_kind=? AND owner_id=? AND status IN ('advisory','blocked','repair')
             ORDER BY created_at DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ["claims_json", "unsupported_json", "supported_json", "suggested_ops_json", "repair_json"]:
            if k in d:
                d[k[:-5] if k.endswith("_json") else k] = loads(d.pop(k), [] if k != "repair_json" else {})
        out.append(d)
    return out


def _pending_final_gate_feedback(conn, owner_kind: str, owner_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM final_gate_feedback_queue
             WHERE owner_kind=? AND owner_id=? AND status='pending'
             ORDER BY created_at DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _pending_confirmations(conn, owner_kind: str, owner_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM user_confirmations
             WHERE owner_kind=? AND owner_id=? AND status='pending'
             ORDER BY created_at DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
        out.append(d)
    return out


def _pending_delayed(conn, owner_kind: str, owner_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM delayed_replies
             WHERE owner_kind=? AND owner_id=? AND status='pending'
             ORDER BY queued_at DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = loads(d.pop("metadata_json"), {})
        out.append(d)
    return out


def _recent_digests(conn, owner_kind: str, owner_id: str, limit: int = 3) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM delayed_reply_digests
             WHERE owner_kind=? AND owner_id=?
             ORDER BY created_at DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["delayed_reply_ids"] = loads(d.pop("delayed_reply_ids_json"), [])
        d["metadata"] = loads(d.pop("metadata_json"), {})
        out.append(d)
    return out


def _open_dream_findings(conn, owner_kind: str, owner_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM dream_audit_findings
             WHERE owner_kind=? AND owner_id=? AND status IN ('open','planned')
             ORDER BY created_at DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
        d["metadata"] = loads(d.pop("metadata_json"), {})
        out.append(d)
    return out


def _recent_dream_entries(conn, owner_kind: str, owner_id: str, limit: int = 3) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM dream_entries
             WHERE owner_kind=? AND owner_id=?
             ORDER BY created_at DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ["symbols_json", "source_memory_ids_json", "source_event_ids_json", "source_goal_ids_json"]:
            if k in d:
                d[k[:-5]] = loads(d.pop(k), [])
        out.append(d)
    return out


def _proactive(conn, owner_kind: str, owner_id: str, limit: int = 5) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if owner_kind != "agent":
        return [], []
    intents = [dict(r) for r in conn.execute(
        """SELECT * FROM proactive_intents
             WHERE agent_id=? AND status IN ('generated','queued')
             ORDER BY importance DESC, created_at DESC LIMIT ?""",
        (owner_id, int(limit)),
    ).fetchall()]
    outbox = [dict(r) for r in conn.execute(
        """SELECT * FROM proactive_outbox
             WHERE agent_id=? AND status IN ('drafted','queued')
             ORDER BY created_at DESC LIMIT ?""",
        (owner_id, int(limit)),
    ).fetchall()]
    return intents, outbox


def _doctor_summary(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    from .doctor import run_doctor

    report = run_doctor(conn, owner_kind, owner_id, write_audit=False)
    checks = report.get("checks", {}) if isinstance(report, dict) else {}
    issues = []
    for name, chk in (checks.items() if isinstance(checks, dict) else []):
        if not chk.get("ok", True):
            issues.append({"check": name, "severity": chk.get("severity") or chk.get("status") or "warning", "message": chk.get("message", "")})
    return {"ok": not issues, "issue_count": len(issues), "issues": issues[:10]}


def build_human_review(conn, owner_kind: str, owner_id: str, *, include_doctor: bool = True,
                       limit: int = 5, persist: bool = True, source: str = "human_review") -> dict[str, Any]:
    """Build and optionally persist a one-page human review."""
    items: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"owner_kind": owner_kind, "owner_id": owner_id}

    control = _row_dict(conn.execute("SELECT * FROM controls WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)).fetchone())
    realtime = _row_dict(conn.execute("SELECT * FROM agent_realtime_state WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)).fetchone()) or {}
    day = get_sleep_day_state(conn, owner_kind, owner_id)
    policy = get_policy(conn, owner_kind, owner_id, create=True)
    policy_validation = validate_policy(policy.get("effective_policy") or {})

    summary["engine_state"] = (control or {}).get("engine_state") or "unknown"
    summary["realtime_mode"] = realtime.get("mode")
    if day:
        summary["sleep"] = {
            "date": day.get("date_key"),
            "sleep_debt_minutes": day.get("cumulative_sleep_debt_minutes"),
            "recovery_pressure": day.get("recovery_pressure"),
            "all_nighter": day.get("all_nighter"),
            "nap_recommended": day.get("nap_recommended"),
        }
        if day.get("all_nighter") or int(day.get("recovery_pressure") or 0) >= 60 or day.get("nap_recommended"):
            items.append(_item(
                "sleep_state", "warning", "睡眠债 / 恢复压力需要注意",
                f"睡眠债 {day.get('cumulative_sleep_debt_minutes')} 分钟，恢复压力 {day.get('recovery_pressure')}。",
                source_table="sleep_day_states", source_id=day.get("id"), section="sleep",
                action_hint={"tool": "life_sleep", "action": "recovery_plan"},
            ))

    if policy_validation.get("conflict_count"):
        for c in policy_validation.get("conflicts", [])[:limit]:
            items.append(_item("policy_conflict", "error", "策略冲突", c.get("message", "策略冲突"), section="policy", action_hint={"tool": "life_policy", "action": "conflicts", "suggested_patch": c.get("suggested_patch", {})}))
    for w in policy_validation.get("warnings", [])[:limit]:
        items.append(_item("policy_warning", "warning", "策略提醒", w.get("message", "策略提醒"), section="policy", action_hint={"tool": "life_policy", "action": "suggestions"}))

    confirmations = _pending_confirmations(conn, owner_kind, owner_id, limit)
    for c in confirmations:
        items.append(_item("user_confirmation", "action", "有待确认的用户侧 Life 写入", c.get("reason") or "需要用户确认", source_table="user_confirmations", source_id=c.get("id"), section="confirmations", action_hint={"tool": "life_confirmation", "action": "confirm/reject", "confirmation_id": c.get("id")}))

    delayed = _pending_delayed(conn, owner_kind, owner_id, limit)
    for d in delayed:
        items.append(_item("delayed_reply", "action", "有延迟回复待处理", d.get("message_preview") or d.get("message_text") or "待回复消息", source_table="delayed_replies", source_id=d.get("id"), section="reply", action_hint={"tool": "life_reply", "action": "release"}))

    digests = _recent_digests(conn, owner_kind, owner_id, limit=3)
    if digests:
        summary["recent_reply_digest"] = digests[0].get("summary_text")

    fg_feedback = _pending_final_gate_feedback(conn, owner_kind, owner_id, limit)
    for f in fg_feedback:
        items.append(_item("final_gate_feedback", "info", "FinalGate 给 Agent 的内部提醒", f.get("message") or "有内部审计反馈", source_table="final_gate_feedback_queue", source_id=f.get("id"), section="final_gate", action_hint={"tool": "life_commit", "note": "如需成为事实，请提交 LifeOps；否则用意图/计划语气表达。"}))

    fg_reports = _latest_final_gate_reports(conn, owner_kind, owner_id, limit=3)
    if fg_reports and not fg_feedback:
        for r in fg_reports[:2]:
            if r.get("status") in {"advisory", "blocked"}:
                items.append(_item("final_gate_report", "info", "最近有 FinalGate advisory", f"{len(r.get('unsupported') or [])} 条 unsupported claim。", source_table="final_gate_reports", source_id=r.get("id"), section="final_gate", action_hint={"tool": "life_final_gate", "action": "get", "report_id": r.get("id")}))

    findings = _open_dream_findings(conn, owner_kind, owner_id, limit)
    for f in findings:
        sev = "warning" if f.get("severity") in {"warning", "error"} else "info"
        items.append(_item("dream_audit_finding", sev, "DreamAudit 发现待处理项", f.get("message") or f.get("finding_type") or "DreamAudit finding", source_table="dream_audit_findings", source_id=f.get("id"), section="dream", action_hint={"tool": "life_dream", "action": "repair_plan/repair", "dream_run_id": f.get("dream_run_id")}))

    dream_entries = _recent_dream_entries(conn, owner_kind, owner_id, limit=3)
    if dream_entries:
        latest = dream_entries[0]
        summary["latest_dream"] = {"id": latest.get("id"), "summary": latest.get("summary"), "share_text": latest.get("share_text")}

    proactive_intents, outbox = _proactive(conn, owner_kind, owner_id, limit)
    for p in proactive_intents:
        items.append(_item("proactive_intent", "action" if p.get("target_type") != "self_journal" else "info", "Agent 有想说的话", p.get("summary") or p.get("intent_type") or "proactive intent", source_table="proactive_intents", source_id=p.get("id"), section="proactive", action_hint={"tool": "life_proactive", "action": "evaluate", "intent_id": p.get("id")}))
    for o in outbox:
        items.append(_item("proactive_outbox", "action", "主动消息在 outbox 等待处理", (o.get("draft_text") or "")[:220], source_table="proactive_outbox", source_id=o.get("id"), section="proactive", action_hint={"tool": "life_proactive", "action": "send/suppress", "outbox_id": o.get("id")}))

    if include_doctor:
        doc = _doctor_summary(conn, owner_kind, owner_id)
        summary["doctor"] = {"ok": doc.get("ok"), "issue_count": doc.get("issue_count")}
        for issue in doc.get("issues", [])[:limit]:
            sev = "error" if issue.get("severity") == "error" else "warning"
            items.append(_item("doctor_warning", sev, f"Doctor: {issue.get('check')}", issue.get("message") or "doctor warning", section="doctor", action_hint={"command": "/life doctor"}))

    # Deduplicate repeated source rows and sort by urgency.
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for it in items:
        key = (it.get("item_type"), it.get("source_id"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    items = sorted(deduped, key=lambda x: (_severity_rank(x.get("severity", "info")), x.get("section", "")))

    counts: dict[str, int] = {}
    severities: dict[str, int] = {}
    for it in items:
        counts[it["section"]] = counts.get(it["section"], 0) + 1
        severities[it["severity"]] = severities.get(it["severity"], 0) + 1
    summary["counts"] = counts
    summary["severities"] = severities
    summary["policy"] = {"profile": (policy.get("effective_policy") or {}).get("profile"), "conflicts": policy_validation.get("conflict_count", 0), "warnings": policy_validation.get("warning_count", 0)}

    # v0.11.18: surface managed-review observability in the human review without
    # running the full report builder (avoid recursive review generation).
    try:
        action_policy = get_review_action_policy(conn, owner_kind, owner_id, create=True).get("policy") or {}
        managed_state = get_managed_review_loop_state(conn, owner_kind, owner_id)
        recent_managed_runs = list_managed_review_loop_runs(conn, owner_kind, owner_id, limit=1)
        latest_acceptance = _latest_managed_acceptance(conn, owner_kind, owner_id)
        latest_stress = _latest_managed_stress(conn, owner_kind, owner_id)
        summary["managed_review"] = {
            "enabled": bool(action_policy.get("allow_agent_managed_loop")),
            "today": {"runs": managed_state.get("run_count"), "actions": managed_state.get("action_count"), "failures": managed_state.get("failure_count")},
            "latest_run_status": (recent_managed_runs[0].get("status") if recent_managed_runs else None),
            "acceptance_status": latest_acceptance.get("status"),
            "stress_status": latest_stress.get("status"),
        }
        if action_policy.get("allow_agent_managed_loop") and latest_acceptance.get("status") != "passed":
            items.append(_item("managed_review_observability", "warning", "Managed Review 尚未通过验收", f"latest acceptance={latest_acceptance.get('status')}", section="review", action_hint={"tool": "life_review", "action": "managed_acceptance"}))
        if action_policy.get("allow_agent_managed_loop") and latest_stress.get("status") not in {"passed", "completed"}:
            items.append(_item("managed_review_observability", "warning", "Managed Review 尚未通过压力测试", f"latest stress={latest_stress.get('status')}", section="review", action_hint={"tool": "life_review", "action": "managed_stress"}))
        if int(managed_state.get("failure_count") or 0) > 0:
            items.append(_item("managed_review_observability", "warning", "Managed Review 今日有失败记录", f"failure_count={managed_state.get('failure_count')}", section="review", action_hint={"tool": "life_review", "action": "managed_observability"}))
    except Exception:
        pass

    run_id = new_id("review")
    if persist:
        # Assign item ids before rendering so the human-facing /life review page
        # can show the exact id needed for preview/apply/dismiss commands.
        for it in items:
            it["id"] = new_id("reviewitem")
    rendered = render_human_review(summary, items)
    if persist:
        conn.execute(
            """INSERT INTO human_review_runs(id, owner_kind, owner_id, status, severity, summary_json, section_counts_json, item_count, rendered_text)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, owner_kind, owner_id, "created", _overall_severity(items), dumps(summary), dumps(counts), len(items), rendered),
        )
        for it in items:
            conn.execute(
                """INSERT INTO human_review_items(id, owner_kind, owner_id, review_run_id, item_type, severity, title, message, source_table, source_id, action_hint_json, status)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (it.get("id"), owner_kind, owner_id, run_id, it.get("item_type"), it.get("severity"), it.get("title"), it.get("message"), it.get("source_table"), it.get("source_id"), dumps(it.get("action_hint") or {}), "open"),
            )
        append_audit(conn, owner_kind, owner_id, "human_review_created", "info", "Human review generated", {"review_run_id": run_id, "item_count": len(items), "counts": counts})
    return {"ok": True, "review_run_id": run_id, "summary": summary, "items": items, "rendered": rendered}


def _overall_severity(items: list[dict[str, Any]]) -> str:
    if any(i.get("severity") == "error" for i in items):
        return "error"
    if any(i.get("severity") == "warning" for i in items):
        return "warning"
    if any(i.get("severity") == "action" for i in items):
        return "action"
    return "ok"


def render_human_review(summary: dict[str, Any], items: list[dict[str, Any]]) -> str:
    lines = ["LifeEngine Review", "================="]
    lines.append(f"状态：{summary.get('engine_state')} / realtime={summary.get('realtime_mode')}")
    sleep = summary.get("sleep") or {}
    if sleep:
        lines.append(f"睡眠：睡眠债 {sleep.get('sleep_debt_minutes')} 分钟，恢复压力 {sleep.get('recovery_pressure')}，通宵={sleep.get('all_nighter')}，建议补觉={sleep.get('nap_recommended')}")
    pol = summary.get("policy") or {}
    lines.append(f"策略：{pol.get('profile')}，冲突 {pol.get('conflicts', 0)}，提醒 {pol.get('warnings', 0)}")
    managed = summary.get("managed_review") or {}
    if managed:
        today = managed.get("today") or {}
        lines.append(f"Managed Review：enabled={managed.get('enabled')} today runs={today.get('runs', 0)} actions={today.get('actions', 0)} failures={today.get('failures', 0)} acceptance={managed.get('acceptance_status')} stress={managed.get('stress_status')}")
    if summary.get("latest_dream"):
        ld = summary["latest_dream"]
        lines.append(f"最近梦：{(ld.get('summary') or ld.get('share_text') or '')[:160]}")
    if summary.get("recent_reply_digest"):
        lines.append(f"最近延迟回复摘要：{summary.get('recent_reply_digest')[:180]}")
    if summary.get("doctor"):
        lines.append(f"Doctor：{'ok' if summary['doctor'].get('ok') else '有提醒'}，issues={summary['doctor'].get('issue_count')}")
    lines.append("")
    if not items:
        lines.append("没有需要人类处理的项目。")
    else:
        lines.append("待处理 / 建议：")
        for idx, it in enumerate(items[:12], start=1):
            item_id = it.get("id") or "未持久化"
            lines.append(f"{idx}. id={item_id} [{it.get('severity')}] {it.get('title')} — {it.get('message')}")
            hint = it.get("action_hint") or {}
            if hint:
                if hint.get("command"):
                    lines.append(f"   建议：{hint.get('command')}")
                elif hint.get("tool"):
                    lines.append(f"   建议工具：{hint.get('tool')} action={hint.get('action')}")
            lines.append(f"   操作：/life review preview {item_id}；执行安全项：/life review apply {item_id}；忽略：/life review dismiss {item_id}")
    lines.append("")
    lines.append("常用：/life call 立刻叫醒；/life dream 查看梦；/life policy conflicts 查策略；/life doctor 深度检查；/life advanced 看高级命令。")
    return "\n".join(lines)


def list_review_runs(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM human_review_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = loads(d.pop("summary_json"), {})
        d["section_counts"] = loads(d.pop("section_counts_json"), {})
        out.append(d)
    return out


def get_review_run(conn, review_run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM human_review_runs WHERE id=?", (review_run_id,)).fetchone()
    if not row:
        return None
    run = dict(row)
    run["summary"] = loads(run.pop("summary_json"), {})
    run["section_counts"] = loads(run.pop("section_counts_json"), {})
    items = []
    for r in conn.execute("SELECT * FROM human_review_items WHERE review_run_id=? ORDER BY created_at", (review_run_id,)).fetchall():
        d = dict(r)
        d["action_hint"] = loads(d.pop("action_hint_json"), {})
        items.append(d)
    run["items"] = items
    return run


def dismiss_review_item(conn, owner_kind: str, owner_id: str, item_id: str, *, reason: str = "dismissed") -> dict[str, Any]:
    row = conn.execute("SELECT * FROM human_review_items WHERE id=? AND owner_kind=? AND owner_id=?", (item_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise ValueError(f"review item not found: {item_id}")
    conn.execute("UPDATE human_review_items SET status='dismissed', resolved_at=datetime('now') WHERE id=?", (item_id,))
    append_audit(conn, owner_kind, owner_id, "human_review_item_dismissed", "info", reason, {"item_id": item_id})
    return dict(conn.execute("SELECT * FROM human_review_items WHERE id=?", (item_id,)).fetchone())

# ----- Review action application (v0.11.13) -------------------------------

def get_review_item(conn, owner_kind: str, owner_id: str, item_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_items WHERE id=? AND owner_kind=? AND owner_id=?",
        (item_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"review item not found: {item_id}")
    d = dict(row)
    d["action_hint"] = loads(d.pop("action_hint_json"), {})
    return d


def list_review_action_runs(conn, owner_kind: str, owner_id: str, *, item_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if item_id:
        rows = conn.execute(
            "SELECT * FROM human_review_action_runs WHERE owner_kind=? AND owner_id=? AND item_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, item_id, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM human_review_action_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, int(limit)),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ["input_json", "plan_json", "output_json"]:
            d[k[:-5]] = loads(d.pop(k), {} if k != "plan_json" else {})
        out.append(d)
    return out


def get_review_action_run(conn, owner_kind: str, owner_id: str, action_run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_action_runs WHERE id=? AND owner_kind=? AND owner_id=?",
        (action_run_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"review action run not found: {action_run_id}")
    d = dict(row)
    for k in ["input_json", "plan_json", "output_json"]:
        d[k[:-5]] = loads(d.pop(k), {})
    return d


def plan_review_item_action(conn, owner_kind: str, owner_id: str, item_id: str, *, choice: str | None = None) -> dict[str, Any]:
    """Return a safe action plan for a review item without mutating state."""
    item = get_review_item(conn, owner_kind, owner_id, item_id)
    hint = item.get("action_hint") or {}
    item_type = item.get("item_type")
    choice = (choice or "auto").strip().lower()
    plan: dict[str, Any] = {
        "ok": True,
        "item": item,
        "item_type": item_type,
        "choice": choice,
        "supported": True,
        "safe_auto": False,
        "application_type": "noop",
        "requires_choice": False,
        "message": "No automatic action is available for this review item.",
    }
    if item.get("status") not in {"open", "created", "pending", "action"}:
        plan.update({"supported": False, "message": f"Review item status is not open: {item.get('status')}"})
        return plan
    if item_type == "sleep_state":
        plan.update({"application_type": "direct", "tool": "life_sleep", "action": "recovery_plan", "safe_auto": True, "message": "Create a recovery sleep plan if sleep pressure is high."})
    elif item_type == "delayed_reply":
        plan.update({"application_type": "lifeops", "tool": "life_reply", "action": "release", "safe_auto": True, "ops": [{"type": "RELEASE_DELAYED_REPLIES", "payload": {"reason": "released from /life review action", "source": "life_review_action", "limit": 1}}], "message": "Release pending delayed replies and create a digest."})
    elif item_type == "dream_audit_finding":
        plan.update({"application_type": "dream_repair", "tool": "life_dream", "action": "repair", "safe_auto": True, "dream_run_id": hint.get("dream_run_id"), "finding_id": item.get("source_id"), "message": "Apply DreamAudit safe repair LifeOps for this finding."})
    elif item_type == "proactive_intent":
        plan.update({"application_type": "lifeops", "tool": "life_proactive", "action": "evaluate", "safe_auto": True, "ops": [{"type": "EVALUATE_PROACTIVE_INTENT", "payload": {"intent_id": hint.get("intent_id") or item.get("source_id"), "manual": True, "source": "life_review_action"}}], "message": "Evaluate this proactive intent against delivery policy."})
    elif item_type == "proactive_outbox":
        if choice not in {"send", "suppress"}:
            plan.update({"application_type": "manual_choice", "requires_choice": True, "safe_auto": False, "choices": ["send", "suppress"], "message": "Choose send or suppress for proactive outbox items."})
        elif choice == "send":
            plan.update({"application_type": "lifeops", "tool": "life_proactive", "action": "send", "safe_auto": False, "ops": [{"type": "MARK_PROACTIVE_SENT", "payload": {"outbox_id": hint.get("outbox_id") or item.get("source_id"), "manual": True, "source": "life_review_action"}}], "message": "Mark this outbox message as sent."})
        else:
            plan.update({"application_type": "lifeops", "tool": "life_proactive", "action": "suppress", "safe_auto": False, "ops": [{"type": "SUPPRESS_PROACTIVE_INTENT", "payload": {"intent_id": hint.get("intent_id") or item.get("source_id"), "reason": "suppressed from /life review", "source": "life_review_action"}}], "message": "Suppress the related proactive intent."})
    elif item_type == "user_confirmation":
        if choice not in {"confirm", "reject"}:
            plan.update({"application_type": "manual_choice", "requires_choice": True, "choices": ["confirm", "reject"], "safe_auto": False, "message": "Choose confirm or reject for user-life confirmation items."})
        else:
            plan.update({"application_type": "confirmation", "tool": "life_confirmation", "action": choice, "confirmation_id": hint.get("confirmation_id") or item.get("source_id"), "safe_auto": False, "message": f"{choice} this user-life pending confirmation."})
    elif item_type == "policy_conflict":
        patch = hint.get("suggested_patch") or {}
        if patch:
            plan.update({"application_type": "policy_patch", "tool": "life_policy", "action": "patch", "safe_auto": False, "policy_patch": patch, "message": "Apply the suggested policy patch for this conflict."})
        else:
            plan.update({"application_type": "manual_review", "requires_choice": True, "safe_auto": False, "message": "Policy conflict has no safe automatic patch; review /life policy conflicts."})
    elif item_type == "policy_warning":
        plan.update({"application_type": "policy_suggestions", "tool": "life_policy", "action": "suggestions", "safe_auto": True, "message": "Record policy suggestions for this warning."})
    elif item_type in {"final_gate_feedback", "final_gate_report", "doctor_warning"}:
        plan.update({"application_type": "manual_review", "supported": True, "safe_auto": False, "message": "This item is diagnostic. Review trace/doctor output; no automatic state change is applied."})
    return plan


def record_review_action_run(conn, owner_kind: str, owner_id: str, *, item_id: str | None, review_run_id: str | None,
                             mode: str, status: str, input_obj: dict[str, Any] | None = None,
                             plan: dict[str, Any] | None = None, output: dict[str, Any] | None = None,
                             transaction_id: str | None = None, receipt_id: str | None = None,
                             error: str | None = None) -> dict[str, Any]:
    action_run_id = new_id("reviewact")
    conn.execute(
        """INSERT INTO human_review_action_runs(
             id, owner_kind, owner_id, item_id, review_run_id, mode, status, input_json, plan_json,
             output_json, transaction_id, receipt_id, error, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ? IN ('applied','planned','noop','failed','needs_choice','manual') THEN datetime('now') ELSE NULL END)""",
        (action_run_id, owner_kind, owner_id, item_id, review_run_id, mode, status, dumps(input_obj or {}), dumps(plan or {}), dumps(output or {}), transaction_id, receipt_id, error, status),
    )
    append_audit(conn, owner_kind, owner_id, "human_review_action_run", "info" if status not in {"failed"} else "error", f"Review action {status}", {"action_run_id": action_run_id, "item_id": item_id, "mode": mode, "transaction_id": transaction_id, "receipt_id": receipt_id, "error": error})
    return get_review_action_run(conn, owner_kind, owner_id, action_run_id)


def mark_review_item_resolved(conn, owner_kind: str, owner_id: str, item_id: str, *, action_run_id: str | None = None, status: str = "applied") -> None:
    conn.execute(
        "UPDATE human_review_items SET status=?, resolved_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?",
        (status, item_id, owner_kind, owner_id),
    )
    append_journal(conn, owner_kind, owner_id, "human_review_item_resolved", {"item_id": item_id, "action_run_id": action_run_id, "status": status}, "life_review_action")

# ----- Review action policy and batch application (v0.11.14) --------------

DEFAULT_REVIEW_ACTION_POLICY: dict[str, Any] = {
    "mode": "manual",
    "allow_safe_batch": True,
    "allow_agent_safe_apply": True,
    "max_batch_items": 10,
    "default_safe_only": True,
    "safe_item_types": [
        "sleep_state",
        "delayed_reply",
        "dream_audit_finding",
        "proactive_intent",
        "policy_warning",
    ],
    "manual_choice_item_types": [
        "user_confirmation",
        "proactive_outbox",
        "policy_conflict",
    ],
    "deny_item_types": [
        "doctor_warning",
        "final_gate_feedback",
        "final_gate_report",
    ],
    "safe_sections": ["sleep", "reply", "dream", "proactive", "policy"],
    "require_dry_run_first": False,
    "allow_policy_patch": False,
    "allow_safe_undo": True,
    "max_undo_items": 10,
    "allow_agent_managed_loop": False,
    "agent_managed_sections": ["sleep", "reply", "dream", "proactive", "policy"],
    "agent_managed_daily_action_limit": 5,
    "agent_managed_failure_budget": 2,
    "agent_managed_min_minutes_between_runs": 20,
    "agent_managed_safe_only": True,
    "agent_managed_trigger_sources": ["heartbeat"],
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalize_review_action_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    policy = _deep_merge(DEFAULT_REVIEW_ACTION_POLICY, raw or {})
    # Defensive normalization; keep everything serializable and predictable.
    for key in ["safe_item_types", "manual_choice_item_types", "deny_item_types", "safe_sections", "agent_managed_sections", "agent_managed_trigger_sources"]:
        value = policy.get(key)
        if not isinstance(value, list):
            policy[key] = list(DEFAULT_REVIEW_ACTION_POLICY[key])
        else:
            policy[key] = [str(x) for x in value]
    policy["max_batch_items"] = max(1, min(int(policy.get("max_batch_items") or 10), 100))
    policy["allow_safe_batch"] = bool(policy.get("allow_safe_batch", True))
    policy["allow_agent_safe_apply"] = bool(policy.get("allow_agent_safe_apply", True))
    policy["default_safe_only"] = bool(policy.get("default_safe_only", True))
    policy["require_dry_run_first"] = bool(policy.get("require_dry_run_first", False))
    policy["allow_policy_patch"] = bool(policy.get("allow_policy_patch", False))
    policy["allow_safe_undo"] = bool(policy.get("allow_safe_undo", True))
    policy["max_undo_items"] = max(1, min(int(policy.get("max_undo_items") or 10), 100))
    policy["allow_agent_managed_loop"] = bool(policy.get("allow_agent_managed_loop", False))
    policy["agent_managed_daily_action_limit"] = max(0, min(int(policy.get("agent_managed_daily_action_limit") or 0), 100))
    policy["agent_managed_failure_budget"] = max(0, min(int(policy.get("agent_managed_failure_budget") or 0), 50))
    policy["agent_managed_min_minutes_between_runs"] = max(0, min(int(policy.get("agent_managed_min_minutes_between_runs") or 0), 24 * 60))
    policy["agent_managed_safe_only"] = bool(policy.get("agent_managed_safe_only", True))
    policy["mode"] = str(policy.get("mode") or "manual")
    return policy


def validate_review_action_policy(policy: dict[str, Any]) -> dict[str, Any]:
    policy = _normalize_review_action_policy(policy)
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if policy["allow_safe_batch"] and not policy["safe_item_types"]:
        conflicts.append({"code": "safe_batch_without_safe_types", "message": "allow_safe_batch=true but safe_item_types is empty."})
    if policy.get("allow_agent_managed_loop") and not policy.get("allow_safe_batch"):
        conflicts.append({"code": "agent_managed_without_safe_batch", "message": "Agent-managed review loop requires allow_safe_batch=true."})
    if policy.get("allow_agent_managed_loop") and policy.get("agent_managed_daily_action_limit", 0) <= 0:
        conflicts.append({"code": "agent_managed_without_daily_limit", "message": "Agent-managed review loop requires a positive daily action limit."})
    unsafe_managed = set(policy.get("agent_managed_sections") or []) - set(policy.get("safe_sections") or []) - {"all", "*"}
    if unsafe_managed:
        conflicts.append({"code": "agent_managed_unsafe_sections", "message": f"Agent-managed sections not in safe_sections: {sorted(unsafe_managed)}"})
    overlap = set(policy["safe_item_types"]) & set(policy["deny_item_types"])
    if overlap:
        conflicts.append({"code": "safe_and_denied_overlap", "message": f"Item types cannot be both safe and denied: {sorted(overlap)}"})
    if "user_confirmation" in policy["safe_item_types"]:
        conflicts.append({"code": "unsafe_user_confirmation_auto", "message": "user_confirmation must never be safe-auto; it needs explicit confirm/reject."})
    if "proactive_outbox" in policy["safe_item_types"]:
        warnings.append({"code": "proactive_outbox_safe", "message": "proactive_outbox safe-auto can send messages unexpectedly; prefer explicit send/suppress."})
    if policy["max_batch_items"] > 25:
        warnings.append({"code": "large_batch_limit", "message": "Large review batches are harder to audit; keep max_batch_items <= 25 for normal use."})
    return {
        "ok": not conflicts,
        "policy": policy,
        "conflict_count": len(conflicts),
        "warning_count": len(warnings),
        "conflicts": conflicts,
        "warnings": warnings,
    }


def get_review_action_policy(conn, owner_kind: str, owner_id: str, *, create: bool = True) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_action_policies WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchone()
    if not row and create:
        policy = _normalize_review_action_policy({})
        conn.execute(
            "INSERT OR IGNORE INTO human_review_action_policies(owner_kind, owner_id, policy_json, updated_by) VALUES(?,?,?,?)",
            (owner_kind, owner_id, dumps(policy), "default"),
        )
        row = conn.execute(
            "SELECT * FROM human_review_action_policies WHERE owner_kind=? AND owner_id=?",
            (owner_kind, owner_id),
        ).fetchone()
    if not row:
        return {"ok": False, "policy": _normalize_review_action_policy({}), "exists": False}
    d = dict(row)
    d["policy"] = _normalize_review_action_policy(loads(d.pop("policy_json"), {}))
    d["validation"] = validate_review_action_policy(d["policy"])
    d["ok"] = True
    return d


def set_review_action_policy(conn, owner_kind: str, owner_id: str, *, policy_patch: dict[str, Any] | None = None,
                             replace_policy: dict[str, Any] | None = None, updated_by: str = "user") -> dict[str, Any]:
    current = get_review_action_policy(conn, owner_kind, owner_id, create=True).get("policy") or {}
    if replace_policy is not None:
        new_policy = _normalize_review_action_policy(replace_policy)
    else:
        new_policy = _normalize_review_action_policy(_deep_merge(current, policy_patch or {}))
    validation = validate_review_action_policy(new_policy)
    if not validation.get("ok"):
        append_audit(conn, owner_kind, owner_id, "human_review_action_policy_rejected", "warning", "Review action policy patch rejected", {"validation": validation})
        return {"ok": False, "policy": new_policy, "validation": validation, "error": "policy has conflicts"}
    conn.execute(
        """INSERT INTO human_review_action_policies(owner_kind, owner_id, policy_json, updated_by, updated_at)
             VALUES(?,?,?,?,datetime('now'))
             ON CONFLICT(owner_kind, owner_id) DO UPDATE SET policy_json=excluded.policy_json, updated_by=excluded.updated_by, updated_at=datetime('now')""",
        (owner_kind, owner_id, dumps(new_policy), updated_by),
    )
    append_audit(conn, owner_kind, owner_id, "human_review_action_policy_updated", "info", "Review action policy updated", {"policy": new_policy})
    return get_review_action_policy(conn, owner_kind, owner_id, create=True)


def _item_is_batch_safe(item: dict[str, Any], plan: dict[str, Any], policy: dict[str, Any], *, safe_only: bool = True, section: str | None = None) -> tuple[bool, str | None]:
    item_type = str(item.get("item_type") or "")
    # section is not stored directly in the DB, but review_run items encode common source types.
    hint = item.get("action_hint") or {}
    if section and section not in {"all", "*"}:
        if item_type == "sleep_state" and section != "sleep":
            return False, "section_mismatch"
        if item_type == "delayed_reply" and section != "reply":
            return False, "section_mismatch"
        if item_type == "dream_audit_finding" and section != "dream":
            return False, "section_mismatch"
        if item_type in {"policy_conflict", "policy_warning"} and section != "policy":
            return False, "section_mismatch"
        if item_type in {"proactive_intent", "proactive_outbox"} and section != "proactive":
            return False, "section_mismatch"
        if item_type == "user_confirmation" and section != "confirmations":
            return False, "section_mismatch"
    if item_type in set(policy.get("deny_item_types") or []):
        return False, "denied_by_policy"
    if safe_only:
        if item_type not in set(policy.get("safe_item_types") or []):
            return False, "not_safe_item_type"
        if not plan.get("safe_auto"):
            return False, "plan_not_safe_auto"
    if plan.get("requires_choice"):
        return False, "requires_choice"
    if not plan.get("supported", True):
        return False, "unsupported"
    if not policy.get("allow_safe_batch", True):
        return False, "batch_disabled_by_policy"
    return True, None


def select_review_items_for_batch(conn, owner_kind: str, owner_id: str, *, review_run_id: str | None = None,
                                  section: str | None = None, item_ids: list[str] | None = None,
                                  safe_only: bool = True, limit: int | None = None) -> list[dict[str, Any]]:
    policy = get_review_action_policy(conn, owner_kind, owner_id, create=True)["policy"]
    max_items = int(limit or policy.get("max_batch_items") or 10)
    if item_ids:
        rows = []
        for item_id in item_ids[:max_items]:
            try:
                rows.append(get_review_item(conn, owner_kind, owner_id, item_id))
            except Exception:
                continue
    else:
        if not review_run_id:
            latest = conn.execute(
                "SELECT id FROM human_review_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 1",
                (owner_kind, owner_id),
            ).fetchone()
            review_run_id = latest[0] if latest else None
        if not review_run_id:
            return []
        dbrows = conn.execute(
            """SELECT * FROM human_review_items
                 WHERE owner_kind=? AND owner_id=? AND review_run_id=? AND status='open'
                 ORDER BY created_at LIMIT ?""",
            (owner_kind, owner_id, review_run_id, max_items * 3),
        ).fetchall()
        rows = []
        for r in dbrows:
            d = dict(r)
            d["action_hint"] = loads(d.pop("action_hint_json"), {})
            rows.append(d)
    selected = []
    for item in rows:
        plan = plan_review_item_action(conn, owner_kind, owner_id, item["id"])
        ok, reason = _item_is_batch_safe(item, plan, policy, safe_only=safe_only, section=section)
        item["batch_plan"] = plan
        item["batch_skip_reason"] = reason
        if ok:
            selected.append(item)
        if len(selected) >= max_items:
            break
    return selected


def record_review_batch_run(conn, owner_kind: str, owner_id: str, *, review_run_id: str | None, mode: str,
                            section: str | None, safe_only: bool, selected_item_ids: list[str], plan: dict[str, Any],
                            results: list[dict[str, Any]] | None = None, status: str = "planned",
                            error: str | None = None) -> dict[str, Any]:
    batch_id = new_id("reviewbatch")
    tx_ids = []
    receipt_ids = []
    for r in results or []:
        if r.get("transaction_id"):
            tx_ids.append(r.get("transaction_id"))
        if r.get("receipt_id"):
            receipt_ids.append(r.get("receipt_id"))
        ar = r.get("action_run") or {}
        if ar.get("transaction_id"):
            tx_ids.append(ar.get("transaction_id"))
        if ar.get("receipt_id"):
            receipt_ids.append(ar.get("receipt_id"))
    conn.execute(
        """INSERT INTO human_review_batch_runs(
             id, owner_kind, owner_id, review_run_id, mode, section, safe_only, status,
             selected_item_ids_json, plan_json, results_json, transaction_ids_json, receipt_ids_json, error, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ? IN ('planned','applied','failed','skipped') THEN datetime('now') ELSE NULL END)""",
        (batch_id, owner_kind, owner_id, review_run_id, mode, section, 1 if safe_only else 0, status,
         dumps(selected_item_ids), dumps(plan), dumps(results or []), dumps(sorted(set(tx_ids))), dumps(sorted(set(receipt_ids))), error, status),
    )
    for r in results or []:
        item_id = r.get("item_id") or ((r.get("plan") or {}).get("item") or {}).get("id")
        if not item_id:
            continue
        ar = r.get("action_run") or {}
        conn.execute(
            """INSERT INTO human_review_batch_items(id, batch_run_id, item_id, action_run_id, status, plan_json, output_json, error, completed_at)
                 VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
            (new_id("reviewbatchitem"), batch_id, item_id, ar.get("id"), r.get("status") or ("applied" if r.get("applied") else "planned"), dumps(r.get("plan") or {}), dumps(r.get("output") or r), r.get("error")),
        )
    append_audit(conn, owner_kind, owner_id, "human_review_batch_run", "info" if status != "failed" else "error", f"Review batch {status}", {"batch_run_id": batch_id, "mode": mode, "count": len(selected_item_ids), "section": section, "safe_only": safe_only})
    return get_review_batch_run(conn, owner_kind, owner_id, batch_id)


def list_review_batch_runs(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM human_review_batch_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [_decode_review_batch_run(conn, dict(r), include_items=False) for r in rows]


def get_review_batch_run(conn, owner_kind: str, owner_id: str, batch_run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_batch_runs WHERE id=? AND owner_kind=? AND owner_id=?",
        (batch_run_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"review batch run not found: {batch_run_id}")
    return _decode_review_batch_run(conn, dict(row), include_items=True)


def _decode_review_batch_run(conn, d: dict[str, Any], *, include_items: bool) -> dict[str, Any]:
    for k in ["selected_item_ids_json", "transaction_ids_json", "receipt_ids_json"]:
        d[k[:-5]] = loads(d.pop(k), [])
    for k in ["plan_json"]:
        d[k[:-5]] = loads(d.pop(k), {})
    for k in ["results_json"]:
        d[k[:-5]] = loads(d.pop(k), [])
    d["safe_only"] = bool(d.get("safe_only"))
    if include_items:
        items = []
        for r in conn.execute("SELECT * FROM human_review_batch_items WHERE batch_run_id=? ORDER BY created_at", (d["id"],)).fetchall():
            item = dict(r)
            for k in ["plan_json", "output_json"]:
                item[k[:-5]] = loads(item.pop(k), {})
            items.append(item)
        d["items"] = items
    return d




# ----- Agent-managed review loop (v0.11.16) ------------------------------

def _date_key(now: str | None = None) -> str:
    text = now or now_iso()
    return str(text)[:10]


def get_managed_review_loop_state(conn, owner_kind: str, owner_id: str, *, date_key: str | None = None) -> dict[str, Any]:
    dk = date_key or _date_key()
    conn.execute(
        """INSERT OR IGNORE INTO human_review_managed_loop_state(owner_kind, owner_id, date_key)
             VALUES(?,?,?)""",
        (owner_kind, owner_id, dk),
    )
    row = conn.execute(
        "SELECT * FROM human_review_managed_loop_state WHERE owner_kind=? AND owner_id=? AND date_key=?",
        (owner_kind, owner_id, dk),
    ).fetchone()
    return dict(row) if row else {"owner_kind": owner_kind, "owner_id": owner_id, "date_key": dk, "run_count": 0, "action_count": 0, "failure_count": 0}


def list_managed_review_loop_runs(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM human_review_managed_loop_runs
             WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for key in ["policy_json", "decision_json", "output_json"]:
            d[key[:-5]] = loads(d.pop(key), {})
        out.append(d)
    return out


def get_managed_review_loop_run(conn, owner_kind: str, owner_id: str, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_managed_loop_runs WHERE id=? AND owner_kind=? AND owner_id=?",
        (run_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"managed review loop run not found: {run_id}")
    d = dict(row)
    for key in ["policy_json", "decision_json", "output_json"]:
        d[key[:-5]] = loads(d.pop(key), {})
    return d


def record_managed_review_loop_run(conn, owner_kind: str, owner_id: str, *, trigger_source: str,
                                   tick_id: str | None, status: str, policy: dict[str, Any],
                                   decision: dict[str, Any], review_run_id: str | None = None,
                                   batch_run_id: str | None = None, selected_count: int = 0,
                                   applied_count: int = 0, skipped_count: int = 0, failed_count: int = 0,
                                   daily_action_count_before: int = 0, daily_action_limit: int = 0,
                                   failure_count_before: int = 0, failure_budget: int = 0,
                                   output: dict[str, Any] | None = None, error: str | None = None,
                                   now: str | None = None) -> dict[str, Any]:
    run_id = new_id("reviewloop")
    conn.execute(
        """INSERT INTO human_review_managed_loop_runs(
             id, owner_kind, owner_id, trigger_source, tick_id, status, policy_json, decision_json,
             review_run_id, batch_run_id, selected_count, applied_count, skipped_count, failed_count,
             daily_action_count_before, daily_action_limit, failure_count_before, failure_budget,
             output_json, error, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ? IN ('planned','applied','noop','skipped','blocked','failed','partial') THEN datetime('now') ELSE NULL END)""",
        (run_id, owner_kind, owner_id, trigger_source, tick_id, status, dumps(policy), dumps(decision),
         review_run_id, batch_run_id, int(selected_count), int(applied_count), int(skipped_count), int(failed_count),
         int(daily_action_count_before), int(daily_action_limit), int(failure_count_before), int(failure_budget),
         dumps(output or {}), error, status),
    )
    dk = _date_key(now)
    state = get_managed_review_loop_state(conn, owner_kind, owner_id, date_key=dk)
    new_run_count = int(state.get("run_count") or 0) + 1
    new_action_count = int(state.get("action_count") or 0) + max(0, int(applied_count))
    new_failure_count = int(state.get("failure_count") or 0) + max(0, int(failed_count))
    conn.execute(
        """UPDATE human_review_managed_loop_state
              SET run_count=?, action_count=?, failure_count=?, last_run_id=?, last_run_at=datetime('now'), last_status=?, updated_at=datetime('now')
            WHERE owner_kind=? AND owner_id=? AND date_key=?""",
        (new_run_count, new_action_count, new_failure_count, run_id, status, owner_kind, owner_id, dk),
    )
    append_audit(conn, owner_kind, owner_id, "agent_managed_review_loop", "info" if status not in {"failed"} else "error", f"Agent-managed review loop {status}", {"run_id": run_id, "trigger_source": trigger_source, "selected": selected_count, "applied": applied_count, "failed": failed_count, "batch_run_id": batch_run_id, "error": error})
    return get_managed_review_loop_run(conn, owner_kind, owner_id, run_id)


def decide_managed_review_loop(conn, owner_kind: str, owner_id: str, *, trigger_source: str = "manual",
                               now: str | None = None, force: bool = False) -> dict[str, Any]:
    policy_row = get_review_action_policy(conn, owner_kind, owner_id, create=True)
    policy = policy_row.get("policy") or {}
    validation = validate_review_action_policy(policy)
    state = get_managed_review_loop_state(conn, owner_kind, owner_id, date_key=_date_key(now))
    limit = int(policy.get("agent_managed_daily_action_limit") or 0)
    failures_allowed = int(policy.get("agent_managed_failure_budget") or 0)
    remaining = max(0, limit - int(state.get("action_count") or 0))
    failure_remaining = max(0, failures_allowed - int(state.get("failure_count") or 0))
    allowed_triggers = set(policy.get("agent_managed_trigger_sources") or [])
    decision: dict[str, Any] = {
        "ok": True,
        "allowed": True,
        "force": force,
        "trigger_source": trigger_source,
        "policy_mode": policy.get("mode"),
        "allow_agent_managed_loop": bool(policy.get("allow_agent_managed_loop")),
        "daily_action_limit": limit,
        "daily_action_count": int(state.get("action_count") or 0),
        "remaining_actions": remaining,
        "failure_budget": failures_allowed,
        "failure_count": int(state.get("failure_count") or 0),
        "failure_remaining": failure_remaining,
        "validation": validation,
        "reasons": [],
    }
    if not force:
        if not policy.get("allow_agent_managed_loop"):
            decision["allowed"] = False
            decision["reasons"].append("agent-managed review loop is disabled by review action policy")
        if trigger_source not in allowed_triggers:
            decision["allowed"] = False
            decision["reasons"].append(f"trigger_source {trigger_source!r} is not allowed")
        if validation.get("conflict_count"):
            decision["allowed"] = False
            decision["reasons"].append("review action policy has conflicts")
        if remaining <= 0:
            decision["allowed"] = False
            decision["reasons"].append("daily managed-review action limit reached")
        if failure_remaining <= 0:
            decision["allowed"] = False
            decision["reasons"].append("managed-review failure budget exhausted")
    return {"policy": policy, "state": state, "decision": decision}

# ----- Review undo / rollback trace (v0.11.15) ---------------------------

def _facts_from_action_run(action_run: dict[str, Any]) -> list[dict[str, Any]]:
    out = action_run.get("output") or {}
    commit = out.get("commit") if isinstance(out, dict) else None
    if not isinstance(commit, dict):
        # Batch items can nest output in different shapes; keep this defensive.
        commit = ((out.get("output") or {}).get("commit") if isinstance(out.get("output"), dict) else None) if isinstance(out, dict) else None
    receipt = (commit or {}).get("receipt") or {}
    facts = receipt.get("facts") or []
    return facts if isinstance(facts, list) else []


def _evidence_values(action_run: dict[str, Any], key: str) -> list[Any]:
    vals: list[Any] = []
    for fact in _facts_from_action_run(action_run):
        ev = (fact or {}).get("evidence") or {}
        v = ev.get(key)
        if isinstance(v, list):
            vals.extend(v)
        elif v is not None:
            vals.append(v)
    # Preserve order while deduping.
    out: list[Any] = []
    seen: set[str] = set()
    for v in vals:
        marker = str(v)
        if marker not in seen:
            seen.add(marker)
            out.append(v)
    return out


def _find_delayed_digest_ids_for_replies(conn, owner_kind: str, owner_id: str, reply_ids: list[str]) -> list[str]:
    if not reply_ids:
        return []
    reply_set = set(reply_ids)
    rows = conn.execute(
        "SELECT * FROM delayed_reply_digests WHERE owner_kind=? AND owner_id=? AND status IN ('created','released') ORDER BY created_at DESC LIMIT 100",
        (owner_kind, owner_id),
    ).fetchall()
    digest_ids: list[str] = []
    for r in rows:
        ids = set(loads(dict(r).get("delayed_reply_ids_json"), []))
        if ids & reply_set:
            digest_ids.append(r["id"])
    return digest_ids


def plan_review_action_undo(conn, owner_kind: str, owner_id: str, action_run_id: str) -> dict[str, Any]:
    """Plan a conservative undo for a previously-applied review action.

    Undo is intentionally narrow. It never claims to time-travel arbitrary LifeOps.
    It supports only reversible review UX actions that have clear local state:
    delayed-reply release and freshly-created recovery-sleep plans. Everything
    else returns a traceable unsupported plan.
    """
    action_run = get_review_action_run(conn, owner_kind, owner_id, action_run_id)
    plan = action_run.get("plan") or {}
    output = action_run.get("output") or {}
    item = plan.get("item") or {}
    out: dict[str, Any] = {
        "ok": True,
        "supported": False,
        "safe_undo": False,
        "action_run_id": action_run_id,
        "action_status": action_run.get("status"),
        "application_type": plan.get("application_type"),
        "item_type": plan.get("item_type") or item.get("item_type"),
        "message": "No safe undo is available for this review action.",
        "steps": [],
        "warnings": [],
    }
    if action_run.get("status") != "applied":
        out.update({"supported": False, "message": f"Only applied review actions can be undone; status={action_run.get('status')}"})
        return out
    if action_run.get("undo_status") in {"undone", "applied"}:
        out.update({"supported": False, "message": "This review action already has an undo applied."})
        return out

    app_type = plan.get("application_type")
    ops = plan.get("ops") or []
    op_types = [o.get("type") for o in ops if isinstance(o, dict)]

    if app_type == "lifeops" and "RELEASE_DELAYED_REPLIES" in op_types:
        reply_ids = [str(x) for x in _evidence_values(action_run, "released_reply_ids")]
        if not reply_ids:
            # Older action runs before v0.11.15 may lack receipt evidence; do not guess.
            out.update({"supported": False, "message": "Cannot undo delayed-reply release because released reply ids were not captured in the receipt."})
            return out
        digest_ids = _find_delayed_digest_ids_for_replies(conn, owner_kind, owner_id, reply_ids)
        out.update({
            "supported": True,
            "safe_undo": True,
            "undo_type": "reopen_delayed_replies",
            "reply_ids": reply_ids,
            "digest_ids": digest_ids,
            "message": "Reopen released delayed replies and mark generated digest(s) as undone.",
            "steps": [
                {"table": "delayed_replies", "operation": "status released -> pending", "ids": reply_ids},
                {"table": "delayed_reply_digests", "operation": "status -> undone", "ids": digest_ids},
            ],
        })
        return out

    if app_type == "direct" and plan.get("tool") == "life_sleep":
        sleep_plan = output.get("sleep_plan") or ((output.get("output") or {}).get("sleep_plan") if isinstance(output.get("output"), dict) else None) or {}
        sleep_plan_id = sleep_plan.get("id")
        recovery_plan_id = output.get("sleep_recovery_plan_id") or ((output.get("output") or {}).get("sleep_recovery_plan_id") if isinstance(output.get("output"), dict) else None)
        if not sleep_plan_id:
            out.update({"supported": False, "message": "Cannot undo recovery sleep plan because sleep_plan_id was not captured."})
            return out
        sessions = conn.execute("SELECT id,status FROM sleep_sessions WHERE owner_kind=? AND owner_id=? AND sleep_plan_id=?", (owner_kind, owner_id, sleep_plan_id)).fetchall()
        if sessions:
            out.update({"supported": False, "message": "Cannot safely undo recovery sleep plan because a sleep session already exists.", "sessions": [dict(r) for r in sessions]})
            return out
        row = conn.execute("SELECT * FROM sleep_plans WHERE id=? AND owner_kind=? AND owner_id=?", (sleep_plan_id, owner_kind, owner_id)).fetchone()
        if not row:
            out.update({"supported": False, "message": "sleep plan not found."})
            return out
        sp = dict(row)
        if sp.get("status") not in {"scheduled", "planned", "missed"}:
            out.update({"supported": False, "message": f"Cannot safely undo sleep plan in status {sp.get('status')}."})
            return out
        out.update({
            "supported": True,
            "safe_undo": True,
            "undo_type": "cancel_recovery_sleep_plan",
            "sleep_plan_id": sleep_plan_id,
            "sleep_recovery_plan_id": recovery_plan_id,
            "event_id": sp.get("event_id"),
            "schedule_block_id": sp.get("schedule_block_id"),
            "message": "Cancel the generated recovery sleep plan, event, schedule block, wake jobs, and recovery-plan marker if not yet started.",
            "steps": [
                {"table": "sleep_plans", "operation": "status -> cancelled", "ids": [sleep_plan_id]},
                {"table": "sleep_recovery_plans", "operation": "status -> cancelled", "ids": [recovery_plan_id] if recovery_plan_id else []},
                {"table": "events/schedule_blocks/wake_jobs", "operation": "cancel linked scheduled state"},
            ],
        })
        return out

    if app_type in {"policy_suggestions", "manual_review", "noop"}:
        out.update({"supported": True, "safe_undo": True, "undo_type": "noop", "message": "This action did not mutate durable life state; undo is a no-op."})
        return out

    out.update({
        "supported": False,
        "safe_undo": False,
        "message": f"Undo is not automatic for application_type={app_type}. Use trace/backup restore or an explicit corrective LifeOps transaction if needed.",
    })
    return out


def record_review_undo_run(conn, owner_kind: str, owner_id: str, *, target_kind: str, target_id: str,
                           mode: str, status: str, undo_plan: dict[str, Any], output: dict[str, Any] | None = None,
                           action_run_ids: list[str] | None = None, batch_run_id: str | None = None,
                           transaction_ids: list[str] | None = None, receipt_ids: list[str] | None = None,
                           error: str | None = None) -> dict[str, Any]:
    undo_id = new_id("reviewundo")
    conn.execute(
        """INSERT INTO human_review_undo_runs(
             id, owner_kind, owner_id, target_kind, target_id, batch_run_id, action_run_id, mode, status,
             undo_plan_json, output_json, action_run_ids_json, transaction_ids_json, receipt_ids_json, error, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ? IN ('planned','undone','partial','failed','noop','unsupported') THEN datetime('now') ELSE NULL END)""",
        (undo_id, owner_kind, owner_id, target_kind, target_id, batch_run_id, target_id if target_kind == "action_run" else None,
         mode, status, dumps(undo_plan or {}), dumps(output or {}), dumps(action_run_ids or []), dumps(transaction_ids or []), dumps(receipt_ids or []), error, status),
    )
    append_audit(conn, owner_kind, owner_id, "human_review_undo_run", "info" if status not in {"failed"} else "error", f"Review undo {status}", {"undo_run_id": undo_id, "target_kind": target_kind, "target_id": target_id, "error": error})
    return get_review_undo_run(conn, owner_kind, owner_id, undo_id)


def _decode_review_undo_run(conn, d: dict[str, Any], *, include_items: bool = False) -> dict[str, Any]:
    for k in ["undo_plan_json", "output_json"]:
        d[k[:-5]] = loads(d.pop(k), {})
    for k in ["action_run_ids_json", "transaction_ids_json", "receipt_ids_json"]:
        d[k[:-5]] = loads(d.pop(k), [])
    if include_items:
        items = []
        for r in conn.execute("SELECT * FROM human_review_undo_items WHERE undo_run_id=? ORDER BY created_at", (d["id"],)).fetchall():
            item = dict(r)
            for k in ["undo_plan_json", "output_json"]:
                item[k[:-5]] = loads(item.pop(k), {})
            items.append(item)
        d["items"] = items
    return d


def list_review_undo_runs(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM human_review_undo_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_decode_review_undo_run(conn, dict(r), include_items=False) for r in rows]


def get_review_undo_run(conn, owner_kind: str, owner_id: str, undo_run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM human_review_undo_runs WHERE id=? AND owner_kind=? AND owner_id=?", (undo_run_id, owner_kind, owner_id)).fetchone()
    if not row:
        raise ValueError(f"review undo run not found: {undo_run_id}")
    return _decode_review_undo_run(conn, dict(row), include_items=True)


def apply_review_action_undo(conn, owner_kind: str, owner_id: str, action_run_id: str, *, dry_run: bool = False,
                             reason: str = "review undo") -> dict[str, Any]:
    plan = plan_review_action_undo(conn, owner_kind, owner_id, action_run_id)
    if dry_run:
        run = record_review_undo_run(conn, owner_kind, owner_id, target_kind="action_run", target_id=action_run_id, mode="preview", status="planned", undo_plan=plan, action_run_ids=[action_run_id])
        return {"ok": True, "undone": False, "plan": plan, "undo_run": run}
    if not plan.get("supported") or not plan.get("safe_undo"):
        run = record_review_undo_run(conn, owner_kind, owner_id, target_kind="action_run", target_id=action_run_id, mode="apply", status="unsupported", undo_plan=plan, action_run_ids=[action_run_id], error=plan.get("message"))
        return {"ok": False, "undone": False, "plan": plan, "undo_run": run, "error": plan.get("message")}

    output: dict[str, Any] = {}
    try:
        undo_type = plan.get("undo_type")
        if undo_type == "reopen_delayed_replies":
            reply_ids = [str(x) for x in plan.get("reply_ids") or []]
            digest_ids = [str(x) for x in plan.get("digest_ids") or []]
            for rid in reply_ids:
                conn.execute("UPDATE delayed_replies SET status='pending', released_at=NULL, release_reason=NULL WHERE id=? AND owner_kind=? AND owner_id=? AND status='released'", (rid, owner_kind, owner_id))
            for did in digest_ids:
                conn.execute("UPDATE delayed_reply_digests SET status='undone', released_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?", (did, owner_kind, owner_id))
            try:
                from .events import set_realtime_state
                set_realtime_state(conn, owner_kind, owner_id, mode="waiting_to_reply", reply_mode="defer_until_available", source="life_review_undo", reason=reason)
            except Exception:
                pass
            output = {"reopened_reply_ids": reply_ids, "undone_digest_ids": digest_ids}
            append_journal(conn, owner_kind, owner_id, "human_review_undo_reopened_delayed_replies", output, "life_review_undo")
        elif undo_type == "cancel_recovery_sleep_plan":
            sleep_plan_id = plan.get("sleep_plan_id")
            recovery_plan_id = plan.get("sleep_recovery_plan_id")
            event_id = plan.get("event_id")
            block_id = plan.get("schedule_block_id")
            if recovery_plan_id:
                conn.execute("UPDATE sleep_recovery_plans SET status='cancelled', updated_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?", (recovery_plan_id, owner_kind, owner_id))
            conn.execute("UPDATE sleep_plans SET status='cancelled', updated_at=datetime('now'), completed_at=datetime('now') WHERE id=? AND owner_kind=? AND owner_id=?", (sleep_plan_id, owner_kind, owner_id))
            if event_id:
                try:
                    from .events import transition_event
                    transition_event(conn, owner_kind, owner_id, event_id, "cancelled", reason, "life_review_undo")
                except Exception as exc:
                    output.setdefault("event_cancel_error", str(exc))
            if block_id:
                try:
                    from .events import update_schedule_block_status
                    update_schedule_block_status(conn, owner_kind, owner_id, block_id, "cancelled", reason, "life_review_undo")
                except Exception as exc:
                    output.setdefault("schedule_cancel_error", str(exc))
            conn.execute("UPDATE wake_jobs SET status='cancelled' WHERE owner_kind=? AND owner_id=? AND target_id=? AND status IN ('pending','running')", (owner_kind, owner_id, sleep_plan_id))
            output.update({"sleep_plan_id": sleep_plan_id, "sleep_recovery_plan_id": recovery_plan_id, "event_id": event_id, "schedule_block_id": block_id})
            append_journal(conn, owner_kind, owner_id, "human_review_undo_cancelled_recovery_sleep", output, "life_review_undo")
        elif undo_type == "noop":
            output = {"noop": True, "message": plan.get("message")}
        else:
            raise ValueError(f"unsupported undo_type: {undo_type}")
        run = record_review_undo_run(conn, owner_kind, owner_id, target_kind="action_run", target_id=action_run_id, mode="apply", status="noop" if plan.get("undo_type") == "noop" else "undone", undo_plan=plan, output=output, action_run_ids=[action_run_id])
        conn.execute("UPDATE human_review_action_runs SET undo_status=?, undo_run_id=?, undo_plan_json=? WHERE id=? AND owner_kind=? AND owner_id=?", ("undone", run.get("id"), dumps(plan), action_run_id, owner_kind, owner_id))
        return {"ok": True, "undone": True, "plan": plan, "output": output, "undo_run": run}
    except Exception as exc:
        run = record_review_undo_run(conn, owner_kind, owner_id, target_kind="action_run", target_id=action_run_id, mode="apply", status="failed", undo_plan=plan, output=output, action_run_ids=[action_run_id], error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "undone": False, "plan": plan, "output": output, "undo_run": run, "error": f"{type(exc).__name__}: {exc}"}


def plan_review_batch_undo(conn, owner_kind: str, owner_id: str, batch_run_id: str) -> dict[str, Any]:
    batch = get_review_batch_run(conn, owner_kind, owner_id, batch_run_id)
    action_ids = [i.get("action_run_id") for i in batch.get("items", []) if i.get("action_run_id")]
    # Reverse order to undo later changes first.
    action_ids = list(reversed(action_ids))
    plans = []
    supported_count = 0
    for aid in action_ids:
        p = plan_review_action_undo(conn, owner_kind, owner_id, aid)
        plans.append(p)
        if p.get("supported") and p.get("safe_undo"):
            supported_count += 1
    return {"ok": True, "batch_run_id": batch_run_id, "action_run_ids": action_ids, "supported_count": supported_count, "total_count": len(action_ids), "plans": plans, "message": "Undo supported actions in reverse batch order."}


def apply_review_batch_undo(conn, owner_kind: str, owner_id: str, batch_run_id: str, *, dry_run: bool = False,
                            safe_only: bool = True, reason: str = "review batch undo") -> dict[str, Any]:
    plan = plan_review_batch_undo(conn, owner_kind, owner_id, batch_run_id)
    action_ids = plan.get("action_run_ids") or []
    if dry_run:
        run = record_review_undo_run(conn, owner_kind, owner_id, target_kind="batch_run", target_id=batch_run_id, batch_run_id=batch_run_id, mode="preview", status="planned", undo_plan=plan, action_run_ids=action_ids)
        return {"ok": True, "undone": False, "plan": plan, "undo_run": run}
    results = []
    undo_item_rows = []
    for aid, action_plan in zip(action_ids, plan.get("plans") or []):
        if safe_only and not (action_plan.get("supported") and action_plan.get("safe_undo")):
            result = {"ok": False, "undone": False, "action_run_id": aid, "plan": action_plan, "status": "unsupported", "error": action_plan.get("message")}
        else:
            result = apply_review_action_undo(conn, owner_kind, owner_id, aid, dry_run=False, reason=reason)
            result["action_run_id"] = aid
            result["status"] = "undone" if result.get("undone") else "failed"
        results.append(result)
    status = "undone" if results and all(r.get("undone") for r in results) else ("partial" if any(r.get("undone") for r in results) else "unsupported")
    run = record_review_undo_run(conn, owner_kind, owner_id, target_kind="batch_run", target_id=batch_run_id, batch_run_id=batch_run_id, mode="apply", status=status, undo_plan=plan, output={"results": results}, action_run_ids=action_ids)
    for r in results:
        conn.execute(
            """INSERT INTO human_review_undo_items(id, undo_run_id, target_kind, target_id, action_run_id, batch_run_id, status, undo_plan_json, output_json, error, completed_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (new_id("reviewundoitem"), run.get("id"), "action_run", r.get("action_run_id"), r.get("action_run_id"), batch_run_id, r.get("status") or ("undone" if r.get("undone") else "failed"), dumps(r.get("plan") or {}), dumps(r), r.get("error")),
        )
    conn.execute("UPDATE human_review_batch_runs SET undo_status=?, undo_run_id=?, undo_plan_json=? WHERE id=? AND owner_kind=? AND owner_id=?", ("undone" if status == "undone" else status, run.get("id"), dumps(plan), batch_run_id, owner_kind, owner_id))
    return {"ok": True, "undone": any(r.get("undone") for r in results), "status": status, "plan": plan, "results": results, "undo_run": get_review_undo_run(conn, owner_kind, owner_id, run.get("id"))}


# ----- Agent-managed review acceptance and stress (v0.11.17) -------------

def _decode_managed_acceptance_run(conn, row, *, include_scenarios: bool = False) -> dict[str, Any]:
    d = dict(row) if row else {}
    if not d:
        return d
    d["output"] = loads(d.pop("output_json"), {})
    if include_scenarios:
        rows = conn.execute(
            "SELECT * FROM human_review_managed_acceptance_scenarios WHERE acceptance_run_id=? ORDER BY created_at, scenario_key",
            (d["id"],),
        ).fetchall()
        scenarios = []
        for r in rows:
            sd = dict(r)
            sd["details"] = loads(sd.pop("details_json"), {})
            scenarios.append(sd)
        d["scenarios"] = scenarios
    return d


def list_managed_review_acceptance_runs(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM human_review_managed_acceptance_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [_decode_managed_acceptance_run(conn, r) for r in rows]


def get_managed_review_acceptance_run(conn, owner_kind: str, owner_id: str, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_managed_acceptance_runs WHERE id=? AND owner_kind=? AND owner_id=?",
        (run_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"managed review acceptance run not found: {run_id}")
    return _decode_managed_acceptance_run(conn, row, include_scenarios=True)


def begin_managed_review_acceptance_run(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    run_id = new_id("mgrevaccept")
    conn.execute(
        "INSERT INTO human_review_managed_acceptance_runs(id, owner_kind, owner_id, status) VALUES(?,?,?,?)",
        (run_id, owner_kind, owner_id, "running"),
    )
    append_audit(conn, owner_kind, owner_id, "managed_review_acceptance_started", "info", "Agent-managed review acceptance started", {"run_id": run_id})
    return {"id": run_id, "owner_kind": owner_kind, "owner_id": owner_id, "status": "running"}


def record_managed_review_acceptance_scenario(conn, owner_kind: str, owner_id: str, run_id: str, scenario_key: str,
                                              status: str, summary: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    sid = new_id("mgrevscn")
    conn.execute(
        """INSERT INTO human_review_managed_acceptance_scenarios(
             id, acceptance_run_id, owner_kind, owner_id, scenario_key, status, summary, details_json, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
        (sid, run_id, owner_kind, owner_id, scenario_key, status, summary, dumps(details or {})),
    )
    return dict(conn.execute("SELECT * FROM human_review_managed_acceptance_scenarios WHERE id=?", (sid,)).fetchone())


def finish_managed_review_acceptance_run(conn, owner_kind: str, owner_id: str, run_id: str, output: dict[str, Any] | None = None,
                                         error: str | None = None) -> dict[str, Any]:
    rows = conn.execute("SELECT status FROM human_review_managed_acceptance_scenarios WHERE acceptance_run_id=?", (run_id,)).fetchall()
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for r in rows:
        st = r["status"]
        if st in counts:
            counts[st] += 1
    status = "passed" if counts["failed"] == 0 else "failed"
    if error:
        status = "failed"
    conn.execute(
        """UPDATE human_review_managed_acceptance_runs
              SET status=?, scenario_count=?, passed_count=?, failed_count=?, skipped_count=?, output_json=?, error=?, completed_at=datetime('now')
            WHERE id=? AND owner_kind=? AND owner_id=?""",
        (status, len(rows), counts["passed"], counts["failed"], counts["skipped"], dumps(output or {}), error, run_id, owner_kind, owner_id),
    )
    append_audit(conn, owner_kind, owner_id, "managed_review_acceptance_finished", "info" if status == "passed" else "error", f"Agent-managed review acceptance {status}", {"run_id": run_id, **counts, "error": error})
    return get_managed_review_acceptance_run(conn, owner_kind, owner_id, run_id)


def _decode_managed_stress_run(row) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["input"] = loads(d.pop("input_json"), {})
        d["output"] = loads(d.pop("output_json"), {})
    return d


def list_managed_review_stress_runs(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM human_review_managed_stress_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [_decode_managed_stress_run(r) for r in rows]


def get_managed_review_stress_run(conn, owner_kind: str, owner_id: str, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_managed_stress_runs WHERE id=? AND owner_kind=? AND owner_id=?",
        (run_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"managed review stress run not found: {run_id}")
    return _decode_managed_stress_run(row)


def record_managed_review_stress_run(conn, owner_kind: str, owner_id: str, *, stress_kind: str, input_obj: dict[str, Any],
                                     status: str, output: dict[str, Any] | None = None, created_count: int = 0,
                                     selected_count: int = 0, applied_count: int = 0, failed_count: int = 0,
                                     duration_ms: int | None = None, error: str | None = None) -> dict[str, Any]:
    run_id = new_id("mgrevstress")
    conn.execute(
        """INSERT INTO human_review_managed_stress_runs(
             id, owner_kind, owner_id, status, stress_kind, input_json, output_json, created_count,
             selected_count, applied_count, failed_count, duration_ms, error, completed_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (run_id, owner_kind, owner_id, status, stress_kind, dumps(input_obj), dumps(output or {}), int(created_count), int(selected_count), int(applied_count), int(failed_count), duration_ms, error),
    )
    append_audit(conn, owner_kind, owner_id, "managed_review_stress_run", "info" if status != "failed" else "error", f"Managed review stress {status}", {"run_id": run_id, "stress_kind": stress_kind, "created_count": created_count, "selected_count": selected_count, "applied_count": applied_count, "failed_count": failed_count, "error": error})
    return get_managed_review_stress_run(conn, owner_kind, owner_id, run_id)

# ----- Managed review observability and release readiness (v0.11.18) ------

def _latest_managed_acceptance(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT * FROM human_review_managed_acceptance_runs
             WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 1""",
        (owner_kind, owner_id),
    ).fetchone()
    if not row:
        return {"exists": False, "status": "missing"}
    d = _decode_managed_acceptance_run(conn, row, include_scenarios=False)
    d["exists"] = True
    return d


def _latest_managed_stress(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT * FROM human_review_managed_stress_runs
             WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 1""",
        (owner_kind, owner_id),
    ).fetchone()
    if not row:
        return {"exists": False, "status": "missing"}
    d = _decode_managed_stress_run(row)
    d["exists"] = True
    return d


def _managed_review_observability_signals(policy: dict[str, Any], validation: dict[str, Any], state: dict[str, Any],
                                          recent_runs: list[dict[str, Any]], latest_acceptance: dict[str, Any],
                                          latest_stress: dict[str, Any], doctor: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    signals: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    readiness = "ready"

    def signal(level: str, code: str, message: str, **extra: Any) -> None:
        nonlocal readiness
        signals.append({"level": level, "code": code, "message": message, **extra})
        if level == "blocker":
            readiness = "blocked"
        elif level == "warning" and readiness == "ready":
            readiness = "needs_review"

    if validation.get("conflict_count"):
        signal("blocker", "policy_conflicts", f"Review action policy has {validation.get('conflict_count')} conflict(s).", conflicts=validation.get("conflicts", []))
        recommendations.append({"action": "life_review policy", "message": "Resolve review action policy conflicts before enabling managed review."})

    if not policy.get("allow_agent_managed_loop"):
        # Disabled is safe, but not release-ready for automatic managed review.
        signal("info", "managed_loop_disabled", "Agent-managed review loop is currently disabled.")
        recommendations.append({"action": "life_review set_policy", "message": "Enable allow_agent_managed_loop only after acceptance/stress are passing."})
        if readiness == "ready":
            readiness = "disabled"

    if policy.get("allow_agent_managed_loop") and int(policy.get("agent_managed_daily_action_limit") or 0) <= 0:
        signal("blocker", "no_daily_limit", "Managed review is enabled but daily action limit is not positive.")

    if latest_acceptance.get("status") != "passed":
        level = "warning" if latest_acceptance.get("exists") else "warning"
        signal(level, "acceptance_not_passing", f"Latest managed acceptance status is {latest_acceptance.get('status')}.", latest_acceptance=latest_acceptance)
        recommendations.append({"action": "life_review managed_acceptance", "message": "Run managed acceptance before enabling or shipping managed review."})

    if latest_stress.get("status") not in {"passed", "completed"}:
        signal("warning", "stress_not_passing", f"Latest managed stress status is {latest_stress.get('status')}.", latest_stress=latest_stress)
        recommendations.append({"action": "life_review managed_stress", "message": "Run managed stress to confirm batch limits and duplicate handling."})

    if doctor.get("issue_count"):
        signal("warning", "doctor_issues", f"Doctor reports {doctor.get('issue_count')} issue(s).", doctor=doctor)
        recommendations.append({"action": "/life doctor", "message": "Review doctor warnings before enabling managed review."})

    if recent_runs:
        last = recent_runs[0]
        if last.get("status") in {"failed", "partial"}:
            signal("warning", "last_managed_run_not_clean", f"Last managed review run status is {last.get('status')}.", run_id=last.get("id"))
    else:
        signal("info", "no_managed_runs", "No managed review loop runs have been recorded yet.")

    if int(state.get("failure_count") or 0) > 0:
        signal("warning", "managed_failures_today", f"Managed review has {state.get('failure_count')} failure(s) today.")

    if readiness == "ready" and not policy.get("allow_agent_managed_loop"):
        readiness = "disabled"
    return readiness, signals, recommendations


def render_managed_review_observability(report: dict[str, Any]) -> str:
    lines = ["LifeEngine Managed Review Observability", "====================================="]
    lines.append(f"状态：{report.get('readiness_status')} / owner={report.get('owner_kind')}:{report.get('owner_id')}")
    policy = report.get("policy") or {}
    lines.append(f"策略：allow_agent_managed_loop={policy.get('allow_agent_managed_loop')} limit={policy.get('agent_managed_daily_action_limit')} sections={policy.get('agent_managed_sections')}")
    state = report.get("managed_state") or {}
    lines.append(f"今日：runs={state.get('run_count', 0)} actions={state.get('action_count', 0)} failures={state.get('failure_count', 0)} last={state.get('last_status')}")
    acc = report.get("latest_acceptance") or {}
    stress = report.get("latest_stress") or {}
    lines.append(f"验收：acceptance={acc.get('status')} stress={stress.get('status')}")
    doctor = report.get("doctor") or {}
    lines.append(f"Doctor：issues={doctor.get('issue_count', 0)}")
    sigs = report.get("signals") or []
    if sigs:
        lines.append("")
        lines.append("信号：")
        for s in sigs[:10]:
            lines.append(f"- [{s.get('level')}] {s.get('code')}: {s.get('message')}")
    recs = report.get("recommendations") or []
    if recs:
        lines.append("")
        lines.append("建议：")
        for r in recs[:8]:
            lines.append(f"- {r.get('message')} ({r.get('action')})")
    return "\n".join(lines)


def build_managed_review_observability_report(conn, owner_kind: str, owner_id: str, *, persist: bool = True,
                                              include_doctor: bool = True) -> dict[str, Any]:
    policy_row = get_review_action_policy(conn, owner_kind, owner_id, create=True)
    policy = policy_row.get("policy") or {}
    validation = validate_review_action_policy(policy)
    state = get_managed_review_loop_state(conn, owner_kind, owner_id)
    recent_runs = list_managed_review_loop_runs(conn, owner_kind, owner_id, limit=5)
    latest_acceptance = _latest_managed_acceptance(conn, owner_kind, owner_id)
    latest_stress = _latest_managed_stress(conn, owner_kind, owner_id)
    doctor = _doctor_summary(conn, owner_kind, owner_id) if include_doctor else {"ok": True, "issue_count": 0, "issues": []}
    review_summary = build_human_review(conn, owner_kind, owner_id, include_doctor=False, limit=3, persist=False, source="managed_review_observability").get("summary", {})
    readiness, signals, recommendations = _managed_review_observability_signals(policy, validation, state, recent_runs, latest_acceptance, latest_stress, doctor)
    report = {
        "ok": True,
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "status": "created",
        "readiness_status": readiness,
        "policy": policy,
        "policy_validation": validation,
        "managed_state": state,
        "recent_runs": recent_runs,
        "latest_acceptance": latest_acceptance,
        "latest_stress": latest_stress,
        "doctor": doctor,
        "review_summary": review_summary,
        "signals": signals,
        "recommendations": recommendations,
    }
    rendered = render_managed_review_observability(report)
    report["rendered"] = rendered
    if persist:
        rid = new_id("mgrevobs")
        conn.execute(
            """INSERT INTO human_review_managed_observability_reports(
                 id, owner_kind, owner_id, status, readiness_status, policy_json, policy_validation_json,
                 managed_state_json, recent_runs_json, latest_acceptance_json, latest_stress_json,
                 doctor_json, review_summary_json, signals_json, recommendations_json, rendered_text
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, owner_kind, owner_id, "created", readiness, dumps(policy), dumps(validation), dumps(state), dumps(recent_runs), dumps(latest_acceptance), dumps(latest_stress), dumps(doctor), dumps(review_summary), dumps(signals), dumps(recommendations), rendered),
        )
        report["report_id"] = rid
        append_audit(conn, owner_kind, owner_id, "managed_review_observability_report", "info" if readiness not in {"blocked"} else "error", f"Managed review observability: {readiness}", {"report_id": rid, "readiness_status": readiness})
    return report


def list_managed_review_observability_reports(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM human_review_managed_observability_reports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [_decode_managed_review_observability_report(r) for r in rows]


def get_managed_review_observability_report(conn, owner_kind: str, owner_id: str, report_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_managed_observability_reports WHERE id=? AND owner_kind=? AND owner_id=?",
        (report_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"managed review observability report not found: {report_id}")
    return _decode_managed_review_observability_report(row)


def _decode_managed_review_observability_report(row) -> dict[str, Any]:
    d = dict(row)
    for k in ["policy_json", "policy_validation_json", "managed_state_json", "recent_runs_json", "latest_acceptance_json", "latest_stress_json", "doctor_json", "review_summary_json", "signals_json", "recommendations_json"]:
        d[k[:-5]] = loads(d.pop(k), [] if k.endswith("runs_json") or k in {"signals_json", "recommendations_json"} else {})
    if "rendered_text" in d:
        d["rendered"] = d.pop("rendered_text")
    return d


def build_managed_review_release_readiness_report(conn, owner_kind: str, owner_id: str, *, persist: bool = True) -> dict[str, Any]:
    obs = build_managed_review_observability_report(conn, owner_kind, owner_id, persist=True)
    checks: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def check(name: str, ok: bool, message: str, *, required: bool = True, **extra: Any) -> None:
        row = {"name": name, "ok": bool(ok), "message": message, "required": required, **extra}
        checks.append(row)
        if not ok and required:
            blockers.append(row)
        elif not ok:
            warnings.append(row)

    policy = obs.get("policy") or {}
    validation = obs.get("policy_validation") or {}
    acc = obs.get("latest_acceptance") or {}
    stress = obs.get("latest_stress") or {}
    doctor = obs.get("doctor") or {}
    state = obs.get("managed_state") or {}

    check("policy_has_no_conflicts", not validation.get("conflict_count"), f"conflicts={validation.get('conflict_count')}")
    check("managed_loop_explicitly_enabled", bool(policy.get("allow_agent_managed_loop")), "allow_agent_managed_loop must be true for automatic managed review", required=False)
    check("daily_limit_positive", int(policy.get("agent_managed_daily_action_limit") or 0) > 0, f"limit={policy.get('agent_managed_daily_action_limit')}")
    check("acceptance_passed", acc.get("status") == "passed", f"latest acceptance={acc.get('status')}")
    check("stress_passed", stress.get("status") in {"passed", "completed"}, f"latest stress={stress.get('status')}")
    check("doctor_clean", not doctor.get("issue_count"), f"doctor issues={doctor.get('issue_count')}", required=False)
    check("failure_budget_available", int(state.get("failure_count") or 0) < int(policy.get("agent_managed_failure_budget") or 0), f"failures today={state.get('failure_count')} budget={policy.get('agent_managed_failure_budget')}", required=False)

    required_total = sum(1 for c in checks if c.get("required")) or 1
    required_ok = sum(1 for c in checks if c.get("required") and c.get("ok"))
    optional_ok = sum(1 for c in checks if not c.get("required") and c.get("ok"))
    score = int(round((required_ok / required_total) * 80 + optional_ok * 5))
    score = max(0, min(score, 100))
    if blockers:
        readiness = "blocked"
        recommendation = "Do not enable agent-managed review automatically until blockers are resolved."
    elif warnings:
        readiness = "ready_with_warnings"
        recommendation = "Managed review can be trialed, but review warnings first and keep daily limits low."
    else:
        readiness = "ready"
        recommendation = "Managed review is ready for safe automatic operation under the current policy."
    rendered = render_managed_review_release_readiness({"readiness_status": readiness, "score": score, "checks": checks, "blockers": blockers, "warnings": warnings, "recommendation": recommendation, "observability_report_id": obs.get("report_id")})
    report = {"ok": True, "owner_kind": owner_kind, "owner_id": owner_id, "status": "created", "readiness_status": readiness, "score": score, "checks": checks, "blockers": blockers, "warnings": warnings, "observability_report_id": obs.get("report_id"), "recommendation": recommendation, "rendered": rendered}
    if persist:
        rid = new_id("mgrevready")
        conn.execute(
            """INSERT INTO human_review_managed_release_readiness_reports(
                 id, owner_kind, owner_id, status, readiness_status, score, checks_json, blockers_json,
                 warnings_json, observability_report_id, recommendation, rendered_text
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, owner_kind, owner_id, "created", readiness, score, dumps(checks), dumps(blockers), dumps(warnings), obs.get("report_id"), recommendation, rendered),
        )
        report["report_id"] = rid
        append_audit(conn, owner_kind, owner_id, "managed_review_release_readiness_report", "info" if readiness != "blocked" else "warning", f"Managed review release readiness: {readiness}", {"report_id": rid, "score": score})
    return report


def render_managed_review_release_readiness(report: dict[str, Any]) -> str:
    lines = ["LifeEngine Managed Review Release Readiness", "=========================================="]
    lines.append(f"状态：{report.get('readiness_status')} / score={report.get('score')}")
    lines.append(f"建议：{report.get('recommendation')}")
    checks = report.get("checks") or []
    if checks:
        lines.append("")
        lines.append("检查：")
        for c in checks:
            mark = "✓" if c.get("ok") else "✗"
            required = "required" if c.get("required") else "optional"
            lines.append(f"- {mark} {c.get('name')} ({required}): {c.get('message')}")
    return "\n".join(lines)


def list_managed_review_release_readiness_reports(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM human_review_managed_release_readiness_reports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [_decode_managed_review_release_readiness_report(r) for r in rows]


def get_managed_review_release_readiness_report(conn, owner_kind: str, owner_id: str, report_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM human_review_managed_release_readiness_reports WHERE id=? AND owner_kind=? AND owner_id=?",
        (report_id, owner_kind, owner_id),
    ).fetchone()
    if not row:
        raise ValueError(f"managed review release readiness report not found: {report_id}")
    return _decode_managed_review_release_readiness_report(row)


def _decode_managed_review_release_readiness_report(row) -> dict[str, Any]:
    d = dict(row)
    for k in ["checks_json", "blockers_json", "warnings_json"]:
        d[k[:-5]] = loads(d.pop(k), [])
    if "rendered_text" in d:
        d["rendered"] = d.pop("rendered_text")
    return d
