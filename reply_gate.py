"""ReplyGate / delayed replies / call override for LifeEngine v0.11.2.

ReplyGate is operational state, not narrative fact.  It decides whether an
incoming user message should be allowed immediately, deferred while the agent is
asleep or inside an uninterruptible event, or force-released through a call
interface.  All decisions are traceable in SQLite and journaled.
"""

from __future__ import annotations

from typing import Any

from .events import get_realtime_state, set_realtime_state, transition_event, update_schedule_block_status
from .jsonutil import dumps, loads
from .sleep import get_active_sleep_session, interrupt_sleep_session
from .time_utils import normalized_iso, now_iso, to_epoch
from .trace import append_journal, new_id
from .sleep_reply_dream_policy import get_policy as get_srd_policy, render_delayed_digest

DEFER_MODES = {"asleep", "napping", "dreaming", "uninterruptible_event", "waiting_to_reply"}
BUSY_MODES = {"busy", "recovering"}
CALL_WORDS = {"call", "wake", "醒醒", "叫醒", "紧急", "urgent", "emergency", "立刻", "马上"}


def _decode_delayed(row) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["metadata"] = loads(d.pop("metadata_json"), {})
    return d


def _decode_decision(row) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["policy"] = loads(d.pop("policy_json"), {})
        d["state_snapshot"] = loads(d.pop("state_snapshot_json"), {})
    return d


def _decode_call(row) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["result"] = loads(d.pop("result_json"), {})
    return d




def _decode_digest(row) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["delayed_reply_ids"] = loads(d.pop("delayed_reply_ids_json"), [])
        d["metadata"] = loads(d.pop("metadata_json"), {})
    return d

def create_delayed_reply_digest(conn, owner_kind: str, owner_id: str, replies: list[dict[str, Any]] | list[Any], *,
                                release_reason: str = "released", source: str = "reply_gate") -> dict[str, Any] | None:
    if not replies:
        return None
    decoded = [_decode_delayed(r) if not isinstance(r, dict) else r for r in replies]
    ids = [r.get("id") for r in decoded if r.get("id")]
    previews = [str(r.get("message_preview") or r.get("message_text") or "").strip() for r in decoded]
    lines = []
    for idx, preview in enumerate([p for p in previews if p], start=1):
        lines.append(f"{idx}. {preview[:120]}")
    raw_summary = "；".join(lines[:5]) if lines else "没有可用摘要"
    if lines and len(lines) > 5:
        raw_summary += f"；另外还有 {len(lines)-5} 条。"
    policy = get_srd_policy(conn, owner_kind, owner_id)
    summary = render_delayed_digest(policy, count=len(decoded), summary=raw_summary)
    digest_id = new_id("rpdigest")
    conn.execute(
        """INSERT INTO delayed_reply_digests(id, owner_kind, owner_id, status, delayed_reply_ids_json, message_count, summary_text, release_reason, created_by, metadata_json)
             VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (digest_id, owner_kind, owner_id, "created", dumps(ids), len(decoded), summary, release_reason, source, dumps({"previews": previews[:10]})),
    )
    append_journal(conn, owner_kind, owner_id, "delayed_reply_digest_created", {"digest_id": digest_id, "reply_ids": ids, "message_count": len(decoded)}, source)
    row = conn.execute("SELECT * FROM delayed_reply_digests WHERE id=?", (digest_id,)).fetchone()
    return _decode_digest(row)

def list_delayed_reply_digests(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM delayed_reply_digests WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_decode_digest(r) for r in rows]

def _module_gate(control: dict[str, Any] | None, key: str, default: str) -> str:
    gates = (control or {}).get("module_gates") or {}
    return str(gates.get(key, default) or default).lower()


def _preview(text: str | None, max_len: int = 300) -> str:
    s = (text or "").replace("\n", " ").strip()
    return s[:max_len]


def _contains_call_override(text: str | None, words: list[str] | None = None) -> bool:
    low = (text or "").lower()
    call_words = words or CALL_WORDS
    return any(str(w).lower() in low for w in call_words)


def _state_expired(state: dict[str, Any] | None, now_ts: int | None = None) -> bool:
    if not state:
        return False
    lease_ts = state.get("lease_expires_at_ts")
    if lease_ts is None:
        return False
    now_ts = now_ts if now_ts is not None else to_epoch(now_iso())
    return now_ts is not None and int(lease_ts) <= int(now_ts)


def record_reply_gate_decision(conn, owner_kind: str, owner_id: str, *, decision: str, reason: str,
                               session_id: str | None = None, turn_id: str | None = None,
                               user_id: str | None = None, message_text: str | None = None,
                               state: dict[str, Any] | None = None, policy: dict[str, Any] | None = None,
                               trace_id: str | None = None, source: str = "reply_gate") -> dict[str, Any]:
    rid = new_id("replygate")
    state = state or get_realtime_state(conn, owner_kind, owner_id)
    conn.execute(
        """INSERT INTO reply_gate_decisions(
             id, owner_kind, owner_id, session_id, turn_id, user_id, incoming_message_preview,
             decision, reason, mode, active_event_id, active_schedule_block_id, active_sleep_session_id,
             interruptibility_level, reply_mode, state_snapshot_json, policy_json, trace_id, source
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rid, owner_kind, owner_id, session_id, turn_id, user_id, _preview(message_text), decision, reason,
            state.get("mode") if state else None, (state or {}).get("active_event_id"),
            (state or {}).get("active_schedule_block_id"), (state or {}).get("active_sleep_session_id"),
            (state or {}).get("interruptibility_level"), (state or {}).get("reply_mode"),
            dumps(state or {}), dumps(policy or {}), trace_id, source,
        ),
    )
    row = conn.execute("SELECT * FROM reply_gate_decisions WHERE id=?", (rid,)).fetchone()
    append_journal(conn, owner_kind, owner_id, "reply_gate_decision", {"decision_id": rid, "decision": decision, "reason": reason}, source)
    return _decode_decision(row)


