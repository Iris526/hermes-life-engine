"""Relationship notes (v0.18.0 P2) — what the USER told the agent about the
user's OWN life.

The companionship setting is mutual: the agent has its life, the user has
theirs, and they tell each other. Before this, the engine modelled only the
agent's side — it could never ask "你上次说的那个面试怎么样了". A relationship
note is one durable thing the user shared about their life; the companion/idle
outreach loop and dreams draw on these to follow up and to ground what the
agent says in the user's world.

Recording is character-agnostic: notes are data the host/agent writes during
chat (via the ``life_relationship`` tool), nothing hardcoded.
"""

from __future__ import annotations

from typing import Any

from .time_utils import now_iso, to_epoch
from .trace import append_journal, new_id

DEFAULT_USER_ID = "anonymous-user"


def resolve_primary_user(conn, agent_id: str, explicit: str | None = None) -> str:
    """The single canonical user_id for the relationship / companion / dream loop.

    The producer (life_relationship) and every consumer — companion idle outreach,
    dreams, reflection/opinions, and the tick follow-up scan — must agree on WHICH
    user the notes belong to, or "你讲生活→她入梦/回访" silently reads empty (a note
    recorded under one id, looked up under another). This was the audit's critical
    user_id split: the producer defaulted to ``anonymous-user`` while companion
    resolved to canon ``proactive.default_target_user_id`` — so once a real target
    is configured in production the loop breaks. All four sides now resolve here.

    The canonical id is canon ``proactive.default_target_user_id`` (the configured
    recipient); an explicit id (a real multi-user sender) always wins; otherwise
    ``DEFAULT_USER_ID``.
    """
    if explicit:
        return str(explicit)
    try:
        from .canon import get_active_canon
        canon = get_active_canon(conn, "agent", agent_id) or {}
        target = (canon.get("proactive") or {}).get("default_target_user_id")
        if target:
            return str(target)
    except Exception:
        pass
    return DEFAULT_USER_ID


def _now_epoch(now: str | None = None) -> int:
    return int(to_epoch(now or now_iso()))


def record_relationship_note(conn, agent_id: str, user_id: str | None = None, *,
                             content: str, topic: str | None = None, salience: int = 50,
                             sentiment: str | None = None, follow_up_after_hours: float | None = None,
                             source: str = "life_relationship", now: str | None = None) -> dict[str, Any]:
    """Remember something the user shared about their life."""
    if not content or not str(content).strip():
        raise ValueError("relationship note content is required")
    user_id = user_id or DEFAULT_USER_ID
    note_id = new_id("relnote")
    follow_up_due_ts = None
    if follow_up_after_hours is not None:
        try:
            follow_up_due_ts = _now_epoch(now) + int(float(follow_up_after_hours) * 3600)
        except (TypeError, ValueError):
            follow_up_due_ts = None
    conn.execute(
        """INSERT INTO relationship_notes(
             id, agent_id, user_id, topic, content, salience, sentiment,
             follow_up_due_ts, source
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (note_id, agent_id, user_id, topic, str(content).strip(),
         max(0, min(100, int(salience))), sentiment, follow_up_due_ts, source),
    )
    append_journal(conn, "agent", agent_id, "relationship_note_recorded",
                   {"note_id": note_id, "topic": topic, "user_id": user_id}, source)
    return get_relationship_note(conn, note_id)


def get_relationship_note(conn, note_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM relationship_notes WHERE id=?", (note_id,)).fetchone()
    return dict(row) if row else {}


def list_relationship_notes(conn, agent_id: str, user_id: str | None = None, *,
                            limit: int = 20, status: str = "active") -> list[dict[str, Any]]:
    user_id = user_id or DEFAULT_USER_ID
    rows = conn.execute(
        "SELECT * FROM relationship_notes WHERE agent_id=? AND user_id=? AND status=? "
        "ORDER BY created_at DESC LIMIT ?",
        (agent_id, user_id, status, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_salient_notes(conn, agent_id: str, user_id: str | None = None, *, limit: int = 3) -> list[dict[str, Any]]:
    """A few of the user's life notes to ground dreams / idle shares."""
    user_id = user_id or DEFAULT_USER_ID
    rows = conn.execute(
        "SELECT * FROM relationship_notes WHERE agent_id=? AND user_id=? AND status='active' "
        "ORDER BY salience DESC, created_at DESC LIMIT ?",
        (agent_id, user_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def notes_due_for_followup(conn, agent_id: str, user_id: str | None = None, *,
                           now: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Notes whose follow-up time has arrived and that haven't been circled back on."""
    user_id = user_id or DEFAULT_USER_ID
    cutoff = _now_epoch(now)
    rows = conn.execute(
        "SELECT * FROM relationship_notes WHERE agent_id=? AND user_id=? AND status='active' "
        "AND followed_up_at IS NULL AND follow_up_due_ts IS NOT NULL AND follow_up_due_ts <= ? "
        "ORDER BY salience DESC, follow_up_due_ts ASC LIMIT ?",
        (agent_id, user_id, cutoff, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_followed_up(conn, note_id: str, *, now: str | None = None) -> None:
    conn.execute(
        "UPDATE relationship_notes SET followed_up_at=?, last_referenced_at=?, updated_at=datetime('now') WHERE id=?",
        (now or now_iso(), now or now_iso(), note_id),
    )


def mark_referenced(conn, note_id: str, *, now: str | None = None) -> None:
    conn.execute(
        "UPDATE relationship_notes SET last_referenced_at=?, updated_at=datetime('now') WHERE id=?",
        (now or now_iso(), note_id),
    )
