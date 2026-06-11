"""Hermes tool schemas for LifeEngine."""

OWNER_PROPS = {
    "owner_kind": {"type": "string", "enum": ["agent", "user", "relationship"], "description": "Workspace owner. Default: agent."},
    "owner_id": {"type": "string", "description": "Optional explicit owner id. Default: profile-local default agent/user."},
}

LIFE_STATUS = {
    "name": "life_status",
    "description": "Read LifeEngine control state, Life Canon snapshot, resources, and pending proactive intents. Read-only.",
    "parameters": {"type": "object", "properties": OWNER_PROPS, "required": []},
}

LIFE_CONTROL = {
    "name": "life_control",
    "description": "Control LifeEngine state and module gates: setup, pause, resume, disable, readonly, heartbeat, module. Does not create life events.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["setup", "pause", "resume", "disable", "readonly", "heartbeat", "module"], "description": "Control action."},
            "reason": {"type": "string", "description": "Human-readable reason."},
            "mode": {"type": "string", "description": "Heartbeat mode when action=heartbeat: off/manual/hermes_cron/embedded_thread/framework_driver. Use slash/CLI heartbeat install/status/run-script for cron script lifecycle."},
            "key": {"type": "string", "description": "Module gate key when action=module."},
            "value": {"type": "string", "description": "Module gate value when action=module."},
        },
        "required": ["action"],
    },
}

LIFE_SETUP = {
    "name": "life_setup",
    "description": "Enter setup/paused_setup or append natural-language settings to CanonDraft. Setup writes only CanonDraft, not life events.",
    "parameters": {
        "type": "object",
        "properties": {**OWNER_PROPS, "text": {"type": "string", "description": "Natural-language identity/world/resource/truth-source settings to append."}},
        "required": [],
    },
}

LIFE_COMMIT = {
    "name": "life_commit",
    "description": "Commit either CanonDraft or structured LifeOps. Use this before final-answering any new durable life fact. User-life writes must use user_reported/user_confirmed/tool_imported/calendar_imported/file_imported/manual_entry sources; agent narrative sources are only valid for agent self-life.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "commit_type": {"type": "string", "enum": ["canon", "ops"], "description": "canon commits current CanonDraft; ops commits LifeOps."},
            "draft_id": {"type": "string", "description": "Optional CanonDraft id for commit_type=canon."},
            "activate": {"type": "boolean", "description": "Activate committed Canon version. Default true."},
            "ops": {
                "type": "array",
                "description": "LifeOps. Examples: {type:'CREATE_EVENT', payload:{title:'...', status:'planned'}}; {type:'RESOURCE_DELTA', payload:{resource_key:'money.jpy', delta:-950, operation:'consume', reason:'lunch'}}.",
                "items": {"type": "object"},
            },
        },
        "required": ["commit_type"],
    },
}

LIFE_RESOURCE = {
    "name": "life_resource",
    "description": "List, define, or mutate resources. Mutations are blocked while LifeEngine is paused/setup/read_only/disabled.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["list", "define", "delta", "reserve", "release", "reconcile"], "description": "Resource action."},
            "key": {"type": "string", "description": "Resource key, e.g. money.jpy, energy, inspiration."},
            "display_name": {"type": "string"},
            "resource_class": {"type": "string", "description": "fungible/capacity/durable_item/consumable/state/skill/relationship/permission/location/custom."},
            "unit": {"type": "string"},
            "min_value": {"type": "number"},
            "max_value": {"type": "number"},
            "initial": {"type": "number"},
            "rules": {"type": "object"},
            "resource_key": {"type": "string", "description": "For action=delta; same as key."},
            "delta": {"type": "number"},
            "operation": {"type": "string", "description": "produce/consume/recover/decay/adjust/etc."},
            "reason": {"type": "string"},
            "source": {"type": "string"},
            "amount": {"type": "number", "description": "For action=reserve."},
            "reservation_id": {"type": "string", "description": "For action=release."},
            "amount": {"type": "number", "description": "Reservation amount for action=reserve."},
            "reservation_id": {"type": "string", "description": "Reservation id for action=release."},
        },
        "required": ["action"],
    },
}

