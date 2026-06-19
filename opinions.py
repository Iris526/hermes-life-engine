"""Agent opinions + the reflection loop (v0.18.0 P4).

The audit's last finding: experience accumulated but never changed the agent.
Persona drift was only a tone hint; memories were write-only; reflections'
proposed ops were never applied; there were no persistent opinions. So she could
never surprise you with "我最近想明白一件事…".

This module closes that loop. An **opinion** is a durable stance the agent forms
about something in its world (a place, a person, an activity, an idea):
like / dislike / concern / value / discovery, with a signed strength and a
confidence that grows as lived experience reinforces it. The periodic
**reflection** pass asks the host model (LifeAuthor) to look back over recent
experience and (a) form/reinforce a few opinions and (b) write one line of
self-narrative ("这季度我好像变得更爱往外跑了"). autonomy / companion / dreams
then draw on these, so experience visibly changes what she does and says.

Degrades like the rest of v0.18: no host model → reflection is a no-op (opinions
simply don't update), never an error.
"""

from __future__ import annotations

from typing import Any

from . import emotion
from . import life_author
from . import relationship as rel
from .jsonutil import loads
from .memory import create_memory
from .trace import append_journal, new_id

REFLECTION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "opinions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "target": {"type": "string"},
                    "opinion_type": {"type": "string", "enum": ["like", "dislike", "concern", "value", "discovery"]},
                    "strength": {"type": "number"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["target", "opinion_type", "strength"],
            },
        },
        "self_narrative": {"type": "string"},
    },
    "required": ["self_narrative"],
}


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo


