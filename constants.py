"""LifeEngine constants and defaults."""

from __future__ import annotations

PLUGIN_NAME = "lifeengine"
PLUGIN_VERSION = "0.11.18"
DB_FILENAME = "lifeengine.db"
VECTOR_DIM = 384

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
    "autonomy": "manual",
    "proactive": "pending_only",
    "execution": "auto",
    "serendipity": "low",
    "diary": "manual",
    "truth_sources": "auto",
    "user_life": "off",
    "relationship_memory": "auto",
    "final_audit": "advisory",
    "human_surface": "simple",
    "srd_policy": "auto",
}

DEFAULT_CANON_TEMPLATE = {
    "identity": {},
    "worldview": {},
    "truth_sources": {},
    "resources": {},
    "schedule_rules": {},
    "behavior_rules": {},
    "autonomy": {},
    "proactive": {},
    "execution": {"defaultOutcomePolicy": "narrative_simulator", "allowPostpone": True, "allowPartial": True},
    "sleep": {"coreSleepRequired": True, "defaultSleepHours": 7.5, "defaultBedtime": "23:30", "defaultWakeTime": "07:00", "allowAllNighter": True, "allowNap": True},
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
<LIFEENGINE_PROTOCOL>
LifeEngine is enabled for this Hermes profile when its state is active or setup-related.
Life Canon is the highest agent-life truth source below platform/runtime policy.
Do not create durable agent-life facts merely by saying them. Use LifeEngine tools to commit LifeOps first when a durable fact must become state.
Heartbeat execution must use the execution simulator: due plans may complete, partially complete, fail, skip, or postpone based on Canon, resources, TruthSources, and schedule state.
When LifeEngine is setup/paused_setup, only collect CanonDraft settings; do not advance life, create events, consume resources, or write diary entries.
When LifeEngine is paused/read_only/disabled, do not mutate life state.
Agent Life and User Life share schemas but not truth policy: agent self-life may use narrative reality if Canon allows it; user life must not be invented. FinalGate is advisory by default: use any FinalGate feedback internally to commit missing LifeOps or rephrase claims; do not expose gate diagnostics to the user unless explicitly asked.
</LIFEENGINE_PROTOCOL>
""".strip()