def assess_reply_gate(conn, owner_kind: str, owner_id: str, control: dict[str, Any] | None, *,
                      message_text: str | None = None, session_id: str | None = None,
                      turn_id: str | None = None, user_id: str | None = None,
                      force_call: bool = False, trace_id: str | None = None,
                      write: bool = True, source: str = "reply_gate") -> dict[str, Any]:
    state = get_realtime_state(conn, owner_kind, owner_id)
    policy_cfg = get_srd_policy(conn, owner_kind, owner_id).get("effective_policy", {}).get("reply", {})
    mode_gate = _module_gate(control, "reply_gate", policy_cfg.get("gate_mode", "advisory"))
    if mode_gate == "policy":
        mode_gate = str(policy_cfg.get("gate_mode", "advisory"))
    now_ts = to_epoch(now_iso())
    expired = _state_expired(state, now_ts)
    call_requested = bool(force_call or _contains_call_override(message_text, policy_cfg.get("call_words")))
    policy = {"reply_gate_mode": mode_gate, "call_requested": call_requested, "expired_lease": expired, "srd_reply_policy": policy_cfg}

    if mode_gate in {"off", "disabled"}:
        decision, reason = "allow", "reply_gate disabled"
    elif expired:
        decision, reason = "allow", "state lease expired; fail-safe allow"
    elif call_requested:
        decision, reason = "call_override", "call override requested"
    elif (state or {}).get("mode") in {"asleep", "napping", "dreaming"}:
        if mode_gate in {"auto", "strict"}:
            decision, reason = "defer", "agent is sleeping"
        else:
            decision, reason = "advisory", "agent is sleeping; advise model, do not block"
    elif (state or {}).get("mode") == "uninterruptible_event" or (state or {}).get("reply_mode") == "defer_until_event_end":
        if mode_gate in {"auto", "strict"}:
            decision, reason = "defer", "agent is in an uninterruptible event"
        else:
            decision, reason = "advisory", "agent is in an uninterruptible event; advise model, do not block"
    elif (state or {}).get("mode") == "waiting_to_reply":
        decision, reason = "advisory", "agent has delayed replies waiting"
    elif (state or {}).get("mode") in BUSY_MODES:
        decision, reason = "allow", "agent is busy but interruptible"
    else:
        decision, reason = "allow", "agent available"

    if write:
        dec = record_reply_gate_decision(
            conn, owner_kind, owner_id, decision=decision, reason=reason, session_id=session_id, turn_id=turn_id,
            user_id=user_id, message_text=message_text, state=state, policy=policy, trace_id=trace_id, source=source,
        )
    else:
        dec = {"decision": decision, "reason": reason, "policy": policy, "state_snapshot": state}
    return {"ok": True, "decision": dec, "state": state, "gate_mode": mode_gate, "should_defer": decision == "defer", "should_call": decision == "call_override"}


