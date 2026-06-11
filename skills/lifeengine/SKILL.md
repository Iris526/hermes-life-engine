# LifeEngine Skill

Use LifeEngine when the conversation concerns the agent's own life, user life records, resources, plans, schedule, diary, memory, world/persona settings, truth sources, inventory, meals, long-term goals, life arcs, autonomy, or proactive communication.

Core rules:

1. During setup/paused_setup, only write CanonDraft settings. Do not create life events, resource ledger entries, memories, diary entries, inventory records, goals, or arcs.
2. During paused/read_only/disabled, do not mutate life state.
3. Before final-answering any new durable agent-life fact, call `life_commit` or a convenience tool that routes through LifeOps.
4. Use `life_resource` for scalar resources. Money is only one resource; time, energy, mood, focus, inspiration, skills, relationship state, and permissions may also be resources.
5. Use `life_inventory` for entity resources: wardrobe items, supplies, books, durable possessions, consumables, and meal records.
6. Use `life_goal` for long-term goals, life arcs, milestones, event decomposition, dependencies, progress, and reflection.
7. Use `life_truth` to resolve or observe Canon-bound external facts before planning around weather, time, location, currency, market prices, or other truth domains.
8. Use `life_autonomy` for explicit autonomy planning; heartbeat may run autonomy only when the module gate permits it.
9. Use `life_proactive` when the agent wants to share, ask for help, report progress/failure, or follow up. Do not directly claim a proactive message was sent unless an outbox row was marked sent.
10. Use `life_confirmation` before writing uncertain user-life facts. Do not invent user-life facts.
11. Use `life_trace` to explain why a life state changed. Every committed operation should be traceable through transaction, op, receipt, journal, and trace spans.
12. Agent Life and User Life use the same schemas but different truth policy. Agent self-life may use narrative reality when Canon allows it; User Life requires user/tool/file/calendar/manual evidence.

Typical setup flow:

- `life_control(action="setup")`
- Collect user settings with `life_setup(text="...")` or setup-mode natural language.
- `life_commit(commit_type="canon")`
- `life_control(action="resume")`

Typical event flow:

- Create plan: `life_commit(commit_type="ops", ops=[{"type":"CREATE_EVENT","payload":{...}}])`
- Schedule it: `life_commit(... CREATE_SCHEDULE_BLOCK ...)`
- Complete it: `life_commit(... COMPLETE_EVENT / RESOURCE_DELTA / CREATE_MEMORY ...)`
- Explain it: `life_trace(action="explain", event_id="event_...")`

Typical long-term goal flow:

- Create a goal: `life_goal(action="create", title="准备七月考试", goal_type="study")`
- Create or link a life arc: `life_goal(action="arc", title="考试准备生活弧线", arc_type="study")`
- Decompose a large event: `life_goal(action="decompose", parent_event_id="event_...", goal_id="goal_...", children=[...])`
- Complete child events through `life_event`.
- Compute and commit progress: `life_goal(action="progress", goal_id="goal_...")`, then `life_goal(action="update_progress", goal_id="goal_...")`.
- Reflect: `life_goal(action="reflect", content="...", related_goal_id="goal_...", create_memory_entry=true)`.


Typical proactive flow:

- Create an intent: `life_proactive(action="create", summary="...", intent_type="report_progress")`
- Evaluate policy: `life_proactive(action="evaluate", intent_id="proactive_...")`
- If pending_only, mention naturally next turn when appropriate.
- If outbox queued and an adapter actually delivers it, mark sent: `life_proactive(action="send", outbox_id="outbox_...", result={...})`.
- Suppress or expire stale/private items with `life_proactive(action="suppress"|"expire")`.


## Sleep / Reply / Dream policy

Use `life_policy` to inspect and tune the high-level policy for sleep timing, reply-gate behavior, delayed-reply digest templates, dream sharing, and DreamAudit repair preferences. Prefer `/life policy` for humans and `life_policy` for Agent self-management.
