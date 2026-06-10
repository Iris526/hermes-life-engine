"""Validation layer for LifeOps, owner policies, time, resources, schedules, and goals."""

from __future__ import annotations

from typing import Any

from .constants import MUTATION_BLOCKING_STATES
from .jsonutil import dumps
from .time_utils import parse_datetime, normalize_range, to_epoch
from .trace import append_audit, new_id
from .lifecycle import event_transition_allowed, schedule_transition_allowed

AGENT_NARRATIVE_SOURCES = {
    "agent_prediction",
    "agent_retro_assertion",
    "agent_spontaneous",
    "agent_narrative_assertion",
    "autonomy",
    "heartbeat_narrative",
}

USER_ALLOWED_FACT_SOURCES = {
    "user_reported",
    "user_confirmed",
    "tool_imported",
    "calendar_imported",
    "file_imported",
    "manual_entry",
    "life_commit_tool",
    "life_event_tool",
    "life_resource_tool",
    "life_memory_tool",
    "life_diary_tool",
    "life_inventory_tool",
    "life_goal_tool",
    "cli",
}

EVENT_STATUSES = {
    "draft", "planned", "scheduled", "ready", "in_progress", "partial", "completed",
    "postponed", "rescheduled", "cancelled", "failed", "abandoned", "archived",
    "discarded", "skipped", "paused", "missed",
}

EVENT_ALLOWED_TRANSITIONS = {
    "draft": {"planned", "discarded", "cancelled"},
    "planned": {"scheduled", "cancelled", "abandoned", "in_progress"},
    "scheduled": {"ready", "rescheduled", "cancelled", "in_progress", "completed", "missed"},
    "ready": {"in_progress", "skipped", "postponed", "cancelled"},
    "in_progress": {"partial", "completed", "failed", "paused"},
    "partial": {"scheduled", "completed", "abandoned"},
    "postponed": {"rescheduled", "cancelled", "abandoned", "scheduled"},
    "rescheduled": {"scheduled", "cancelled"},
    "failed": {"rescheduled", "abandoned", "archived"},
    "completed": {"archived"},
    "cancelled": {"archived"},
    "missed": {"rescheduled", "cancelled", "archived"},
}

TERMINAL_EVENT_STATUSES = {"completed", "cancelled", "failed", "abandoned", "archived", "discarded"}

ALLOWED_OPS = {
    "CREATE_EVENT",
    "UPDATE_EVENT_STATUS",
    "CREATE_SCHEDULE_BLOCK",
    "UPDATE_SCHEDULE_BLOCK_STATUS",
    "COMPLETE_EVENT",
    "RESOURCE_DEFINE",
    "RESOURCE_DELTA",
    "RESOURCE_RESERVE",
    "RESOURCE_RELEASE",
    "CREATE_MEMORY",
    "CREATE_DIARY",
    "CREATE_PROACTIVE_INTENT",
    "EVALUATE_PROACTIVE_INTENT",
    "MARK_PROACTIVE_SENT",
    "SUPPRESS_PROACTIVE_INTENT",
    "EXPIRE_PROACTIVE_INTENTS",
    "CREATE_SERENDIPITY_EVENT",
    "AUTONOMY_CREATE_GOAL_STEP",
    "AUTONOMY_SCHEDULE_EVENT",
    "CREATE_INVENTORY_ITEM",
    "UPDATE_INVENTORY_ITEM",
    "INVENTORY_DELTA",
    "INVENTORY_MOVE",
    "CREATE_MEAL_RECORD",
    # v0.6 long-term life structure
    "CREATE_LIFE_ARC",
    "CREATE_GOAL",
    "UPDATE_GOAL_PROGRESS",
    "CREATE_GOAL_MILESTONE",
    "LINK_EVENT_TO_GOAL",
    "CREATE_EVENT_DEPENDENCY",
    "DECOMPOSE_EVENT",
    "CREATE_REFLECTION",
    "CREATE_GOAL_MILESTONE",
    "RECOMPUTE_EVENT_PROGRESS",
    "AUTONOMY_CREATE_GOAL_STEP",
    "AUTONOMY_SCHEDULE_EVENT",
}

