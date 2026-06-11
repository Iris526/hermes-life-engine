"""Narrative execution simulator and serendipity engine for LifeEngine v0.9.

The simulator turns due schedule blocks into execution decisions. It is
conservative and deterministic: it never mutates life state directly. It records
an execution decision and returns proposed LifeOps. Runtime commits those ops
through the normal Validator -> Transaction -> Journal -> CommitReceipt path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .events import get_event, create_event
from .jsonutil import dumps, loads
from .time_utils import normalized_iso, parse_datetime
from .trace import append_journal, new_id

TERMINAL_EVENT_STATUSES = {"completed", "cancelled", "failed", "abandoned", "archived", "discarded"}
OUTDOOR_EVENT_TYPES = {"purchase", "travel", "social", "health", "fitness", "walk", "outdoor"}
BAD_WEATHER_WORDS = {"rain", "light_rain", "heavy_rain", "storm", "snow", "typhoon", "thunder", "windy"}
SLEEP_SENSITIVE_TYPES = {"work", "study", "creative", "fitness", "health", "purchase", "travel", "social", "maintenance", "fieldwork", "repair_task"}
SLEEP_EXEMPT_TYPES = {"sleep", "core_sleep", "nap", "recovery_sleep", "dream", "meal", "reflection", "serendipity", "rest"}


def _row_dict(row) -> dict[str, Any] | None:
    if not row:
        return None
    return dict(row)


def _decode_decision(row) -> dict[str, Any]:
    d = dict(row)
    d["score"] = loads(d.pop("score_json"), {})
    d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
    return d


def _decode_sleep_adjustment_row(row) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    d["sleep_context"] = loads(d.pop("sleep_context_json"), {})
    d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
    return d


def get_execution_decision(conn, decision_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM execution_decisions WHERE id=?", (decision_id,)).fetchone()
    if not row:
        raise ValueError(f"execution decision not found: {decision_id}")
    d = _decode_decision(row)
    adj = conn.execute("SELECT * FROM execution_sleep_adjustments WHERE execution_decision_id=? ORDER BY created_at DESC LIMIT 1", (decision_id,)).fetchone()
    if adj:
        d["sleep_adjustment"] = _decode_sleep_adjustment_row(adj)
    return d


def list_execution_decisions(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM execution_decisions WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [_decode_decision(r) for r in rows]


def record_execution_decision(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    tick_id: str | None,
    trace_id: str | None,
    wake_job_id: str | None,
    schedule_block_id: str | None,
    event_id: str | None,
    decision_type: str,
    status: str,
    reason: str,
    score: dict[str, Any] | None = None,
    proposed_ops: list[dict[str, Any]] | None = None,
    result_transaction_id: str | None = None,
    result_receipt_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    decision_id = new_id("exec")
    conn.execute(
        """INSERT INTO execution_decisions(id, owner_kind, owner_id, tick_id, trace_id, wake_job_id,
              schedule_block_id, event_id, decision_type, status, reason, score_json, proposed_ops_json,
              result_transaction_id, result_receipt_id, error)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            decision_id,
            owner_kind,
            owner_id,
            tick_id,
            trace_id,
            wake_job_id,
            schedule_block_id,
            event_id,
            decision_type,
            status,
            reason,
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
        "execution_decision_recorded",
        {"decision_id": decision_id, "decision_type": decision_type, "status": status, "event_id": event_id, "reason": reason, "ops": proposed_ops or []},
        "execution_simulator",
    )
    return get_execution_decision(conn, decision_id)


