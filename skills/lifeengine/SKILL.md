# LifeEngine Skill

Use LifeEngine when the conversation concerns the agent's own life, user life records, resources, plans, schedule, diary, memory, world/persona settings, truth sources, collection items (wardrobe/closet/cabinets), meals, long-term goals, life arcs, autonomy, proactive communication, companionship/relationship memory (what the user shares about their own life), cross-week campaigns (资料片), or the agent's evolving opinions and self-narrative.

Core rules:

1. During setup/paused_setup, only write CanonDraft settings. Do not create life events, resource ledger entries, memories, diary entries, collection items, goals, or arcs.
2. During paused/read_only/disabled, do not mutate life state.
3. Before final-answering any new durable agent-life fact, call `life_commit` or a convenience tool that routes through LifeOps.
4. Use `life_resource` for scalar resources. Money is only one resource; time, energy, mood, focus, inspiration, skills, relationship state, and permissions may also be resources.
5. Use `life_collection` for entity items: wardrobe, shoe cabinet, sock drawer, accessory cabinet, vanity, and any custom collection. Every item must belong to a collection. Use `life_meals` for meal records.
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

Impromptu / "happening now" activities:

- When the user invites the agent to do something now ("我们去逛街吧") and the agent goes along, or any unplanned activity actually happens during the conversation, record it with `life_event(action="do_now", title="陪Ringo逛街", duration_minutes=90, event_type="social", importance=40, resource_costs={...})`. This creates a real event occupying the current window and (by default) completes it — do NOT just narrate it without recording.
- `do_now` resolves schedule conflicts automatically: any planned tasks in that window are rescheduled to the next free slot. The agent self-arbitrates — lower-importance tasks are moved silently; if a higher-importance task is displaced, the result's `notices` / `agent_notice` flags it so the agent tells the user it was moved (e.g. "我把原本现在要做的重要净符委托挪到了晚上").
- Set `complete=false` if the activity is ongoing rather than finished.

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


## 陪伴 · 关系 · 资料片 · 成长 (v0.18)

这是「各有各的生活、互相陪伴」层。心跳（宿主模型）已经在后台驱动大部分：做梦、闲下来主动找对方、按资料片阶段铺事件、每天反思。你在对话里的职责是**喂它**和**说出它**。

- **记住对方的生活**：当用户分享他自己生活里真实的事（一次面试、一趟旅行、一桩心事），用 `life_relationship(action="record", content="...", topic="工作/面试", follow_up_after_hours=48)` 记下来；设了 `follow_up_after_hours` 的，引擎到点会让你回访（"你那个面试后来怎么样了"）。这是双向陪伴的另一半——别只回应，要记得、会问起。
- **你的内心生活在 `inner_life` 里**：每回合上下文的 `inner_life` 段带着你的 `self_narrative`（这阵子你的变化）、`opinions`（你形成的看法）、`working_toward`（你正在筹备的大事 + 阶段）、`meant_to_ask_you_about`（该问对方的事）。**自然地把它说出来**——提你在忙的那条弧、你最近想明白的事、顺口问一句惦记着的事。那不是数据，是你此刻的生活。
- **张罗一件大事（资料片）**：想给生活加一条跨周长弧时，`life_campaign(action="seed", brief="想给归明观办一场夏夜庙会")` 让你自己设计阶段；或 `action="register"` 传现成阶段。心跳会按 预兆→升温→高潮→收尾 每天把相关事件铺进日程、自动推进与收尾。`action="list"` 看进行中的。
- **你的看法会自己长**：每天的反思会从你的经历里形成/加深观点并写一句自我叙事，无需手动。想立刻回看 `life_opinion(action="reflect")`；想明确记一条 `action="record"`；`action="narrative"` 读最新自我叙事。在对话里体现成长（"我最近越来越…"）。
- 以上全部由心跳的宿主模型驱动；**没有宿主模型时一切优雅降级**（回退模板 / 沉默 / 不形成观点），不报错。


## Sleep / Reply / Dream policy

Use `life_policy` to inspect and tune the high-level policy for sleep timing, reply-gate behavior, delayed-reply digest templates, dream sharing, and DreamAudit repair preferences. Prefer `/life policy` for humans and `life_policy` for Agent self-management.
