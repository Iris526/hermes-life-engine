# LifeEngine v0.12.10 — Prompt / Context Slimming and Progressive Disclosure

## Problem

LifeEngine has grown many modules: Canon, schedule, events, resources, sleep, dreams, reply gate, collections, behavior mapping, review, and WebUI. Injecting all state into every turn makes prompts large and makes the system look prompt-driven. That is the wrong direction.

## Principle

LifeEngine correctness must be enforced by code, not prompt text.

- durable facts: LifeOps + Validator + Transaction + CommitReceipt
- schedules: ScheduleBlock + WakeJob + Execution Simulator
- resources: Resource Ledger + Reconcile
- collections: Collection/Item/Asset rules + Resolver
- private behavior sources: code-level redaction + behavior mapping
- final claims: FinalGate advisory + receipt/canonical evidence
- trace: life_journal + trace_runs + audit

Prompt context is only a turn-local user interface for the model.

## New Mechanism

v0.12.10 introduces `context_policy.py`.

It provides:

- context modes: `micro`, `slim`, `balanced`, `debug`
- character budget cap
- intent/domain inference from the current user message
- progressive disclosure: inject only relevant sections
- tool map: tell the Agent which tool to call for details
- `prompt_context_runs` trace table

## Default

Default mode is `slim` with a 5200 character budget.

Always injected:

- engine state
- owner scope
- minimal realtime state
- required setting status
- next schedule summary
- small resource snapshot
- small recent event snapshot
- tool map

Conditionally injected:

- schedule details only for schedule-like turns
- closet/collection hints only for dressing/closet turns
- behavior mapping hints only for behavior/source turns
- dream state only for dream turns
- reply/sleep state only for sleep/reply turns
- review hints only for review turns

## Commands

Human-friendly:

```text
/life context
/life context runs
/life context set micro 2400
/life context set slim
/life context set balanced
/life context set debug
```

Agent tool:

```json
{"action":"policy"}
{"action":"runs","limit":10}
{"action":"set","mode":"slim","budget_chars":5200}
```

## Non-goals

This does not weaken LifeEngine rules. It removes rule spam from prompts and moves the burden back to code. If the Agent needs detail, it must call a LifeEngine tool.
