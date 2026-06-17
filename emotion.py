"""Agent-triggerable emotional reactions on the mood resource.

Mood is a vital resource in ``[-100, 100]``. Before this module the mood slot
was written by a handful of system sources (meals, serendipity) and read by
almost nothing — effectively decorative. ``record_mood_reaction`` gives the
agent its *own* lever: anything it experiences (a warm message from Ringo, a
委托 gone wrong, a small win) can move how it feels.

Design notes:
- **Bounded per reaction** (``MAX_REACTION``) so one event can't swing the
  whole mood; sustained feeling needs sustained reactions.
- **Auditable**: every reaction goes through ``apply_delta`` (so the doctor
  invariant ``account == SUM(ledger)`` still holds) and appends a
  ``mood_reaction`` journal entry with the agent's stated reason.
- **Fed back into behavior**: mood is surfaced in the agent's context capsule
  and biases autonomy/proactive tone (see ``mood_bias``), and its trend feeds
  the ``optimism`` persona trait. It is no longer a dead gauge.
"""

from __future__ import annotations

from typing import Any

from .resources import apply_delta
from .trace import append_journal

# A single reaction can move mood at most this much in either direction. Mood
# is a slow-moving mstate, not a knob — keeping each reaction small means the
# gauge reflects an accumulation of lived moments rather than one big swing.
MAX_REACTION = 20.0

# Soft bands used by ``mood_bias`` and the context capsule. Mood is [-100, 100].
LOW_MOOD = -25.0
HIGH_MOOD = 30.0


def record_mood_reaction(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    delta: float,
    reason: str = "",
    trigger: str | None = None,
    source: str = "life_mood",
    canon_version: int | None = None,
    transaction_id: str | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Apply a bounded mood change the agent attributes to something it felt."""
    try:
        d = float(delta)
    except (TypeError, ValueError) as exc:
        raise ValueError("mood reaction delta must be numeric") from exc
    if d == 0:
        raise ValueError("mood reaction delta must be non-zero")
    d = max(-MAX_REACTION, min(MAX_REACTION, d))
    operation = "produce" if d > 0 else "consume"
    account = apply_delta(
        conn, owner_kind, owner_id, "mood", d, operation,
        reason or ("心情变好" if d > 0 else "心情变差"), source,
    )
    append_journal(
        conn, owner_kind, owner_id, "mood_reaction",
        {"delta": account["delta"], "reason": reason, "trigger": trigger, "value_after": account["new_value"]},
        source, transaction_id=transaction_id, canon_version=canon_version,
    )
    return {
        "resource": "mood",
        "delta": account["delta"],
        "value_after": account["new_value"],
        "reason": reason,
        "trigger": trigger,
    }


def current_mood(conn, owner_kind: str, owner_id: str) -> float | None:
    row = conn.execute(
        "SELECT current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key='mood'",
        (owner_kind, owner_id),
    ).fetchone()
    return float(row["current_value"]) if row and row["current_value"] is not None else None


def mood_band(mood: float | None) -> str:
    """Coarse band for behavior biasing and display: low / neutral / high."""
    if mood is None:
        return "unknown"
    if mood <= LOW_MOOD:
        return "low"
    if mood >= HIGH_MOOD:
        return "high"
    return "neutral"


def mood_bias(mood: float | None) -> dict[str, Any]:
    """Soft behavioral hints derived from current mood.

    Returned hints are intentionally gentle — they nudge tone and activity
    preference, they do not hard-gate. Consumers (autonomy, proactive) read
    these instead of re-deriving the bands.
    """
    band = mood_band(mood)
    if band == "low":
        return {
            "band": "low",
            "seek": ["comfort", "social", "rest", "creative"],
            "tone": "subdued",
            "proactive_urge": -0.2,
            "note": "心情偏低，倾向找点安慰/陪伴或做轻松的事来回暖。",
        }
    if band == "high":
        return {
            "band": "high",
            "seek": ["ambitious", "creative", "social", "share"],
            "tone": "buoyant",
            "proactive_urge": 0.2,
            "note": "心情很好，更有干劲、也更想主动分享。",
        }
    return {"band": band, "seek": [], "tone": "even", "proactive_urge": 0.0, "note": ""}


def recent_mood_reactions(conn, owner_kind: str, owner_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Most recent agent-attributed mood reactions, newest first (for review/UI)."""
    rows = conn.execute(
        """SELECT created_at, delta, reason, operation FROM resource_ledger
              WHERE owner_kind=? AND owner_id=? AND resource_key='mood'
                AND (source LIKE 'life_mood%' OR source IN ('autonomy','proactive'))
              ORDER BY created_at DESC, rowid DESC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return [{"at": r["created_at"], "delta": r["delta"], "reason": r["reason"]} for r in rows]
