"""SleepPlan / SleepSession operations for LifeEngine v0.11.1.

A sleep plan is the intended sleep schedule.  A sleep session is what actually
happened.  This separation lets planned sleep and actual sleep diverge due to
chatting, insomnia, user interruption, alarms, or all-nighters.
"""

from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .events import complete_event, create_event, create_schedule_block, set_realtime_state, get_realtime_state, transition_event, update_schedule_block_status
from .jsonutil import dumps, loads
from .resources import apply_delta
from .time_utils import normalize_range, normalized_iso, now_iso, to_epoch, parse_datetime
from .trace import append_journal, new_id
from .sleep_effects import record_post_sleep_day_state, get_sleep_day_state, list_sleep_day_states, plan_recovery_sleep_if_needed


def _decode_plan(row) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["constraints"] = loads(d.pop("constraints_json"), {})
        d["decision"] = loads(d.pop("decision_json"), {})
    return d


def _decode_session(row) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["resource_effects"] = loads(d.pop("resource_effects_json"), {})
    return d


def _create_wake_job(conn, owner_kind: str, owner_id: str, wake_at: str | None, reason: str, target_id: str | None) -> dict[str, Any] | None:
    if not wake_at:
        return None
    wake_at_iso = normalized_iso(wake_at)
    wake_at_ts = to_epoch(wake_at_iso)
    idem = f"{owner_kind}:{owner_id}:{reason}:{target_id}:{wake_at_ts}"
    job_id = new_id("wake")
    conn.execute(
        """INSERT OR IGNORE INTO wake_jobs(id, owner_kind, owner_id, wake_at, wake_at_ts, reason, target_id, status, idempotency_key)
              VALUES(?,?,?,?,?,?,?,?,?)""",
        (job_id, owner_kind, owner_id, wake_at_iso, wake_at_ts, reason, target_id, "pending", idem),
    )
    row = conn.execute("SELECT * FROM wake_jobs WHERE idempotency_key=?", (idem,)).fetchone()
    return dict(row) if row else None


def _record_sleep_transition(conn, owner_kind: str, owner_id: str, session_id: str,
                             from_status: str | None, to_status: str, *, reason: str | None = None,
                             source: str = "sleep", metadata: dict[str, Any] | None = None) -> str:
    tid = new_id("slptr")
    at = now_iso()
    try:
        conn.execute(
            """INSERT INTO sleep_session_state_transitions(id, owner_kind, owner_id, sleep_session_id, from_status, to_status, reason, source, occurred_at, occurred_at_ts, metadata_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, owner_kind, owner_id, session_id, from_status, to_status, reason, source, at, to_epoch(at), dumps(metadata or {})),
        )
    except Exception:
        pass
    return tid


def get_sleep_plan(conn, sleep_plan_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sleep_plans WHERE id=?", (sleep_plan_id,)).fetchone()
    if not row:
        raise ValueError(f"sleep plan not found: {sleep_plan_id}")
    return _decode_plan(row)


def get_sleep_session(conn, sleep_session_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sleep_sessions WHERE id=?", (sleep_session_id,)).fetchone()
    if not row:
        raise ValueError(f"sleep session not found: {sleep_session_id}")
    return _decode_session(row)


def list_sleep_plans(conn, owner_kind: str, owner_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    clauses = ["owner_kind=?", "owner_id=?"]
    params: list[Any] = [owner_kind, owner_id]
    if status:
        clauses.append("status=?")
        params.append(status)
    params.append(int(limit))
    rows = conn.execute(
        f"SELECT * FROM sleep_plans WHERE {' AND '.join(clauses)} ORDER BY COALESCE(planned_sleep_at_ts, unixepoch(created_at)) DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    return [_decode_plan(r) for r in rows]


def list_sleep_sessions(conn, owner_kind: str, owner_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    clauses = ["owner_kind=?", "owner_id=?"]
    params: list[Any] = [owner_kind, owner_id]
    if status:
        clauses.append("status=?")
        params.append(status)
    params.append(int(limit))
    rows = conn.execute(
        f"SELECT * FROM sleep_sessions WHERE {' AND '.join(clauses)} ORDER BY COALESCE(actual_sleep_at_ts, actual_wake_at_ts, unixepoch(created_at)) DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    return [_decode_session(r) for r in rows]


def get_active_sleep_session(conn, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT * FROM sleep_sessions WHERE owner_kind=? AND owner_id=? AND status='asleep'
             ORDER BY actual_sleep_at_ts DESC, created_at DESC LIMIT 1""",
        (owner_kind, owner_id),
    ).fetchone()
    return _decode_session(row) if row else None


