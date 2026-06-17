"""Living persona layer for LifeEngine (v0.14.0).

The agent's *identity* lives in Canon and is owned by the user — it only changes
through ``/life setup`` + ``/life commit``.  This module adds a SEPARATE,
auto-drifting *persona* layer: a small set of bounded traits that slowly move in
response to lived experience (completed events, mood/energy trends, sleep
quality, serendipity) and slowly relax back toward their Canon-seeded baseline
when that experience stops.

Design rules (mirror the rest of the engine):

- Persona never mutates Canon.  Drift is its own table + ledger.
- Every drift goes through a ``PERSONA_DRIFT`` LifeOp so it lands in the
  transaction / receipt / journal / trace chain like any other life fact.
- Drift is gradual, bounded ``[-1, 1]``, and reversible (decay toward baseline).
- When a trait drifts far from baseline for long enough, we surface a
  *proposal* to consolidate it into Canon — we never auto-edit Canon.

Traits feed back into behavior by being injected into the per-turn context
capsule and by lightly biasing the autonomy planner.
"""

from __future__ import annotations

from typing import Any

from .jsonutil import dumps, loads
from .trace import append_journal, new_id

# --- Trait registry -------------------------------------------------------

# Canonical drifting traits.  value/baseline are in [-1, 1]; 0 == neutral.
PERSONA_TRAITS: tuple[str, ...] = (
    "curiosity",
    "sociability",
    "diligence",
    "optimism",
    "caution",
    "expressiveness",
)

# Drift dynamics.  Kept small so a trait needs sustained evidence to move and
# always relaxes home in the absence of signal.
LR = 0.04          # learning rate toward the signal target
MOM = 0.6          # momentum carry-over
DECAY = 0.02       # pull back toward baseline each drift step
MAX_STEP = 0.05    # cap on a single drift step magnitude
BOUND = 1.0        # trait value bound (symmetric)

# Consolidation proposal thresholds.
CONSOLIDATE_THRESHOLD = 0.5   # |value - baseline| beyond this is "notable"
CONSOLIDATE_MIN_EVIDENCE = 50  # need this much accumulated evidence first

# Keyword hints for seeding initial trait values from Canon free text.
_SEED_KEYWORDS: dict[str, tuple[tuple[str, float], ...]] = {
    "curiosity": (("好奇", 0.5), ("探索", 0.4), ("求知", 0.4), ("curious", 0.5), ("explore", 0.4)),
    "sociability": (("外向", 0.5), ("社交", 0.5), ("热情", 0.3), ("internal", -0.3), ("内向", -0.5), ("社恐", -0.6), ("outgoing", 0.5), ("shy", -0.5)),
    "diligence": (("勤奋", 0.6), ("自律", 0.5), ("认真", 0.4), ("懒", -0.5), ("diligent", 0.6), ("lazy", -0.5)),
    "optimism": (("乐观", 0.6), ("积极", 0.4), ("开朗", 0.4), ("悲观", -0.6), ("optimistic", 0.6), ("pessimistic", -0.6)),
    "caution": (("谨慎", 0.6), ("小心", 0.4), ("稳重", 0.4), ("冲动", -0.5), ("cautious", 0.6), ("reckless", -0.5)),
    "expressiveness": (("健谈", 0.5), ("表达", 0.4), ("话多", 0.4), ("沉默", -0.5), ("寡言", -0.5), ("expressive", 0.5), ("quiet", -0.4)),
}


def _clamp(v: float, lo: float = -BOUND, hi: float = BOUND) -> float:
    return max(lo, min(hi, v))