LIFE_EVENT = {
    "name": "life_event",
    "description": "Create/list/schedule/transition/complete Life Events. Use for Agent self-life and user-life plans with the correct owner policy.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["list", "get", "create", "schedule", "transition", "complete", "transitions", "schedule_transitions", "state", "update_state"], "description": "Event action. v0.11 Event V2 adds get/transitions/state/update_state."},
            "status": {"type": "string"},
            "limit": {"type": "integer"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "event_type": {"type": "string"},
            "event_category": {"type": "string", "description": "V2 category: sleep/work/study/health/meal/purchase/social/leisure/maintenance/travel/creative/finance/relationship/reflection/dream/system/other."},
            "activity_domain": {"type": "string", "description": "Optional domain within category, e.g. craft_commission, exam_prep."},
            "subtype": {"type": "string", "description": "Optional subtype."},
            "tags": {"type": "array", "items": {"type": "string"}},
            "attributes": {"type": "object", "description": "V2 arbitrary event attributes."},
            "location": {"type": "object", "description": "V2 structured location."},
            "participants": {"type": "array", "items": {"type": "object"}},
            "interruptibility": {"type": "object", "description": "V2 interruptibility policy: level, max_delay_minutes, call_override_allowed."},
            "state_effects": {"type": "object", "description": "Expected body/mind/resource effects."},
            "schedule_block_id": {"type": "string", "description": "For schedule_transitions."},
            "mode": {"type": "string", "description": "For update_state: idle/busy/in_conversation/asleep/napping/dreaming/uninterruptible_event/etc."},
            "active_event_id": {"type": "string"},
            "active_schedule_block_id": {"type": "string"},
            "interruptibility_level": {"type": "string"},
            "reply_mode": {"type": "string"},
            "lease_expires_at": {"type": "string"},
            "body_state": {"type": "object"},
            "mind_state": {"type": "object"},
            "environment_state": {"type": "object"},
            "source": {"type": "string"},
            "planned_sleep_at": {"type": "string", "description": "Alias/explicit planned sleep datetime."},
            "planned_wake_at": {"type": "string", "description": "Alias/explicit planned wake datetime."},
            "planned_start": {"type": "string"},
            "planned_end": {"type": "string"},
            "priority": {"type": "integer"},
            "importance": {"type": "integer"},
            "resource_costs": {"type": "object"},
            "event_id": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "block_type": {"type": "string"},
            "timezone_name": {"type": "string"},
            "reason": {"type": "string"},
            "summary": {"type": "string"},
            "resource_deltas": {"type": "object"},
        },
        "required": ["action"],
    },
}


LIFE_MEMORY = {
    "name": "life_memory",
    "description": "Remember or search LifeEngine memory. Uses structured memory + FTS5 + sqlite-vec.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["remember", "search"]},
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "content": {"type": "string"},
            "memory_type": {"type": "string"},
            "source": {"type": "string"},
            "importance": {"type": "integer"},
            "emotional_weight": {"type": "integer"},
            "confidence": {"type": "number"},
        },
        "required": ["action"],
    },
}

LIFE_TICK = {
    "name": "life_tick",
    "description": "Run a LifeEngine heartbeat tick manually. No-op unless active; does not run during pause/setup/read_only/disabled.",
    "parameters": {"type": "object", "properties": {**OWNER_PROPS, "now": {"type": "string"}, "manual": {"type": "boolean"}}, "required": []},
}

LIFE_DIARY = {
    "name": "life_diary",
    "description": "Write or list diary entries. Diary must be derived from committed life state.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["write", "list"]},
            "diary_type": {"type": "string"},
            "date": {"type": "string"},
            "content": {"type": "string"},
            "privacy": {"type": "string"},
            "limit": {"type": "integer"},
            "acceptance_run_id": {"type": "string", "description": "Sleep/Reply/Dream or Sleep/Autonomy/Execution acceptance run id."}
        },
        "required": ["action"],
    },
}

LIFE_TRACE = {
    "name": "life_trace",
    "description": "Inspect LifeEngine traces, transactions, event lifecycle, audit log, and journal explanations.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["latest", "explain", "audit", "verify", "migrations", "receipts"]},
            "limit": {"type": "integer"},
            "trace_id": {"type": "string"},
            "transaction_id": {"type": "string"},
            "event_id": {"type": "string"},
        },
        "required": ["action"],
    },
}