def create_delayed_reply(conn, owner_kind: str, owner_id: str, *, message_text: str, reason: str = "deferred by ReplyGate",
                         user_id: str | None = None, session_id: str | None = None, turn_id: str | None = None,
                         gate_decision_id: str | None = None, expires_at: str | None = None,
                         metadata: dict[str, Any] | None = None, source: str = "reply_gate") -> dict[str, Any]:
    did = new_id("delreply")
    expires_iso = normalized_iso(expires_at) if expires_at else None
    expires_ts = to_epoch(expires_iso) if expires_iso else None
    conn.execute(
        """INSERT INTO delayed_replies(
             id, owner_kind, owner_id, user_id, session_id, turn_id, message_text, message_preview,
             gate_decision_id, reason, status, expires_at, expires_at_ts, metadata_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (did, owner_kind, owner_id, user_id, session_id, turn_id, message_text, _preview(message_text), gate_decision_id,
         reason, "pending", expires_iso, expires_ts, dumps(metadata or {})),
    )
    current_state = get_realtime_state(conn, owner_kind, owner_id)
    set_realtime_state(
        conn, owner_kind, owner_id, mode="waiting_to_reply", reply_mode="defer_until_available",
        active_event_id=current_state.get("active_event_id"),
        active_action_id=current_state.get("active_action_id"),
        active_schedule_block_id=current_state.get("active_schedule_block_id"),
        active_sleep_session_id=current_state.get("active_sleep_session_id"),
        interruptibility_level=current_state.get("interruptibility_level"),
        source=source, reason="delayed reply queued",
    )
    append_journal(conn, owner_kind, owner_id, "delayed_reply_created", {"delayed_reply_id": did, "gate_decision_id": gate_decision_id, "reason": reason}, source)
    row = conn.execute("SELECT * FROM delayed_replies WHERE id=?", (did,)).fetchone()
    return _decode_delayed(row)


def list_delayed_replies(conn, owner_kind: str, owner_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    params: list[Any] = [owner_kind, owner_id]
    clause = "owner_kind=? AND owner_id=?"
    if status:
        clause += " AND status=?"
        params.append(status)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM delayed_replies WHERE {clause} ORDER BY queued_at DESC LIMIT ?", tuple(params)).fetchall()
    return [_decode_delayed(r) for r in rows]


def release_delayed_replies(conn, owner_kind: str, owner_id: str, *, reason: str = "released", status: str = "pending",
                            source: str = "reply_gate", limit: int = 50) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status=? ORDER BY queued_at ASC LIMIT ?",
        (owner_kind, owner_id, status, int(limit)),
    ).fetchall()
    ids = [r["id"] for r in rows]
    now = now_iso()
    for did in ids:
        conn.execute("UPDATE delayed_replies SET status='released', released_at=?, release_reason=? WHERE id=?", (now, reason, did))
    digest = None
    decoded_rows = [_decode_delayed(r) for r in rows]
    if ids:
        digest = create_delayed_reply_digest(conn, owner_kind, owner_id, decoded_rows, release_reason=reason, source=source)
        set_realtime_state(conn, owner_kind, owner_id, mode="idle", reply_mode="immediate", source=source, reason="delayed replies released")
        append_journal(conn, owner_kind, owner_id, "delayed_replies_released", {"reply_ids": ids, "reason": reason, "digest_id": digest.get("id") if digest else None}, source)
    return {"ok": True, "released_count": len(ids), "replies": decoded_rows, "digest": digest}


def call_override(conn, owner_kind: str, owner_id: str, *, reason: str = "call override", user_id: str | None = None,
                  session_id: str | None = None, turn_id: str | None = None, message_text: str | None = None,
                  trace_id: str | None = None, source: str = "life_call", **_: Any) -> dict[str, Any]:
    state = get_realtime_state(conn, owner_kind, owner_id)
    active_sleep_session_id = (state or {}).get("active_sleep_session_id")
    active_event_id = (state or {}).get("active_event_id")
    active_block_id = (state or {}).get("active_schedule_block_id")
    gate = record_reply_gate_decision(conn, owner_kind, owner_id, decision="call_override", reason=reason,
                                      session_id=session_id, turn_id=turn_id, user_id=user_id, message_text=message_text,
                                      state=state, policy={"override": True}, trace_id=trace_id, source=source)
    result: dict[str, Any] = {"gate_decision": gate, "previous_state": state}
    if active_sleep_session_id:
        try:
            result["sleep_interrupt"] = interrupt_sleep_session(conn, owner_kind, owner_id, sleep_session_id=active_sleep_session_id,
                                                                 source="call_override", reason=reason, user_id=user_id,
                                                                 session_id=session_id, turn_id=turn_id, caused_wake=True,
                                                                 metadata={"call_override": True})
        except Exception as exc:
            result["sleep_interrupt_error"] = f"{type(exc).__name__}: {exc}"
    elif active_event_id:
        try:
            transition_event(conn, owner_kind, owner_id, active_event_id, "partial", f"interrupted by call override: {reason}", source)
            result["event_interrupted"] = active_event_id
        except Exception as exc:
            result["event_interrupt_error"] = f"{type(exc).__name__}: {exc}"
        if active_block_id:
            try:
                update_schedule_block_status(conn, owner_kind, owner_id, active_block_id, "skipped", f"interrupted by call override: {reason}", source)
                result["schedule_interrupted"] = active_block_id
            except Exception as exc:
                result["schedule_interrupt_error"] = f"{type(exc).__name__}: {exc}"
    set_realtime_state(conn, owner_kind, owner_id, mode="in_conversation", active_event_id=None, active_schedule_block_id=None,
                       active_sleep_session_id=None, interruptibility_level="interruptible", reply_mode="immediate",
                       source=source, reason=reason, body_state={"called": True, "sleeping": False})
    released = release_delayed_replies(conn, owner_kind, owner_id, reason="released by call override", source=source)
    result["released_delayed_replies"] = released
    cid = new_id("call")
    conn.execute(
        """INSERT INTO call_overrides(
             id, owner_kind, owner_id, user_id, session_id, turn_id, reason, target_kind, target_id,
             interrupted_sleep_session_id, interrupted_event_id, gate_decision_id, result_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, owner_kind, owner_id, user_id, session_id, turn_id, reason,
         "sleep_session" if active_sleep_session_id else "event" if active_event_id else "state",
         active_sleep_session_id or active_event_id, active_sleep_session_id, active_event_id, gate.get("id"), dumps(result)),
    )
    append_journal(conn, owner_kind, owner_id, "call_override", {"call_override_id": cid, "reason": reason, "result": result}, source)
    row = conn.execute("SELECT * FROM call_overrides WHERE id=?", (cid,)).fetchone()
    return {"ok": True, "call_override": _decode_call(row), "result": result, "realtime_state": get_realtime_state(conn, owner_kind, owner_id)}


