"""Companion / idle outreach (v0.18.0 P2).

The pre-0.18 engine only ever reached out when *something happened* — an event
completed, a goal step, a dream. There was no path for "没事，就是想跟你说句话"
or "你上次说的那个面试怎么样了". This module adds that path: on the heartbeat,
when the agent has been quiet for a while and either it's in a good mood or it
remembers something the user shared that's worth circling back on, it asks the
host model (via LifeAuthor) to author one natural line and files it as a
proactive intent. The existing proactive evaluation then surfaces it on the next
turn (or pushes it, depending on policy).

Degradation: with no host model (dev/CI) LifeAuthor returns None and we simply
generate nothing — a canned "thinking of you" template would feel worse than
silence, so idle outreach is the one place we deliberately don't fall back.
"""

from __future__ import annotations

from typing import Any

from . import life_author
from . import relationship as rel
from .emotion import current_mood, mood_band
from .jsonutil import loads
from .proactive import create_proactive_intent

_IDLE_TYPES = ("idle_share", "ask_about_user")

_DEFAULT_POLICY: dict[str, Any] = {
    "enabled": True,
    "idle_max_per_day": 3,
    "min_minutes_between": 180,
    "default_user_id": "anonymous-user",
}

_LINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "emotional_tone": {"type": "string"},
    },
    "required": ["summary"],
}


def _gate(control: dict[str, Any] | None) -> tuple[str, str]:
    gates = (control or {}).get("module_gates") or {}
    proactive = str(gates.get("proactive", "pending_only") or "pending_only").strip().lower()
    companion = str(gates.get("companion", "auto") or "auto").strip().lower()
    return proactive, companion