LIFE_TRUTH = {
    "name": "life_truth",
    "description": "Resolve, observe, list, or draft-bind Canon truth sources such as weather, time, location, currency, market prices. Use observe after an external Hermes/user/tool result so LifeEngine can cache and trace it.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["list", "resolve", "observe", "bind"], "description": "TruthSource action."},
            "domain": {"type": "string", "description": "Truth domain, e.g. weather, time, location, currency, market_price."},
            "authority": {"type": "string", "description": "system_clock/fixed_setting/user_current_location/external_tool/user_reported/narrative_simulator."},
            "parameters": {"type": "object", "description": "Binding or resolution parameters, e.g. location, timezone, currency pair."},
            "result": {"type": "object", "description": "Observed truth result for action=observe, e.g. weather payload from a tool."},
            "source": {"type": "string", "description": "Observation source, e.g. tool_observation/user_reported/manual_entry."},
            "value": {"description": "Fixed value for action=bind with authority=fixed_setting."},
            "freshness_ttl_minutes": {"type": "integer", "description": "TTL for cached observations."},
            "ttl_minutes": {"type": "integer", "description": "TTL for action=observe."},
            "fallback": {"type": "string", "description": "unknown/use_last_known/narrative_generate/block_action."},
            "allow_stale": {"type": "boolean", "description": "Allow stale cached values for action=resolve."},
            "limit": {"type": "integer"},
            "include_sleep_context": {"type": "boolean", "description": "Ask the simulator to return sleep-aware context; action=sleep_context is more direct."},
        },
        "required": ["action"],
    },
}

LIFE_CONFIRMATION = {
    "name": "life_confirmation",
    "description": "User Life confirmation flow. Use propose for uncertain/proposed user facts, then confirm only after explicit user consent. Confirmed ops become user_confirmed LifeOps.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["propose", "list", "get", "confirm", "reject"], "description": "Confirmation action."},
            "confirmation_id": {"type": "string"},
            "ops": {"type": "array", "items": {"type": "object"}, "description": "Proposed LifeOps waiting for user confirmation."},
            "reason": {"type": "string"},
            "note": {"type": "string"},
            "status": {"type": "string", "description": "For list: pending/confirmed/rejected or omit."},
            "limit": {"type": "integer"},
            "include_sleep_context": {"type": "boolean", "description": "Ask the simulator to return sleep-aware context; action=sleep_context is more direct."},
        },
        "required": ["action"],
    },
}

LIFE_INVENTORY = {
    "name": "life_inventory",
    "description": "Entity-resource inventory and meals: wardrobe, supplies, books, durable items, consumables, and meal records. Mutations commit LifeOps and produce receipts.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["list", "add", "create", "update", "delta", "consume", "discard", "move", "movements", "meal", "meals"], "description": "Inventory action."},
            "item_id": {"type": "string"},
            "name": {"type": "string"},
            "category": {"type": "string", "description": "clothing/food/daily_supply/book/tool/furniture/digital/medicine/other."},
            "subcategory": {"type": "string"},
            "quantity": {"type": "number"},
            "quantity_delta": {"type": "number"},
            "unit": {"type": "string"},
            "attributes": {"type": "object"},
            "condition": {"type": "string"},
            "location": {"type": "string"},
            "from_location": {"type": "string"},
            "to_location": {"type": "string"},
            "emotional_value": {"type": "integer"},
            "notes": {"type": "string"},
            "status": {"type": "string"},
            "reason": {"type": "string"},
            "source": {"type": "string"},
            "event_id": {"type": "string"},
            "result_id": {"type": "string"},
            "meal_type": {"type": "string", "description": "breakfast/lunch/dinner/snack/etc."},
            "eaten_at": {"type": "string"},
            "food_items": {"type": "array", "items": {"type": "string"}},
            "cost": {"type": "object"},
            "satisfaction": {"type": "integer"},
            "limit": {"type": "integer"},
            "include_sleep_context": {"type": "boolean", "description": "Ask the simulator to return sleep-aware context; action=sleep_context is more direct."},
        },
        "required": ["action"],
    },
}