def list_call_overrides(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM call_overrides WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return [_decode_call(r) for r in rows]


def reply_gate_status(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    return {
        "realtime_state": get_realtime_state(conn, owner_kind, owner_id),
        "pending_delayed_replies": list_delayed_replies(conn, owner_kind, owner_id, status="pending", limit=10),
        "recent_decisions": [_decode_decision(r) for r in conn.execute("SELECT * FROM reply_gate_decisions WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 10", (owner_kind, owner_id)).fetchall()],
        "recent_calls": list_call_overrides(conn, owner_kind, owner_id, limit=5),
    }


def reply_gate_doctor(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    stale = conn.execute(
        "SELECT * FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status='pending' AND queued_at < datetime('now','-12 hours')",
        (owner_kind, owner_id),
    ).fetchall()
    for r in stale:
        findings.append({"type": "stale_delayed_reply", "severity": "warning", "delayed_reply_id": r["id"], "message": "delayed reply pending for more than 12 hours"})
    expired = conn.execute(
        "SELECT * FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status='pending' AND expires_at_ts IS NOT NULL AND expires_at_ts < unixepoch('now')",
        (owner_kind, owner_id),
    ).fetchall()
    for r in expired:
        findings.append({"type": "expired_delayed_reply", "severity": "info", "delayed_reply_id": r["id"], "message": "delayed reply has expired"})
    state = get_realtime_state(conn, owner_kind, owner_id)
    if _state_expired(state):
        findings.append({"type": "reply_gate_lease_expired", "severity": "warning", "message": "realtime state lease is expired", "state": state})
    for f in findings:
        conn.execute("INSERT INTO reply_gate_recoveries(id, owner_kind, owner_id, recovery_type, severity, message, metadata_json) VALUES(?,?,?,?,?,?,?)",
                     (new_id("replyrec"), owner_kind, owner_id, f["type"], f.get("severity", "warning"), f.get("message"), dumps(f)))
    return {"status": "ok" if not findings else "warn", "findings": findings}
