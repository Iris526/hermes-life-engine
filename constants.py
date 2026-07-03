"""LifeEngine constants and defaults."""

from __future__ import annotations

from dataclasses import dataclass

PLUGIN_NAME = "lifeengine"
PLUGIN_VERSION = "0.18.0"
DB_FILENAME = "lifeengine.db"
VECTOR_DIM = 384

# v0.14.0 living-loop timing constants.
# The heartbeat historically applied a flat recovery amount per tick assuming a
# ~5-minute cadence. We now settle resources against REAL elapsed wall-clock
# time, reinterpreting the per-tick rule as a per-baseline amount.
TICK_BASELINE_MIN = 5      # minutes a single `heartbeat_recovery`/`metabolism` rule represents
GAP_CAP_MIN = 120          # cap settled minutes after a long offline gap
GAP_THRESHOLD_MIN = 30     # elapsed beyond this records a `life_gap` marker

ENGINE_STATES = {
    "uninitialized",
    "setup_required",
    "setup",
    "active",
    "paused",
    "paused_setup",
    "read_only",
    "migrating",
    "disabled",
    "archived",
}

MUTATION_BLOCKING_STATES = {"paused", "paused_setup", "read_only", "disabled", "migrating", "archived"}
SETUP_STATES = {"setup_required", "setup", "paused_setup"}

DEFAULT_AGENT_ID = "default-agent"
DEFAULT_USER_ID = "anonymous-user"

# ---------------------------------------------------------------------------
# Module gates — a SINGLE source of truth (轴二-2 gate schema).
#
# Historically DEFAULT_MODULE_GATES was a hand-written dict of ~37 keys, of
# which the 2026-07-02 audit found 18 had NO runtime read ("杂物抽屉"): the
# rule "any rule not enforced by the compiler/grep will be violated" applied to
# itself. Every gate is now a GateSpec whose `consumer` names where it is
# actually READ; a gate with no consumer must not exist here (pinned by
# test_lifeengine_gate_schema). DEFAULT_MODULE_GATES is DERIVED, never
# hand-copied, so it cannot drift from the specs.
#
# `set_module_gate` validates writes against these specs (was zero-validation),
# so an unknown key or an out-of-vocabulary value is rejected instead of
# silently poisoning the drawer.
# ---------------------------------------------------------------------------

# Shared on/off vocabulary accepted by every simple mode gate. Read sites treat
# {off, disabled, false, manual} as "off" and {auto, on} as "on"; richer gates
# extend this with their own modes.
_GATE_ONOFF: tuple[str, ...] = ("auto", "on", "off", "disabled", "manual", "false")


@dataclass(frozen=True)
class GateSpec:
    """One module gate's contract: how it is typed, defaulted, and consumed.

    kind:
      "mode"    — an enum gate; a written value must be one of `allowed`
                  (compared case-insensitively).
      "numeric" — an integer config that lives in the gate dict for storage
                  convenience but is NOT part of the mode value-space; a written
                  value must parse to an int within `numeric_range`.
    consumer:   file:line (or short note) where the gate is READ at runtime.
                This is the anti-drift anchor — a gate with no real consumer is
                dead code and does not belong in the registry.
    """

    kind: str
    default: str
    consumer: str
    allowed: tuple[str, ...] = ()
    numeric_range: tuple[int, int] | None = None