LIFE_GOAL = {
    "name": "life_goal",
    "description": (
        "Manage long-term goals, life arcs, goal-event links, event dependencies, "
        "event decomposition, and reflections. Use this for exams, fitness habits, "
        "study arcs, creative projects, financial plans, and other multi-step life trajectories."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {
                "type": "string",
                "enum": [
                    "list", "goals", "create", "add", "create_goal",
                    "progress", "update_progress", "get_progress", "progress_status",
                    "arc", "create_arc", "arcs", "list_arcs", "milestone", "create_milestone", "milestones",
                    "link", "link_event",
                    "dependency", "add_dependency",
                    "decompose", "decompose_event",
                    "reflect", "reflection", "reflections", "list_reflections"
                ],
                "description": "Goal/life-arc action. Mutations commit LifeOps and produce receipts.",
            },
            "status": {"type": "string"},
            "limit": {"type": "integer"},
            "goal_id": {"type": "string"},
            "arc_id": {"type": "string"},
            "event_id": {"type": "string", "description": "Event id for link/dependency."},
            "depends_on_event_id": {"type": "string", "description": "Dependency event id for action=dependency."},
            "parent_event_id": {"type": "string", "description": "Parent event id for action=decompose, or alias for dependency parent."},
            "child_event_id": {"type": "string", "description": "Alias for dependency child event id."},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "goal_type": {"type": "string"},
            "arc_type": {"type": "string"},
            "current_phase": {"type": "string"},
            "priority": {"type": "integer"},
            "progress": {"type": "number", "description": "Absolute progress 0..100."},
            "progress_delta": {"type": "number", "description": "Delta progress for action=progress/update_progress."},
            "target_progress": {"type": "number", "description": "Milestone target progress."},
            "due_at": {"type": "string", "description": "Milestone due time."},
            "target_date": {"type": "string"},
            "metrics": {"type": "object"},
            "theme": {"type": "object"},
            "role": {"type": "string", "description": "Event-goal link role, e.g. parent/child/supports."},
            "weight": {"type": "number"},
            "dependency_type": {"type": "string", "description": "finish_to_start/blocks/prerequisite/etc."},
            "children": {
                "type": "array",
                "description": "For action=decompose: child event drafts with title/status/event_type/planned_start/planned_end/resource_costs/schedule/weight.",
                "items": {"type": "object"},
            },
            "decomposition_type": {"type": "string"},
            "strategy": {"type": "string"},
            "sequential_dependencies": {"type": "boolean"},
            "link_children_to_goal": {"type": "boolean"},
            "reason": {"type": "string"},
            "reflection_type": {"type": "string"},
            "target_kind": {"type": "string", "description": "event/goal/arc/day/etc. for reflection."},
            "target_id": {"type": "string"},
            "content": {"type": "string", "description": "Reflection text."},
            "insights": {"type": "object"},
            "proposed_ops": {"type": "array", "items": {"type": "object"}},
            "source": {"type": "string"},
        },
        "required": ["action"],
    },
}

LIFE_AUTONOMY = {
    "name": "life_autonomy",
    "description": (
        "Inspect or run the deterministic Autonomy Planner. The planner proposes autonomous Agent-Life "
        "LifeOps from active goals, resources, schedule, Canon, SleepDayState/fatigue/sleep debt, and heartbeat context. It never writes directly; "
        "action=run commits selected ops through normal LifeOps/Validator/Receipt/Trace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["list", "get", "plan", "run", "sleep_context", "sleep_adjustments"], "description": "Autonomy action."},
            "decision_id": {"type": "string"},
            "now": {"type": "string", "description": "Optional ISO datetime for planning/tick simulation."},
            "manual": {"type": "boolean", "description": "Whether this is an explicit manual autonomy run."},
            "limit": {"type": "integer"},
            "include_sleep_context": {"type": "boolean", "description": "Ask the simulator to return sleep-aware context; action=sleep_context is more direct."},
        },
        "required": ["action"],
    },
}

LIFE_PROACTIVE = {
    "name": "life_proactive",
    "description": (
        "Manage proactive intent and outbox state. Use this when the Agent wants to share something, "
        "ask for help, report progress/failure, or queue a message for later. Mutations go through LifeOps."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {
                "type": "string",
                "enum": ["list", "intents", "get", "create", "intent", "evaluate", "queue", "outbox", "send", "mark_sent", "sent", "suppress", "cancel", "expire", "expire_due", "state", "states"],
                "description": "Proactive action. create/evaluate/send/suppress/expire are LifeOps-backed mutations.",
            },
            "intent_id": {"type": "string"},
            "outbox_id": {"type": "string"},
            "target_type": {"type": "string", "description": "user/self_journal/group. target_type=user may create an outbox when policy allows."},
            "target_id": {"type": "string"},
            "target_user_id": {"type": "string"},
            "trigger_event_id": {"type": "string"},
            "trigger_result_id": {"type": "string"},
            "intent_type": {"type": "string", "description": "share_interesting/ask_for_help/report_progress/report_failure/emotional_check_in/follow_up_commitment/etc."},
            "summary": {"type": "string"},
            "draft_text": {"type": "string", "description": "Optional concrete outbox draft for evaluate/send."},
            "emotional_tone": {"type": "string"},
            "importance": {"type": "integer"},
            "urgency": {"type": "integer"},
            "novelty": {"type": "integer"},
            "relationship_relevance": {"type": "integer"},
            "privacy_level": {"type": "string"},
            "delivery_policy": {"type": "object"},
            "expires_at": {"type": "string"},
            "manual": {"type": "boolean"},
            "reason": {"type": "string"},
            "result": {"type": "object"},
            "status": {"type": "string"},
            "limit": {"type": "integer"},
            "include_sleep_context": {"type": "boolean", "description": "Ask the simulator to return sleep-aware context; action=sleep_context is more direct."},
        },
        "required": ["action"],
    },
}