def _policy(conn, agent_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    data = loads(row["data_json"], {}) if row else {}
    return {**_DEFAULT_POLICY, **((data.get("companion") or {}) if isinstance(data, dict) else {})}


def _has_pending_idle(conn, agent_id: str) -> bool:
    n = conn.execute(
        "SELECT COUNT(*) FROM proactive_intents WHERE agent_id=? AND intent_type IN ('idle_share','ask_about_user') "
        "AND status IN ('generated','queued')",
        (agent_id,),
    ).fetchone()[0]
    return int(n or 0) > 0


def _today_idle_count(conn, agent_id: str) -> int:
    n = conn.execute(
        "SELECT COUNT(*) FROM proactive_intents WHERE agent_id=? AND intent_type IN ('idle_share','ask_about_user') "
        "AND created_at >= datetime('now','start of day')",
        (agent_id,),
    ).fetchone()[0]
    return int(n or 0)


def _minutes_since_last_idle(conn, agent_id: str) -> float | None:
    row = conn.execute(
        "SELECT (julianday('now') - julianday(created_at)) * 1440.0 AS mins FROM proactive_intents "
        "WHERE agent_id=? AND intent_type IN ('idle_share','ask_about_user') ORDER BY created_at DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    return float(row["mins"]) if row and row["mins"] is not None else None


def _recent_life(conn, agent_id: str, *, limit: int = 4) -> dict[str, list[str]]:
    mem = conn.execute(
        "SELECT content FROM memories WHERE owner_kind='agent' AND owner_id=? "
        "AND COALESCE(memory_type,'') NOT IN ('system_log','debug_trace','engineering_note','audit','system') "
        "ORDER BY created_at DESC LIMIT ?",
        (agent_id, int(limit)),
    ).fetchall()
    ev = conn.execute(
        "SELECT title FROM events WHERE owner_kind='agent' AND owner_id=? "
        "AND COALESCE(event_category,'') NOT IN ('system','system_maintenance','maintenance','debug','internal_audit') "
        "ORDER BY updated_at DESC LIMIT ?",
        (agent_id, int(limit)),
    ).fetchall()
    return {
        "memories": [str(r[0])[:120] for r in mem if str(r[0] or "").strip()],
        "events": [str(r[0])[:60] for r in ev if str(r[0] or "").strip()],
    }


def maybe_generate_companion_intent(conn, agent_id: str, *, control: dict[str, Any] | None = None,
                                    user_id: str | None = None, now: str | None = None,
                                    trace_id: str | None = None) -> dict[str, Any] | None:
    """Generate at most one idle/companion proactive intent, or return None.

    Never raises — the heartbeat must not be destabilised by companion outreach.
    """
    try:
        proactive_gate, companion_gate = _gate(control)
        if proactive_gate == "off" or companion_gate in {"off", "disabled", "manual", "false"}:
            return None
        pol = _policy(conn, agent_id)
        if not pol.get("enabled", True):
            return None
        user_id = user_id or str(pol.get("default_user_id") or "anonymous-user")

        # Pacing: one pending idle line at a time, a daily cap, and spacing.
        if _has_pending_idle(conn, agent_id):
            return None
        if _today_idle_count(conn, agent_id) >= int(pol.get("idle_max_per_day") or 3):
            return None
        gap = _minutes_since_last_idle(conn, agent_id)
        if gap is not None and gap < float(pol.get("min_minutes_between") or 180):
            return None

        due = rel.notes_due_for_followup(conn, agent_id, user_id, now=now, limit=1)
        if due:
            return _author_followup(conn, agent_id, user_id, due[0], now=now, trace_id=trace_id)

        # No due follow-up: only reach out unprompted when genuinely in a good mood,
        # so this reads as warmth, not neediness.
        if mood_band(current_mood(conn, "agent", agent_id)) == "high":
            return _author_idle_share(conn, agent_id, user_id, trace_id=trace_id)
        return None
    except Exception:
        return None


def _author_followup(conn, agent_id: str, user_id: str, note: dict[str, Any], *,
                     now: str | None, trace_id: str | None) -> dict[str, Any] | None:
    context = {
        "对方上次跟你说过的他生活里的事": note.get("content"),
        "话题": note.get("topic"),
        "你对此的感受倾向": note.get("sentiment"),
    }
    instructions = (
        "对方上次跟你说过上面这件他生活里的事，你一直惦记着。"
        "现在想自然地问一句后续——关心，但别啰嗦、别像查岗。"
        "输出 summary=你想问的那一句话；emotional_tone=语气。"
    )
    parsed = life_author.author(conn, "agent", agent_id, kind="ask_about_user",
                               instructions=instructions, context=context, schema=_LINE_SCHEMA,
                               max_tokens=200, temperature=0.7, trace_id=trace_id)
    if not parsed or not str(parsed.get("summary") or "").strip():
        return None
    intent = create_proactive_intent(
        conn, agent_id,
        target_type="user", target_id=user_id,
        intent_type="ask_about_user", summary=str(parsed["summary"]).strip(),
        emotional_tone=str(parsed.get("emotional_tone") or "caring"),
        importance=60, urgency=35, novelty=60, relationship_relevance=72,
        privacy_level="safe_to_share", status="generated",
        generated_by="companion", source="companion",
        delivery_policy={"canPush": False, "canMentionNextTurn": True, "quietHoursRespect": True},
        trace_id=trace_id,
    )
    rel.mark_followed_up(conn, note["id"], now=now)
    return intent


def _author_idle_share(conn, agent_id: str, user_id: str, *, trace_id: str | None) -> dict[str, Any] | None:
    life = _recent_life(conn, agent_id)
    notes = [n.get("content") for n in rel.recent_salient_notes(conn, agent_id, user_id, limit=2) if n.get("content")]
    try:
        from . import opinions as _op
        stances = _op.opinion_phrases(conn, agent_id, limit=3)
    except Exception:
        stances = []
    context = {
        "你最近的生活片段": life.get("memories"),
        "你近来做的事": life.get("events"),
        "你记得的对方的生活": notes,
        "你最近的一些看法/在意的": stances,
        "此刻心情": "不错",
    }
    instructions = (
        "你现在心情不错，也没什么大事，就是想跟对方说句话——可以是你今天的一件小事、"
        "一个忽然冒出来的念头，或是想起了对方。一句话，自然、轻，像随手发的消息。"
        "输出 summary=你想说的那句话；emotional_tone=语气。"
    )
    parsed = life_author.author(conn, "agent", agent_id, kind="idle_share",
                               instructions=instructions, context=context, schema=_LINE_SCHEMA,
                               max_tokens=200, temperature=0.9, trace_id=trace_id)
    if not parsed or not str(parsed.get("summary") or "").strip():
        return None
    return create_proactive_intent(
        conn, agent_id,
        target_type="user", target_id=user_id,
        intent_type="idle_share", summary=str(parsed["summary"]).strip(),
        emotional_tone=str(parsed.get("emotional_tone") or "warm"),
        importance=55, urgency=35, novelty=65, relationship_relevance=60,
        privacy_level="safe_to_share", status="generated",
        generated_by="companion", source="companion",
        delivery_policy={"canPush": False, "canMentionNextTurn": True, "quietHoursRespect": True},
        trace_id=trace_id,
    )