def create_sleep_plan(conn, owner_kind: str, owner_id: str, *, planned_sleep_at: str | None = None, planned_wake_at: str | None = None,
                      planned_start: str | None = None, planned_end: str | None = None, sleep_type: str | None = None,
                      date: str | None = None, plan_type: str = "core_sleep", timezone_name: str = "UTC",
                      alarm_at: str | None = None, alarm_label: str | None = None,
                      wake_policy: str = "natural_or_alarm", constraints: dict[str, Any] | None = None,
                      decision: dict[str, Any] | None = None, title: str | None = None,
                      canon_version: int | None = None, source: str = "sleep_plan", **_: Any) -> dict[str, Any]:
    planned_sleep_at = planned_sleep_at or planned_start
    planned_wake_at = planned_wake_at or planned_end
    if sleep_type:
        plan_type = sleep_type
    start_iso, end_iso, start_ts, end_ts = normalize_range(planned_sleep_at, planned_wake_at, default_tz=timezone_name or "UTC")
    if start_iso is None or end_iso is None:
        raise ValueError("planned_sleep_at and planned_wake_at are required")
    duration = int((end_ts - start_ts) / 60) if start_ts is not None and end_ts is not None and end_ts >= start_ts else None
    if duration is None or duration <= 0:
        raise ValueError("planned wake must be after planned sleep")
    alarm_iso = normalized_iso(alarm_at) if alarm_at else None
    alarm_ts = to_epoch(alarm_iso) if alarm_iso else None
    plan_id = new_id("sleepplan")
    event = create_event(
        conn, owner_kind, owner_id,
        title=title or ("夜间核心睡眠" if plan_type == "core_sleep" else "小憩 / 午睡"),
        description="SleepPlan generated by LifeEngine sleep layer.",
        event_type=plan_type,
        event_category="sleep",
        activity_domain="sleep",
        subtype=plan_type,
        source=source,
        status="planned",
        planned_start=start_iso,
        planned_end=end_iso,
        priority=80 if plan_type == "core_sleep" else 45,
        importance=85 if plan_type == "core_sleep" else 50,
        tags=["sleep", plan_type],
        attributes={"sleep_plan_id": plan_id, "wake_policy": wake_policy, "alarm_at": alarm_iso},
        interruptibility={"level": "sleep_interruptible", "call_override_allowed": True, "ordinary_message_policy": "defer_or_wake_by_policy"},
        state_effects={"energy": "+recovery", "sleep_debt_minutes": "-recovery"},
        canon_version=canon_version,
    )
    block = create_schedule_block(
        conn, owner_kind, owner_id, start_iso, end_iso,
        event_id=event["id"], block_type="sleep", timezone_name=timezone_name or "UTC", status="planned",
        lock_strength="hard" if plan_type == "core_sleep" else "soft",
        interruptibility={"level": "sleep_interruptible", "call_override_allowed": True},
    )
    conn.execute(
        """INSERT INTO sleep_plans(id, owner_kind, owner_id, date, status, plan_type, event_id, schedule_block_id,
               planned_sleep_at, planned_sleep_at_ts, planned_wake_at, planned_wake_at_ts, planned_duration_minutes,
               timezone, alarm_at, alarm_at_ts, alarm_label, wake_policy, constraints_json, decision_json, canon_version)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (plan_id, owner_kind, owner_id, date, "scheduled", plan_type, event["id"], block["id"], start_iso, start_ts, end_iso, end_ts, duration,
         timezone_name or "UTC", alarm_iso, alarm_ts, alarm_label, wake_policy, dumps(constraints or {}), dumps(decision or {}), canon_version),
    )
    start_job = _create_wake_job(conn, owner_kind, owner_id, start_iso, "sleep_plan_start", plan_id)
    wake_job = _create_wake_job(conn, owner_kind, owner_id, alarm_iso or end_iso, "sleep_plan_wake", plan_id)
    append_journal(conn, owner_kind, owner_id, "sleep_plan_created", {"sleep_plan_id": plan_id, "event_id": event["id"], "schedule_block_id": block["id"], "start_wake_job": start_job, "wake_wake_job": wake_job}, source, canon_version=canon_version)
    return {"sleep_plan": get_sleep_plan(conn, plan_id), "event": event, "schedule_block": block, "start_wake_job": start_job, "wake_wake_job": wake_job}


def start_sleep_session(conn, owner_kind: str, owner_id: str, *, sleep_plan_id: str, now: str | None = None,
                        source: str = "sleep", reason: str | None = None, **_: Any) -> dict[str, Any]:
    active = get_active_sleep_session(conn, owner_kind, owner_id)
    if active:
        return {"sleep_session": active, "already_asleep": True}
    plan = get_sleep_plan(conn, sleep_plan_id)
    if plan["owner_kind"] != owner_kind or plan["owner_id"] != owner_id:
        raise ValueError("sleep plan owner mismatch")
    if plan["status"] in {"completed", "cancelled", "missed", "skipped"}:
        raise ValueError(f"cannot start sleep plan in status {plan['status']}")
    at = normalized_iso(now or now_iso())
    at_ts = to_epoch(at)
    session_id = new_id("sleepsess")
    if plan.get("event_id"):
        try:
            transition_event(conn, owner_kind, owner_id, plan["event_id"], "in_progress", reason or "sleep started", source)
        except Exception:
            pass
    if plan.get("schedule_block_id"):
        try:
            update_schedule_block_status(conn, owner_kind, owner_id, plan["schedule_block_id"], "in_progress", reason or "sleep started", source)
        except Exception:
            pass
    conn.execute(
        """INSERT INTO sleep_sessions(id, owner_kind, owner_id, sleep_plan_id, event_id, schedule_block_id, session_type, status,
               actual_sleep_at, actual_sleep_at_ts, planned_duration_minutes, resource_effects_json)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session_id, owner_kind, owner_id, sleep_plan_id, plan.get("event_id"), plan.get("schedule_block_id"), plan.get("plan_type") or "core_sleep", "asleep", at, at_ts, plan.get("planned_duration_minutes"), dumps({})),
    )
    conn.execute("UPDATE sleep_plans SET status='asleep', updated_at=datetime('now') WHERE id=?", (sleep_plan_id,))
    _record_sleep_transition(conn, owner_kind, owner_id, session_id, None, "asleep", reason=reason or "sleep session started", source=source, metadata={"sleep_plan_id": sleep_plan_id})
    set_realtime_state(conn, owner_kind, owner_id, mode="asleep" if plan.get("plan_type") == "core_sleep" else "napping",
                       active_event_id=plan.get("event_id"), active_schedule_block_id=plan.get("schedule_block_id"), active_sleep_session_id=session_id,
                       interruptibility_level="sleep_interruptible", reply_mode="defer_or_wake", body_state={"sleeping": True}, source=source, reason=reason or "sleep session started")
    append_journal(conn, owner_kind, owner_id, "sleep_session_started", {"sleep_session_id": session_id, "sleep_plan_id": sleep_plan_id, "actual_sleep_at": at}, source)
    return {"sleep_session": get_sleep_session(conn, session_id), "sleep_plan": get_sleep_plan(conn, sleep_plan_id), "realtime_state": get_realtime_state(conn, owner_kind, owner_id)}