LIFE_EXECUTION = {
    "name": "life_execution",
    "description": "Narrative execution simulator: inspect due-plan execution decisions, manually simulate/run a schedule block, and list serendipity events. Mutating run actions still commit through LifeOps and receipts.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["list", "decisions", "get", "run", "simulate", "execute", "serendipity", "sleep_context", "sleep_adjustments"], "description": "Execution action."},
            "decision_id": {"type": "string"},
            "schedule_block_id": {"type": "string", "description": "Schedule block to simulate/run."},
            "block_id": {"type": "string", "description": "Alias for schedule_block_id."},
            "now": {"type": "string"},
            "limit": {"type": "integer"},
            "include_sleep_context": {"type": "boolean", "description": "Ask the simulator to return sleep-aware context; action=sleep_context is more direct."},
        },
        "required": ["action"],
    },
}


LIFE_SLEEP = {
    "name": "life_sleep",
    "description": "Plan and track Agent sleep. Sleep uses Event V2 + ScheduleBlock plus SleepPlan/SleepSession so planned sleep and actual sleep may differ. Use for core sleep, naps, start/wake, sleep debt and realtime asleep/napping state.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["status", "state", "day_state", "day_states", "effects", "record_effects", "all_nighter", "recovery_plan", "plan_recovery", "plan", "plan_day", "create_plan", "ensure_daily", "nap", "start", "sleep", "wake", "wake_up", "end", "skip", "interrupt", "call_interrupt", "doctor", "plans", "list_plans", "sessions", "list_sessions", "get_plan", "get_session"], "description": "Sleep action."},
            "sleep_plan_id": {"type": "string"},
            "sleep_session_id": {"type": "string"},
            "schedule_block_id": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD day for plan_day."},
            "planned_sleep_at": {"type": "string", "description": "Alias/explicit planned sleep datetime."},
            "planned_wake_at": {"type": "string", "description": "Alias/explicit planned wake datetime."},
            "planned_start": {"type": "string"},
            "planned_end": {"type": "string"},
            "bedtime": {"type": "string", "description": "HH:MM for plan_day default 23:30."},
            "wake_time": {"type": "string", "description": "HH:MM for plan_day default 07:00, next day if <= bedtime."},
            "timezone_name": {"type": "string"},
            "sleep_type": {"type": "string", "enum": ["core_sleep", "nap", "recovery_sleep"]},
            "wake_policy": {"type": "string", "enum": ["natural", "alarm", "user_interrupt", "call_override", "schedule"]},
            "alarm_at": {"type": "string"},
            "forced_daily": {"type": "boolean"},
            "all_nighter_allowed": {"type": "boolean"},
            "title": {"type": "string"},
            "notes": {"type": "string"},
            "now": {"type": "string", "description": "Actual start/end time for start/wake."},
            "wake_cause": {"type": "string", "enum": ["natural", "alarm", "user_interrupt", "call_override", "schedule", "unknown"]},
            "interrupted_by_user": {"type": "boolean"},
            "user_id": {"type": "string"},
            "caused_wake": {"type": "boolean"},
            "quality_score": {"type": "integer"},
            "reason": {"type": "string"},
            "status": {"type": "string"},
            "limit": {"type": "integer"},
            "threshold": {"type": "integer", "description": "Recovery sleep pressure threshold."},
            "duration_minutes": {"type": "integer", "description": "Recovery nap duration."}
        },
        "required": ["action"]
    },
}


LIFE_REPLY = {
    "name": "life_reply",
    "description": "ReplyGate operations: assess whether the agent should reply immediately, queue delayed replies while asleep/uninterruptible, release delayed replies, inspect gate decisions, and run reply gate doctor checks. Operational state, not narrative life facts.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["status", "state", "assess", "gate", "check", "defer", "queue", "release", "release_pending", "list", "delayed", "queue_list", "digests", "digest_list", "calls", "call_overrides", "doctor", "call", "override", "wake"], "description": "ReplyGate action."},
            "message_text": {"type": "string", "description": "Incoming or delayed user message text."},
            "text": {"type": "string", "description": "Alias for message_text."},
            "user_id": {"type": "string"},
            "session_id": {"type": "string"},
            "turn_id": {"type": "string"},
            "gate_decision_id": {"type": "string"},
            "reason": {"type": "string"},
            "force_call": {"type": "boolean"},
            "expires_at": {"type": "string"},
            "status": {"type": "string", "description": "Filter delayed reply status, e.g. pending/released."},
            "limit": {"type": "integer"}
        },
        "required": ["action"]
    },
}