USER_WRITE_OPS = {
    "CREATE_EVENT", "CREATE_MEMORY", "RESOURCE_DELTA", "CREATE_DIARY",
    "CREATE_INVENTORY_ITEM", "UPDATE_INVENTORY_ITEM", "INVENTORY_DELTA", "INVENTORY_MOVE",
    "CREATE_MEAL_RECORD", "CREATE_LIFE_ARC", "CREATE_GOAL", "UPDATE_GOAL_PROGRESS", "CREATE_GOAL_MILESTONE",
    "LINK_EVENT_TO_GOAL", "CREATE_EVENT_DEPENDENCY", "DECOMPOSE_EVENT", "CREATE_REFLECTION", "RECOMPUTE_EVENT_PROGRESS",
    "CREATE_GOAL_MILESTONE",
    "RECOMPUTE_EVENT_PROGRESS",
    "AUTONOMY_CREATE_GOAL_STEP",
    "AUTONOMY_SCHEDULE_EVENT",
}


class ValidationError(ValueError):
    pass


def _require(payload: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if payload.get(k) in (None, "")]
    if missing:
        raise ValidationError(f"missing required field(s): {', '.join(missing)}")


def _validate_time_range(payload: dict[str, Any], start_key: str = "start", end_key: str = "end") -> None:
    if not payload.get(start_key) and not payload.get(end_key):
        return
    tz = payload.get("timezone_name") or payload.get("timezone") or "UTC"
    try:
        start = parse_datetime(payload.get(start_key), default_tz=tz)
        end = parse_datetime(payload.get(end_key), default_tz=tz)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if start and end and end <= start:
        raise ValidationError(f"{end_key} must be after {start_key}")


def _normalize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    p = dict(payload)
    start, end, start_ts, end_ts = normalize_range(p.get("planned_start"), p.get("planned_end"), default_tz=p.get("timezone") or "UTC")
    p["planned_start"] = start
    p["planned_end"] = end
    if start_ts is not None:
        p["planned_start_ts"] = start_ts
    if end_ts is not None:
        p["planned_end_ts"] = end_ts
    return p


def _normalize_schedule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    p = dict(payload)
    tz = p.get("timezone_name") or p.get("timezone") or "UTC"
    start, end, start_ts, end_ts = normalize_range(p.get("start"), p.get("end"), default_tz=tz)
    p["start"] = start
    p["end"] = end
    p["start_ts"] = start_ts
    p["end_ts"] = end_ts
    return p


def _validate_0_100(name: str, value: Any) -> None:
    try:
        v = float(value)
    except Exception as exc:
        raise ValidationError(f"{name} must be numeric") from exc
    if not 0 <= v <= 100:
        raise ValidationError(f"{name} must be 0..100")


def validate_owner_policy(owner_kind: str, op_type: str, payload: dict[str, Any], source: str) -> None:
    """Prevent Agent narrative rules from leaking into User Life."""
    payload_source = str(payload.get("source") or source or "")
    if owner_kind == "user":
        if payload_source in AGENT_NARRATIVE_SOURCES:
            raise ValidationError(f"user life cannot be written from agent narrative source: {payload_source}")
        if op_type in USER_WRITE_OPS:
            if payload_source and payload_source not in USER_ALLOWED_FACT_SOURCES:
                raise ValidationError(
                    "user life writes must come from user_reported/user_confirmed/tool_imported/"
                    f"calendar_imported/file_imported/manual_entry; got {payload_source!r}"
                )
            if payload.get("requires_confirmation") and not payload.get("confirmed_by_user"):
                raise ValidationError("user life write requires confirmed_by_user=true")