def _seed_values_from_canon(canon: dict[str, Any] | None) -> dict[str, float]:
    """Infer starting trait values from Canon identity / behavior_rules text."""
    canon = canon or {}
    identity = canon.get("identity") or {}
    text_parts = [
        str(identity.get("selfDescription") or identity.get("self_description") or ""),
        str(identity.get("name") or ""),
        dumps(canon.get("behavior_rules") or {}),
        str((canon.get("worldview") or {}).get("raw_world_description") or ""),
    ]
    blob = " ".join(text_parts).lower()
    out: dict[str, float] = {}
    for trait, hints in _SEED_KEYWORDS.items():
        score = 0.0
        for kw, weight in hints:
            if kw.lower() in blob:
                score += weight
        out[trait] = _clamp(score)
    return out


def ensure_persona(conn, owner_kind: str, owner_id: str, canon: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Lazily seed persona traits if missing; return current trait map.

    Seeding is lazy (on first read/drift) rather than hooked into canon commit
    so it also works for pre-existing profiles and avoids a canon->persona
    import cycle.
    """
    rows = conn.execute(
        "SELECT trait_key, value, baseline, momentum, evidence_count FROM persona_traits WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchall()
    present = {r["trait_key"] for r in rows}
    missing = [t for t in PERSONA_TRAITS if t not in present]
    if missing:
        seeds = _seed_values_from_canon(canon)
        for trait in missing:
            v = float(seeds.get(trait, 0.0))
            conn.execute(
                """INSERT OR IGNORE INTO persona_traits(owner_kind, owner_id, trait_key, value, baseline, momentum, evidence_count)
                       VALUES(?,?,?,?,?,?,0)""",
                (owner_kind, owner_id, trait, v, v, 0.0),
            )
        rows = conn.execute(
            "SELECT trait_key, value, baseline, momentum, evidence_count FROM persona_traits WHERE owner_kind=? AND owner_id=?",
            (owner_kind, owner_id),
        ).fetchall()
    return {r["trait_key"]: dict(r) for r in rows}


def get_persona(conn, owner_kind: str, owner_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT trait_key, value, baseline, momentum, evidence_count, updated_at FROM persona_traits WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchall()
    return {r["trait_key"]: dict(r) for r in rows}


# --- Experience -> drift signals -----------------------------------------

def compute_drift_signals(conn, owner_kind: str, owner_id: str, *, window_minutes: float = 60.0,
                          now: str | None = None) -> dict[str, float]:
    """Derive small trait targets in [-1, 1] from recent lived experience.

    Signals are intentionally weak and saturating: a single tick only nudges,
    sustained patterns are what actually move a trait.
    """
    signals: dict[str, float] = {}

    def _bump(trait: str, amount: float) -> None:
        signals[trait] = _clamp(signals.get(trait, 0.0) + amount)

    # 1) Completed events by category over a recent window.
    minutes = max(5.0, float(window_minutes))
    rows = conn.execute(
        f"""SELECT COALESCE(event_category, event_type, 'other') AS cat, COUNT(*) AS n
              FROM events
             WHERE owner_kind=? AND owner_id=? AND status='completed'
               AND updated_at >= datetime('now', '-{int(minutes)} minutes')
             GROUP BY cat""",
        (owner_kind, owner_id),
    ).fetchall()
    for r in rows:
        cat = str(r["cat"] or "other")
        n = int(r["n"] or 0)
        if not n:
            continue
        if cat in {"work", "study", "maintenance", "finance"}:
            _bump("diligence", 0.3 * n)
        if cat in {"social", "relationship"}:
            _bump("sociability", 0.4 * n)
            _bump("expressiveness", 0.2 * n)
        if cat in {"creative", "leisure"}:
            _bump("curiosity", 0.3 * n)
            _bump("expressiveness", 0.2 * n)
        if cat in {"health", "fitness", "travel"}:
            _bump("optimism", 0.1 * n)

    # 2) Mood / energy trend (current vital levels) -> optimism.
    vit = conn.execute(
        "SELECT resource_key, current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key IN ('mood','energy','fatigue')",
        (owner_kind, owner_id),
    ).fetchall()
    vmap = {r["resource_key"]: float(r["current_value"] or 0) for r in vit}
    if "mood" in vmap:
        _bump("optimism", vmap["mood"] / 100.0)  # mood is [-100,100]
    if vmap.get("fatigue", 0) >= 70:
        _bump("optimism", -0.2)
        _bump("diligence", -0.1)

    # 3) Serendipity outcomes -> caution.
    ser = conn.execute(
        f"""SELECT serendipity_type, COUNT(*) AS n FROM serendipity_events
              WHERE owner_kind=? AND owner_id=?
                AND created_at >= datetime('now', '-{int(minutes)} minutes')
             GROUP BY serendipity_type""",
        (owner_kind, owner_id),
    ).fetchall() if _table_exists(conn, "serendipity_events") else []
    for r in ser:
        st = str(r["serendipity_type"] or "")
        n = int(r["n"] or 0)
        if "problem" in st:
            _bump("caution", 0.3 * n)
        elif "discovery" in st:
            _bump("curiosity", 0.2 * n)
            _bump("optimism", 0.1 * n)

    # 4) Sleep quality -> optimism / diligence.
    if _table_exists(conn, "sleep_day_states"):
        sd = conn.execute(
            "SELECT recovery_pressure, all_nighter FROM sleep_day_states WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 1",
            (owner_kind, owner_id),
        ).fetchone()
        if sd:
            rp = float(sd["recovery_pressure"] or 0)
            if rp >= 60:
                _bump("optimism", -0.2)
                _bump("diligence", -0.1)
            if sd["all_nighter"]:
                _bump("caution", 0.1)

    return {k: v for k, v in signals.items() if abs(v) > 1e-9}


def _table_exists(conn, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)


# --- Drift application (PERSONA_DRIFT LifeOp handler) ----------------------

def apply_persona_drift(conn, owner_kind: str, owner_id: str, *, signals: dict[str, float] | None = None,
                        canon: dict[str, Any] | None = None, tick_id: str | None = None,
                        trace_id: str | None = None, transaction_id: str | None = None,
                        source: str = "heartbeat", gain: float = 1.0) -> dict[str, Any]:
    """Apply one bounded drift step toward ``signals`` (with baseline decay).

    Traits with no signal still relax toward baseline.  Returns the per-trait
    deltas actually applied (after clamping) for receipts/trace.
    """
    traits = ensure_persona(conn, owner_kind, owner_id, canon)
    signals = signals or {}
    changes: list[dict[str, Any]] = []
    for trait in PERSONA_TRAITS:
        row = traits.get(trait) or {"value": 0.0, "baseline": 0.0, "momentum": 0.0, "evidence_count": 0}
        value = float(row.get("value") or 0.0)
        baseline = float(row.get("baseline") or 0.0)
        momentum = float(row.get("momentum") or 0.0)
        evidence = int(row.get("evidence_count") or 0)

        target = _clamp(float(signals.get(trait, 0.0)))
        has_signal = trait in signals
        if has_signal:
            # Learning toward the signal, with momentum carry-over.
            momentum = MOM * momentum + LR * (target - value) * float(gain)
            evidence += 1
        else:
            # No experience this step: bleed momentum so it cannot keep pushing
            # the trait past where signals took it. This makes drift reversible —
            # once experience stops, only the baseline pull remains and the trait
            # monotonically relaxes home.
            momentum *= 0.5
        step = max(-MAX_STEP, min(MAX_STEP, momentum))
        pull = DECAY * (baseline - value)
        new_value = _clamp(value + step + pull)
        delta = new_value - value
        # Always persist relaxation/evidence even when delta is tiny.
        conn.execute(
            """UPDATE persona_traits SET value=?, momentum=?, evidence_count=?, last_drift_at=datetime('now'), updated_at=datetime('now')
                   WHERE owner_kind=? AND owner_id=? AND trait_key=?""",
            (new_value, momentum, evidence, owner_kind, owner_id, trait),
        )
        if abs(delta) > 1e-4:
            log_id = new_id("perdrift")
            conn.execute(
                """INSERT INTO persona_drift_log(id, owner_kind, owner_id, trait_key, delta, value_after, reason, evidence_json, tick_id, trace_id, transaction_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (log_id, owner_kind, owner_id, trait, delta, new_value,
                 f"{source} drift", dumps({"target": target, "had_signal": has_signal}), tick_id, trace_id, transaction_id),
            )
            changes.append({"trait": trait, "delta": round(delta, 4), "value": round(new_value, 4)})

    if changes:
        append_journal(conn, owner_kind, owner_id, "persona_drift",
                       {"changes": changes, "source": source}, source, transaction_id=transaction_id)
    return {"ok": True, "changes": changes, "trait_count": len(PERSONA_TRAITS)}


# --- Consolidation proposals (persona -> Canon, proposal only) ------------

def propose_consolidation(conn, owner_kind: str, owner_id: str) -> list[dict[str, Any]]:
    """Return traits that have drifted notably and persistently from baseline.

    These are surfaced in the human review inbox as *suggestions* to fold into
    Canon — never auto-applied.
    """
    rows = conn.execute(
        "SELECT trait_key, value, baseline, evidence_count FROM persona_traits WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchall()
    proposals: list[dict[str, Any]] = []
    for r in rows:
        value = float(r["value"] or 0.0)
        baseline = float(r["baseline"] or 0.0)
        evidence = int(r["evidence_count"] or 0)
        drift = value - baseline
        if abs(drift) >= CONSOLIDATE_THRESHOLD and evidence >= CONSOLIDATE_MIN_EVIDENCE:
            proposals.append({
                "trait": r["trait_key"],
                "baseline": round(baseline, 3),
                "value": round(value, 3),
                "drift": round(drift, 3),
                "direction": "higher" if drift > 0 else "lower",
                "evidence_count": evidence,
            })
    return proposals


# --- Rendering ------------------------------------------------------------

def _trait_phrase(trait: str, value: float) -> str:
    level = "偏高" if value >= 0.33 else ("偏低" if value <= -0.33 else "中性")
    return f"{trait}{level}"


def render_persona_capsule(traits: dict[str, dict[str, Any]], *, work_compact: bool = False) -> dict[str, Any]:
    """Compact persona view for the per-turn LLM context.

    Lists only traits that meaningfully deviate from neutral, plus a one-line
    tone hint.  On work-compact platforms only the tone hint is exposed.
    """
    if not traits:
        return {}
    notable = []
    for trait in PERSONA_TRAITS:
        row = traits.get(trait)
        if not row:
            continue
        value = float(row.get("value") or 0.0)
        if abs(value) >= 0.33:
            notable.append((trait, value))
    notable.sort(key=lambda t: -abs(t[1]))
    tone = "、".join(_trait_phrase(t, v) for t, v in notable[:4]) or "性格平稳，无明显偏向"
    if work_compact:
        return {"tone_hint": tone}
    return {
        "tone_hint": tone,
        "traits": {t: round(float((traits.get(t) or {}).get("value") or 0.0), 3) for t in PERSONA_TRAITS},
        "note": "活体人格层：会随经历缓慢漂移，仅作语气/倾向提示，不是 Canon 身份事实。",
    }


def persona_status(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """Human/status-facing persona summary."""
    traits = get_persona(conn, owner_kind, owner_id)
    if not traits:
        return {"seeded": False, "traits": {}}
    return {
        "seeded": True,
        "traits": {t: {"value": round(float(traits[t]["value"]), 3),
                       "baseline": round(float(traits[t]["baseline"]), 3),
                       "evidence_count": int(traits[t]["evidence_count"])}
                   for t in traits},
        "consolidation_proposals": propose_consolidation(conn, owner_kind, owner_id),
    }