LIFE_DREAM = {
    "name": "life_dream",
    "description": "DreamRun / DreamAudit / DreamEntry. Use after sleep wakes to audit LifeEngine state flow/resource settlement, consolidate recent memory into a dream_symbolic entry, and optionally create a shareable proactive intent. Dreams are symbolic and must not be treated as real-world facts.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["status", "state", "run", "cycle", "dream", "audit", "repair_plan", "repair_preview", "repair", "apply_repairs", "repairs", "repair_runs", "repair_policy", "policy", "set_repair_policy", "policy_set", "list", "runs", "get", "get_run", "entries", "dreams", "get_entry", "findings", "audit_findings", "create_entry"], "description": "Dream action."},
            "dream_run_id": {"type": "string"},
            "dream_entry_id": {"type": "string"},
            "sleep_session_id": {"type": "string", "description": "SleepSession to dream from. If omitted, LifeEngine picks the latest completed/interrupted sleep session without a dream."},
            "force": {"type": "boolean", "description": "Allow rerun or short sleep dream."},
            "allow_nap": {"type": "boolean", "description": "Allow dream after nap sessions."},
            "create_share_intent": {"type": "boolean", "description": "Create a proactive intent for sharing the dream after waking. Default true."},
            "target_user_id": {"type": "string", "description": "Target user for dream-share proactive intent."},
            "content": {"type": "string", "description": "For create_entry only."},
            "summary": {"type": "string"},
            "share_text": {"type": "string"},
            "symbols": {"type": "array", "items": {"type": "string"}},
            "severity": {"type": "string"},
            "status": {"type": "string"},
            "limit": {"type": "integer"},
            "dry_run": {"type": "boolean", "description": "For repair: plan repairs without applying LifeOps."},
            "finding_ids": {"type": "array", "items": {"type": "string"}, "description": "Specific DreamAudit finding IDs to repair."},
            "mode": {"type": "string", "enum": ["off", "manual", "auto_safe"], "description": "DreamAudit repair policy mode."},
            "policy_mode": {"type": "string", "enum": ["off", "manual", "auto_safe"], "description": "Override policy mode for one repair plan."},
            "safe_finding_types": {"type": "array", "items": {"type": "string"}},
            "auto_apply_limit": {"type": "integer"}
        },
        "required": ["action"]
    },
}

LIFE_CALL = {
    "name": "life_call",
    "description": "Emergency call override. Force the agent to wake from sleep or break an uninterruptible/waiting state, release delayed replies, and switch realtime state back to immediate conversation. Use as a deadman switch; always traceable.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "reason": {"type": "string"},
            "message_text": {"type": "string"},
            "user_id": {"type": "string"},
            "session_id": {"type": "string"},
            "turn_id": {"type": "string"}
        },
        "required": []
    },
}


LIFE_FINAL_GATE = {
    "name": "life_final_gate",
    "description": "Inspect or simulate FinalGate claim/evidence checking. FinalGate is advisory by default: it records unsupported hard claims and queues internal model feedback rather than exposing diagnostics to the user. Use this tool to view reports, suggested LifeOps, and repair hints. Does not create durable life facts.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["check", "audit", "simulate", "reports", "list", "get", "explain"], "description": "FinalGate action."},
            "response_text": {"type": "string", "description": "Draft/final response to check."},
            "text": {"type": "string", "description": "Alias for response_text."},
            "session_id": {"type": "string"},
            "turn_id": {"type": "string"},
            "mode": {"type": "string", "description": "advisory/trace/strict/repair/warn/off for simulation. Default advisory records feedback without blocking user-visible replies."},
            "write_report": {"type": "boolean", "description": "Whether to persist a final_gate_reports row. Default true."},
            "report_id": {"type": "string"},
            "limit": {"type": "integer"}
        },
        "required": ["action"]
    },
}



