"""Sleep day-state effects for LifeEngine v0.11.5.

This module turns completed/interrupted SleepSession rows into a durable day
state: all-nighter detection, cumulative sleep debt, next-day energy/focus/mood
penalties, and optional recovery-sleep pressure.  It is intentionally separate
from SleepSession itself: planned/actual sleep remains the source event, while
sleep_day_states is the materialized physiological aftermath that Autonomy,
Dream, and ReplyGate can read.
"""

from __future__ import annotations

from typing import Any

from .events import get_realtime_state, set_realtime_state
from .jsonutil import dumps, loads
from .resources import apply_delta
from .time_utils import normalized_iso, now_iso
from .trace import append_journal, new_id


def _decode_day_state(row) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["resource_ledger_ids"] = loads(d.pop("resource_ledger_ids_json"), [])
        d["body_state"] = loads(d.pop("body_state_json"), {})
        d["mind_state"] = loads(d.pop("mind_state_json"), {})
        d["all_nighter"] = bool(d.get("all_nighter"))
        d["nap_recommended"] = bool(d.get("nap_recommended"))
    return d


def _resource_defined(conn, owner_kind: str, owner_id: str, key: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM resource_definitions WHERE owner_kind=? AND owner_id=? AND key=?",
        (owner_kind, owner_id, key),
    ).fetchone())


def _apply_if_defined(conn, owner_kind: str, owner_id: str, key: str, delta: float, operation: str, reason: str,
                      source: str, event_id: str | None = None, schedule_block_id: str | None = None) -> str | None:
    if not delta or not _resource_defined(conn, owner_kind, owner_id, key):
        return None
    row = apply_delta(
        conn, owner_kind, owner_id, key, float(delta), operation, reason, source,
        event_id=event_id, schedule_block_id=schedule_block_id,
    )
    return row.get("id") if isinstance(row, dict) else None


def _date_from_iso(iso: str | None) -> str:
    return (iso or now_iso())[:10]


def _previous_cumulative_debt(conn, owner_kind: str, owner_id: str, date_key: str) -> int:
    row = conn.execute(
        """SELECT cumulative_sleep_debt_minutes FROM sleep_day_states
             WHERE owner_kind=? AND owner_id=? AND date_key < ?
             ORDER BY date_key DESC LIMIT 1""",
        (owner_kind, owner_id, date_key),
    ).fetchone()
    return int(row[0]) if row else 0


def compute_sleep_day_effects(*, planned_minutes: int, actual_minutes: int, previous_debt: int = 0) -> dict[str, Any]:
    planned = max(0, int(planned_minutes or 0))
    actual = max(0, int(actual_minutes or 0))
    debt_delta = max(0, planned - actual)
    over_recovery = max(0, actual - planned)
    all_nighter = planned >= 240 and actual < 90
    cumulative = max(0, previous_debt + debt_delta - int(over_recovery * 0.5))
    energy_penalty = min(55, int(debt_delta / 15) + (25 if all_nighter else 0))
    focus_penalty = min(50, int(debt_delta / 20) + (22 if all_nighter else 0))
    mood_penalty = min(35, int(debt_delta / 45) + (12 if all_nighter else 0))
    fatigue_delta = min(75, int(debt_delta / 10) + (30 if all_nighter else 0))
    recovery_pressure = min(100, int(cumulative / 6) + (25 if all_nighter else 0))
    nap_recommended = recovery_pressure >= 60 or all_nighter or fatigue_delta >= 55
    return {
        "planned_sleep_minutes": planned,
        "actual_sleep_minutes": actual,
        "sleep_debt_delta_minutes": debt_delta,
        "cumulative_sleep_debt_minutes": cumulative,
        "all_nighter": all_nighter,
        "energy_penalty": energy_penalty,
        "focus_penalty": focus_penalty,
        "mood_penalty": mood_penalty,
        "fatigue_delta": fatigue_delta,
        "recovery_pressure": recovery_pressure,
        "nap_recommended": nap_recommended,
    }


