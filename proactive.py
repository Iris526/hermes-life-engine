"""Proactive intent, outbox, and delivery policy layer for LifeEngine v0.8.

This module deliberately separates three things:

1. ProactiveIntent: the Agent has something it wants to say.
2. ProactiveOutbox: a concrete message draft prepared for a target user.
3. ProactiveState: relationship-level cooldown, daily budget, and pending topics.

No external push delivery is performed here. Hermes adapters may later consume
queued outbox rows and mark them sent. This keeps the core framework-neutral and
traceable.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from .jsonutil import dumps, loads
from .trace import append_audit, append_journal, new_id
from .time_utils import parse_datetime, to_epoch

ACTIVE_INTENT_STATUSES = {"generated", "queued"}
TERMINAL_INTENT_STATUSES = {"sent", "suppressed", "expired", "cancelled", "merged"}
PROACTIVE_MODES = {"off", "pending_only", "manual_send", "auto_send"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _date_key() -> str:
    return _now().date().isoformat()


def _as_dict(row) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    if "delivery_policy_json" in d:
        d["delivery_policy"] = loads(d.pop("delivery_policy_json"), {})
    if "score_json" in d:
        d["score"] = loads(d.pop("score_json"), {})
    if "decision_json" in d:
        d["decision"] = loads(d.pop("decision_json"), {})
    if "payload_json" in d:
        d["payload"] = loads(d.pop("payload_json"), {})
    if "result_json" in d:
        d["result"] = loads(d.pop("result_json"), {})
    if "policy_json" in d:
        d["policy"] = loads(d.pop("policy_json"), {})
    if "pending_intent_ids_json" in d:
        d["pending_intent_ids"] = loads(d.pop("pending_intent_ids_json"), [])
    return d


def _get_canon_policy(conn, agent_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    if not row:
        return {}
    data = loads(row["data_json"], {}) or {}
    return data.get("proactive") or {}


def _gate_policy(control: dict[str, Any] | None, canon_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    canon_policy = canon_policy or {}
    gates = (control or {}).get("module_gates") or {}
    mode = str(gates.get("proactive") or canon_policy.get("mode") or "pending_only")
    if mode not in PROACTIVE_MODES:
        mode = "pending_only"
    return {
        "mode": mode,
        "max_per_day": int(canon_policy.get("max_per_day", canon_policy.get("daily_limit", 1))),
        "min_score_to_queue": int(canon_policy.get("min_score_to_queue", 50)),
        "min_score_to_auto_send": int(canon_policy.get("min_score_to_auto_send", 75)),
        "cooldown_minutes": int(canon_policy.get("cooldown_minutes", 180)),
        "quiet_hours": canon_policy.get("quiet_hours") or {},
        "default_target_user_id": canon_policy.get("default_target_user_id") or "anonymous-user",
    }


def ensure_proactive_state(conn, agent_id: str, user_id: str | None = None) -> dict[str, Any]:
    user_id = user_id or "anonymous-user"
    row = conn.execute(
        "SELECT * FROM agent_user_proactive_state WHERE agent_id=? AND user_id=?",
        (agent_id, user_id),
    ).fetchone()
    today = _date_key()
    if row:
        d = _as_dict(row) or {}
        if d.get("last_daily_reset_date") != today:
            conn.execute(
                "UPDATE agent_user_proactive_state SET daily_sent_count=0, last_daily_reset_date=?, updated_at=datetime('now') WHERE agent_id=? AND user_id=?",
                (today, agent_id, user_id),
            )
            row = conn.execute("SELECT * FROM agent_user_proactive_state WHERE agent_id=? AND user_id=?", (agent_id, user_id)).fetchone()
            d = _as_dict(row) or {}
        return d
    state_id = new_id("prostate")
    conn.execute(
        """INSERT INTO agent_user_proactive_state(id, agent_id, user_id, state, pending_intent_ids_json,
              user_responsiveness_score, interruption_sensitivity, daily_sent_count, last_daily_reset_date)
              VALUES(?,?,?,?,?,?,?,?,?)""",
        (state_id, agent_id, user_id, "silent", dumps([]), 50, 50, 0, today),
    )
    return _as_dict(conn.execute("SELECT * FROM agent_user_proactive_state WHERE id=?", (state_id,)).fetchone()) or {}


def list_proactive_states(conn, agent_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agent_user_proactive_state WHERE agent_id=? ORDER BY updated_at DESC LIMIT ?",
        (agent_id, int(limit)),
    ).fetchall()
    return [_as_dict(r) or {} for r in rows]


def _target_user(intent: dict[str, Any], policy: dict[str, Any] | None = None) -> str:
    if intent.get("target_type") == "user" and intent.get("target_id"):
        return str(intent["target_id"])
    if intent.get("target_user_id"):
        return str(intent["target_user_id"])
    return str((policy or {}).get("default_target_user_id") or "anonymous-user")


def _update_state_pending(conn, agent_id: str, user_id: str, intent_id: str, state: str) -> dict[str, Any]:
    current = ensure_proactive_state(conn, agent_id, user_id)
    pending = list(current.get("pending_intent_ids") or [])
    if intent_id not in pending and state in {"has_something_to_share", "wants_help", "waiting_for_user_reply"}:
        pending.append(intent_id)
    if state == "silent":
        pending = [pid for pid in pending if pid != intent_id]
    conn.execute(
        """UPDATE agent_user_proactive_state SET state=?, pending_intent_ids_json=?, updated_at=datetime('now')
              WHERE agent_id=? AND user_id=?""",
        (state, dumps(pending), agent_id, user_id),
    )
    return ensure_proactive_state(conn, agent_id, user_id)


def get_proactive_intent(conn, intent_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM proactive_intents WHERE id=?", (intent_id,)).fetchone()
    if not row:
        raise ValueError(f"proactive intent not found: {intent_id}")
    return _as_dict(row) or {}


def list_proactive_intents(conn, agent_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM proactive_intents WHERE agent_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
            (agent_id, status, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM proactive_intents WHERE agent_id=? ORDER BY created_at DESC LIMIT ?",
            (agent_id, int(limit)),
        ).fetchall()
    return [_as_dict(r) or {} for r in rows]


def create_proactive_intent(conn, agent_id: str, **payload: Any) -> dict[str, Any]:
    intent_id = new_id("proactive")
    expires_at = payload.get("expires_at")
    expires_at_ts = None
    if expires_at:
        expires_at_ts = to_epoch(expires_at)
    conn.execute(
        """INSERT INTO proactive_intents(id, agent_id, target_type, target_id, trigger_event_id,
               trigger_result_id, intent_type, summary, emotional_tone, importance, urgency, novelty,
               relationship_relevance, privacy_level, status, delivery_policy_json, expires_at,
               expires_at_ts, generated_by, trace_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            intent_id,
            agent_id,
            payload.get("target_type", "self_journal"),
            payload.get("target_id"),
            payload.get("trigger_event_id"),
            payload.get("trigger_result_id"),
            payload.get("intent_type", "share_interesting"),
            payload.get("summary", ""),
            payload.get("emotional_tone"),
            int(payload.get("importance", 50)),
            int(payload.get("urgency", 50)),
            int(payload.get("novelty", 50)),
            int(payload.get("relationship_relevance", 50)),
            payload.get("privacy_level", "safe_to_share"),
            payload.get("status", "generated"),
            dumps(payload.get("delivery_policy", {})),
            expires_at,
            expires_at_ts,
            payload.get("generated_by", payload.get("source", "life_commit")),
            payload.get("trace_id"),
        ),
    )
    append_journal(conn, "agent", agent_id, "proactive_intent_created", {"intent_id": intent_id, "summary": payload.get("summary", "")}, payload.get("source") or "proactive")
    return get_proactive_intent(conn, intent_id)