def _sleep_effects(duration: int | None, planned_duration: int | None) -> tuple[dict[str, Any], float | None, int | None]:
    duration = int(duration or 0)
    planned = int(planned_duration or duration or 1)
    quality = max(0.0, min(1.0, duration / max(planned, 1))) if planned else None
    sleep_debt_delta = max(0, planned - duration) if planned else 0
    energy_recovery = max(0, min(80, int(duration / 8)))
    fatigue_delta = -max(0, min(90, int(duration / 7)))
    return {"energy_delta_estimate": energy_recovery, "fatigue_delta_estimate": fatigue_delta, "sleep_debt_delta_minutes": sleep_debt_delta}, quality, sleep_debt_delta


def wake_sleep_session(conn, owner_kind: str, owner_id: str, *, sleep_session_id: str | None = None, sleep_plan_id: str | None = None,
                       now: str | None = None, wake_cause: str = "natural", interrupted_by: str | None = None,
                       source: str = "sleep", reason: str | None = None, **_: Any) -> dict[str, Any]:
    if sleep_session_id:
        session = get_sleep_session(conn, sleep_session_id)
    else:
        session = get_active_sleep_session(conn, owner_kind, owner_id)
        if not session and sleep_plan_id:
            row = conn.execute("SELECT * FROM sleep_sessions WHERE owner_kind=? AND owner_id=? AND sleep_plan_id=? ORDER BY created_at DESC LIMIT 1", (owner_kind, owner_id, sleep_plan_id)).fetchone()
            session = _decode_session(row) if row else None
    if not session:
        if sleep_plan_id:
            plan = get_sleep_plan(conn, sleep_plan_id)
            conn.execute("UPDATE sleep_plans SET status='missed', updated_at=datetime('now'), completed_at=datetime('now') WHERE id=?", (sleep_plan_id,))
            if plan.get("schedule_block_id"):
                try:
                    update_schedule_block_status(conn, owner_kind, owner_id, plan["schedule_block_id"], "missed", "sleep wake arrived without session", source)
                except Exception:
                    pass
            day_state = None
            try:
                day_state = record_post_sleep_day_state(conn, owner_kind, owner_id, sleep_plan_id=sleep_plan_id, source="sleep_all_nighter")
            except Exception as exc:
                day_state = {"error": f"{type(exc).__name__}: {exc}"}
            append_journal(conn, owner_kind, owner_id, "sleep_plan_missed", {"sleep_plan_id": sleep_plan_id, "day_state": day_state}, source)
            return {"sleep_plan": get_sleep_plan(conn, sleep_plan_id), "sleep_session": None, "missed": True, "sleep_day_state": day_state}
        raise ValueError("no active sleep session")
    if session["status"] in {"completed", "awake", "interrupted", "cancelled", "missed"}:
        return {"sleep_session": session, "already_awake": True}
    wake_at = normalized_iso(now or now_iso())
    wake_ts = to_epoch(wake_at)
    start_ts = session.get("actual_sleep_at_ts") or wake_ts
    duration = int((wake_ts - start_ts) / 60) if wake_ts is not None and start_ts is not None and wake_ts >= start_ts else 0
    effects, quality, debt_delta = _sleep_effects(duration, session.get("planned_duration_minutes"))
    planned = int(session.get("planned_duration_minutes") or duration or 0)
    final_status = "interrupted" if wake_cause in {"user_interrupt", "call_override", "interrupted"} and duration < planned else "completed"
    conn.execute(
        """UPDATE sleep_sessions SET status=?, actual_wake_at=?, actual_wake_at_ts=?, actual_duration_minutes=?, wake_cause=?, interrupted_by=?,
               quality_score=?, sleep_debt_delta_minutes=?, resource_effects_json=?, updated_at=datetime('now'), completed_at=datetime('now') WHERE id=?""",
        (final_status, wake_at, wake_ts, duration, wake_cause, interrupted_by, quality, debt_delta, dumps(effects), session["id"]),
    )
    plan_id = session.get("sleep_plan_id")
    if plan_id:
        conn.execute("UPDATE sleep_plans SET status=?, updated_at=datetime('now'), completed_at=datetime('now') WHERE id=?", ("completed" if final_status in {"awake", "completed"} else "missed", plan_id))
    _record_sleep_transition(conn, owner_kind, owner_id, session["id"], session.get("status"), final_status, reason=f"wake cause={wake_cause}", source=source, metadata={"duration": duration, "quality": quality})
    if session.get("event_id"):
        try:
            complete_event(conn, owner_kind, owner_id, session["event_id"], f"sleep woke: {wake_cause}; duration={duration}m", {}, source)
        except Exception:
            try:
                transition_event(conn, owner_kind, owner_id, session["event_id"], "partial", f"sleep interrupted: {wake_cause}", source)
            except Exception:
                pass
    if session.get("schedule_block_id"):
        try:
            update_schedule_block_status(conn, owner_kind, owner_id, session["schedule_block_id"], "completed" if final_status in {"awake", "completed"} else "missed", f"sleep woke: {wake_cause}", source)
        except Exception:
            pass
    ledger = []
    # Settle registered resources only; if the user has not registered them,
    # the realtime body state still records estimated effects without creating
    # ad-hoc resources.
    try:
        if conn.execute("SELECT 1 FROM resource_definitions WHERE owner_kind=? AND owner_id=? AND key='energy'", (owner_kind, owner_id)).fetchone():
            ledger.append(apply_delta(conn, owner_kind, owner_id, "energy", float(effects.get("energy_delta_estimate") or 0), "recover", "sleep energy recovery", source, event_id=session.get("event_id"), schedule_block_id=session.get("schedule_block_id")))
        if conn.execute("SELECT 1 FROM resource_definitions WHERE owner_kind=? AND owner_id=? AND key='sleep_debt'", (owner_kind, owner_id)).fetchone() and debt_delta:
            ledger.append(apply_delta(conn, owner_kind, owner_id, "sleep_debt", float(debt_delta), "adjust", "sleep debt settlement", source, event_id=session.get("event_id"), schedule_block_id=session.get("schedule_block_id")))
    except Exception as exc:
        effects["ledger_error"] = f"{type(exc).__name__}: {exc}"
    set_realtime_state(conn, owner_kind, owner_id, mode="idle", active_event_id=None, active_schedule_block_id=None, active_sleep_session_id=None,
                       interruptibility_level="interruptible", reply_mode="immediate", body_state={"sleeping": False, "last_sleep_duration_minutes": duration, **effects}, source=source, reason=reason or f"sleep woke: {wake_cause}")
    sleep_day_state = None
    try:
        sleep_day_state = record_post_sleep_day_state(conn, owner_kind, owner_id, sleep_session_id=session["id"], source="post_sleep_effects")
    except Exception as exc:
        effects["sleep_day_state_error"] = f"{type(exc).__name__}: {exc}"
    append_journal(conn, owner_kind, owner_id, "sleep_session_woke", {"sleep_session_id": session["id"], "wake_cause": wake_cause, "duration": duration, "quality": quality, "ledger": ledger, "sleep_day_state": sleep_day_state}, source)
    return {"sleep_session": get_sleep_session(conn, session["id"]), "sleep_plan": get_sleep_plan(conn, plan_id) if plan_id else None, "resource_effects": effects, "sleep_day_state": sleep_day_state, "realtime_state": get_realtime_state(conn, owner_kind, owner_id), "ledger": ledger}


