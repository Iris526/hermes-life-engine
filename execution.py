"""Narrative execution simulator and serendipity engine for LifeEngine v0.9.

The simulator turns due schedule blocks into execution decisions. It is
conservative and deterministic: it never mutates life state directly. It records
an execution decision and returns proposed LifeOps. Runtime commits those ops
through the normal Validator -> Transaction -> Journal -> CommitReceipt path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import life_author
from .events import get_event, create_event, due_schedule_blocks, due_wake_jobs
from .jsonutil import dumps, loads
from .lifecycle import event_transition_allowed
from .time_utils import normalized_iso, parse_datetime
from .trace import append_journal, new_id

TERMINAL_EVENT_STATUSES = {"completed", "cancelled", "failed", "abandoned", "archived", "discarded"}
OUTDOOR_EVENT_TYPES = {"purchase", "travel", "social", "health", "fitness", "walk", "outdoor"}
BAD_WEATHER_WORDS = {"rain", "light_rain", "heavy_rain", "storm", "snow", "typhoon", "thunder", "windy"}
SLEEP_SENSITIVE_TYPES = {"work", "study", "creative", "fitness", "health", "purchase", "travel", "social", "maintenance", "fieldwork", "repair_task"}
SLEEP_EXEMPT_TYPES = {"sleep", "core_sleep", "nap", "recovery_sleep", "dream", "meal", "reflection", "serendipity", "rest"}
BODY_RESOURCE_CLASSES = {"vital", "capacity"}

_EXECUTION_NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "narrative": {
            "type": "string",
            "description": "一句写入事件完成结果的自然叙事摘要。",
        },
        "memory": {
            "type": "string",
            "description": "一句写入 episodic memory 的已完成生活记忆。",
        },
    },
    "required": ["narrative", "memory"],
}

# LifeAuthor 输出合同：只承载偶遇/小意外的人类可见文本；生命周期限于单次
# 事务外 authoring，缺字段或字段非法时整体回落旧版确定性模板。
_SERENDIPITY_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {
            "type": "string",
            "description": "一句写入 serendipity event 标题的自然小意外摘要。",
        },
        "description": {
            "type": "string",
            "description": "一句写入 serendipity event 描述的生活化背景说明。",
        },
    },
    "required": ["title", "description"],
}


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
            """SELECT a.current_value, d.min_value, d.resource_class FROM resource_accounts a
                   LEFT JOIN resource_definitions d ON d.owner_kind=a.owner_kind AND d.owner_id=a.owner_id AND d.key=a.resource_key
                 WHERE a.owner_kind=? AND a.owner_id=? AND a.resource_key=?""",
            (owner_kind, owner_id, key),
        ).fetchone()
        if not row:
            shortages.append({"resource_key": key, "reason": "missing_account", "required_delta": delta, "resource_class": None})
            continue
        current = float(row["current_value"] or 0)
        min_value = float(row["min_value"] if row["min_value"] is not None else 0)
        after = current + delta
        if after < min_value:
            shortages.append({"resource_key": key, "current": current, "delta": delta, "min_value": min_value, "after": after, "resource_class": row["resource_class"]})
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


def _serendipity_text_fallback(event: dict[str, Any]) -> dict[str, str] | None:
    """返回 serendipity 事件的历史确定性文本。

    输入是刚完成的事件；输出包含旧版 `title` / `description`，或在事件类型不触发
    小意外时返回 `None`。调用方是事务外 authoring 准备层和事务内 execution
    消费层。无副作用；这个函数是 no-host、模型空返回、字段缺失或异常时的唯一
    逐字 fallback，维护时不能改变字符串内容。
    """
    event_type = str(event.get("event_type") or "other")
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
        "title": title,
        "description": f"这个小事件由『{event.get('title')}』执行后的叙事模拟产生。",
    }


def _clean_authored_serendipity_field(value: Any) -> str | None:
    """规整 LifeAuthor 返回的 serendipity 文本字段。

    输入是模型 parsed JSON 的 `title` 或 `description`；输出是去除首尾空白后的
    非空字符串，或 `None`。调用方是 serendipity authoring 消费层；无副作用。
    任一字段缺失都会让调用方整体回落旧模板，避免半个模型结果改变 no-host 兼容面。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _authored_serendipity_texts(authored: dict[str, Any] | None, event: dict[str, Any]) -> dict[str, str] | None:
    """把事务外 serendipity authoring 包转换为最终可写文本。

    输入是 LifeAuthor 预生成的 `{title, description}` 和触发事件；输出总是完整的
    两字段文本包，或在该事件类型不触发 serendipity 时返回 `None`。调用方是
    `_serendipity_for`。无副作用；模型结果必须同时包含两个非空字符串，否则整体
    使用 `_serendipity_text_fallback`，保证 fallback byte-identical。
    """
    fallback = _serendipity_text_fallback(event)
    if fallback is None:
        return None
    authored = authored if isinstance(authored, dict) else {}
    title = _clean_authored_serendipity_field(authored.get("title"))
    description = _clean_authored_serendipity_field(authored.get("description"))
    if title and description:
        return {"title": title, "description": description}
    return fallback