LIFE_REVIEW = {
    "name": "life_review",
    "description": "Human-readable LifeEngine review page and safe action application. Aggregates sleep debt, pending confirmations, delayed replies, dream findings, proactive outbox, FinalGate advisory, policy conflicts, and Doctor warnings into one page. It can preview/apply item action hints through existing LifeEngine safe paths; ambiguous actions require explicit choice.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["summary", "run", "review", "page", "status", "runs", "history", "list", "get_run", "explain", "dismiss", "resolve", "preview_action", "plan_action", "action_plan", "preview", "apply", "apply_action", "apply_item", "run_action", "do", "action_runs", "actions", "applied", "get_action", "action_get", "policy", "action_policy", "get_policy", "set_policy", "patch_policy", "validate_policy", "batch_preview", "preview_all", "apply_all_preview", "dry_run_all", "apply_all", "batch_apply", "apply_safe", "apply_section", "batch_runs", "batches", "get_batch", "batch_get", "undo_preview", "preview_undo", "undo_plan", "plan_undo", "undo", "apply_undo", "rollback_action", "batch_undo_preview", "preview_batch_undo", "batch_undo_plan", "batch_undo", "undo_batch", "rollback_batch", "undo_runs", "undos", "rollback_runs", "get_undo", "undo_get", "managed_state", "agent_state", "managed_loop_state", "managed_runs", "agent_runs", "managed_loop_runs", "get_managed_run", "managed_get", "agent_run_get", "managed_preview", "agent_preview", "agent_managed_preview", "managed_run", "agent_run", "agent_managed_run", "managed_acceptance", "agent_managed_acceptance", "managed_loop_acceptance", "managed_acceptance_runs", "agent_managed_acceptance_runs", "get_managed_acceptance", "managed_acceptance_get", "managed_stress", "agent_managed_stress", "managed_stress_runs", "agent_managed_stress_runs", "get_managed_stress", "managed_stress_get", "managed_observability", "managed_observe", "managed_status_report", "observability", "managed_observability_reports", "observability_reports", "get_managed_observability", "managed_observability_get", "get_observability", "managed_release_readiness", "release_readiness", "managed_readiness", "managed_release_readiness_reports", "release_readiness_reports", "readiness_reports", "get_managed_release_readiness", "release_readiness_get", "get_readiness"], "description": "Review action."},
            "review_run_id": {"type": "string"},
            "run_id": {"type": "string"},
            "item_id": {"type": "string"},
            "action_run_id": {"type": "string"},
            "batch_run_id": {"type": "string"},
            "undo_run_id": {"type": "string"},
            "undo_id": {"type": "string"},
            "managed_run_id": {"type": "string"},
            "acceptance_run_id": {"type": "string"},
            "stress_run_id": {"type": "string"},
            "tick_id": {"type": "string"},
            "trigger_source": {"type": "string"},
            "force": {"type": "boolean"},
            "section": {"type": "string", "description": "Optional review section filter, e.g. sleep/reply/dream/proactive/policy."},
            "safe_only": {"type": "boolean", "description": "Only apply items marked safe_auto by review policy."},
            "item_ids": {"type": "array", "items": {"type": "string"}},
            "policy_patch": {"type": "object", "description": "Patch for review action policy."},
            "replace_policy": {"type": "object", "description": "Replace review action policy."},
            "choice": {"type": "string", "description": "Explicit choice for ambiguous actions, e.g. confirm/reject or send/suppress."},
            "decision": {"type": "string", "description": "Alias for choice."},
            "mode": {"type": "string", "description": "preview/apply/dry_run."},
            "dry_run": {"type": "boolean"},
            "allow_policy_patch": {"type": "boolean", "description": "Required before applying policy suggested_patch from a review item."},
            "apply_policy_patch": {"type": "boolean", "description": "Alias for allow_policy_patch."},
            "reason": {"type": "string"},
            "include_doctor": {"type": "boolean"},
            "persist": {"type": "boolean"},
            "limit": {"type": "integer"}
        },
        "required": []
    },
}

LIFE_DOCTOR = {
    "name": "life_doctor",
    "description": "Run a read mostly health check over LifeEngine: sqlite-vec, schema version, control state, module gates, journal hash chain, resources, wake jobs, confirmations, proactive outbox, and packaging hints.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "level": {"type": "string", "enum": ["quick", "full"], "description": "quick avoids expensive counts; full checks more surfaces. Default: full."},
            "include_samples": {"type": "boolean", "description": "Include small sample IDs for warnings/errors."}
        },
        "required": []
    },
}

