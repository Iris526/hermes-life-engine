"""Autonomy planner for LifeEngine.

v0.11.6 makes autonomy sleep-aware: the planner reads SleepDayState,
RealtimeState, and scalar body resources before it decides whether to push a
long-term goal, downshift to a lighter step, or schedule recovery sleep.  It
still never writes directly: proposed ops become life facts only through
LifeOps, Validator, Receipt, Journal, and Trace.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from .jsonutil import dumps, loads
from .time_utils import now_iso, parse_datetime
from .trace import append_journal, new_id


_TERMINAL_EVENT_STATUSES = (
    "completed", "cancelled", "failed", "abandoned", "archived", "discarded", "missed"
)


def _mode(control: dict[str, Any]) -> str:
    gates = control.get("module_gates") or {}
    return str(gates.get("autonomy", "manual") or "manual")


def _account_map(conn, owner_kind: str, owner_id: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT resource_key,current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchall()
    return {r["resource_key"]: float(r["current_value"] or 0) for r in rows}


def _active_events_for_goal(conn, owner_kind: str, owner_id: str, goal_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""SELECT e.* FROM event_goal_links l
              JOIN events e ON e.id=l.event_id
             WHERE l.owner_kind=? AND l.owner_id=? AND l.goal_id=?
               AND e.status NOT IN ({','.join(['?'] * len(_TERMINAL_EVENT_STATUSES))})
             ORDER BY e.priority DESC, e.updated_at DESC""",
        (owner_kind, owner_id, goal_id, *_TERMINAL_EVENT_STATUSES),
    ).fetchall()
    return [dict(r) for r in rows]


def _recent_decision_exists(conn, owner_kind: str, owner_id: str, minutes: int = 60) -> bool:
    row = conn.execute(
        """SELECT 1 FROM autonomy_decisions
              WHERE owner_kind=? AND owner_id=? AND status IN ('committed','proposed')
                AND created_at >= datetime('now', ?)
              LIMIT 1""",
        (owner_kind, owner_id, f"-{int(minutes)} minutes"),
    ).fetchone()
    return bool(row)