def _serendipity_payload_shape(event: dict[str, Any]) -> dict[str, Any] | None:
    """返回 serendipity 非文本字段的确定性形状。

    输入是已满足 completed/importance 门的事件；输出是旧版 payload 中除
    `title` / `description` 外的字段，或在事件类型不触发小意外时返回 `None`。
    调用方是事务内 `_serendipity_for` 和事务外 authoring context。无副作用；
    本函数集中保留 roll/gate/resource 以外的确定性字段，避免文案改造误改事件形状。
    """
    event_type = str(event.get("event_type") or "other")
    if _serendipity_text_fallback(event) is None:
        return None
    importance = int(event.get("importance") or 50)
    return {
        "serendipity_type": "minor_discovery" if event_type not in {"study", "fitness", "health"} else "minor_problem",
        "intensity": min(80, max(20, importance - 15)),
        "trigger_event_id": event.get("id"),
        "emotional_impact": {"mood_delta": 2 if event_type != "study" else 0, "insight": 1},
        "source": "serendipity",
    }


def _serendipity_for(
    event: dict[str, Any],
    decision_type: str,
    serendipity_authoring: dict[str, Any] | None = None,
    allow_authoring: bool = False,
) -> dict[str, Any] | None:
    """生成完成事件后的 serendipity LifeOp。

    输入是执行模拟的事件、决策类型和事务外预生成文本；输出是
    `CREATE_SERENDIPITY_EVENT` proposed op 或 `None`。调用方式是事务内同步消费；
    `allow_authoring` 只保留调用合同标记，本函数不会访问宿主模型。副作用为零；
    completed/importance/event_type 门和非文本 payload 与旧版保持一致。
    """
    _ = allow_authoring
    importance = int(event.get("importance") or 50)
    if decision_type != "completed" or importance < 55:
        return None
    text = _authored_serendipity_texts(serendipity_authoring, event)
    shape = _serendipity_payload_shape(event)
    if text is None or shape is None:
        return None
    return {
        "type": "CREATE_SERENDIPITY_EVENT",
        "payload": {
            **text,
            **shape,
        },
    }