def record_post_sleep_day_state(conn, owner_kind: str, owner_id: str, *, sleep_session_id: str | None = None,
                                sleep_plan_id: str | None = None, date_key: str | None = None,
                                source: str = "sleep_effects", apply_resource_effects: bool = True) -> dict[str, Any]:
    """Record next-day state effects for a completed/interrupted sleep session or missed plan."""
    session = None
    plan = None
    if sleep_session_id:
        row = conn.execute("SELECT * FROM sleep_sessions WHERE id=? AND owner_kind=? AND owner_id=?", (sleep_session_id, owner_kind, owner_id)).fetchone()
        session = dict(row) if row else None
        if session and session.get("sleep_plan_id"):
            sleep_plan_id = session.get("sleep_plan_id")
    if sleep_plan_id:
        row = conn.execute("SELECT * FROM sleep_plans WHERE id=? AND owner_kind=? AND owner_id=?", (sleep_plan_id, owner_kind, owner_id)).fetchone()
        plan = dict(row) if row else None
    if not session and not plan:
        raise ValueError("sleep_session_id or sleep_plan_id is required")

    planned = int((session or {}).get("planned_duration_minutes") or (plan or {}).get("planned_duration_minutes") or 0)
    actual = int((session or {}).get("actual_duration_minutes") or 0)
    wake_iso = (session or {}).get("actual_wake_at") or (plan or {}).get("planned_wake_at") or now_iso()
    key = date_key or _date_from_iso(wake_iso)
    prev = _previous_cumulative_debt(conn, owner_kind, owner_id, key)
    effects = compute_sleep_day_effects(planned_minutes=planned, actual_minutes=actual, previous_debt=prev)
    ledger_ids: list[str] = []
    if apply_resource_effects:
        event_id = (session or {}).get("event_id") or (plan or {}).get("event_id")
        block_id = (session or {}).get("schedule_block_id") or (plan or {}).get("schedule_block_id")
        for key_name, delta, op, reason in [
            ("energy", -effects["energy_penalty"], "consume", "sleep insufficiency next-day energy penalty"),
            ("focus", -effects["focus_penalty"], "consume", "sleep insufficiency focus penalty"),
            ("mood", -effects["mood_penalty"], "consume", "sleep insufficiency mood penalty"),
            ("fatigue", effects["fatigue_delta"], "adjust", "sleep insufficiency fatigue increase"),
        ]:
            try:
                lid = _apply_if_defined(conn, owner_kind, owner_id, key_name, delta, op, reason, source, event_id=event_id, schedule_block_id=block_id)
                if lid:
                    ledger_ids.append(lid)
            except Exception:
                # Resource ledgers are strict, but sleep-day-state must still be
                # recorded so Autonomy/Execution can react to sleep debt.
                continue
    body_state = {
        "sleep_debt_minutes": effects["cumulative_sleep_debt_minutes"],
        "last_sleep_debt_delta_minutes": effects["sleep_debt_delta_minutes"],
        "fatigue_delta_from_sleep": effects["fatigue_delta"],
        "recovery_pressure": effects["recovery_pressure"],
        "nap_recommended": effects["nap_recommended"],
        "all_nighter": effects["all_nighter"],
    }
    mind_state = {
        "focus_penalty_from_sleep": effects["focus_penalty"],
        "mood_penalty_from_sleep": effects["mood_penalty"],
    }
    state_id = new_id("sleepday")
    conn.execute(
        """INSERT INTO sleep_day_states(
             id, owner_kind, owner_id, date_key, source_sleep_plan_id, source_sleep_session_id,
             planned_sleep_minutes, actual_sleep_minutes, sleep_debt_delta_minutes,
             cumulative_sleep_debt_minutes, all_nighter, energy_penalty, focus_penalty,
             mood_penalty, fatigue_delta, recovery_pressure, nap_recommended,
             resource_ledger_ids_json, body_state_json, mind_state_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(owner_kind, owner_id, date_key) DO UPDATE SET
             source_sleep_plan_id=excluded.source_sleep_plan_id,
             source_sleep_session_id=excluded.source_sleep_session_id,
             planned_sleep_minutes=excluded.planned_sleep_minutes,
             actual_sleep_minutes=excluded.actual_sleep_minutes,
             sleep_debt_delta_minutes=excluded.sleep_debt_delta_minutes,
             cumulative_sleep_debt_minutes=excluded.cumulative_sleep_debt_minutes,
             all_nighter=excluded.all_nighter,
             energy_penalty=excluded.energy_penalty,
             focus_penalty=excluded.focus_penalty,
             mood_penalty=excluded.mood_penalty,
             fatigue_delta=excluded.fatigue_delta,
             recovery_pressure=excluded.recovery_pressure,
             nap_recommended=excluded.nap_recommended,
             resource_ledger_ids_json=excluded.resource_ledger_ids_json,
             body_state_json=excluded.body_state_json,
             mind_state_json=excluded.mind_state_json,
             updated_at=datetime('now')""",
        (
            state_id, owner_kind, owner_id, key, (plan or {}).get("id"), (session or {}).get("id"),
            effects["planned_sleep_minutes"], effects["actual_sleep_minutes"], effects["sleep_debt_delta_minutes"],
            effects["cumulative_sleep_debt_minutes"], 1 if effects["all_nighter"] else 0,
            effects["energy_penalty"], effects["focus_penalty"], effects["mood_penalty"], effects["fatigue_delta"],
            effects["recovery_pressure"], 1 if effects["nap_recommended"] else 0,
            dumps(ledger_ids), dumps(body_state), dumps(mind_state),
        ),
    )
    row = conn.execute("SELECT * FROM sleep_day_states WHERE owner_kind=? AND owner_id=? AND date_key=?", (owner_kind, owner_id, key)).fetchone()
    day_state = _decode_day_state(row)
    current = get_realtime_state(conn, owner_kind, owner_id)
    set_realtime_state(
        conn, owner_kind, owner_id,
        mode=current.get("mode"),
        active_event_id=current.get("active_event_id"),
        active_action_id=current.get("active_action_id"),
        active_schedule_block_id=current.get("active_schedule_block_id"),
        active_sleep_session_id=current.get("active_sleep_session_id"),
        interruptibility_level=current.get("interruptibility_level"),
        reply_mode=current.get("reply_mode"),
        body_state=body_state,
        mind_state=mind_state,
        source=source,
        reason="post-sleep day state effects",
    )
    append_journal(conn, owner_kind, owner_id, "sleep_day_state_recorded", {"sleep_day_state": day_state}, source)
    return day_state