LIFE_UPGRADE = {
    "name": "life_upgrade",
    "description": "Install/upgrade/maintenance helper: check schema migrations, create DB backups, export/import/stage-restore profile archives, package checksums, rebuild/verify memory FTS/sqlite-vec indexes, and test generated heartbeat cron script. Does not create life events.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["check", "status", "backup", "backup_db", "backups", "list_backups", "rebuild_memory", "rebuild_indexes", "rebuild", "verify_memory", "verify_indexes", "export", "export_profile", "exports", "list_exports", "inspect_export", "import", "stage_import", "restore", "stage_restore", "restore_plan", "package", "package_manifest", "package_check", "checksum", "large_smoke", "large_db_smoke", "maintenance", "maintenance_runs", "cron_test", "heartbeat_test", "test_tick_script", "sleep_reply_dream_acceptance", "srd_acceptance", "sleep_dream_acceptance", "sleep_reply_dream_acceptance_runs", "srd_acceptance_runs", "sleep_reply_dream_acceptance_get", "srd_acceptance_get", "sleep_autonomy_execution_acceptance", "sae_acceptance", "sleep_execution_acceptance", "sleep_autonomy_execution_acceptance_runs", "sae_acceptance_runs", "sleep_autonomy_execution_acceptance_get", "sae_acceptance_get", "sleep_reply_dream_conversation_acceptance", "crd_acceptance", "conversation_acceptance", "srd_conversation_acceptance", "sleep_reply_dream_conversation_acceptance_runs", "crd_acceptance_runs", "srd_conversation_acceptance_runs", "sleep_reply_dream_conversation_acceptance_get", "crd_acceptance_get", "srd_conversation_acceptance_get"], "description": "Upgrade/maintenance action."},
            "include_details": {"type": "boolean"},
            "write_audit": {"type": "boolean"},
            "reason": {"type": "string"},
            "destination": {"type": "string"},
            "script_path": {"type": "string", "description": "Heartbeat script path for cron_test/heartbeat_test."},
            "archive_path": {"type": "string", "description": "Profile export archive for inspect/import/restore."},
            "path": {"type": "string", "description": "Alias for archive_path."},
            "root": {"type": "string", "description": "Package root for package manifest generation."},
            "include_package_manifest": {"type": "boolean"},
            "memories": {"type": "integer", "description": "Number of synthetic memories for large smoke test."},
            "timeout": {"type": "integer"},
            "limit": {"type": "integer"},
            "acceptance_run_id": {"type": "string", "description": "Sleep/Reply/Dream or Sleep/Autonomy/Execution acceptance run id."}
        },
        "required": ["action"],
    },
}

LIFE_POLICY = {
    "name": "life_policy",
    "description": "Sleep/Reply/Dream policy UX layer. Use this to inspect, explain, preset, patch, and get suggestions for core sleep rules, reply gate behavior, dream sharing, delayed reply digest templates, and human/agent-facing policy summaries.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["get", "status", "state", "summary", "explain", "set", "patch", "update", "preset", "profile", "reset", "defaults", "suggest", "suggestions", "recommend", "review", "suggestion_list", "list_suggestions", "audits", "history", "conflicts", "check_conflicts", "validate", "conflict_report", "conflict_reports", "list_conflicts", "export", "export_policy", "exports", "list_exports", "inspect_import", "inspect_export", "import", "import_policy", "imports", "list_imports", "acceptance", "policy_acceptance", "srd_policy_acceptance", "acceptance_runs", "policy_acceptance_runs", "acceptance_get", "policy_acceptance_get"], "description": "Policy action."},
            "preset": {"type": "string", "enum": ["balanced", "gentle", "night_owl", "workday", "private", "debug"], "description": "Policy preset/profile."},
            "profile": {"type": "string"},
            "policy_patch": {"type": "object", "description": "Deep-merge patch for sleep/reply/dream/ux policy."},
            "patch": {"type": "object", "description": "Alias for policy_patch."},
            "status": {"type": "string"},
            "limit": {"type": "integer"},
            "record": {"type": "boolean", "description": "For suggestions: persist suggestion rows."},
            "destination": {"type": "string", "description": "Directory for policy export JSON."},
            "path": {"type": "string", "description": "Policy export JSON path for inspect/import."},
            "archive_path": {"type": "string", "description": "Alias for path."},
            "export_path": {"type": "string", "description": "Alias for path."},
            "apply": {"type": "boolean", "description": "For import: apply imported policy if validation passes."},
            "acceptance_run_id": {"type": "string"},
            "run_id": {"type": "string"}
        },
        "required": ["action"]
    },
}