def validate_op_shape(op_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if op_type not in ALLOWED_OPS:
        raise ValidationError(f"unknown LifeOp type: {op_type}")
    payload = dict(payload)
    if op_type == "CREATE_EVENT":
        _require(payload, "title")
        payload = _normalize_event_payload(payload)
        status = str(payload.get("status") or "planned")
        if status not in EVENT_STATUSES:
            raise ValidationError(f"invalid event status: {status}")
        _validate_time_range(payload, "planned_start", "planned_end")
        _validate_0_100("event progress", payload.get("progress", 0))
    elif op_type == "UPDATE_EVENT_STATUS":
        _require(payload, "event_id", "status")
        if str(payload["status"]) not in EVENT_STATUSES:
            raise ValidationError(f"invalid event status: {payload['status']}")
    elif op_type == "CREATE_SCHEDULE_BLOCK":
        _require(payload, "start", "end")
        payload = _normalize_schedule_payload(payload)
        _validate_time_range(payload, "start", "end")
    elif op_type == "UPDATE_SCHEDULE_BLOCK_STATUS":
        _require(payload, "schedule_block_id", "status")
        if str(payload.get("status")) not in {"planned", "locked", "ready", "in_progress", "completed", "skipped", "cancelled", "rescheduled", "missed"}:
            raise ValidationError(f"invalid schedule block status: {payload.get('status')}")
    elif op_type == "COMPLETE_EVENT":
        _require(payload, "event_id")
    elif op_type == "RESOURCE_DEFINE":
        _require(payload, "key")
        if payload.get("min_value") is not None and payload.get("max_value") is not None:
            if float(payload["max_value"]) < float(payload["min_value"]):
                raise ValidationError("resource max_value must be >= min_value")
    elif op_type == "RESOURCE_DELTA":
        _require(payload, "resource_key", "reason")
        try:
            float(payload.get("delta"))
        except Exception as exc:
            raise ValidationError("resource delta must be numeric") from exc
    elif op_type == "RESOURCE_RESERVE":
        _require(payload, "resource_key")
        try:
            amount = float(payload.get("amount"))
        except Exception as exc:
            raise ValidationError("reservation amount must be numeric") from exc
        if amount <= 0:
            raise ValidationError("reservation amount must be positive")
    elif op_type == "RESOURCE_RELEASE":
        _require(payload, "reservation_id")
    elif op_type == "CREATE_MEMORY":
        _require(payload, "content")
    elif op_type == "CREATE_DIARY":
        if payload.get("content") is not None and not str(payload.get("content")).strip():
            raise ValidationError("diary content cannot be empty when provided")
    elif op_type == "CREATE_PROACTIVE_INTENT":
        _require(payload, "summary")
        for key in ("importance", "urgency", "novelty", "relationship_relevance"):
            if payload.get(key) is not None:
                _validate_0_100(key, payload.get(key))
    elif op_type == "EVALUATE_PROACTIVE_INTENT":
        # intent_id is optional: absent means evaluate due generated intents.
        pass
    elif op_type == "MARK_PROACTIVE_SENT":
        _require(payload, "outbox_id")
    elif op_type == "SUPPRESS_PROACTIVE_INTENT":
        _require(payload, "intent_id")
    elif op_type == "EXPIRE_PROACTIVE_INTENTS":
        pass
    elif op_type == "CREATE_SERENDIPITY_EVENT":
        _require(payload, "title")
        if payload.get("intensity") is not None:
            _validate_0_100("serendipity intensity", payload.get("intensity"))
    elif op_type == "AUTONOMY_CREATE_GOAL_STEP":
        _require(payload, "goal_id", "title")
        if payload.get("start") or payload.get("end"):
            _validate_time_range(payload, "start", "end")
        if payload.get("weight") is not None:
            try:
                float(payload.get("weight"))
            except Exception as exc:
                raise ValidationError("autonomy step weight must be numeric") from exc
    elif op_type == "AUTONOMY_SCHEDULE_EVENT":
        _require(payload, "event_id", "start", "end")
        _validate_time_range(payload, "start", "end")
    elif op_type == "CREATE_INVENTORY_ITEM":
        _require(payload, "name")
        if float(payload.get("quantity", 1)) < 0:
            raise ValidationError("inventory quantity cannot be negative")
    elif op_type == "UPDATE_INVENTORY_ITEM":
        _require(payload, "item_id")
    elif op_type in {"INVENTORY_DELTA", "INVENTORY_MOVE"}:
        _require(payload, "item_id")
        if op_type == "INVENTORY_DELTA":
            _require(payload, "quantity_delta", "reason")
        try:
            float(payload.get("quantity_delta", 0))
        except Exception as exc:
            raise ValidationError("inventory quantity_delta must be numeric") from exc
    elif op_type == "CREATE_MEAL_RECORD":
        _require(payload, "meal_type")
        if payload.get("cost_amount") is not None:
            try:
                float(payload.get("cost_amount"))
            except Exception as exc:
                raise ValidationError("meal cost_amount must be numeric") from exc
    elif op_type == "CREATE_LIFE_ARC":
        _require(payload, "title")
        if payload.get("progress") is not None:
            _validate_0_100("life arc progress", payload.get("progress"))
    elif op_type == "CREATE_GOAL":
        _require(payload, "title")
        if payload.get("progress") is not None:
            _validate_0_100("goal progress", payload.get("progress"))
    elif op_type == "UPDATE_GOAL_PROGRESS":
        _require(payload, "goal_id")
        if payload.get("progress") is not None:
            _validate_0_100("goal progress", payload.get("progress"))
        if payload.get("progress_delta") is not None:
            try:
                float(payload.get("progress_delta"))
            except Exception as exc:
                raise ValidationError("goal progress_delta must be numeric") from exc
    elif op_type == "CREATE_GOAL_MILESTONE":
        _require(payload, "goal_id", "title")
        if payload.get("target_progress") is not None:
            _validate_0_100("milestone target_progress", payload.get("target_progress"))
        if payload.get("due_at"):
            try:
                payload["due_at_ts"] = to_epoch(payload.get("due_at"))
            except Exception as exc:
                raise ValidationError("invalid milestone due_at") from exc
    elif op_type == "LINK_EVENT_TO_GOAL":
        _require(payload, "goal_id", "event_id")
        try:
            float(payload.get("contribution_weight", payload.get("weight", 1.0)))
        except Exception as exc:
            raise ValidationError("goal-event weight must be numeric") from exc
    elif op_type == "CREATE_EVENT_DEPENDENCY":
        _require(payload, "event_id", "depends_on_event_id")
    elif op_type == "DECOMPOSE_EVENT":
        _require(payload, "parent_event_id")
        children = payload.get("child_events") or payload.get("children")
        if not isinstance(children, list) or not children:
            raise ValidationError("DECOMPOSE_EVENT requires a non-empty children list")
        payload["child_events"] = children
        for child in children:
            if not isinstance(child, dict) or not child.get("title"):
                raise ValidationError("each decomposition child requires title")
            if child.get("planned_start") or child.get("planned_end"):
                _validate_time_range(child, "planned_start", "planned_end")
            sched = child.get("schedule") or {}
            if sched:
                _validate_time_range(sched, "start", "end")
    elif op_type == "CREATE_REFLECTION":
        _require(payload, "content")
    elif op_type == "CREATE_GOAL_MILESTONE":
        _require(payload, "goal_id", "title")
        if payload.get("target_progress") is not None:
            _validate_0_100("milestone target_progress", payload.get("target_progress"))
    elif op_type == "RECOMPUTE_EVENT_PROGRESS":
        _require(payload, "event_id")
    elif op_type == "AUTONOMY_CREATE_GOAL_STEP":
        _require(payload, "goal_id")
        if payload.get("start") or payload.get("end"):
            _validate_time_range(payload, "start", "end")
    elif op_type == "AUTONOMY_SCHEDULE_EVENT":
        _require(payload, "event_id", "start", "end")
        _validate_time_range(payload, "start", "end")
    return payload



def validate_schedule_event_against_db(conn, owner_kind: str, owner_id: str, event_id: str | None) -> None:
    if not event_id:
        return
    row = _event_row(conn, owner_kind, owner_id, event_id)
    if not row:
        raise ValidationError(f"event not found: {event_id}")
    from .lifecycle import assert_event_schedulable, LifecycleError
    try:
        assert_event_schedulable(row["status"])
    except LifecycleError as exc:
        raise ValidationError(str(exc)) from exc

def validate_schedule_block_against_db(conn, owner_kind: str, owner_id: str, payload: dict[str, Any]) -> None:
    if payload.get("start_ts") is None or payload.get("end_ts") is None:
        return
    row = conn.execute(
        """SELECT id,start,end FROM schedule_blocks
             WHERE owner_kind=? AND owner_id=? AND status IN ('planned','locked','ready','in_progress')
               AND start_ts IS NOT NULL AND end_ts IS NOT NULL
               AND NOT(end_ts <= ? OR start_ts >= ?) LIMIT 1""",
        (owner_kind, owner_id, payload["start_ts"], payload["end_ts"]),
    ).fetchone()
    if row:
        raise ValidationError(f"schedule overlap with {row['id']} ({row['start']} - {row['end']})")


def validate_resource_delta_against_db(conn, owner_kind: str, owner_id: str, payload: dict[str, Any]) -> None:
    key = payload.get("resource_key")
    if not key or payload.get("allow_ad_hoc") is True:
        return
    row = conn.execute(
        "SELECT 1 FROM resource_definitions WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if not row:
        raise ValidationError(f"resource delta uses undefined resource: {key}. Define it first.")


def validate_resource_reservation_against_db(conn, owner_kind: str, owner_id: str, payload: dict[str, Any]) -> None:
    key = payload.get("resource_key")
    if not key:
        return
    amount = float(payload.get("amount", 0))
    acct = conn.execute(
        "SELECT current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    if not acct:
        raise ValidationError(f"cannot reserve resource without account: {key}")
    reserved = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM resource_reservations WHERE owner_kind=? AND owner_id=? AND resource_key=? AND status='reserved'",
        (owner_kind, owner_id, key),
    ).fetchone()[0]
    available = float(acct["current_value"]) - float(reserved or 0)
    if amount > available:
        raise ValidationError(f"resource {key} reservation exceeds available value: requested {amount}, available {available}")


def validate_inventory_delta_against_db(conn, owner_kind: str, owner_id: str, payload: dict[str, Any]) -> None:
    item_id = payload.get("item_id")
    if not item_id:
        return
    row = conn.execute("SELECT quantity FROM inventory_items WHERE owner_kind=? AND owner_id=? AND id=?", (owner_kind, owner_id, item_id)).fetchone()
    if not row:
        raise ValidationError(f"inventory item not found: {item_id}")
    if not payload.get("allow_negative"):
        new_q = float(row["quantity"]) + float(payload.get("quantity_delta", 0))
        if new_q < 0:
            raise ValidationError(f"inventory delta would make item quantity negative: {new_q}")


def _assert_event_exists(conn, owner_kind: str, owner_id: str, event_id: str | None, label: str = "event") -> None:
    if not event_id:
        return
    row = conn.execute("SELECT 1 FROM events WHERE owner_kind=? AND owner_id=? AND id=?", (owner_kind, owner_id, event_id)).fetchone()
    if not row:
        raise ValidationError(f"{label} not found: {event_id}")


def _event_row(conn, owner_kind: str, owner_id: str, event_id: str | None):
    if not event_id:
        return None
    return conn.execute("SELECT * FROM events WHERE owner_kind=? AND owner_id=? AND id=?", (owner_kind, owner_id, event_id)).fetchone()


def _schedule_row(conn, owner_kind: str, owner_id: str, block_id: str | None):
    if not block_id:
        return None
    return conn.execute("SELECT * FROM schedule_blocks WHERE owner_kind=? AND owner_id=? AND id=?", (owner_kind, owner_id, block_id)).fetchone()


def validate_event_transition_against_db(conn, owner_kind: str, owner_id: str, event_id: str, new_status: str) -> None:
    row = _event_row(conn, owner_kind, owner_id, event_id)
    if not row:
        raise ValidationError(f"event not found: {event_id}")
    old_status = row["status"]
    if not event_transition_allowed(old_status, new_status):
        raise ValidationError(f"invalid event status transition {old_status} -> {new_status}")


def validate_schedule_transition_against_db(conn, owner_kind: str, owner_id: str, block_id: str, new_status: str) -> None:
    row = _schedule_row(conn, owner_kind, owner_id, block_id)
    if not row:
        raise ValidationError(f"schedule block not found: {block_id}")
    old_status = row["status"]
    if not schedule_transition_allowed(old_status, new_status):
        raise ValidationError(f"invalid schedule block status transition {old_status} -> {new_status}")


def _assert_goal_exists(conn, owner_kind: str, owner_id: str, goal_id: str | None) -> None:
    if not goal_id:
        return
    row = conn.execute("SELECT 1 FROM goals WHERE owner_kind=? AND owner_id=? AND id=?", (owner_kind, owner_id, goal_id)).fetchone()
    if not row:
        raise ValidationError(f"goal not found: {goal_id}")


def _assert_arc_exists(conn, owner_kind: str, owner_id: str, arc_id: str | None) -> None:
    if not arc_id:
        return
    row = conn.execute("SELECT 1 FROM life_arcs WHERE owner_kind=? AND owner_id=? AND id=?", (owner_kind, owner_id, arc_id)).fetchone()
    if not row:
        raise ValidationError(f"life arc not found: {arc_id}")


def validate_life_ops(conn, owner_kind: str, owner_id: str, control: dict[str, Any], ops: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    if control["engine_state"] in MUTATION_BLOCKING_STATES:
        raise ValidationError(f"LifeEngine is {control['engine_state']}; life mutations are blocked")
    if not isinstance(ops, list) or not ops:
        raise ValidationError("ops must be a non-empty list")
    normalized: list[dict[str, Any]] = []

    # v0.9.1: validate lifecycle transitions across the whole transaction, not
    # just against the pre-transaction database snapshot.  This permits valid
    # sequences such as scheduled -> in_progress -> partial in one commit while
    # still rejecting impossible jumps such as completed -> planned.
    event_status_cache: dict[str, str] = {}
    schedule_status_cache: dict[str, str] = {}

    def current_event_status(event_id: str) -> str:
        if event_id in event_status_cache:
            return event_status_cache[event_id]
        row = _event_row(conn, owner_kind, owner_id, event_id)
        if not row:
            raise ValidationError(f"event not found: {event_id}")
        event_status_cache[event_id] = row["status"]
        return event_status_cache[event_id]

    def current_schedule_status(block_id: str) -> str:
        if block_id in schedule_status_cache:
            return schedule_status_cache[block_id]
        row = _schedule_row(conn, owner_kind, owner_id, block_id)
        if not row:
            raise ValidationError(f"schedule block not found: {block_id}")
        schedule_status_cache[block_id] = row["status"]
        return schedule_status_cache[block_id]

    for idx, op in enumerate(ops):
        if not isinstance(op, dict):
            raise ValidationError(f"op[{idx}] must be an object")
        op_type = str(op.get("type") or op.get("op_type") or "").upper()
        payload = op.get("payload") or {k: v for k, v in op.items() if k not in {"type", "op_type"}}
        if not isinstance(payload, dict):
            raise ValidationError(f"op[{idx}].payload must be an object")
        payload = validate_op_shape(op_type, payload)
        validate_owner_policy(owner_kind, op_type, payload, source)
        if op_type == "CREATE_EVENT":
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("parent_event_id"), "parent event")
            _assert_goal_exists(conn, owner_kind, owner_id, payload.get("goal_id"))
        if op_type == "CREATE_SCHEDULE_BLOCK":
            validate_schedule_event_against_db(conn, owner_kind, owner_id, payload.get("event_id"))
            validate_schedule_block_against_db(conn, owner_kind, owner_id, payload)
            if payload.get("event_id"):
                old_status = current_event_status(payload["event_id"])
                if not event_transition_allowed(old_status, "scheduled"):
                    raise ValidationError(f"invalid event status transition {old_status} -> scheduled")
                event_status_cache[payload["event_id"]] = "scheduled"
        if op_type == "UPDATE_EVENT_STATUS":
            event_id = payload.get("event_id")
            old_status = current_event_status(event_id)
            new_status = payload.get("status")
            if not event_transition_allowed(old_status, new_status):
                raise ValidationError(f"invalid event status transition {old_status} -> {new_status}")
            event_status_cache[event_id] = str(new_status)
        if op_type == "COMPLETE_EVENT":
            event_id = payload.get("event_id")
            old_status = current_event_status(event_id)
            if not event_transition_allowed(old_status, "completed"):
                raise ValidationError(f"invalid event status transition {old_status} -> completed")
            event_status_cache[event_id] = "completed"
        if op_type == "UPDATE_SCHEDULE_BLOCK_STATUS":
            block_id = payload.get("schedule_block_id")
            old_status = current_schedule_status(block_id)
            new_status = payload.get("status")
            if not schedule_transition_allowed(old_status, new_status):
                raise ValidationError(f"invalid schedule block status transition {old_status} -> {new_status}")
            schedule_status_cache[block_id] = str(new_status)
        if op_type == "RESOURCE_DELTA":
            validate_resource_delta_against_db(conn, owner_kind, owner_id, payload)
        if op_type == "RESOURCE_RESERVE":
            validate_resource_reservation_against_db(conn, owner_kind, owner_id, payload)
        if op_type in {"INVENTORY_MOVE", "INVENTORY_DELTA", "UPDATE_INVENTORY_ITEM"}:
            validate_inventory_delta_against_db(conn, owner_kind, owner_id, payload)
        if op_type == "CREATE_MEAL_RECORD" and payload.get("cost_resource_key"):
            validate_resource_delta_against_db(conn, owner_kind, owner_id, {"resource_key": payload.get("cost_resource_key")})
        if op_type == "CREATE_GOAL":
            _assert_arc_exists(conn, owner_kind, owner_id, payload.get("arc_id"))
        if op_type == "UPDATE_GOAL_PROGRESS":
            _assert_goal_exists(conn, owner_kind, owner_id, payload.get("goal_id"))
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("event_id"))
        if op_type == "LINK_EVENT_TO_GOAL":
            _assert_goal_exists(conn, owner_kind, owner_id, payload.get("goal_id"))
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("event_id"))
        if op_type == "CREATE_EVENT_DEPENDENCY":
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("event_id"))
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("depends_on_event_id"), "dependency event")
        if op_type == "DECOMPOSE_EVENT":
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("parent_event_id"), "parent event")
            _assert_goal_exists(conn, owner_kind, owner_id, payload.get("goal_id"))
        if op_type == "RECOMPUTE_EVENT_PROGRESS":
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("event_id"))
        if op_type == "AUTONOMY_CREATE_GOAL_STEP":
            # validate AUTONOMY_CREATE_GOAL_STEP
            _assert_goal_exists(conn, owner_kind, owner_id, payload.get("goal_id"))
        if op_type == "AUTONOMY_SCHEDULE_EVENT":
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("event_id"))
            schedule_payload = validate_op_shape("CREATE_SCHEDULE_BLOCK", {"start": payload.get("start"), "end": payload.get("end")})
            validate_schedule_block_against_db(conn, owner_kind, owner_id, schedule_payload)
        if op_type == "CREATE_REFLECTION":
            if payload.get("target_kind") == "event":
                _assert_event_exists(conn, owner_kind, owner_id, payload.get("target_id"))
            if payload.get("target_kind") == "goal":
                _assert_goal_exists(conn, owner_kind, owner_id, payload.get("target_id"))
        if op_type == "CREATE_GOAL_MILESTONE":
            _assert_goal_exists(conn, owner_kind, owner_id, payload.get("goal_id"))
        if op_type == "RECOMPUTE_EVENT_PROGRESS":
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("event_id"))
        if op_type == "AUTONOMY_CREATE_GOAL_STEP":
            _assert_goal_exists(conn, owner_kind, owner_id, payload.get("goal_id"))
        if op_type == "AUTONOMY_SCHEDULE_EVENT":
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("event_id"))
            validate_schedule_block_against_db(conn, owner_kind, owner_id, {"start": payload.get("start"), "end": payload.get("end"), **payload})
        if op_type == "CREATE_SERENDIPITY_EVENT":
            _assert_event_exists(conn, owner_kind, owner_id, payload.get("trigger_event_id"), "trigger event")
        if op_type in {"CREATE_PROACTIVE_INTENT", "EVALUATE_PROACTIVE_INTENT", "MARK_PROACTIVE_SENT", "SUPPRESS_PROACTIVE_INTENT", "EXPIRE_PROACTIVE_INTENTS"}:
            if owner_kind != "agent":
                raise ValidationError("proactive LifeOps are only valid for agent self-life")
            if op_type in {"EVALUATE_PROACTIVE_INTENT", "SUPPRESS_PROACTIVE_INTENT"} and payload.get("intent_id"):
                row = conn.execute("SELECT 1 FROM proactive_intents WHERE agent_id=? AND id=?", (owner_id, payload.get("intent_id"))).fetchone()
                if not row:
                    raise ValidationError(f"proactive intent not found: {payload.get('intent_id')}")
            if op_type == "MARK_PROACTIVE_SENT":
                row = conn.execute("SELECT 1 FROM proactive_outbox WHERE agent_id=? AND id=?", (owner_id, payload.get("outbox_id"))).fetchone()
                if not row:
                    raise ValidationError(f"proactive outbox not found: {payload.get('outbox_id')}")
        normalized.append({"type": op_type, "payload": payload})
    return normalized


def create_pending_user_confirmation(conn, owner_kind: str, owner_id: str, ops: list[dict[str, Any]], reason: str,
                                     session_id: str | None = None, turn_id: str | None = None) -> dict[str, Any]:
    confirmation_id = new_id("confirm")
    conn.execute(
        """INSERT INTO user_confirmations(id, owner_kind, owner_id, proposed_ops_json, reason, session_id, turn_id)
              VALUES(?,?,?,?,?,?,?)""",
        (confirmation_id, owner_kind, owner_id, dumps(ops), reason, session_id, turn_id),
    )
    append_audit(conn, owner_kind, owner_id, "user_confirmation_required", "info", reason, {"confirmation_id": confirmation_id})
    return {"confirmation_id": confirmation_id, "status": "pending", "reason": reason}