GATE_SPECS: dict[str, GateSpec] = {
    # --- core heartbeat / living loop -------------------------------------
    "heartbeat": GateSpec("mode", "manual", "runtime.py tick / heartbeat_authoring.py", _GATE_ONOFF),
    "schedule": GateSpec("mode", "auto", "runtime.py _ensure_daily_rhythm_for_tick", _GATE_ONOFF),
    "daily_rhythm": GateSpec("mode", "auto", "runtime.py _ensure_daily_rhythm_for_tick (living_rhythm = legacy read-alias)", _GATE_ONOFF),
    "passive_metabolism": GateSpec("mode", "auto", "runtime.py _settle_resources", _GATE_ONOFF),
    "personality_drift": GateSpec("mode", "auto", "runtime.py _run_persona_drift_for_tick / dream.py", _GATE_ONOFF),
    "meals": GateSpec("mode", "auto", "runtime.py _settle_meals_for_tick", _GATE_ONOFF),
    "recurring_activities": GateSpec("mode", "auto", "runtime.py _materialize_recurring / _settle_supply_chain / _roll_opportunities", _GATE_ONOFF),
    "campaigns": GateSpec("mode", "auto", "runtime.py _run_campaigns_for_tick", _GATE_ONOFF),
    # --- reply / autonomy / proactive -------------------------------------
    "reply_gate": GateSpec("mode", "advisory", "reply_gate.py / runtime.py assess_incoming_message", _GATE_ONOFF + ("advisory", "strict")),
    "autonomy": GateSpec("mode", "full", "autonomy.py / runtime.py _run_autonomy_for_tick", _GATE_ONOFF + ("full", "low_spontaneity")),
    "proactive": GateSpec("mode", "pending_only", "runtime.py _run_proactive_for_tick / proactive.py / companion.py", _GATE_ONOFF + ("pending_only", "auto_send", "full")),
    "managed_review_loop": GateSpec("mode", "auto", "runtime.py startup_check / _run_managed_review_for_tick", _GATE_ONOFF),
    "final_audit": GateSpec("mode", "advisory", "runtime.py audit_final_output / doctor persona-guard", _GATE_ONOFF + ("advisory", "strict", "trace", "repair")),
    # --- truth / dream ----------------------------------------------------
    "truth_sources": GateSpec("mode", "auto", "truth_sources.py read", _GATE_ONOFF),
    "dream": GateSpec("mode", "auto", "runtime.py wake-dream / heartbeat_authoring.py", _GATE_ONOFF + ("daily", "on", "manual_ok")),
    # --- v0.18.0 generative inner life ------------------------------------
    # LifeAuthor: generative content via the host model. Resolves to off at
    # runtime when the host PluginLlm facade isn't importable (dev/CI), so the
    # engine falls back to deterministic templates.
    "life_author": GateSpec("mode", "auto", "life_author.py author() gate", _GATE_ONOFF),
    # companion: idle / companionship outreach (reaches out when quiet + in a
    # good mood, or to follow up on what you told her). Needs life_author +
    # proactive; degrades to silence without a host.
    "companion": GateSpec("mode", "auto", "runtime.py _run_companion_for_tick / companion.py", _GATE_ONOFF),
    # reflection: once-a-day look-back that forms/reinforces opinions and a
    # self-narrative from lived experience. Needs life_author; no-op without.
    "reflection": GateSpec("mode", "auto", "runtime.py _run_reflection_for_tick / heartbeat_authoring.py", _GATE_ONOFF),
    # --- context policy ---------------------------------------------------
    # context_mode is a mode enum with its OWN vocabulary (not on/off).
    "context_mode": GateSpec("mode", "slim", "context_policy.py ContextPolicy.from_control", ("micro", "slim", "balanced", "debug")),
    # context_budget_chars is a NUMERIC config that happens to be stored in the
    # gate dict — kept OUT of the mode value-space (audit: 数值配置混进门控值域).
    # Range must stay in sync with context_policy.MIN/MAX_BUDGET_CHARS
    # (pinned by test_lifeengine_gate_schema).
    "context_budget_chars": GateSpec("numeric", "5200", "context_policy.py ContextPolicy.from_control", numeric_range=(1800, 12000)),
}

# Derived — never hand-copied, so it cannot drift from GATE_SPECS.
DEFAULT_MODULE_GATES = {key: spec.default for key, spec in GATE_SPECS.items()}


def validate_gate_value(key: str, value: str) -> str:
    """Validate & normalize a module-gate write against GATE_SPECS.

    Raises ValueError on an unknown key, a non-integer/out-of-range numeric, or
    a value outside a mode gate's vocabulary. Returns the value to store
    (numeric values are canonicalized to their int form).
    """
    spec = GATE_SPECS.get(key)
    if spec is None:
        raise ValueError(
            f"unknown module gate: {key!r} (known: {', '.join(sorted(GATE_SPECS))})"
        )
    v = str(value).strip()
    if spec.kind == "numeric":
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"module gate {key!r} takes an integer, got {value!r}")
        lo, hi = spec.numeric_range  # type: ignore[misc]
        if not (lo <= n <= hi):
            raise ValueError(f"module gate {key!r} must be in [{lo}, {hi}], got {n}")
        return str(n)
    if v.lower() not in {a.lower() for a in spec.allowed}:
        raise ValueError(
            f"module gate {key!r} takes one of {list(spec.allowed)}, got {value!r}"
        )
    return v