def _score_intent(intent: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    importance = int(intent.get("importance") or 50)
    urgency = int(intent.get("urgency") or 50)
    novelty = int(intent.get("novelty") or 50)
    relevance = int(intent.get("relationship_relevance") or 50)
    interruption = int((state or {}).get("interruption_sensitivity") or 50)
    daily_count = int((state or {}).get("daily_sent_count") or 0)
    raw = importance * 0.35 + urgency * 0.25 + novelty * 0.15 + relevance * 0.25
    penalty = max(0, interruption - 50) * 0.25 + daily_count * 12
    score = max(0, min(100, round(raw - penalty, 2)))
    return {"score": score, "importance": importance, "urgency": urgency, "novelty": novelty, "relationship_relevance": relevance, "interruption_penalty": penalty}


def _is_expired(intent: dict[str, Any]) -> bool:
    ts = intent.get("expires_at_ts")
    if ts is None and intent.get("expires_at"):
        try:
            ts = to_epoch(intent.get("expires_at"))
        except Exception:
            ts = None
    return ts is not None and int(ts) <= int(_now().timestamp())


def _quiet_hours_active(policy: dict[str, Any]) -> bool:
    qh = policy.get("quiet_hours") or {}
    if not isinstance(qh, dict) or not qh.get("start") or not qh.get("end"):
        return False
    # Conservative local-clock check. Full timezone support belongs in Canon's
    # truth-source/time policy; this keeps v0.8 deterministic and safe.
    now_hm = _now().strftime("%H:%M")
    start = str(qh.get("start"))
    end = str(qh.get("end"))
    if start <= end:
        return start <= now_hm < end
    return now_hm >= start or now_hm < end


def list_outbox(conn, agent_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM proactive_outbox WHERE agent_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
            (agent_id, status, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM proactive_outbox WHERE agent_id=? ORDER BY created_at DESC LIMIT ?",
            (agent_id, int(limit)),
        ).fetchall()
    return [_as_dict(r) or {} for r in rows]


def get_outbox_message(conn, outbox_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM proactive_outbox WHERE id=?", (outbox_id,)).fetchone()
    if not row:
        raise ValueError(f"proactive outbox not found: {outbox_id}")
    return _as_dict(row) or {}


def _create_outbox(conn, agent_id: str, user_id: str, intent: dict[str, Any], draft_text: str, status: str = "queued", delivery_channel: str | None = None) -> dict[str, Any]:
    existing = conn.execute(
        "SELECT * FROM proactive_outbox WHERE agent_id=? AND intent_id=? AND status IN ('drafted','queued') ORDER BY created_at DESC LIMIT 1",
        (agent_id, intent["id"]),
    ).fetchone()
    if existing:
        return _as_dict(existing) or {}
    outbox_id = new_id("outbox")
    conn.execute(
        """INSERT INTO proactive_outbox(id, agent_id, target_user_id, intent_id, draft_text, status, delivery_channel)
              VALUES(?,?,?,?,?,?,?)""",
        (outbox_id, agent_id, user_id, intent["id"], draft_text, status, delivery_channel),
    )
    return get_outbox_message(conn, outbox_id)


def evaluate_proactive_intent(
    conn,
    agent_id: str,
    intent_id: str | None = None,
    *,
    control: dict[str, Any] | None = None,
    target_user_id: str | None = None,
    manual: bool = False,
    trace_id: str | None = None,
    draft_text: str | None = None,
) -> dict[str, Any]:
    canon_policy = _get_canon_policy(conn, agent_id)
    policy = _gate_policy(control, canon_policy)
    mode = policy["mode"]
    if intent_id:
        intents = [get_proactive_intent(conn, intent_id)]
    else:
        intents = list_proactive_intents(conn, agent_id, status="generated", limit=10)
    evaluated: list[dict[str, Any]] = []
    for intent in intents:
        user_id = target_user_id or _target_user(intent, policy)
        state = ensure_proactive_state(conn, agent_id, user_id)
        score = _score_intent(intent, state)
        decision = "none"
        reason = ""
        outbox = None
        if intent.get("status") in TERMINAL_INTENT_STATUSES:
            decision, reason = "skip", f"terminal status {intent.get('status')}"
        elif _is_expired(intent):
            conn.execute("UPDATE proactive_intents SET status='expired', expired_at=datetime('now'), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "expire", "reason": "expires_at passed"}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "silent")
            decision, reason = "expire", "expires_at passed"
        elif mode == "off":
            conn.execute("UPDATE proactive_intents SET status='suppressed', suppressed_at=datetime('now'), suppression_reason=?, score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", ("proactive module off", dumps(score), dumps({"decision": "suppress", "reason": "proactive module off"}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "suppressed_by_policy")
            decision, reason = "suppress", "proactive module off"
        elif intent.get("privacy_level") == "agent_private" and intent.get("target_type") == "user":
            conn.execute("UPDATE proactive_intents SET status='suppressed', suppressed_at=datetime('now'), suppression_reason=?, score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", ("agent_private cannot target user", dumps(score), dumps({"decision": "suppress", "reason": "agent_private cannot target user"}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "suppressed_by_policy")
            decision, reason = "suppress", "agent_private cannot target user"
        elif score["score"] < policy["min_score_to_queue"] and not manual:
            conn.execute("UPDATE proactive_intents SET status='suppressed', suppressed_at=datetime('now'), suppression_reason=?, score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", ("score below queue threshold", dumps(score), dumps({"decision": "suppress", "reason": "score below queue threshold", "policy": policy}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "suppressed_by_policy")
            decision, reason = "suppress", "score below queue threshold"
        elif mode == "pending_only":
            conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "pending_only", "reason": "keep pending for next turn", "policy": policy}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "has_something_to_share")
            decision, reason = "queue_pending", "pending_only keeps it for next user turn"
        elif mode == "manual_send" and not manual:
            conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "manual_send_pending", "reason": "manual approval required", "policy": policy}), intent["id"]))
            _update_state_pending(conn, agent_id, user_id, intent["id"], "has_something_to_share")
            decision, reason = "queue_pending", "manual_send requires explicit send"
        else:
            if _quiet_hours_active(policy) and not manual:
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "quiet_hours", "reason": "quiet hours active", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "has_something_to_share")
                decision, reason = "queue_pending", "quiet hours active"
            elif int(state.get("daily_sent_count") or 0) >= int(policy["max_per_day"]) and not manual:
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "daily_limit", "reason": "daily proactive budget exhausted", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "cooldown")
                decision, reason = "queue_pending", "daily proactive budget exhausted"
            elif mode == "auto_send" and score["score"] < policy["min_score_to_auto_send"] and not manual:
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (dumps(score), dumps({"decision": "score_below_auto_send", "reason": "queued but not pushed", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "has_something_to_share")
                decision, reason = "queue_pending", "score below auto-send threshold"
            else:
                msg = draft_text or f"我有件事想跟你说：{intent.get('summary','')}"
                outbox = _create_outbox(conn, agent_id, user_id, intent, msg, status="queued", delivery_channel="hermes")
                conn.execute("UPDATE proactive_intents SET status='queued', queued_at=COALESCE(queued_at, datetime('now')), result_outbox_id=?, score_json=?, decision_json=?, updated_at=datetime('now') WHERE id=?", (outbox.get("id"), dumps(score), dumps({"decision": "outbox_queued", "reason": "delivery allowed", "policy": policy}), intent["id"]))
                _update_state_pending(conn, agent_id, user_id, intent["id"], "waiting_for_user_reply")
                decision, reason = "outbox_queued", "delivery allowed"
        eval_id = new_id("proeval")
        conn.execute(
            """INSERT INTO proactive_evaluations(id, agent_id, target_user_id, intent_id, mode, score,
                  decision, reason, policy_json, trace_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (eval_id, agent_id, user_id, intent["id"], mode, float(score["score"]), decision, reason, dumps(policy), trace_id),
        )
        append_journal(conn, "agent", agent_id, "proactive_intent_evaluated", {"intent_id": intent["id"], "decision": decision, "reason": reason, "score": score, "outbox_id": outbox.get("id") if outbox else None}, "proactive")
        evaluated.append({"evaluation_id": eval_id, "intent_id": intent["id"], "decision": decision, "reason": reason, "score": score, "outbox": outbox, "state": ensure_proactive_state(conn, agent_id, user_id)})
    return {"evaluated": evaluated, "policy": policy}


def mark_outbox_sent(conn, agent_id: str, outbox_id: str, *, result: dict[str, Any] | None = None, manual: bool = True) -> dict[str, Any]:
    msg = get_outbox_message(conn, outbox_id)
    if msg.get("agent_id") != agent_id:
        raise ValueError("outbox owner mismatch")
    user_id = msg.get("target_user_id") or "anonymous-user"
    intent_id = msg.get("intent_id")
    conn.execute(
        "UPDATE proactive_outbox SET status='sent', sent_at=datetime('now'), delivery_result_json=? WHERE id=?",
        (dumps(result or {"manual": manual}), outbox_id),
    )
    if intent_id:
        conn.execute("UPDATE proactive_intents SET status='sent', sent_at=datetime('now'), updated_at=datetime('now') WHERE id=?", (intent_id,))
    state = ensure_proactive_state(conn, agent_id, user_id)
    pending = [pid for pid in (state.get("pending_intent_ids") or []) if pid != intent_id]
    cooldown = (_now() + timedelta(minutes=180)).isoformat()
    conn.execute(
        """UPDATE agent_user_proactive_state SET state='cooldown', pending_intent_ids_json=?, last_proactive_sent_at=datetime('now'),
              next_allowed_proactive_at=?, daily_sent_count=daily_sent_count+1, updated_at=datetime('now')
              WHERE agent_id=? AND user_id=?""",
        (dumps(pending), cooldown, agent_id, user_id),
    )
    append_journal(conn, "agent", agent_id, "proactive_outbox_sent", {"outbox_id": outbox_id, "intent_id": intent_id}, "proactive")
    return {"outbox": get_outbox_message(conn, outbox_id), "state": ensure_proactive_state(conn, agent_id, user_id)}


def suppress_intent(conn, agent_id: str, intent_id: str, reason: str = "manual suppress") -> dict[str, Any]:
    intent = get_proactive_intent(conn, intent_id)
    user_id = _target_user(intent, {"default_target_user_id": "anonymous-user"})
    conn.execute(
        "UPDATE proactive_intents SET status='suppressed', suppressed_at=datetime('now'), suppression_reason=?, updated_at=datetime('now') WHERE id=?",
        (reason, intent_id),
    )
    _update_state_pending(conn, agent_id, user_id, intent_id, "suppressed_by_policy")
    append_journal(conn, "agent", agent_id, "proactive_intent_suppressed", {"intent_id": intent_id, "reason": reason}, "proactive")
    return get_proactive_intent(conn, intent_id)


def expire_intents(conn, agent_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id FROM proactive_intents WHERE agent_id=? AND status IN ('generated','queued') AND expires_at_ts IS NOT NULL AND expires_at_ts <= ?",
        (agent_id, int(_now().timestamp())),
    ).fetchall()
    expired = []
    for r in rows:
        conn.execute("UPDATE proactive_intents SET status='expired', expired_at=datetime('now'), updated_at=datetime('now') WHERE id=?", (r["id"],))
        expired.append(r["id"])
    if expired:
        append_journal(conn, "agent", agent_id, "proactive_intents_expired", {"intent_ids": expired}, "proactive")
    return {"expired": expired, "count": len(expired)}
