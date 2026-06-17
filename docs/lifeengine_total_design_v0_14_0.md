# LifeEngine v0.14.0 — Living Loop (活体生命循环)

## Purpose

v0.13.0 made the observatory feel like a game. v0.14.0 makes the *life itself*
feel continuous and the *personality* feel alive. It fixes three structural
gaps that made the runtime feel "stuttery" and static:

1. **Time was a tick counter, not a clock.** Resource recovery applied a flat
   amount per heartbeat regardless of how much real time had passed. Close the
   laptop for hours and the life either froze or jumped.
2. **Idle was free.** Nothing was consumed between events, so an idle agent
   monotonically recovered to full. Being alive had no cost.
3. **Experience never changed the agent.** Canon (identity) only changes via
   `/life commit`; the agent never grew from what it lived.

## Design Principles

1. Life time is settled against **real elapsed wall-clock time**, not tick count.
2. Wall-clock **gaps are reconciled** (capped + marked), never replayed as a burst.
3. Being awake has a **passive metabolic cost**; state curves are continuous.
4. Personality is a **separate living layer** that drifts from experience and
   feeds back into behavior — but **Canon stays user-owned**. Drift only ever
   *proposes* consolidation into Canon.
5. Every change still flows through `apply_delta` / LifeOps so doctor
   reconciliation and the journal hash-chain stay intact.

---

## Feature 1 — Real-time settlement + gap reconciliation

- `LifeEngineRuntime._minutes_since_last_tick(...)` computes elapsed minutes from
  the most recent completed `heartbeat_runs.started_at`. First tick → `0`.
- `_apply_resource_recovery` → `_settle_resources(minutes_elapsed, resume_policy)`:
  - Each resource's `heartbeat_recovery` rule is reinterpreted as a per-baseline
    amount (`TICK_BASELINE_MIN = 5`) → `per_minute = heartbeat_recovery / 5`,
    settled as `per_minute * effective_minutes`. A normal 5-minute tick settles
    ≈ the old flat amount (backward compatible).
  - `resume_policy='mark_gap_only'` (previously a dormant `controls` column):
    `effective_minutes = min(minutes_elapsed, GAP_CAP_MIN=120)`. When
    `minutes_elapsed > GAP_THRESHOLD_MIN=30`, a `life_gap` journal entry is
    written and the tick result carries a `gap` field.
- All settlement goes through `apply_delta(operation="settle", source="heartbeat")`.

## Feature 2 — Passive metabolism

- Each vital resource gains an optional `metabolism` rule (per-minute net drift)
  in `DEFAULT_CANON_TEMPLATE`: `energy -0.06`, `focus -0.04`, `mood -0.01`,
  `fatigue +0.05`. Applied in the same elapsed pass via
  `apply_delta(operation="metabolism", source="heartbeat")`.
- Net effect: awake & idle slowly declines; sleep's large recovery (via the
  sleep system's own ledger) still dominates rest.
- Gated by module gate `passive_metabolism` (default `auto`); `off` keeps
  recovery but skips metabolism.

## Feature 3 — Living persona drift

- New tables `persona_traits` and `persona_drift_log` (schema v50). Six bounded
  traits in `[-1, 1]`: curiosity, sociability, diligence, optimism, caution,
  expressiveness. Seeded lazily from Canon identity/behavior_rules text.
- `persona.py`:
  - `compute_drift_signals` derives weak, saturating targets from recent
    completed events (by category), mood/energy/fatigue, serendipity outcomes,
    and sleep quality.
  - `apply_persona_drift` moves each trait one bounded step
    (`LR/MOM/DECAY/MAX_STEP`) toward its signal and relaxes toward baseline —
    gradual, bounded, reversible. Logged to `persona_drift_log` + journal.
  - New LifeOp `PERSONA_DRIFT` (validators / `_apply_op` / receipts) keeps drift
    in the audit chain.
- **Triggers:** small drift each heartbeat (gated `personality_drift`, default
  `auto`), amplified on `run_dream_cycle` (dreams = internalization).
- **Feedback into behavior:** persona is injected into the per-turn context
  capsule (trimmed to a tone hint on work-compact platforms) and lightly biases
  the autonomy planner.
- **Consolidation:** when `|value - baseline| >= 0.5` with enough evidence,
  `propose_consolidation` surfaces a suggestion in the human review inbox to
  fold the change into Canon. Never auto-applied.

## Feature 4 — Impromptu activity capture + conflict resolution

The planned-first scheduler had no path for an activity that happens *now* from
conversation (user: "let's go shopping"). It never became an event, never hit
the schedule, and an occupied slot just hard-errored on overlap.

- New op `RECORD_IMPROMPTU_ACTIVITY` + tool action `life_event(action="do_now")`
  (`impromptu.py`). One atomic orchestration:
  1. The activity is real and occupies `[now, now+duration]`; it is recorded as
     an event and (by default) completed immediately.
  2. Any planned blocks overlapping that window **yield the slot** — they are
     rescheduled to the next free slot (`_next_free_slot`), their event going
     `rescheduled` → `scheduled` again at the new time.
  3. Priority governs *surfacing*, not *whether* to yield: lower-importance
     tasks move silently; a displaced higher-importance task is flagged in
     `notices` / `agent_notice` so the agent tells the user it was moved
     ("agent self-arbitrates").
- Conflicts are rescheduled *before* the impromptu block is created, so the
  existing overlap guard in `create_schedule_block` is satisfied with **no
  change to the core invariant** and no relaxation of the hard-overlap rule for
  ordinary planned scheduling.
- SKILL.md instructs the agent to use `do_now` whenever a conversational/
  unplanned activity actually happens, instead of only narrating it.

---

## New / changed surfaces

- Module gates: `passive_metabolism`, `personality_drift`.
- Constants: `TICK_BASELINE_MIN`, `GAP_CAP_MIN`, `GAP_THRESHOLD_MIN`; resource
  `metabolism` rules.
- LifeOps: `PERSONA_DRIFT`, `RECORD_IMPROMPTU_ACTIVITY`.
- `life_event` action: `do_now` (impromptu capture + conflict resolution).
- Doctor: persona value-bound check.
- `life_status` / WebUI snapshot: persona summary.

## Version

- Plugin version `0.14.0`; schema version `50`.