def _serendipity_authoring_context(
    conn,
    owner_kind: str,
    owner_id: str,
    block: dict[str, Any],
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """收集某个 completed block 是否会产生 serendipity 的只读上下文。

    输入是 owner 与一个 schedule block；输出是给 LifeAuthor 的 compact context，
    或在该 block 当前不会自然完成/不会触发 serendipity 时返回 `None`。调用方是
    heartbeat/manual 的事务外 authoring 准备层。副作用限定为 SELECT：复用完成分支
    判断读取事件、资源、天气、睡眠与依赖状态；不记录 decision、不创建 LifeOps、
    不结算资源、不改变 schedule。`authoring_now` 是 heartbeat 预先算好的当前时间
    事实块，只作为 LifeAuthor 上下文，不参与触发判断。
    """
    completion_context = _completion_authoring_context(
        conn, owner_kind, owner_id, block, authoring_now=authoring_now,
    )
    if not completion_context:
        return None
    event_id = block.get("event_id")
    if not event_id:
        return None
    event = get_event(conn, str(event_id))
    importance = int(event.get("importance") or 50)
    if importance < 55:
        return None
    fallback = _serendipity_text_fallback(event)
    shape = _serendipity_payload_shape(event)
    if fallback is None or shape is None:
        return None
    context = {
        "trigger_event": completion_context.get("event") or {},
        "schedule_block": completion_context.get("schedule_block") or {},
        "completion_outcome": completion_context.get("outcome") or {},
        "serendipity": {
            "fallback_title": fallback["title"],
            "fallback_description": fallback["description"],
            "serendipity_type": shape["serendipity_type"],
            "intensity": shape["intensity"],
            "emotional_impact": shape["emotional_impact"],
        },
    }
    if completion_context.get("authoring_now"):
        context["authoring_now"] = completion_context.get("authoring_now")
    return context


def _author_serendipity_text(
    conn,
    owner_kind: str,
    owner_id: str,
    context: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, str] | None:
    """用 LifeAuthor 生成 serendipity 的 title/description。

    输入是 `_serendipity_authoring_context` 产出的只读上下文；输出是完整的
    `{title, description}`，或在无 host、门控关闭、模型失败、字段为空、字段缺失、
    以及调用方误处于 SQLite 事务内时返回 `None`。调用方式是事务外 best-effort；
    除 LifeAuthor 自身审计外不写生活事实、不触发 LifeOps、不结算资源。
    """
    if getattr(conn, "in_transaction", False):
        return None
    try:
        parsed = life_author.author(
            conn,
            owner_kind,
            owner_id,
            kind="serendipity",
            instructions=(
                "为一个已完成事件后自然冒出来的小意外/偶遇写两条中文文本。"
                " title 是写入小意外事件标题的一句话；description 是补充背景的一句话。"
                " 保持轻、小、具体，像亲身经历后顺手记下的生活纹理；不要改变事件是否发生、"
                "强度、影响或资源结果；不要提 LifeEngine、调度、数据库、tick、资源账本、"
                "执行模拟器或模型；不要硬塞人物名或世界观设定。"
            ),
            context=context,
            schema=_SERENDIPITY_TEXT_SCHEMA,
            max_tokens=180,
            temperature=0.65,
            trace_id=trace_id,
        )
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    title = _clean_authored_serendipity_field(parsed.get("title"))
    description = _clean_authored_serendipity_field(parsed.get("description"))
    if not title or not description:
        return None
    return {"title": title, "description": description}


def prepare_serendipity_authoring_for_block(
    conn,
    owner_kind: str,
    owner_id: str,
    block: dict[str, Any] | None,
    *,
    trace_id: str | None = None,
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """在写事务外为单个 completed block 预生成 serendipity 文案。

    输入是已选中的 schedule block；输出是 `{title, description}` 或 `None`。
    调用方包括 `life_execution run/simulate` 手动路径和 heartbeat tick 预备层。
    失败处理是全程吞掉异常并返回 `None`，让事务内 serendipity 使用旧 title/
    description；副作用只允许 LifeAuthor 审计，不会创建事件、记忆、资源流水或
    proposed ops。`authoring_now` 只在 heartbeat 路径传入，不改变手动路径合同。
    """
    if not isinstance(block, dict):
        return None
    try:
        context = _serendipity_authoring_context(
            conn, owner_kind, owner_id, block, authoring_now=authoring_now,
        )
        if not context:
            return None
        return _author_serendipity_text(conn, owner_kind, owner_id, context, trace_id=trace_id)
    except Exception:
        return None


def prepare_serendipity_authoring_for_tick(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    now: str,
    trace_id: str | None = None,
    limit: int = 20,
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """在 heartbeat 写事务外预生成 serendipity 文案包。

    输入来自 `prepare_heartbeat_authoring` 的 owner、逻辑时间和 trace；输出是
    `{block_id: {title, description}}`，只在本次 tick 内使用。调用方式是同步
    best-effort：逐个 due block 只读判断是否会自然完成并触发 serendipity，再调用
    LifeAuthor 的 `serendipity` kind。无 host、模型空返回或任意异常都会跳过该 block，
    事务内 `_serendipity_for` 继续使用旧版七类标题和描述模板。`authoring_now` 是
    同一 heartbeat tick 共享的事实时间块，只传给 LifeAuthor context。
    """
    authored: dict[str, dict[str, str]] = {}
    try:
        blocks = _execution_authoring_blocks_for_tick(conn, owner_kind, owner_id, now, limit=limit)
    except Exception:
        return authored
    for block in blocks:
        block_id = str(block.get("id") or "")
        if not block_id:
            continue
        item = prepare_serendipity_authoring_for_block(
            conn, owner_kind, owner_id, block, trace_id=trace_id,
            authoring_now=authoring_now,
        )
        if item:
            authored[block_id] = item
    return authored


def _completion_result_fallback(title: Any) -> str:
    """返回执行完成结果的历史确定性模板。

    输入是事件标题，输出逐字兼容旧版 `COMPLETE_EVENT.summary` 的字符串。调用方是
    执行模拟 completed 分支；无副作用。这个函数存在的维护约束是 no-host、
    LifeAuthor 返回空或事务外 authoring 失败时，必须保持旧结果摘要 byte-identical。
    """
    return f"执行完成：{title}"


def _completion_memory_fallback(title: Any) -> str:
    """返回执行完成记忆的历史确定性模板。

    输入是事件标题，输出逐字兼容旧版 `CREATE_MEMORY.content` 的字符串。调用方是
    执行模拟 completed 分支；无副作用。这个函数和 `_completion_result_fallback`
    一起保证无宿主模型的开发/CI 路径不改变任何人类可见文本。
    """
    return f"完成了『{title}』。"


def _clean_authored_completion_field(value: Any) -> str | None:
    """规整 LifeAuthor 返回的单行完成文本。

    输入是模型 parsed JSON 中的某个字段；输出是去除首尾空白后的非空字符串，或
    `None`。调用方是执行完成 authoring 消费层。副作用为零；字段为空时调用方会
    逐字段回落到旧模板，不让半成品覆盖确定性 fallback。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _authored_completion_texts(authored: dict[str, Any] | None, title: Any) -> dict[str, str]:
    """把事务外 authoring 包转换为最终 result/memory 文本。

    输入是 `{narrative, memory}` 的短期内存包和事件标题；输出总是包含
    `narrative`、`memory` 两个字符串。调用方是 `simulate_schedule_block_execution`
    的 completed 分支。无副作用；每个字段单独降级，保证 LifeAuthor 缺字段、空字段
    或完全缺席时仍逐字使用旧模板。
    """
    authored = authored if isinstance(authored, dict) else {}
    narrative = _clean_authored_completion_field(authored.get("narrative"))
    memory = _clean_authored_completion_field(authored.get("memory"))
    return {
        "narrative": narrative or _completion_result_fallback(title),
        "memory": memory or _completion_memory_fallback(title),
    }


def _completion_authoring_context(
    conn,
    owner_kind: str,
    owner_id: str,
    block: dict[str, Any],
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """收集某个 schedule block 是否会进入 completed 分支的只读上下文。

    输入是 owner 与一个 schedule block；输出是给 LifeAuthor 的 compact context，
    或在该 block 当前不会自然完成时返回 `None`。调用方是 heartbeat/manual 的
    事务外 authoring 准备层。副作用限定为 SELECT：读取 event、资源余额、天气、
    睡眠压力和依赖状态；不记录 execution_decision、不创建 LifeOps、不结算资源。
    `authoring_now` 是可选 heartbeat 当前时间事实，只注入上下文，不参与完成判定。
    """
    event_id = block.get("event_id")
    if not event_id:
        return None
    event = get_event(conn, str(event_id))
    if event.get("status") in TERMINAL_EVENT_STATUSES:
        return None

    resource_costs = event.get("resource_costs") or {}
    shortages = _resource_shortages(conn, owner_kind, owner_id, resource_costs)
    unmet_dependencies = _dependencies_unmet(conn, owner_kind, owner_id, str(event_id))
    weather = _latest_weather(conn, owner_kind, owner_id)
    bad_weather = _weather_is_bad(weather)
    sleep_ctx = _latest_sleep_execution_context(conn, owner_kind, owner_id)
    event_type = str(event.get("event_type") or "other")
    importance = int(event.get("importance") or 50)

    if unmet_dependencies:
        return None
    if _sleep_adjusted_ops(event, block, sleep_ctx, importance, postpone_ops_fn=lambda *_args, **_kwargs: []):
        return None
    if bad_weather and event_type in OUTDOOR_EVENT_TYPES and importance < 85:
        return None

    hard_shortages = [s for s in shortages if (s.get("resource_class") or "") not in BODY_RESOURCE_CLASSES]
    if hard_shortages:
        return None
    pushed_through_vital = bool(shortages) and not hard_shortages
    context = {
        "event": {
            "id": event.get("id"),
            "title": event.get("title"),
            "event_type": event_type,
            "importance": importance,
            "status_before": event.get("status"),
            "description": event.get("description"),
            "tags": event.get("tags") or [],
        },
        "schedule_block": {
            "id": block.get("id"),
            "block_type": block.get("block_type"),
            "start": block.get("start"),
            "end": block.get("end"),
            "timezone": block.get("timezone"),
        },
        "outcome": {
            "decision_type": "completed",
            "reason": "pushed through low energy" if pushed_through_vital else "resources and conditions ok",
            "pushed_through_vital": pushed_through_vital,
            "resource_deltas": resource_costs,
            "shortages": shortages,
            "weather": weather,
            "sleep_context": {
                "severity": sleep_ctx.get("severity"),
                "sleep_debt_minutes": sleep_ctx.get("sleep_debt_minutes"),
                "fatigue": sleep_ctx.get("fatigue"),
                "focus_penalty": sleep_ctx.get("focus_penalty"),
            },
        },
    }
    if authoring_now:
        context["authoring_now"] = authoring_now
    return context


def _author_execution_completion(
    conn,
    owner_kind: str,
    owner_id: str,
    context: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, str] | None:
    """用 LifeAuthor 生成执行完成结果和记忆文本。

    输入是 `_completion_authoring_context` 产出的只读上下文；输出是可供 completed
    分支消费的 `{narrative, memory}`，或在无 host、门控关闭、模型失败、字段为空、
    以及调用方误处于 SQLite 事务内时返回 `None`。调用方式是事务外 best-effort；
    除 LifeAuthor 自身审计外不写生活事实、不触发 LifeOps、不结算资源。
    """
    if getattr(conn, "in_transaction", False):
        return None
    try:
        parsed = life_author.author(
            conn,
            owner_kind,
            owner_id,
            kind="execution_narrative",
            instructions=(
                "为一个刚自然完成的日程事件写两条生活化中文文本。"
                " narrative 是写入结果摘要的一句话；memory 是写入个人 episodic memory 的一句话。"
                " 保持具体、像亲历后的记录，不要像系统播报；不要提 LifeEngine、调度、数据库、"
                "tick、资源账本或执行模拟器；不要硬塞人物名或世界观设定。"
            ),
            context=context,
            schema=_EXECUTION_NARRATIVE_SCHEMA,
            max_tokens=220,
            temperature=0.6,
            trace_id=trace_id,
        )
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    narrative = _clean_authored_completion_field(parsed.get("narrative"))
    memory = _clean_authored_completion_field(parsed.get("memory"))
    if not narrative and not memory:
        return None
    out: dict[str, str] = {}
    if narrative:
        out["narrative"] = narrative
    if memory:
        out["memory"] = memory
    return out


def prepare_execution_completion_authoring_for_block(
    conn,
    owner_kind: str,
    owner_id: str,
    block: dict[str, Any] | None,
    *,
    trace_id: str | None = None,
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """在写事务外为单个执行完成 block 预生成叙事文本。

    输入是已选中的 schedule block；输出是 `{narrative, memory}` 或 `None`。
    调用方包括 `life_execution run/simulate` 手动路径和 heartbeat tick 预备层。
    失败处理是全程吞掉异常并返回 `None`，让事务内 completed 分支使用旧模板；
    副作用只允许 LifeAuthor 审计，不会创建事件、记忆、结果、资源流水或 proposed ops。
    `authoring_now` 只在 heartbeat 路径传入，不改变手动路径合同。
    """
    if not isinstance(block, dict):
        return None
    try:
        context = _completion_authoring_context(
            conn, owner_kind, owner_id, block, authoring_now=authoring_now,
        )
        if not context:
            return None
        return _author_execution_completion(conn, owner_kind, owner_id, context, trace_id=trace_id)
    except Exception:
        return None


def _execution_authoring_blocks_for_tick(
    conn,
    owner_kind: str,
    owner_id: str,
    now: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """列出 heartbeat 本轮可能完成的 schedule block。

    输入是 tick 的 owner 与逻辑时间；输出按 block id 去重的只读 block 列表。调用方
    是 heartbeat 的事务外 execution authoring 准备层。副作用只有 SELECT：读取
    pending wake jobs 和 due schedule sweep 候选，不 claim/finish wake job，也不改
    schedule 状态；真正执行仍由 runtime 在写事务内重新读取并提交。
    """
    by_id: dict[str, dict[str, Any]] = {}
    for job in due_wake_jobs(conn, owner_kind, owner_id, now):
        if job.get("reason") != "schedule_block_end" or not job.get("target_id"):
            continue
        row = conn.execute(
            "SELECT * FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?",
            (job["target_id"], owner_kind, owner_id),
        ).fetchone()
        if not row:
            continue
        block = dict(row)
        if block.get("block_type") == "sleep":
            continue
        if block.get("status") in {"planned", "locked", "ready", "in_progress"}:
            by_id[str(block["id"])] = block
    for block in due_schedule_blocks(conn, owner_kind, owner_id, now):
        if block.get("block_type") == "sleep":
            continue
        by_id.setdefault(str(block["id"]), block)
    return list(by_id.values())[: max(1, int(limit))]


def prepare_execution_completion_authoring_for_tick(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    now: str,
    trace_id: str | None = None,
    limit: int = 20,
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """在 heartbeat 写事务外预生成执行完成叙事包。

    输入来自 `prepare_heartbeat_authoring` 的 owner、逻辑时间和 trace；输出是
    `{block_id: {narrative, memory}}`，只在本次 tick 内使用。调用方式是同步
    best-effort：逐个 due block 只读判断是否会自然完成，再调用 LifeAuthor 的
    `execution_narrative` kind。无 host、模型空返回或任意异常都会跳过该 block，
    事务内执行模拟继续使用旧的 `执行完成：{title}` / `完成了『{title}』。` 模板。
    `authoring_now` 是同一 heartbeat tick 共享的事实时间块，只传给 LifeAuthor context。
    """
    authored: dict[str, dict[str, str]] = {}
    try:
        blocks = _execution_authoring_blocks_for_tick(conn, owner_kind, owner_id, now, limit=limit)
    except Exception:
        return authored
    for block in blocks:
        block_id = str(block.get("id") or "")
        if not block_id:
            continue
        item = prepare_execution_completion_authoring_for_block(
            conn, owner_kind, owner_id, block, trace_id=trace_id,
            authoring_now=authoring_now,
        )
        if item:
            authored[block_id] = item
    return authored


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
    completion_authoring: dict[str, Any] | None = None,
    serendipity_authoring: dict[str, Any] | None = None,
    allow_authoring: bool = False,
) -> dict[str, Any]:
    """记录一次到点日程块的确定性执行决策。

    输入来自 heartbeat 找到的到期 schedule_block、当前控制状态和逻辑时间；
    输出是一条 execution_decision 以及待提交 LifeOps。`completion_authoring`
    和 `serendipity_authoring` 是事务外预生成的人类可见文本包；`allow_authoring`
    只保留调用合同标记，本函数不会访问宿主模型。本函数只写执行审计，不直接改变
    事件/日程/资源，调用方必须继续走 LifeOps 校验和事务提交。失败时由 heartbeat
    标记 wake job 或 fallback sweep 异常，避免状态机半写。
    """
    _ = allow_authoring
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
        old_event_status = str(event.get("status") or "")
        if event_transition_allowed(old_event_status, "rescheduled"):
            event_delay_status = "rescheduled"
        elif event_transition_allowed(old_event_status, "postponed"):
            event_delay_status = "postponed"
        else:
            event_delay_status = old_event_status
        ops: list[dict[str, Any]] = [
            {"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "rescheduled", "reason": reason}},
        ]
        if event_delay_status and event_delay_status != old_event_status:
            ops.append({"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": event_id, "status": event_delay_status, "reason": reason}})
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

    # 身体/心智容量（energy、focus、fatigue、mood 等）不足时，她可以硬撑完成，
    # 由资源账本在下限处截断并留下复盘；真正阻断执行的是钱、材料、库存这类外部硬约束。
    hard_shortages = [s for s in shortages if (s.get("resource_class") or "") not in BODY_RESOURCE_CLASSES]
    pushed_through_vital = bool(shortages) and not hard_shortages
    if hard_shortages:
        if importance >= 75:
            ops = [
                {"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "completed", "reason": "time block elapsed but resources were insufficient"}},
                {"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": event_id, "status": "in_progress", "reason": "attempted despite resource shortage"}},
                {"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": event_id, "status": "partial", "reason": "resource shortage prevented completion"}},
                {"type": "CREATE_REFLECTION", "payload": {"target_kind": "event", "target_id": event_id, "reflection_type": "execution_review", "content": f"『{event.get('title')}』没有完全完成，因为资源不足：{hard_shortages}。", "source": "execution_simulator"}},
            ]
            if owner_kind == "agent":
                ops.append({"type": "CREATE_PROACTIVE_INTENT", "payload": {"target_type": "self_journal", "intent_type": "ask_for_help", "summary": f"『{event.get('title')}』遇到资源不足，想重新规划。", "importance": 80, "urgency": 55, "novelty": 40, "relationship_relevance": 50, "privacy_level": "agent_private", "status": "generated", "source": "execution_simulator"}})
            return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="partial", status="proposed", reason="resource shortage", score=score, proposed_ops=ops)
        ops = postpone_ops("资源不足", days=1, proactive=True)
        return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="postponed", status="proposed", reason="resource shortage", score=score, proposed_ops=ops)

    authored_completion = _authored_completion_texts(completion_authoring, event.get("title"))
    ops = [
        {"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block["id"], "status": "completed", "reason": "execution simulator completed the scheduled block"}},
        {"type": "COMPLETE_EVENT", "payload": {"event_id": event_id, "summary": authored_completion["narrative"], "source": "execution_simulator"}},
    ]
    if importance >= 50:
        ops.append({"type": "CREATE_MEMORY", "payload": {"memory_type": "episodic", "content": authored_completion["memory"], "event_id": event_id, "source": "execution_simulator", "importance": min(100, importance)}})
    ser = _serendipity_for(event, "completed", serendipity_authoring=serendipity_authoring, allow_authoring=False)
    if ser:
        ops.append(ser)
    if pushed_through_vital:
        ops.append({"type": "CREATE_REFLECTION", "payload": {"target_kind": "event", "target_id": event_id, "reflection_type": "execution_review", "content": f"完成『{event.get('title')}』时精力快见底了，但还是硬撑着把它做完了。", "source": "execution_simulator"}})
    if importance >= 75 and owner_kind == "agent":
        ops.append({"type": "CREATE_PROACTIVE_INTENT", "payload": {"target_type": "self_journal", "intent_type": "report_progress", "summary": f"『{event.get('title')}』已经完成，值得记录一下进展。", "importance": min(95, importance), "urgency": 35, "novelty": 45, "relationship_relevance": 45, "privacy_level": "agent_private", "status": "generated", "source": "execution_simulator"}})
    return record_execution_decision(conn, owner_kind, owner_id, tick_id=tick_id, trace_id=trace_id, wake_job_id=wake_job_id, schedule_block_id=block.get("id"), event_id=event_id, decision_type="completed", status="proposed", reason=("pushed through low energy" if pushed_through_vital else "resources and conditions ok"), score=score, proposed_ops=ops)
