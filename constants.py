"""LifeEngine constants and defaults."""

from __future__ import annotations

PLUGIN_NAME = "lifeengine"
PLUGIN_VERSION = "0.14.0"
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
}

DEFAULT_CANON_TEMPLATE = {
    "identity": {},
    "worldview": {},
    "truth_sources": {
        "bindings": {
            "time": {"domain": "time", "authority": "system_clock", "timezone": "Asia/Tokyo", "time_flow": "real_time"},
            "weather": {"domain": "weather", "authority": "narrative_simulator", "mode": "random_local", "freshness_ttl_minutes": 120, "fallback": "narrative_generate"},
        }
    },
    "resources": {
        "definitions": {
            "energy": {"display_name": "Energy", "resource_class": "vital", "unit": "points", "min": 0, "max": 100, "initial": 60, "rules": {"heartbeat_recovery": 3, "metabolism": -0.06}},
            "focus": {"display_name": "Focus", "resource_class": "vital", "unit": "points", "min": 0, "max": 100, "initial": 60, "rules": {"heartbeat_recovery": 2, "metabolism": -0.04}},
            "mood": {"display_name": "Mood", "resource_class": "vital", "unit": "points", "min": -100, "max": 100, "initial": 0, "rules": {"heartbeat_recovery": 1, "metabolism": -0.01}},
            "fatigue": {"display_name": "Fatigue", "resource_class": "vital", "unit": "points", "min": 0, "max": 100, "initial": 20, "rules": {"heartbeat_recovery": -2, "metabolism": 0.05}},
            "sleep_debt_minutes": {"display_name": "Sleep debt", "resource_class": "vital", "unit": "minutes", "min": 0, "initial": 0},
        }
    },
    "schedule_rules": {"timezone": "Asia/Tokyo"},
    "behavior_rules": {},
    "autonomy": {"enabled": True, "default_mode": "full", "agent_decides_self_life": True},
    "proactive": {"mode": "pending_only"},
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