DEFAULT_CANON_TEMPLATE = {
    "identity": {},
    "worldview": {
        "social_slots": {
            "entity_kinds": {},
            "relationship_axes": {},
            "reputation_axes": {},
            "evaluation_axes": {},
            "rumor_channels": {},
        }
    },
    "truth_sources": {
        "bindings": {
            "time": {"domain": "time", "authority": "system_clock", "timezone": "Asia/Tokyo", "time_flow": "real_time"},
            "weather": {"domain": "weather", "authority": "narrative_simulator", "mode": "random_local", "freshness_ttl_minutes": 120, "fallback": "narrative_generate"},
        }
    },
    "resources": {
        "definitions": {
            "energy": {"display_name": "Energy", "resource_class": "vital", "unit": "points", "min": 0, "max": 100, "initial": 60, "rules": {"heartbeat_recovery": 3, "metabolism": -0.06}},
            "mood": {"display_name": "Mood", "resource_class": "vital", "unit": "points", "min": -100, "max": 100, "initial": 0, "rules": {"heartbeat_recovery": 1, "metabolism": -0.01}},
            "fatigue": {"display_name": "Fatigue", "resource_class": "vital", "unit": "points", "min": 0, "max": 100, "initial": 20, "rules": {"heartbeat_recovery": -2, "metabolism": 0.05}},
            "sleep_debt_minutes": {"display_name": "Sleep debt", "resource_class": "vital", "unit": "minutes", "min": 0, "initial": 0},
        }
    },
    "schedule_rules": {"timezone": "Asia/Tokyo"},
    "meals": {
        "enabled": True,
        "needs_food": True,
        # base meals must be accounted for each day (eaten / skipped-with-reason / covered by brunch)
        "times": {"breakfast": "07:30", "lunch": "12:30", "dinner": "19:00"},
        "window_minutes": 150,
        "skip_penalty": {"energy": -6, "mood": -4},
        "default_skip_reason": "忙碌中没能按时吃饭",
        # agent may autonomously DERIVE extra meals it isn't told to eat
        "autonomy": True,
        "snack_tendency": 0.5,          # 0..1 "嘴馋" propensity for optional meals
        "optional": {
            "afternoon_tea": {"window": ["14:30", "16:30"], "mood": 4, "reason": "嘴馋,给自己来了点下午茶"},
            "late_night_snack": {"window": ["22:00", "25:00"], "mood": 3, "reason": "嘴馋,加了顿夜宵"},
        },
        "allow_brunch": True,
        "brunch_window": ["10:00", "12:00"],   # if neither breakfast nor lunch yet, may merge into brunch
        "brunch_reason": "起得晚,早午饭并作一顿 brunch",
    },
    "behavior_rules": {},
    "autonomy": {"enabled": True, "default_mode": "full", "agent_decides_self_life": True},
    "proactive": {"mode": "pending_only"},
    "life_author": {
        "enabled": True,
        "daily_token_budget": 200000,
        "timeout_seconds": 20,
        # model per kind — only honoured if the HOST opts the plugin into model
        # override (plugins.entries.lifeengine.llm.allow_model_override +
        # allowed_models); otherwise ignored and the user's active model is used.
        "models": {},
    },
    "companion": {
        "enabled": True,
        "idle_max_per_day": 3,        # at most this many self-initiated idle/companion lines per day
        "min_minutes_between": 180,   # spacing between idle outreach
    },
    "execution": {"defaultOutcomePolicy": "narrative_simulator", "allowPostpone": True, "allowPartial": True},
    "serendipity": {"dailyMinorEventProbability": 0.25, "dramaLevel": "low", "maxSignificantSurprisesPerWeek": 1},
    "diary": {},
    "sleep": {
        "core_sleep_required": True,
        "default_plan_type": "core_sleep",
        "default_wake_policy": "natural_or_alarm",
        "allow_overnight_delay": True,
        "allow_user_interrupt": True,
        "sleep_debt_resource": "sleep_debt_minutes",
        "fatigue_threshold_for_nap": 75
    },
    "dream": {
        "enabled": True,
        "run_on_core_sleep_wake": True,
        "allow_nap_dreams": False,
        "min_core_dream_minutes": 90,
        "audit_on_dream": True,
        "share_on_wake": True,
        "truth_layer": "dream_symbolic",
        "default_share_user_id": "anonymous-user",
        "repair_policy": "manual",
        "auto_safe_repair_types": ["stale_schedule_block", "pending_delayed_replies", "stale_resource_reservation"]
    },
    "behavior_mapping": {"enabled": True, "private_truth_sources": True, "never_expose_sources": True},
    "user_life_policy": {
        "canInventPastEvents": False,
        "canInventFuturePlans": False,
        "allowsNarrativeReality": False,
        "requiresOwnerConfirmation": True,
    },
    "agent_life_policy": {
        "canInventPastEvents": True,
        "canInventFuturePlans": True,
        "allowsNarrativeReality": True,
        "requiresOwnerConfirmation": False,
    },
}

BOOT_PROTOCOL = """
<LIFEENGINE_BOOT_PROTOCOL>
LifeEngine is a code-enforced runtime. The model should not rely on prompt text to maintain life state. Durable facts, resources, schedules, collection items, dreams, replies, and reviews must be created through LifeEngine tools/LifeOps.
Use the compact context for the current turn only; call tools for details. Do not expose internal diagnostics, private behavior sources, or FinalGate feedback to the user.
</LIFEENGINE_BOOT_PROTOCOL>
""".strip()