def get_sleep_day_state(conn, owner_kind: str, owner_id: str, date_key: str | None = None) -> dict[str, Any] | None:
    if date_key:
        row = conn.execute("SELECT * FROM sleep_day_states WHERE owner_kind=? AND owner_id=? AND date_key=?", (owner_kind, owner_id, date_key)).fetchone()
    else:
        row = conn.execute("SELECT * FROM sleep_day_states WHERE owner_kind=? AND owner_id=? ORDER BY date_key DESC LIMIT 1", (owner_kind, owner_id)).fetchone()
    return _decode_day_state(row) if row else None


def list_sleep_day_states(conn, owner_kind: str, owner_id: str, limit: int = 14) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM sleep_day_states WHERE owner_kind=? AND owner_id=? ORDER BY date_key DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_decode_day_state(r) for r in rows]


def plan_recovery_sleep_if_needed(conn, owner_kind: str, owner_id: str, *, date_key: str | None = None,
                                  threshold: int = 60, duration_minutes: int = 30,
                                  source: str = "sleep_effects") -> dict[str, Any]:
    from datetime import timedelta
    from .sleep import create_sleep_plan
    from .time_utils import parse_datetime

    day = get_sleep_day_state(conn, owner_kind, owner_id, date_key)
    if not day:
        return {"ok": False, "reason": "no sleep_day_state"}
    if int(day.get("recovery_pressure") or 0) < int(threshold) and not day.get("nap_recommended"):
        return {"ok": True, "planned": False, "reason": "recovery pressure below threshold", "sleep_day_state": day}
    existing = conn.execute(
        "SELECT * FROM sleep_recovery_plans WHERE owner_kind=? AND owner_id=? AND date_key=? AND status IN ('planned','scheduled') ORDER BY created_at DESC LIMIT 1",
        (owner_kind, owner_id, day["date_key"]),
    ).fetchone()
    if existing:
        return {"ok": True, "planned": False, "already_exists": dict(existing), "sleep_day_state": day}
    # Conservative default: schedule a soft recovery nap around early afternoon.
    base = parse_datetime(f"{day['date_key']}T14:00:00", default_tz="UTC")
    start = base.isoformat()
    end = (base + timedelta(minutes=int(duration_minutes))).isoformat()
    plan = create_sleep_plan(
        conn, owner_kind, owner_id,
        planned_sleep_at=start, planned_wake_at=end,
        date=day["date_key"], plan_type="recovery_sleep", timezone_name="UTC",
        wake_policy="short_recovery", title="睡眠不足后的补觉 / 小憩",
        constraints={"generated_by": "sleep_day_state", "threshold": threshold},
        decision={"recovery_pressure": day.get("recovery_pressure"), "sleep_debt_minutes": day.get("cumulative_sleep_debt_minutes")},
        source=source,
    )
    rec_id = new_id("sleeprcv")
    conn.execute(
        """INSERT INTO sleep_recovery_plans(id, owner_kind, owner_id, date_key, sleep_day_state_id, sleep_plan_id, reason, pressure, status, metadata_json)
             VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (rec_id, owner_kind, owner_id, day["date_key"], day.get("id"), plan["sleep_plan"]["id"], "sleep debt recovery pressure", int(day.get("recovery_pressure") or 0), "planned", dumps({"duration_minutes": duration_minutes})),
    )
    conn.execute("UPDATE sleep_day_states SET recovery_plan_id=?, updated_at=datetime('now') WHERE id=?", (plan["sleep_plan"]["id"], day.get("id")))
    append_journal(conn, owner_kind, owner_id, "sleep_recovery_plan_created", {"sleep_recovery_plan_id": rec_id, "sleep_plan_id": plan["sleep_plan"]["id"], "sleep_day_state_id": day.get("id")}, source)
    return {"ok": True, "planned": True, "sleep_recovery_plan_id": rec_id, "sleep_day_state": get_sleep_day_state(conn, owner_kind, owner_id, day["date_key"]), **plan}
