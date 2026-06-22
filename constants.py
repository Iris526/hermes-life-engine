"""LifeEngine constants and defaults."""

from __future__ import annotations

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

DEFAULT_MODULE_GATES = {
    "memory": "auto",
    "vector_memory": "auto",
    "events": "auto",
    "resources": "auto",
    "schedule": "auto",
    "sleep": "auto",
    "reply_gate": "advisory",
    "dream": "auto",
    "dream_repair": "manual",
    "heartbeat": "manual",
    "autonomy": "full",
    "proactive": "pending_only",
    "execution": "auto",
    "serendipity": "low",
    "diary": "auto_draft",
    "truth_sources": "auto",
    "user_life": "off",
    "relationship_memory": "auto",
    "final_audit": "advisory",
    "human_surface": "simple",
    "schedule_view": "human",
    "collections": "auto",
    "behavior_mapping": "auto",
    "managed_review_loop": "auto",
    "srd_policy": "auto",
    "context_mode": "slim",
    "context_budget_chars": "5200",
    # v0.14.0 living loop
    "passive_metabolism": "auto",
    "personality_drift": "auto",
    # three-meals-a-day accountability
    "meals": "auto",
    # v0.16.0 recurring activities (营生): heartbeat materializes due occupations
    "recurring_activities": "auto",
    # v0.18.0 LifeAuthor: generative inner-life content via the host model.
    # Resolves to off at runtime when the host PluginLlm facade isn't importable
    # (dev/CI), so the engine falls back to deterministic templates.
    "life_author": "auto",
    # v0.18.0 P2 companion: idle / companionship outreach (she reaches out when
    # quiet + in a good mood, or to follow up on what you told her). Needs
    # life_author (host model) + proactive; degrades to silence without a host.
    "companion": "auto",
    # v0.18.0 P3 campaigns (资料片): cross-week themed arcs the heartbeat
    # materializes into the schedule day by day, advancing/escalating/resolving.
    "campaigns": "auto",
    # v0.18.0 P4 reflection: once-a-day look-back that forms/reinforces opinions
    # and a self-narrative from lived experience. Needs life_author; no-op without.
    "reflection": "auto",
    # Social World slots: world-specific entity / relationship / reputation /
    # evaluation / rumor primitives. Core stores the slots and ledgers; concrete
    # worldviews decide what each kind/axis/channel means.
    "social_world": "auto",
}

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