def update_execution_decision_result(
    conn,
    decision_id: str,
    *,
    status: str,
    result_transaction_id: str | None = None,
    result_receipt_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    conn.execute(
        """UPDATE execution_decisions SET status=?, result_transaction_id=COALESCE(?, result_transaction_id),
              result_receipt_id=COALESCE(?, result_receipt_id), error=?,
              committed_at=CASE WHEN ?='committed' THEN datetime('now') ELSE committed_at END
              WHERE id=?""",
        (status, result_transaction_id, result_receipt_id, error, status, decision_id),
    )
    return get_execution_decision(conn, decision_id)


def list_serendipity_events(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM serendipity_events WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["emotional_impact"] = loads(d.pop("emotional_impact_json"), {})
        d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
        out.append(d)
    return out


def apply_serendipity_event(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    title: str,
    description: str | None = None,
    serendipity_type: str = "minor_discovery",
    intensity: int = 25,
    trigger_event_id: str | None = None,
    trigger_result_id: str | None = None,
    emotional_impact: dict[str, Any] | None = None,
    proposed_ops: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
    canon_version: int | None = None,
    source: str = "serendipity",
    **_ignored: Any,
) -> dict[str, Any]:
    event = create_event(
        conn,
        owner_kind,
        owner_id,
        title=title,
        description=description,
        event_type="serendipity",
        source=source,
        status="completed",
        priority=30,
        importance=max(10, min(100, int(intensity))),
        progress=100,
        visibility="agent_private" if owner_kind == "agent" else "user_private",
        confidence=0.85,
        parent_event_id=trigger_event_id,
        canon_version=canon_version,
    )
    sid = new_id("serendipity")
    conn.execute(
        """INSERT INTO serendipity_events(id, owner_kind, owner_id, event_id, trigger_event_id, trigger_result_id,
              serendipity_type, title, description, intensity, emotional_impact_json, proposed_ops_json, status, trace_id)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sid,
            owner_kind,
            owner_id,
            event["id"],
            trigger_event_id,
            trigger_result_id,
            serendipity_type,
            title,
            description,
            int(intensity),
            dumps(emotional_impact or {}),
            dumps(proposed_ops or []),
            "committed",
            trace_id,
        ),
    )
    append_journal(conn, owner_kind, owner_id, "serendipity_event_created", {"serendipity_id": sid, "event_id": event["id"], "title": title}, source, canon_version=canon_version)
    row = dict(conn.execute("SELECT * FROM serendipity_events WHERE id=?", (sid,)).fetchone())
    row["emotional_impact"] = loads(row.pop("emotional_impact_json"), {})
    row["proposed_ops"] = loads(row.pop("proposed_ops_json"), [])
    row["event"] = event
    return row


def _latest_weather(conn, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT result_json,status FROM truth_source_reads
              WHERE owner_kind=? AND owner_id=? AND domain='weather'
                AND status IN ('observed','cached','resolved','simulated','cached_stale')
              ORDER BY created_at DESC LIMIT 1""",
        (owner_kind, owner_id),
    ).fetchone()
    if not row:
        return None
    return loads(row["result_json"], {})


def _weather_is_bad(weather: dict[str, Any] | None) -> bool:
    if not weather:
        return False
    text = " ".join(str(weather.get(k, "")) for k in ("condition", "summary", "text", "description")).lower()
    return any(w in text for w in BAD_WEATHER_WORDS)


def _resource_shortages(conn, owner_kind: str, owner_id: str, resource_costs: dict[str, Any]) -> list[dict[str, Any]]:
    shortages = []
    for key, raw_delta in (resource_costs or {}).items():
        try:
            delta = float(raw_delta)
        except Exception:
            continue
        if delta >= 0:
            continue
        row = conn.execute(
            """SELECT a.current_value, d.min_value FROM resource_accounts a
                   LEFT JOIN resource_definitions d ON d.owner_kind=a.owner_kind AND d.owner_id=a.owner_id AND d.key=a.resource_key
                 WHERE a.owner_kind=? AND a.owner_id=? AND a.resource_key=?""",
            (owner_kind, owner_id, key),
        ).fetchone()
        if not row:
            shortages.append({"resource_key": key, "reason": "missing_account", "required_delta": delta})
            continue
        current = float(row["current_value"] or 0)
        min_value = float(row["min_value"] if row["min_value"] is not None else 0)
        after = current + delta
        if after < min_value:
            shortages.append({"resource_key": key, "current": current, "delta": delta, "min_value": min_value, "after": after})
    return shortages


def _dependencies_unmet(conn, owner_kind: str, owner_id: str, event_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT depends_on_event_id FROM event_dependencies
              WHERE owner_kind=? AND owner_id=? AND event_id=? AND status='active'""",
        (owner_kind, owner_id, event_id),
    ).fetchall()
    unmet = []
    for row in rows:
        dep = conn.execute("SELECT status FROM events WHERE owner_kind=? AND owner_id=? AND id=?", (owner_kind, owner_id, row["depends_on_event_id"])).fetchone()
        if not dep or dep["status"] != "completed":
            unmet.append(row["depends_on_event_id"])
    return unmet



def _decode_sleep_day_state_row(row) -> dict[str, Any] | None:
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


def _latest_sleep_execution_context(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """Return the latest sleep-day/realtime state as an execution pressure context."""
    day_row = conn.execute(
        "SELECT * FROM sleep_day_states WHERE owner_kind=? AND owner_id=? ORDER BY date_key DESC LIMIT 1",
        (owner_kind, owner_id),
    ).fetchone()
    day = _decode_sleep_day_state_row(day_row)
    state_row = conn.execute(
        "SELECT * FROM agent_realtime_state WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchone()
    realtime = dict(state_row) if state_row else {}
    body = loads(realtime.get("body_state_json"), {}) if realtime else {}
    mind = loads(realtime.get("mind_state_json"), {}) if realtime else {}
    fatigue_account = conn.execute(
        "SELECT current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key='fatigue'",
        (owner_kind, owner_id),
    ).fetchone()
    fatigue_value = float(fatigue_account[0]) if fatigue_account else 0.0
    recovery_pressure = int((day or {}).get("recovery_pressure") or body.get("recovery_pressure") or 0)
    sleep_debt = int((day or {}).get("cumulative_sleep_debt_minutes") or body.get("sleep_debt_minutes") or 0)
    fatigue = max(
        int((day or {}).get("fatigue_delta") or 0),
        int(body.get("fatigue") or body.get("fatigue_delta_from_sleep") or 0),
        int(fatigue_value or 0),
    )
    focus_penalty = int((day or {}).get("focus_penalty") or mind.get("focus_penalty_from_sleep") or 0)
    mood_penalty = int((day or {}).get("mood_penalty") or mind.get("mood_penalty_from_sleep") or 0)
    all_nighter = bool((day or {}).get("all_nighter") or body.get("all_nighter"))
    nap_recommended = bool((day or {}).get("nap_recommended") or body.get("nap_recommended"))
    severity = "ok"
    if all_nighter or recovery_pressure >= 85 or fatigue >= 80:
        severity = "severe"
    elif recovery_pressure >= 60 or nap_recommended or fatigue >= 55 or focus_penalty >= 30:
        severity = "moderate"
    elif sleep_debt >= 90 or fatigue >= 35 or focus_penalty >= 15:
        severity = "mild"
    return {
        "sleep_day_state": day,
        "sleep_day_state_id": (day or {}).get("id"),
        "date_key": (day or {}).get("date_key"),
        "realtime_mode": realtime.get("mode"),
        "sleep_debt_minutes": sleep_debt,
        "recovery_pressure": recovery_pressure,
        "fatigue": fatigue,
        "focus_penalty": focus_penalty,
        "mood_penalty": mood_penalty,
        "all_nighter": all_nighter,
        "nap_recommended": nap_recommended,
        "severity": severity,
        "should_postpone": severity == "severe" or recovery_pressure >= 80,
        "should_downshift": severity in {"moderate", "severe"} or fatigue >= 55 or focus_penalty >= 25,
    }


def get_execution_sleep_context(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    return _latest_sleep_execution_context(conn, owner_kind, owner_id)


def list_execution_sleep_adjustments(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM execution_sleep_adjustments WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [_decode_sleep_adjustment_row(r) for r in rows]


def _event_sleep_sensitive(event: dict[str, Any]) -> bool:
    values = {str(event.get(k) or "").strip().lower() for k in ("event_type", "event_category", "activity_domain", "subtype")}
    values.discard("")
    if values & SLEEP_EXEMPT_TYPES:
        return False
    if values & SLEEP_SENSITIVE_TYPES:
        return True
    # Default to sleep-aware for meaningful planned work when it has costs or high priority.
    return bool(event.get("resource_costs")) or int(event.get("priority") or 0) >= 50


def _record_execution_sleep_adjustment(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    decision_id: str,
    sleep_ctx: dict[str, Any],
    event_id: str | None,
    schedule_block_id: str | None,
    adjustment_type: str,
    severity: str,
    reason: str,
    original_decision_type: str | None,
    adjusted_decision_type: str,
    proposed_ops: list[dict[str, Any]],
) -> dict[str, Any]:
    adj_id = new_id("execsleep")
    conn.execute(
        """INSERT INTO execution_sleep_adjustments(
             id, owner_kind, owner_id, execution_decision_id, sleep_day_state_id, event_id, schedule_block_id,
             adjustment_type, severity, reason, sleep_context_json, original_decision_type, adjusted_decision_type,
             proposed_ops_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            adj_id,
            owner_kind,
            owner_id,
            decision_id,
            sleep_ctx.get("sleep_day_state_id"),
            event_id,
            schedule_block_id,
            adjustment_type,
            severity,
            reason,
            dumps(sleep_ctx),
            original_decision_type,
            adjusted_decision_type,
            dumps(proposed_ops),
        ),
    )
    append_journal(
        conn,
        owner_kind,
        owner_id,
        "execution_sleep_adjustment_recorded",
        {
            "execution_sleep_adjustment_id": adj_id,
            "execution_decision_id": decision_id,
            "event_id": event_id,
            "schedule_block_id": schedule_block_id,
            "adjustment_type": adjustment_type,
            "severity": severity,
            "reason": reason,
            "sleep_context": sleep_ctx,
        },
        "execution_sleep_adjustment",
    )
    row = conn.execute("SELECT * FROM execution_sleep_adjustments WHERE id=?", (adj_id,)).fetchone()
    d = dict(row)
    d["sleep_context"] = loads(d.pop("sleep_context_json"), {})
    d["proposed_ops"] = loads(d.pop("proposed_ops_json"), [])
    return d


def _sleep_adjusted_ops(
    event: dict[str, Any],
    block: dict[str, Any],
    sleep_ctx: dict[str, Any],
    importance: int,
    *,
    postpone_ops_fn,
) -> tuple[str, str, str, list[dict[str, Any]]] | None:
    """Return (decision_type, adjustment_type, reason, ops) when sleep state changes execution."""
    if not _event_sleep_sensitive(event):
        return None
    severity = str(sleep_ctx.get("severity") or "ok")
    if severity == "ok" or severity == "mild":
        return None
    title = event.get("title") or "event"
    if sleep_ctx.get("should_postpone") and importance < 82:
        reason = "睡眠不足、疲劳或通宵状态导致执行推迟"
        return "postponed", "sleep_pressure_postponed", reason, postpone_ops_fn(reason, days=1, proactive=importance >= 50)
    # Important work can still happen, but only as a light/partial attempt.
    reason = "睡眠债和疲劳导致本次只能低强度部分执行"
    ops: list[dict[str, Any]] = [
        {"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "completed", "reason": reason}},
        {"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": event["id"], "status": "in_progress", "reason": "started lightly despite sleep pressure"}},
        {"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": event["id"], "status": "partial", "reason": reason}},
        {"type": "CREATE_REFLECTION", "payload": {"target_kind": "event", "target_id": event["id"], "reflection_type": "execution_review", "content": f"『{title}』因为睡眠不足和疲劳，只做了低强度的一部分。", "source": "execution_sleep_adjustment"}},
    ]
    return "partial", "sleep_pressure_downshifted", reason, ops

def _shifted_range(block: dict[str, Any], days: int = 1) -> tuple[str | None, str | None]:
    start = parse_datetime(block.get("start"))
    end = parse_datetime(block.get("end"))
    if not start or not end:
        return None, None
    return (start + timedelta(days=days)).isoformat(), (end + timedelta(days=days)).isoformat()


def _serendipity_for(event: dict[str, Any], decision_type: str) -> dict[str, Any] | None:
    event_type = str(event.get("event_type") or "other")
    importance = int(event.get("importance") or 50)
    if decision_type != "completed" or importance < 55:
        return None
    title_by_type = {
        "study": "复习时发现了一个需要补强的小点",
        "purchase": "购物时发现了一个新的偏好",
        "health": "行动后注意到自己的身体状态",
        "fitness": "练习后记录了一点身体反馈",
        "travel": "路上遇到一个小发现",
        "walk": "散步时注意到一个小发现",
        "creative": "创作时冒出一个新想法",
    }
    title = title_by_type.get(event_type)
    if not title:
        return None
    return {
        "type": "CREATE_SERENDIPITY_EVENT",
        "payload": {
            "title": title,
            "description": f"这个小事件由『{event.get('title')}』执行后的叙事模拟产生。",
            "serendipity_type": "minor_discovery" if event_type not in {"study", "fitness", "health"} else "minor_problem",
            "intensity": min(80, max(20, importance - 15)),
            "trigger_event_id": event.get("id"),
            "emotional_impact": {"mood_delta": 2 if event_type != "study" else 0, "insight": 1},
            "source": "serendipity",
        },
    }


def simulate_schedule_block_execution(
    conn,
    owner_kind: str,
    owner_id: str,
    control: dict[str, Any],
    *,
    tick_id: str | None,
    trace_id: str | None,
    wake_job_id: str | None,
    block: dict[str, Any],
    now: str | None = None,
    manual: bool = False,
) -> dict[str, Any]:
    """Record and return a deterministic narrative execution decision."""
    event_id = block.get("event_id")
    if not event_id:
        ops = [{"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "completed", "reason": "scheduled block without event elapsed"}}]
        return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=None, decision_type="block_completed", status="proposed", reason="no event attached", score={}, proposed_ops=ops)

    event = get_event(conn, event_id)
    if event.get("status") in TERMINAL_EVENT_STATUSES:
        ops = [{"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "completed", "reason": "event already terminal"}}]
        return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="skip_terminal", status="proposed", reason="event already terminal", score={"event_status": event.get("status")}, proposed_ops=ops)

    resource_costs = event.get("resource_costs") or {}
    shortages = _resource_shortages(conn, owner_kind, owner_id, resource_costs)
    unmet_dependencies = _dependencies_unmet(conn, owner_kind, owner_id, event_id)
    weather = _latest_weather(conn, owner_kind, owner_id)
    bad_weather = _weather_is_bad(weather)
    sleep_ctx = _latest_sleep_execution_context(conn, owner_kind, owner_id)
    event_type = str(event.get("event_type") or "other")
    importance = int(event.get("importance") or 50)
    score = {"importance": importance, "event_type": event_type, "shortages": shortages, "unmet_dependencies": unmet_dependencies, "weather": weather, "bad_weather": bad_weather, "sleep_context": sleep_ctx}

    def postpone_ops(reason: str, days: int = 1, proactive: bool = False) -> list[dict[str, Any]]:
        new_start, new_end = _shifted_range(block, days=days)
        ops: list[dict[str, Any]] = [
            {"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "rescheduled", "reason": reason}},
            {"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": event_id, "status": "rescheduled", "reason": reason}},
        ]
        if new_start and new_end:
            ops.append({"type": "CREATE_SCHEDULE_BLOCK", "payload": {"event_id": event_id, "start": new_start, "end": new_end, "block_type": block.get("block_type") or "planned_event", "timezone_name": block.get("timezone") or "UTC"}})
        if proactive and owner_kind == "agent":
            ops.append({"type": "CREATE_PROACTIVE_INTENT", "payload": {"target_type": "self_journal", "intent_type": "report_failure", "summary": f"『{event.get('title')}』因为{reason}被推迟了。", "importance": min(90, importance + 5), "urgency": 45, "novelty": 35, "relationship_relevance": 40, "privacy_level": "agent_private", "status": "generated", "source": "execution_simulator"}})
        return ops

    if unmet_dependencies:
        ops = postpone_ops("依赖事件尚未完成", days=1, proactive=importance >= 65)
        return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="postponed", status="proposed", reason="dependencies unmet", score=score, proposed_ops=ops)

    sleep_adjusted = _sleep_adjusted_ops(event, block, sleep_ctx, importance, postpone_ops_fn=postpone_ops)
    if sleep_adjusted:
        decision_type, adjustment_type, reason, ops = sleep_adjusted
        decision = record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type=decision_type, status="proposed", reason=reason, score=score, proposed_ops=ops)
        adjustment = _record_execution_sleep_adjustment(conn, owner_kind, owner_id, decision_id=decision["id"], sleep_ctx=sleep_ctx, event_id=event_id, schedule_block_id=block.get("id"), adjustment_type=adjustment_type, severity=str(sleep_ctx.get("severity") or "info"), reason=reason, original_decision_type="completed", adjusted_decision_type=decision_type, proposed_ops=ops)
        decision["sleep_adjustment"] = adjustment
        return decision

    if bad_weather and event_type in OUTDOOR_EVENT_TYPES and importance < 85:
        ops = postpone_ops("天气不适合执行原计划", days=2, proactive=True)
        return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="postponed", status="proposed", reason="bad weather", score=score, proposed_ops=ops)

    if shortages:
        if importance >= 75:
            ops = [
                {"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "completed", "reason": "time block elapsed but resources were insufficient"}},
                {"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": event_id, "status": "in_progress", "reason": "attempted despite resource shortage"}},
                {"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": event_id, "status": "partial", "reason": "resource shortage prevented completion"}},
                {"type": "CREATE_REFLECTION", "payload": {"target_kind": "event", "target_id": event_id, "reflection_type": "execution_review", "content": f"『{event.get('title')}』没有完全完成，因为资源不足：{shortages}。", "source": "execution_simulator"}},
            ]
            if owner_kind == "agent":
                ops.append({"type": "CREATE_PROACTIVE_INTENT", "payload": {"target_type": "self_journal", "intent_type": "ask_for_help", "summary": f"『{event.get('title')}』遇到资源不足，想重新规划。", "importance": 80, "urgency": 55, "novelty": 40, "relationship_relevance": 50, "privacy_level": "agent_private", "status": "generated", "source": "execution_simulator"}})
            return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="partial", status="proposed", reason="resource shortage", score=score, proposed_ops=ops)
        ops = postpone_ops("资源不足", days=1, proactive=True)
        return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="postponed", status="proposed", reason="resource shortage", score=score, proposed_ops=ops)

    ops = [
        {"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "completed", "reason": "execution simulator completed the scheduled block"}},
        {"type": "COMPLETE_EVENT", "payload": {"event_id": event_id, "summary": f"执行完成：{event.get('title')}", "source": "execution_simulator"}},
    ]
    if importance >= 50:
        ops.append({"type": "CREATE_MEMORY", "payload": {"memory_type": "episodic", "content": f"完成了『{event.get('title')}』。", "event_id": event_id, "source": "execution_simulator", "importance": min(100, importance)}})
    ser = _serendipity_for(event, "completed")
    if ser:
        ops.append(ser)
    if importance >= 75 and owner_kind == "agent":
        ops.append({"type": "CREATE_PROACTIVE_INTENT", "payload": {"target_type": "self_journal", "intent_type": "report_progress", "summary": f"『{event.get('title')}』已经完成，值得记录一下进展。", "importance": min(95, importance), "urgency": 35, "novelty": 45, "relationship_relevance": 45, "privacy_level": "agent_private", "status": "generated", "source": "execution_simulator"}})
    return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="completed", status="proposed", reason="resources and conditions ok", score=score, proposed_ops=ops)
