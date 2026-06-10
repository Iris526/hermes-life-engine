"""Autonomy planner for LifeEngine v0.7.

This layer starts conservative and deterministic. It proposes small autonomous
Agent-Life actions from committed Canon/Goal/Resource/Schedule state. The
planner never writes life facts directly: proposed ops become life facts only
when runtime commits them through LifeOps, Validator, Receipt, Journal, and
Trace.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id


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
        """SELECT e.* FROM event_goal_links l
              JOIN events e ON e.id=l.event_id
             WHERE l.owner_kind=? AND l.owner_id=? AND l.goal_id=?
               AND e.status NOT IN ('completed','cancelled','failed','abandoned','archived','discarded','missed')
             ORDER BY e.priority DESC, e.updated_at DESC""",
        (owner_kind, owner_id, goal_id),
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
    if owner_kind != "agent":
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="autonomy only runs for agent life", proposed_ops=[])
    if mode == "off":
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="autonomy module off", proposed_ops=[])
    if mode == "manual" and not manual:
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="autonomy mode is manual", proposed_ops=[])
    if not manual and _recent_decision_exists(conn, owner_kind, owner_id, minutes=45):
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="recent autonomy decision cooldown", proposed_ops=[])

    accounts = _account_map(conn, owner_kind, owner_id)
    energy = accounts.get("energy")
    focus = accounts.get("focus")
    mood = accounts.get("mood")
    score: dict[str, Any] = {"energy": energy, "focus": focus, "mood": mood}

    if energy is not None and energy < 15:
        ops = [{"type": "CREATE_EVENT", "payload": {
            "title": "休息并恢复精力",
            "description": "Autonomy Planner detected low energy and chose recovery instead of pushing a demanding goal.",
            "event_type": "rest",
            "source": "autonomy",
            "status": "planned",
            "priority": 70,
            "importance": 60,
            "resource_costs": {},
        }}]
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed", reason="low energy recovery", score=score, proposed_ops=ops)

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
            return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed", reason="no active goals; write outlook", score=score, proposed_ops=ops)
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="no active goals", score=score, proposed_ops=[])

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
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="no selectable goal", score=score, proposed_ops=[])

    score.update({"goal_id": selected["id"], "goal_title": selected["title"], "goal_priority": selected["priority"]})

    if selected_open_events:
        if mode == "planned_only":
            return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="existing goal event already pending", selected_goal_id=selected["id"], selected_event_id=selected_open_events[0]["id"], score=score, proposed_ops=[])
        if mode in {"full", "low_spontaneity"}:
            ev = selected_open_events[0]
            ops = [{"type": "CREATE_PROACTIVE_INTENT", "payload": {
                "target_type": "self_journal",
                "intent_type": "report_progress",
                "summary": f"我还记得要推进『{selected['title']}』，当前待推进事项是『{ev['title']}』。",
                "importance": min(90, int(selected["priority"] or 50) + 10),
                "urgency": 45,
                "novelty": 20,
                "relationship_relevance": 30,
                "privacy_level": "agent_private",
                "status": "generated",
            }}]
            return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed", reason="existing goal event reminder", selected_goal_id=selected["id"], selected_event_id=ev["id"], score=score, proposed_ops=ops)
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="existing goal event pending", selected_goal_id=selected["id"], selected_event_id=selected_open_events[0]["id"], score=score, proposed_ops=[])

    if mode == "planned_only":
        return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="skipped", reason="planned_only will not create new goal events", selected_goal_id=selected["id"], score=score, proposed_ops=[])

    goal_type = selected.get("goal_type") or "lifestyle"
    event_type = {"study": "study", "health": "health", "fitness": "health", "creative": "creative", "career": "work", "finance": "finance", "relationship": "social"}.get(goal_type, "self_reflection")
    costs: dict[str, float] = {}
    if energy is not None:
        costs["energy"] = -8 if event_type in {"study", "work", "creative"} else -5
    if focus is not None and event_type in {"study", "work", "creative"}:
        costs["focus"] = -10
    title = f"推进目标：{selected['title']}"
    ops = [{"type": "CREATE_EVENT", "payload": {
        "title": title,
        "description": "Autonomy Planner created a small next-step event for an active goal.",
        "event_type": event_type,
        "source": "autonomy",
        "status": "planned",
        "priority": int(selected.get("priority") or 50),
        "importance": min(100, int(selected.get("priority") or 50) + 10),
        "progress": 0,
        "goal_id": selected["id"],
        "resource_costs": costs,
    }}]
    if mode == "full":
        ops.append({"type": "CREATE_PROACTIVE_INTENT", "payload": {
            "target_type": "self_journal",
            "intent_type": "report_progress",
            "summary": f"我给自己安排了一个小步骤来推进『{selected['title']}』。",
            "importance": min(85, int(selected.get("priority") or 50) + 5),
            "urgency": 40,
            "novelty": 35,
            "relationship_relevance": 35,
            "privacy_level": "agent_private",
            "status": "generated",
        }})
    return record_autonomy_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, mode=mode, status="proposed", reason="active goal needs next event", selected_goal_id=selected["id"], score=score, proposed_ops=ops)

# Optional LifeOp handlers reserved for symbolic autonomy ops. Current planner
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

# Backward-compatible LifeOp helpers retained for prior v0.7 drafts.  New
# planner output uses standard CREATE_EVENT / CREATE_SCHEDULE_BLOCK ops, but
# these keep AUTONOMY_* LifeOps valid if a model or old trace replays them.
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
    from .events import get_event, create_schedule_block
    event = get_event(conn, event_id)
    if event["owner_kind"] != owner_kind or event["owner_id"] != owner_id:
        raise ValueError("event owner mismatch")
    block = create_schedule_block(conn, owner_kind, owner_id, start=start, end=end, event_id=event_id, block_type="planned_event", status="planned")
    append_journal(conn, owner_kind, owner_id, "autonomy_event_scheduled", {"event_id": event_id, "schedule_block_id": block["id"], "reason": reason}, "autonomy")
    return {"event": get_event(conn, event_id), "schedule_block": block}
