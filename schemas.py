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
            "action": {"type": "string", "enum": ["list", "create", "schedule", "transition", "complete"], "description": "Event action."},
            "status": {"type": "string"},
            "limit": {"type": "integer"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "event_type": {"type": "string"},
            "source": {"type": "string"},
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
        "LifeOps from active goals, resources, schedule, Canon, and heartbeat context. It never writes directly; "
        "action=run commits selected ops through normal LifeOps/Validator/Receipt/Trace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["list", "get", "plan", "run"], "description": "Autonomy action."},
            "decision_id": {"type": "string"},
            "now": {"type": "string", "description": "Optional ISO datetime for planning/tick simulation."},
            "manual": {"type": "boolean", "description": "Whether this is an explicit manual autonomy run."},
            "limit": {"type": "integer"},
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
            "action": {"type": "string", "enum": ["list", "decisions", "get", "run", "simulate", "execute", "serendipity"], "description": "Execution action."},
            "decision_id": {"type": "string"},
            "schedule_block_id": {"type": "string", "description": "Schedule block to simulate/run."},
            "block_id": {"type": "string", "description": "Alias for schedule_block_id."},
            "now": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    },
}


LIFE_FINAL_GATE = {
    "name": "life_final_gate",
    "description": "Inspect or simulate FinalGate claim/evidence checking. Use it to see why a final answer would be blocked, view suggested LifeOps, or retrieve past FinalGate reports. Does not create durable life facts.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["check", "audit", "simulate", "reports", "list", "get", "explain"], "description": "FinalGate action."},
            "response_text": {"type": "string", "description": "Draft/final response to check."},
            "text": {"type": "string", "description": "Alias for response_text."},
            "session_id": {"type": "string"},
            "turn_id": {"type": "string"},
            "mode": {"type": "string", "description": "strict/repair/trace/off for simulation."},
            "write_report": {"type": "boolean", "description": "Whether to persist a final_gate_reports row. Default true."},
            "report_id": {"type": "string"},
            "limit": {"type": "integer"}
        },
        "required": ["action"]
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
    "description": "Install/upgrade/maintenance helper: check schema migrations, create DB backups, export/import/stage-restore profile archives, package checksums, rebuild/verify memory FTS/sqlite-vec indexes, test generated heartbeat cron script, run concurrency/stress smoke tests, and run v1.0-rc acceptance scenario reports. Does not create life events for the real owner.",
    "parameters": {
        "type": "object",
        "properties": {
            **OWNER_PROPS,
            "action": {"type": "string", "enum": ["check", "status", "backup", "backup_db", "backups", "list_backups", "rebuild_memory", "rebuild_indexes", "rebuild", "verify_memory", "verify_indexes", "export", "export_profile", "exports", "list_exports", "inspect_export", "import", "stage_import", "restore", "stage_restore", "restore_plan", "package", "package_manifest", "package_check", "checksum", "large_smoke", "large_db_smoke", "maintenance", "maintenance_runs", "cron_test", "heartbeat_test", "test_tick_script", "concurrency_smoke", "parallel_commit_smoke", "schedule_overlap_smoke", "parallel_schedule_overlap", "heartbeat_idempotency_smoke", "parallel_heartbeat_smoke", "lifeops_stress", "stress_smoke", "concurrency_runs", "stress_runs", "integration_check", "integration_smoke", "integration_acceptance", "hermes_integration", "surface", "public_surface", "api_surface", "api_freeze", "api_freeze_snapshot", "freeze_snapshot", "api_freeze_status", "api_freezes", "freeze_status", "freeze_snapshots", "mandatory_gate_patch", "core_patch", "core_patch_draft", "core_patches", "core_patch_drafts", "patches", "release_readiness", "release_check", "acceptance", "acceptance_suite", "acceptance_scenarios", "v1_rc_check", "v1_rc_acceptance", "acceptance_reports", "list_acceptance_reports", "acceptance_report", "get_acceptance_report", "acceptance_runs", "acceptance_scenario_runs", "v1_rc_checklists", "v1_rc_checklist"], "description": "Upgrade/maintenance action."},
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
            "workers": {"type": "integer", "description": "Worker count for concurrency smoke tests."},
            "items": {"type": "integer", "description": "Item count for LifeOps stress smoke tests."},
            "events": {"type": "integer", "description": "Alias for items in stress smoke tests."},
            "timeout": {"type": "integer"},
            "limit": {"type": "integer"},
            "snapshot_id": {"type": "string", "description": "API freeze snapshot ID for api_freeze_status."},
            "patch_name": {"type": "string", "description": "Core patch draft name, default mandatory_final_gate."},
            "status": {"type": "string", "description": "Snapshot status for api_freeze."},
            "report_path": {"type": "string", "description": "Output Markdown path for acceptance reports."},
            "report_id": {"type": "string", "description": "Acceptance report ID for get_acceptance_report."},
            "acceptance_run_id": {"type": "string", "description": "Acceptance run ID for scenario listing."},
        },
        "required": ["action"],
    },
}