def _get_latest_sleep_day_state(conn, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM sleep_day_states WHERE owner_kind=? AND owner_id=? ORDER BY date_key DESC LIMIT 1",
        (owner_kind, owner_id),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["all_nighter"] = bool(d.get("all_nighter"))
    d["nap_recommended"] = bool(d.get("nap_recommended"))
    for raw, public, default in [
        ("resource_ledger_ids_json", "resource_ledger_ids", []),
        ("body_state_json", "body_state", {}),
        ("mind_state_json", "mind_state", {}),
    ]:
        if raw in d:
            d[public] = loads(d.pop(raw), default)
    return d


def _get_realtime_state_safe(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    try:
        from .events import get_realtime_state
        return get_realtime_state(conn, owner_kind, owner_id)
    except Exception:
        return {"body_state": {}, "mind_state": {}, "mode": "unknown"}


def _existing_recovery_plan(conn, owner_kind: str, owner_id: str, date_key: str | None = None) -> dict[str, Any] | None:
    params: list[Any] = [owner_kind, owner_id]
    where = "owner_kind=? AND owner_id=? AND status IN ('planned','scheduled','in_progress') AND plan_type='recovery_sleep'"
    if date_key:
        where += " AND date=?"
        params.append(date_key)
    row = conn.execute(
        f"SELECT * FROM sleep_plans WHERE {where} ORDER BY planned_sleep_at_ts DESC, created_at DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    return dict(row) if row else None


def _sleep_context(conn, owner_kind: str, owner_id: str, accounts: dict[str, float], now: str | None = None) -> dict[str, Any]:
    day = _get_latest_sleep_day_state(conn, owner_kind, owner_id)
    realtime = _get_realtime_state_safe(conn, owner_kind, owner_id)
    body = realtime.get("body_state") or {}
    mind = realtime.get("mind_state") or {}

    fatigue_account = accounts.get("fatigue")
    recovery_pressure = int((day or {}).get("recovery_pressure") or body.get("recovery_pressure") or 0)
    sleep_debt = int((day or {}).get("cumulative_sleep_debt_minutes") or body.get("sleep_debt_minutes") or 0)
    fatigue = max(
        int((day or {}).get("fatigue_delta") or 0),
        int(body.get("fatigue") or body.get("fatigue_delta_from_sleep") or 0),
        int(fatigue_account or 0),
    )
    focus_penalty = int((day or {}).get("focus_penalty") or mind.get("focus_penalty_from_sleep") or 0)
    mood_penalty = int((day or {}).get("mood_penalty") or mind.get("mood_penalty_from_sleep") or 0)
    all_nighter = bool((day or {}).get("all_nighter") or body.get("all_nighter"))
    nap_recommended = bool((day or {}).get("nap_recommended") or body.get("nap_recommended"))
    date_key = (day or {}).get("date_key") or (now or now_iso())[:10]
    existing_recovery = _existing_recovery_plan(conn, owner_kind, owner_id, date_key)

    severity = "ok"
    if all_nighter or recovery_pressure >= 85 or fatigue >= 75:
        severity = "severe"
    elif recovery_pressure >= 60 or nap_recommended or fatigue >= 55 or focus_penalty >= 30:
        severity = "moderate"
    elif sleep_debt >= 90 or fatigue >= 35:
        severity = "mild"

    return {
        "date_key": date_key,
        "sleep_day_state": day,
        "sleep_day_state_id": (day or {}).get("id"),
        "realtime_mode": realtime.get("mode"),
        "recovery_pressure": recovery_pressure,
        "sleep_debt_minutes": sleep_debt,
        "fatigue": fatigue,
        "focus_penalty": focus_penalty,
        "mood_penalty": mood_penalty,
        "all_nighter": all_nighter,
        "nap_recommended": nap_recommended,
        "existing_recovery_plan_id": existing_recovery.get("id") if existing_recovery else None,
        "existing_recovery_plan": existing_recovery,
        "severity": severity,
        "should_recover": (all_nighter or recovery_pressure >= 70 or nap_recommended or fatigue >= 65),
        "should_downshift": (severity in {"moderate", "severe"} or focus_penalty >= 25 or fatigue >= 45),
    }


def _recovery_sleep_op(now: str | None, sleep_ctx: dict[str, Any], duration_minutes: int | None = None) -> dict[str, Any]:
    base = parse_datetime(now or now_iso(), default_tz="UTC") + timedelta(minutes=30)
    pressure = int(sleep_ctx.get("recovery_pressure") or 0)
    duration = int(duration_minutes or (45 if pressure >= 85 or sleep_ctx.get("all_nighter") else 30))
    start = base.isoformat()
    end = (base + timedelta(minutes=duration)).isoformat()
    return {
        "type": "CREATE_SLEEP_PLAN",
        "payload": {
            "planned_sleep_at": start,
            "planned_wake_at": end,
            "date": sleep_ctx.get("date_key"),
            "plan_type": "recovery_sleep",
            "sleep_type": "recovery_sleep",
            "timezone_name": "UTC",
            "wake_policy": "short_recovery",
            "title": "睡眠不足后的补觉 / 小憩",
            "constraints": {"generated_by": "autonomy_sleep_adjustment", "severity": sleep_ctx.get("severity")},
            "decision": {
                "recovery_pressure": sleep_ctx.get("recovery_pressure"),
                "sleep_debt_minutes": sleep_ctx.get("sleep_debt_minutes"),
                "fatigue": sleep_ctx.get("fatigue"),
                "all_nighter": sleep_ctx.get("all_nighter"),
            },
            "source": "autonomy_sleep_adjustment",
        },
    }


def _record_sleep_adjustment(conn, owner_kind: str, owner_id: str, decision_id: str, sleep_ctx: dict[str, Any],
                             adjustment_type: str, severity: str, reason: str, proposed_ops: list[dict[str, Any]]) -> None:
    conn.execute(
        """INSERT INTO autonomy_sleep_adjustments(
             id, owner_kind, owner_id, decision_id, sleep_day_state_id, adjustment_type, severity,
             reason, sleep_context_json, proposed_ops_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            new_id("autsleep"), owner_kind, owner_id, decision_id, sleep_ctx.get("sleep_day_state_id"),
            adjustment_type, severity, reason, dumps(sleep_ctx), dumps(proposed_ops),
        ),
    )
    append_journal(conn, owner_kind, owner_id, "autonomy_sleep_adjustment_recorded", {
        "decision_id": decision_id, "adjustment_type": adjustment_type, "severity": severity,
        "reason": reason, "sleep_context": sleep_ctx,
    }, "autonomy")


def get_autonomy_sleep_context(conn, owner_kind: str, owner_id: str, now: str | None = None) -> dict[str, Any]:
    return _sleep_context(conn, owner_kind, owner_id, _account_map(conn, owner_kind, owner_id), now=now)


def list_autonomy_sleep_adjustments(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM autonomy_sleep_adjustments WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sleep_context"] = loads(d.pop("sleep_context_json"), {})
        d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
        out.append(d)
    return out


def get_autonomy_decision(conn, decision_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM autonomy_decisions WHERE id=?", (decision_id,)).fetchone()
    if not row:
        raise ValueError(f"autonomy decision not found: {decision_id}")
    d = dict(row)
    d["score"] = loads(d.pop("score_json"), {})
    d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
    return d


def list_autonomy_decisions(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM autonomy_decisions WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [get_autonomy_decision(conn, r["id"]) for r in rows]


def record_autonomy_decision(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    tick_id: str | None,
    trace_id: str | None,
    mode: str,
    status: str,
    reason: str,
    selected_goal_id: str | None = None,
    selected_event_id: str | None = None,
    score: dict[str, Any] | None = None,
    proposed_ops: list[dict[str, Any]] | None = None,
    result_transaction_id: str | None = None,
    result_receipt_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    decision_id = new_id("autonomy")
    conn.execute(
        """INSERT INTO autonomy_decisions(id, owner_kind, owner_id, tick_id, trace_id, mode, status,
              reason, selected_goal_id, selected_event_id, score_json, proposed_ops_json,
              result_transaction_id, result_receipt_id, error)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            decision_id,
            owner_kind,
            owner_id,
            tick_id,
            trace_id,
            mode,
            status,
            reason,
            selected_goal_id,
            selected_event_id,
            dumps(score or {}),
            dumps(proposed_ops or []),
            result_transaction_id,
            result_receipt_id,
            error,
        ),
    )
    append_journal(
        conn,
        owner_kind,
        owner_id,
        "autonomy_decision_recorded",
        {"decision_id": decision_id, "mode": mode, "status": status, "reason": reason, "ops": proposed_ops or []},
        "autonomy",
    )
    return get_autonomy_decision(conn, decision_id)


def update_autonomy_decision_result(
    conn,
    decision_id: str,
    *,
    status: str,
    result_transaction_id: str | None = None,
    result_receipt_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    conn.execute(
        """UPDATE autonomy_decisions SET status=?, result_transaction_id=COALESCE(?, result_transaction_id),
              result_receipt_id=COALESCE(?, result_receipt_id), error=?,
              committed_at=CASE WHEN ?='committed' THEN datetime('now') ELSE committed_at END
              WHERE id=?""",
        (status, result_transaction_id, result_receipt_id, error, status, decision_id),
    )
    return get_autonomy_decision(conn, decision_id)


def plan_autonomy(
    conn,
    owner_kind: str,
    owner_id: str,
    control: dict[str, Any],
    *,
    tick_id: str | None = None,
    trace_id: str | None = None,
    manual: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    """Return an autonomy decision with proposed LifeOps."""
    mode = _mode(control)

    sleep_ctx: dict[str, Any] = {}
    adjustment: dict[str, Any] | None = None

    def finish(**kwargs: Any) -> dict[str, Any]:
        decision = record_autonomy_decision(conn, owner_kind, owner_id, **kwargs)
        if adjustment:
            _record_sleep_adjustment(
                conn, owner_kind, owner_id, decision["id"], sleep_ctx,
                adjustment.get("type", "sleep_context"), adjustment.get("severity", sleep_ctx.get("severity", "info")),
                kwargs.get("reason") or adjustment.get("reason", "sleep-aware autonomy adjustment"),
                kwargs.get("proposed_ops") or [],
            )
        return decision

    if owner_kind != "agent":
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="autonomy only runs for agent life", proposed_ops=[])
    if mode == "off":
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="autonomy module off", proposed_ops=[])
    if mode == "manual" and not manual:
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="autonomy mode is manual", proposed_ops=[])
    if not manual and _recent_decision_exists(conn, owner_kind, owner_id, minutes=45):
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="recent autonomy decision cooldown", proposed_ops=[])

    accounts = _account_map(conn, owner_kind, owner_id)
    energy = accounts.get("energy")
    focus = accounts.get("focus")
    mood = accounts.get("mood")
    sleep_ctx = _sleep_context(conn, owner_kind, owner_id, accounts, now=now)
    score: dict[str, Any] = {"energy": energy, "focus": focus, "mood": mood, "sleep": sleep_ctx}

    # Sleep debt and all-nighter pressure are allowed to override goal-pushing.
    # This keeps the agent from behaving like an always-on worker after poor sleep.
    if sleep_ctx.get("should_recover") and not sleep_ctx.get("existing_recovery_plan_id"):
        ops = [_recovery_sleep_op(now, sleep_ctx)]
        adjustment = {"type": "recovery_sleep_planned", "severity": sleep_ctx.get("severity", "moderate")}
        return finish(
            tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed",
            reason="sleep debt or fatigue requires recovery sleep before autonomous goal work",
            score=score, proposed_ops=ops,
        )

    if sleep_ctx.get("all_nighter") and sleep_ctx.get("existing_recovery_plan_id"):
        ops = [{"type": "CREATE_EVENT", "payload": {
            "title": "通宵后的低强度恢复安排",
            "description": "Autonomy detected an all-nighter and chose a low-demand recovery activity instead of intensive work.",
            "event_type": "rest",
            "event_category": "health",
            "activity_domain": "recovery",
            "source": "autonomy_sleep_adjustment",
            "status": "planned",
            "priority": 75,
            "importance": 70,
            "tags": ["sleep_debt", "all_nighter", "recovery"],
            "state_effects": {"fatigue": "reduce", "energy": "protect"},
            "resource_costs": {},
        }}]
        adjustment = {"type": "all_nighter_downshift", "severity": "severe"}
        return finish(
            tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed",
            reason="all-nighter downshifted autonomy to low-intensity recovery",
            score=score, proposed_ops=ops,
        )

    if energy is not None and energy < 15:
        ops = [{"type": "CREATE_EVENT", "payload": {
            "title": "休息并恢复精力",
            "description": "Autonomy Planner detected low energy and chose recovery instead of pushing a demanding goal.",
            "event_type": "rest",
            "event_category": "health",
            "activity_domain": "recovery",
            "source": "autonomy",
            "status": "planned",
            "priority": 70,
            "importance": 60,
            "resource_costs": {},
        }}]
        if sleep_ctx.get("severity") in {"mild", "moderate", "severe"}:
            adjustment = {"type": "low_energy_sleep_aware_recovery", "severity": sleep_ctx.get("severity", "mild")}
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed", reason="low energy recovery", score=score, proposed_ops=ops)

    goals = conn.execute(
        """SELECT * FROM goals WHERE owner_kind=? AND owner_id=? AND status='active'
              ORDER BY priority DESC, updated_at ASC LIMIT 10""",
        (owner_kind, owner_id),
    ).fetchall()
    if not goals:
        if mode == "full":
            ops = [{"type": "CREATE_DIARY", "payload": {
                "diary_type": "future_outlook",
                "content": "我暂时没有明确的长期目标。今天的自主复盘是：先观察自己的生活节奏，再决定下一步想培养什么。",
                "privacy": "agent_private",
            }}]
            return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed", reason="no active goals; write outlook", score=score, proposed_ops=ops)
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="no active goals", score=score, proposed_ops=[])

    selected = None
    selected_open_events: list[dict[str, Any]] = []
    for g in goals:
        open_events = _active_events_for_goal(conn, owner_kind, owner_id, g["id"])
        if not open_events:
            selected = dict(g)
            selected_open_events = []
            break
        if selected is None:
            selected = dict(g)
            selected_open_events = open_events

    if selected is None:
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="no selectable goal", score=score, proposed_ops=[])

    score.update({"goal_id": selected["id"], "goal_title": selected["title"], "goal_priority": selected["priority"]})

    if selected_open_events:
        if mode == "planned_only":
            return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="existing goal event already pending", selected_goal_id=selected["id"], selected_event_id=selected_open_events[0]["id"], score=score, proposed_ops=[])
        if mode in {"full", "low_spontaneity"}:
            ev = selected_open_events[0]
            summary = f"我还记得要推进『{selected['title']}』，当前待推进事项是『{ev['title']}』。"
            if sleep_ctx.get("should_downshift"):
                summary += "不过今天睡眠状态一般，我会把强度放轻。"
                adjustment = {"type": "existing_goal_reminder_downshifted", "severity": sleep_ctx.get("severity", "moderate")}
            ops = [{"type": "CREATE_PROACTIVE_INTENT", "payload": {
                "target_type": "self_journal",
                "intent_type": "report_progress",
                "summary": summary,
                "importance": min(90, int(selected["priority"] or 50) + 10),
                "urgency": 45,
                "novelty": 20,
                "relationship_relevance": 30,
                "privacy_level": "agent_private",
                "status": "generated",
            }}]
            return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed", reason="existing goal event reminder", selected_goal_id=selected["id"], selected_event_id=ev["id"], score=score, proposed_ops=ops)
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="existing goal event pending", selected_goal_id=selected["id"], selected_event_id=selected_open_events[0]["id"], score=score, proposed_ops=[])

    if mode == "planned_only":
        return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="planned_only will not create new goal events", selected_goal_id=selected["id"], score=score, proposed_ops=[])

    goal_type = selected.get("goal_type") or "lifestyle"
    base_event_type = {"study": "study", "health": "health", "fitness": "health", "creative": "creative", "career": "work", "finance": "finance", "relationship": "social"}.get(goal_type, "self_reflection")
    event_type = base_event_type
    title = f"推进目标：{selected['title']}"
    description = "Autonomy Planner created a small next-step event for an active goal."
    tags = ["autonomy", "goal_step"]

    costs: dict[str, float] = {}
    if energy is not None:
        costs["energy"] = -8 if base_event_type in {"study", "work", "creative"} else -5
    if focus is not None and base_event_type in {"study", "work", "creative"}:
        costs["focus"] = -10

    if sleep_ctx.get("should_downshift") and base_event_type in {"study", "work", "creative"}:
        title = f"轻量推进目标：{selected['title']}"
        description = "SleepDayState indicated fatigue/sleep debt, so autonomy chose a lighter next step instead of intensive work."
        event_type = "self_reflection" if base_event_type != "study" else "study"
        tags.extend(["sleep_adjusted", "low_intensity"])
        if energy is not None:
            costs["energy"] = -3
        if focus is not None:
            costs["focus"] = -3
        adjustment = {"type": "goal_step_downshifted", "severity": sleep_ctx.get("severity", "moderate")}

    ops = [{"type": "CREATE_EVENT", "payload": {
        "title": title,
        "description": description,
        "event_type": event_type,
        "event_category": "work" if event_type == "work" else ("study" if event_type == "study" else "reflection" if event_type == "self_reflection" else event_type),
        "activity_domain": "goal_progress",
        "source": "autonomy_sleep_adjustment" if adjustment else "autonomy",
        "status": "planned",
        "priority": int(selected.get("priority") or 50),
        "importance": min(100, int(selected.get("priority") or 50) + 10),
        "progress": 0,
        "goal_id": selected["id"],
        "tags": tags,
        "attributes": {"sleep_adjusted": bool(adjustment), "sleep_context": {k: sleep_ctx.get(k) for k in ["severity", "recovery_pressure", "sleep_debt_minutes", "fatigue", "focus_penalty", "all_nighter"]}},
        "resource_costs": costs,
    }}]
    if mode == "full":
        ops.append({"type": "CREATE_PROACTIVE_INTENT", "payload": {
            "target_type": "self_journal",
            "intent_type": "report_progress",
            "summary": f"我给自己安排了一个{'轻量' if adjustment else '小'}步骤来推进『{selected['title']}』。",
            "importance": min(85, int(selected.get("priority") or 50) + 5),
            "urgency": 40,
            "novelty": 35,
            "relationship_relevance": 35,
            "privacy_level": "agent_private",
            "status": "generated",
        }})
    return finish(tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed", reason="sleep-aware active goal next event" if adjustment else "active goal needs next event", selected_goal_id=selected["id"], score=score, proposed_ops=ops)


# Optional LifeOp helpers reserved for symbolic autonomy ops. Current planner
# usually emits normal CREATE_EVENT/CREATE_PROACTIVE_INTENT ops, but keeping
# these handlers makes the runtime extensible without a schema fork.
def apply_autonomy_goal_step(conn, owner_kind: str, owner_id: str, goal_id: str, title: str | None = None,
                             description: str | None = None, event_type: str | None = None,
                             start: str | None = None, end: str | None = None, weight: float = 5,
                             priority: int = 50, importance: int = 50,
                             resource_costs: dict[str, float] | None = None,
                             canon_version: int | None = None, **_ignored: Any) -> dict[str, Any]:
    from .events import create_event, create_schedule_block
    from .goals import get_goal, link_event_to_goal

    goal = get_goal(conn, owner_kind, owner_id, goal_id)
    event = create_event(
        conn, owner_kind, owner_id,
        title=title or f"推进目标：{goal['title']}",
        description=description or f"Autonomy next step for goal {goal['title']}",
        event_type=event_type or goal.get("goal_type") or "lifestyle",
        source="autonomy",
        status="planned",
        planned_start=start,
        planned_end=end,
        priority=priority,
        importance=importance,
        resource_costs=resource_costs or {},
        goal_id=goal_id,
        canon_version=canon_version,
    )
    link = link_event_to_goal(conn, owner_kind, owner_id, goal_id, event["id"], role="autonomy_step", weight=float(weight), source="autonomy")
    block = None
    if start and end:
        block = create_schedule_block(conn, owner_kind, owner_id, start=start, end=end, event_id=event["id"], block_type="planned_event", status="planned")
    append_journal(conn, owner_kind, owner_id, "autonomy_goal_step_created", {"goal_id": goal_id, "event_id": event["id"], "schedule_block_id": block.get("id") if block else None}, "autonomy", canon_version=canon_version)
    return {"goal": goal, "event": event, "link": link, "schedule_block": block}


def apply_autonomy_schedule_event(conn, owner_kind: str, owner_id: str, event_id: str,
                                  start: str, end: str, reason: str | None = None,
                                  **_ignored: Any) -> dict[str, Any]:
    from .events import create_schedule_block, get_event

    event = get_event(conn, event_id)
    if event["owner_kind"] != owner_kind or event["owner_id"] != owner_id:
        raise ValueError("event owner mismatch")
    block = create_schedule_block(conn, owner_kind, owner_id, start=start, end=end, event_id=event_id, block_type="planned_event", status="planned")
    append_journal(conn, owner_kind, owner_id, "autonomy_event_scheduled", {"event_id": event_id, "schedule_block_id": block["id"], "reason": reason}, "autonomy")
    return {"event": get_event(conn, event_id), "schedule_block": block}