def interrupt_sleep_session(conn, owner_kind: str, owner_id: str, *, sleep_session_id: str | None = None, now: str | None = None,
                            source: str = "user_message", reason: str | None = None, user_id: str | None = None,
                            session_id: str | None = None, turn_id: str | None = None, caused_wake: bool = True,
                            metadata: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
    session = get_sleep_session(conn, sleep_session_id) if sleep_session_id else get_active_sleep_session(conn, owner_kind, owner_id)
    if not session:
        return {"ok": True, "sleep_session": None, "interrupted": False, "reason": "not asleep"}
    at = normalized_iso(now or now_iso())
    iid = new_id("sleepint")
    conn.execute(
        """INSERT INTO sleep_interruptions(id, owner_kind, owner_id, sleep_session_id, interrupted_at, interrupted_at_ts,
               source, reason, user_id, session_id, turn_id, caused_wake, metadata_json)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (iid, owner_kind, owner_id, session["id"], at, to_epoch(at), source, reason, user_id, session_id, turn_id, 1 if caused_wake else 0, dumps(metadata or {})),
    )
    append_journal(conn, owner_kind, owner_id, "sleep_interrupted", {"sleep_session_id": session["id"], "interruption_id": iid, "caused_wake": caused_wake, "reason": reason}, source)
    result = {"interruption_id": iid, "sleep_session": session, "caused_wake": caused_wake}
    if caused_wake:
        result["wake"] = wake_sleep_session(conn, owner_kind, owner_id, sleep_session_id=session["id"], now=at, wake_cause="user_interrupt", interrupted_by=user_id or source, source=source, reason=reason or "sleep interrupted")
    return result


def sleep_interruptions(conn, owner_kind: str, owner_id: str, sleep_session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM sleep_interruptions WHERE owner_kind=? AND owner_id=? AND sleep_session_id=? ORDER BY interrupted_at_ts DESC LIMIT ?", (owner_kind, owner_id, sleep_session_id, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = loads(d.pop("metadata_json"), {})
        out.append(d)
    return out


def sleep_doctor(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    stale = conn.execute("""SELECT * FROM sleep_sessions WHERE owner_kind=? AND owner_id=? AND status='asleep'
              AND actual_sleep_at_ts IS NOT NULL AND actual_sleep_at_ts < unixepoch('now') - 16*3600""", (owner_kind, owner_id)).fetchall()
    for r in stale:
        findings.append({"type": "stale_sleep_session", "severity": "warning", "sleep_session_id": r["id"], "message": "sleep session has been asleep for more than 16 hours"})
    missed = conn.execute("""SELECT * FROM sleep_plans WHERE owner_kind=? AND owner_id=? AND status IN ('planned','scheduled')
              AND planned_wake_at_ts IS NOT NULL AND planned_wake_at_ts < unixepoch('now') - 3600""", (owner_kind, owner_id)).fetchall()
    for r in missed:
        findings.append({"type": "missed_sleep_plan", "severity": "info", "sleep_plan_id": r["id"], "message": "sleep plan wake time passed without completion"})
    for f in findings:
        conn.execute("INSERT INTO sleep_doctor_findings(id, owner_kind, owner_id, finding_type, severity, sleep_plan_id, sleep_session_id, message, metadata_json) VALUES(?,?,?,?,?,?,?,?,?)", (new_id("sleepfind"), owner_kind, owner_id, f["type"], f["severity"], f.get("sleep_plan_id"), f.get("sleep_session_id"), f["message"], dumps(f)))
    return {"status": "ok" if not findings else "warn", "findings": findings}


def _compose_sleep_iso(date_key: str | None, time_value: str | None, *, default_time: str, timezone_name: str, roll_day: bool = False) -> str:
    """Compose a timezone-aware ISO string from YYYY-MM-DD + HH:MM or pass through ISO input."""
    if time_value and "T" in str(time_value):
        return normalized_iso(str(time_value), default_tz=timezone_name) or str(time_value)
    date = (date_key or now_iso()[:10])[:10]
    t = str(time_value or default_time)
    if len(t) == 5:
        t = t + ":00"
    tz = ZoneInfo(timezone_name or "UTC")
    dt = datetime.fromisoformat(f"{date}T{t}").replace(tzinfo=tz)
    if roll_day:
        dt = dt + timedelta(days=1)
    return dt.isoformat()



def _compose_sleep_datetime(date_key: str | None, clock_value: str | None, *, default_clock: str, timezone: str = "UTC", roll_after: str | None = None) -> str:
    """Compose an ISO datetime from either a full ISO string or a HH:MM clock.

    If roll_after is provided and the composed timestamp is not after it, roll
    the date forward by one day.  This handles normal overnight sleep plans such
    as 23:30 → 07:00.
    """
    from datetime import timedelta
    value = str(clock_value or default_clock).strip()
    if "T" in value:
        iso = normalized_iso(value, default_tz=timezone) or value
    else:
        date = (date_key or now_iso()[:10])[:10]
        clock = value[-5:] if len(value) >= 5 else default_clock
        iso = normalized_iso(f"{date}T{clock}:00", default_tz=timezone)
    if roll_after and iso:
        start_ts = to_epoch(roll_after, default_tz=timezone)
        end_ts = to_epoch(iso, default_tz=timezone)
        if start_ts is not None and end_ts is not None and end_ts <= start_ts:
            dt = parse_datetime(iso, default_tz=timezone) + timedelta(days=1)
            iso = dt.isoformat()
    return iso


def plan_core_sleep(conn, owner_kind: str, owner_id: str, *, date_key: str | None = None,
                    target_bedtime: str | None = None, target_wake_time: str | None = None,
                    timezone: str = "UTC", alarm_time: str | None = None,
                    alarm_enabled: bool = False, natural_wake_allowed: bool = True,
                    sleep_policy: dict[str, Any] | None = None, busy_score: int = 50,
                    fatigue_score: int = 50, allow_replace: bool = False,
                    source: str = "sleep_planner", canon_version: int | None = None,
                    **kwargs: Any) -> dict[str, Any]:
    """Plan the day's core sleep.

    Core sleep is represented as Event V2 + ScheduleBlock V2 + SleepPlan and
    wake jobs.  Planned and actual sleep intentionally remain separate.
    """
    start = _compose_sleep_datetime(date_key, target_bedtime, default_clock="23:30", timezone=timezone)
    end = _compose_sleep_datetime(date_key, target_wake_time, default_clock="07:00", timezone=timezone, roll_after=start)
    alarm_at = None
    if alarm_enabled and alarm_time:
        alarm_at = _compose_sleep_datetime(date_key, alarm_time, default_clock="07:00", timezone=timezone, roll_after=start)
    return create_sleep_plan(
        conn, owner_kind, owner_id,
        planned_sleep_at=start,
        planned_wake_at=end,
        date=(date_key or (start or now_iso())[:10])[:10],
        plan_type="core_sleep",
        timezone_name=timezone,
        alarm_at=alarm_at,
        wake_policy="alarm" if alarm_enabled else "natural_or_alarm" if natural_wake_allowed else "schedule",
        constraints=sleep_policy or {},
        decision={"busy_score": busy_score, "fatigue_score": fatigue_score, "allow_replace": bool(allow_replace)},
        source=source,
        canon_version=canon_version,
    )


def end_sleep_session(conn, owner_kind: str, owner_id: str, *, sleep_session_id: str | None = None, now: str | None = None,
                      wake_cause: str = "natural", source: str = "life_sleep_tool", notes: str | None = None,
                      **kwargs: Any) -> dict[str, Any]:
    return wake_sleep_session(conn, owner_kind, owner_id, sleep_session_id=sleep_session_id, now=now,
                              wake_cause=wake_cause, source=source, reason=notes, **kwargs)


def skip_sleep_plan(conn, owner_kind: str, owner_id: str, *, sleep_plan_id: str, reason: str = "skipped",
                    source: str = "life_sleep_tool", **_: Any) -> dict[str, Any]:
    plan = get_sleep_plan(conn, sleep_plan_id)
    if plan.get("status") in {"completed", "skipped", "cancelled", "missed"}:
        return {"sleep_plan": plan, "already_terminal": True}
    conn.execute("UPDATE sleep_plans SET status='skipped', updated_at=datetime('now'), completed_at=datetime('now') WHERE id=?", (sleep_plan_id,))
    if plan.get("schedule_block_id"):
        try:
            update_schedule_block_status(conn, owner_kind, owner_id, plan["schedule_block_id"], "skipped", reason, source)
        except Exception:
            pass
    if plan.get("event_id"):
        try:
            transition_event(conn, owner_kind, owner_id, plan["event_id"], "skipped", reason, source)
        except Exception:
            pass
    append_journal(conn, owner_kind, owner_id, "sleep_plan_skipped", {"sleep_plan_id": sleep_plan_id, "reason": reason}, source)
    return {"sleep_plan": get_sleep_plan(conn, sleep_plan_id)}


def sleep_status(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    return {
        "realtime_state": get_realtime_state(conn, owner_kind, owner_id),
        "active_sleep_session": get_active_sleep_session(conn, owner_kind, owner_id),
        "planned": list_sleep_plans(conn, owner_kind, owner_id, status="scheduled", limit=5),
        "recent_sessions": list_sleep_sessions(conn, owner_kind, owner_id, limit=5),
        "latest_day_state": get_sleep_day_state(conn, owner_kind, owner_id),
    }