def get_opinion(conn, opinion_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_opinions WHERE id=?", (opinion_id,)).fetchone()
    return dict(row) if row else {}


def form_or_reinforce_opinion(conn, agent_id: str, *, target: str, opinion_type: str, strength: float,
                              confidence: float = 0.5, reason: str | None = None,
                              formed_from_event_id: str | None = None, reflection_memory_id: str | None = None,
                              source: str = "reflection") -> dict[str, Any]:
    """Form a new opinion, or strengthen an existing one toward fresh evidence."""
    target = str(target or "").strip()
    if not target:
        raise ValueError("opinion target is required")
    opinion_type = str(opinion_type or "like").strip().lower()
    strength = _clamp(strength, -1.0, 1.0)
    confidence = _clamp(confidence, 0.0, 1.0)
    existing = conn.execute(
        "SELECT * FROM agent_opinions WHERE agent_id=? AND target=? AND opinion_type=?",
        (agent_id, target, opinion_type),
    ).fetchone()
    if existing:
        old_s, old_c = float(existing["strength"]), float(existing["confidence"])
        new_s = _clamp(old_s + 0.4 * (strength - old_s), -1.0, 1.0)   # move toward the new evidence
        new_c = _clamp(old_c + 0.1 * (1.0 - old_c) + 0.05, 0.0, 1.0)  # confidence grows with reinforcement
        conn.execute(
            "UPDATE agent_opinions SET strength=?, confidence=?, reason=COALESCE(?, reason), "
            "evidence_count=evidence_count+1, last_reinforced_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (new_s, new_c, reason, existing["id"]),
        )
        oid = existing["id"]
    else:
        oid = new_id("opinion")
        conn.execute(
            """INSERT INTO agent_opinions(
                 id, agent_id, target, opinion_type, strength, confidence, reason,
                 formed_from_event_id, reflection_memory_id, last_reinforced_at
               ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (oid, agent_id, target, opinion_type, strength, confidence, reason,
             formed_from_event_id, reflection_memory_id),
        )
    append_journal(conn, "agent", agent_id, "opinion_formed",
                   {"opinion_id": oid, "target": target, "type": opinion_type, "strength": strength}, source)
    return get_opinion(conn, oid)


def list_opinions(conn, agent_id: str, *, limit: int = 50, status: str = "active") -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agent_opinions WHERE agent_id=? AND status=? ORDER BY updated_at DESC LIMIT ?",
        (agent_id, status, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def salient_opinions(conn, agent_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """The strongest, most-held opinions — for grounding chat / planning / dreams."""
    rows = conn.execute(
        "SELECT * FROM agent_opinions WHERE agent_id=? AND status='active' "
        "ORDER BY confidence * ABS(strength) DESC, updated_at DESC LIMIT ?",
        (agent_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def opinion_phrases(conn, agent_id: str, *, limit: int = 5) -> list[str]:
    """Human-readable one-liners of the agent's current stances, for context."""
    out = []
    for o in salient_opinions(conn, agent_id, limit=limit):
        out.append(f"对「{o['target']}」：{o['opinion_type']}（强度{round(float(o['strength']), 2)}）" + (f" — {o['reason']}" if o.get("reason") else ""))
    return out


def latest_self_narrative(conn, agent_id: str) -> str | None:
    row = conn.execute(
        "SELECT content FROM memories WHERE owner_kind='agent' AND owner_id=? AND memory_type='self_narrative' "
        "ORDER BY created_at DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _reflected_today(conn, agent_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM life_journal WHERE owner_kind='agent' AND owner_id=? AND entry_type='reflection_done' "
        "AND created_at >= datetime('now','start of day') LIMIT 1",
        (agent_id,),
    ).fetchone()
    return row is not None


def _reflection_context(conn, agent_id: str) -> dict[str, Any]:
    events = [r[0] for r in conn.execute(
        "SELECT title FROM events WHERE owner_kind='agent' AND owner_id=? AND status IN ('completed','done') "
        "ORDER BY updated_at DESC LIMIT 8", (agent_id,)).fetchall() if r[0]]
    mems = [str(r[0])[:120] for r in conn.execute(
        "SELECT content FROM memories WHERE owner_kind='agent' AND owner_id=? "
        "AND COALESCE(memory_type,'') NOT IN ('system_log','debug_trace','audit','system','self_narrative') "
        "ORDER BY created_at DESC LIMIT 8", (agent_id,)).fetchall() if r[0]]
    notes = [n.get("content") for n in rel.recent_salient_notes(conn, agent_id, limit=3) if n.get("content")]
    band = emotion.mood_band(emotion.current_mood(conn, "agent", agent_id))
    return {
        "你最近做过/经历的事": events,
        "你最近的生活片段与梦": mems,
        "对方讲过的他的生活": notes,
        "你近来的心情": band,
        "你已有的一些看法": opinion_phrases(conn, agent_id, limit=6),
    }


def run_reflection(conn, agent_id: str, *, owner_kind: str = "agent", now: str | None = None,
                   trace_id: str | None = None, force: bool = False) -> dict[str, Any]:
    """One reflection pass: look back, form/reinforce opinions, write a self-narrative.

    Paced to once per day unless ``force``. Degrades to a no-op without a host
    model (opinions simply don't change). Never raises.
    """
    try:
        if not force and _reflected_today(conn, agent_id):
            return {"ok": True, "skipped": "already reflected today"}
        ctx = _reflection_context(conn, agent_id)
        instructions = (
            "回看上面这些你最近的经历、心情、以及对方讲过的生活，做一次安静的反思。"
            "(1) 形成或加深几条你*对具体事物的看法*——opinions：每条给 target(对什么)、"
            "opinion_type(like/dislike/concern/value/discovery)、strength(-1..1)、confidence(0..1)、reason。"
            "看法要具体（如『夜市』『记账这件事』『最近的钱』），别空泛。"
            "(2) 写一句 self_narrative：用第一人称说说这阵子你自己有什么变化或想明白的事"
            "（如『这阵子我好像越来越爱往外跑了』）。只谈*生活与自己*，不提任何系统/工程。"
        )
        parsed = life_author.author(
            conn, owner_kind, agent_id, kind="reflection",
            instructions=instructions, context=ctx, schema=REFLECTION_SCHEMA,
            max_tokens=700, temperature=0.7, trace_id=trace_id,
        )
        if not parsed:
            # No host model → don't mark done, so it reflects once the host is back.
            return {"ok": True, "degraded": True}
        narrative = str(parsed.get("self_narrative") or "").strip()
        mem = None
        if narrative:
            try:
                mem = create_memory(conn, owner_kind, agent_id, narrative, memory_type="self_narrative",
                                    source="reflection", importance=60, emotional_weight=15, confidence=0.7)
            except Exception:
                mem = None
        mem_id = mem.get("id") if isinstance(mem, dict) else None
        applied = []
        for op in (parsed.get("opinions") or []):
            try:
                o = form_or_reinforce_opinion(
                    conn, agent_id, target=op.get("target", ""), opinion_type=op.get("opinion_type", "like"),
                    strength=op.get("strength", 0), confidence=op.get("confidence", 0.5),
                    reason=op.get("reason"), reflection_memory_id=mem_id, source="reflection",
                )
                applied.append(o.get("id"))
            except Exception:
                continue
        append_journal(conn, owner_kind, agent_id, "reflection_done",
                       {"opinions": len(applied), "narrative": narrative[:120]}, "reflection")
        return {"ok": True, "opinions": applied, "self_narrative": narrative, "memory_id": mem_id}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
