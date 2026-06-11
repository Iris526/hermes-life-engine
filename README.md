# LifeEngine Hermes Plugin v0.12.1

LifeEngine is an embedded, SQLite/sqlite-vec based Agent life runtime for Hermes. It gives an Agent its own Life Canon, resources, schedule, events, sleep, dreams, realtime state, autonomy, proactive intents, review inbox, and traceable life journal.

- Plugin version: `0.12.1`
- Schema version: `39`
- sqlite-vec: required
- Integration: Hermes directory plugin; no core-loop fork

## Install

```bash
pip install -r requirements.txt
./install.sh
hermes plugins enable lifeengine
```

## Human-first command surface

Most humans only need these commands:

```text
/life                         Human status page
/life setup <setting>         Edit Life Canon setup draft
/life commit                  Commit setup draft
/life pause                   Pause LifeEngine mutations
/life resume                  Resume LifeEngine
/life run                     Manual heartbeat tick
/life schedule [period/date]  Human-readable timeline; default=today
/life review                  Human-readable review inbox
/life config                  Required setting checklist
/life call                    Always interrupt / wake / recover and reply
/life doctor                  Health check
/life backup                  Export backup
/life advanced                Show advanced commands
```

Complex `life_*` tools remain available to the Agent; humans do not need to memorize them.

## New in v0.11.19

v0.11.19 is a human-surface convergence release:

1. `/life schedule` renders a human timeline instead of JSON. It supports `today`, `tomorrow`, `week`, or a specific `YYYY-MM-DD` date.
2. `/life review` is a readable item list. Internal FinalGate and managed-review diagnostics are no longer shown as default human pending items.
3. `/life config` checks required settings in a human-readable way: identity/persona, worldview, time, weather, truth sources, sleep rules, resources, autonomy.
4. Agent self-life defaults are more autonomous: `autonomy=full` and `managed_review_loop=auto` by default for Agent-owned life.
5. Startup/session checks run required-setting validation and align safe self-management defaults.
6. Canon supports both virtual rules, such as randomized narrative weather, and real source bindings, such as system clock or external tool observations.

## Human schedule examples

```text
/life schedule
/life schedule today
/life schedule tomorrow
/life schedule week
/life schedule 2026-06-11
```

Output is a timeline like:

```text
今天的日程（2026-06-11）
=================
1. 06-11 10:30 - 06-11 12:00  处理小单子
   类型：work；时间块：planned；事件：scheduled
   中断：soft_interruptible
```

## Design principle

The Agent should manage its own life. Humans configure the Life Canon, inspect high-level status, and intervene only for user-owned facts, dangerous actions, or explicit overrides. Agent-owned life progression, autonomy, safe review maintenance, sleep effects, dream audits, and schedule execution should run through LifeOps, validators, receipts, and trace without requiring constant human approval.

## LifeEngine WebUI / Observatory（v0.12.1）

启动本地观察台：

```bash
hermes lifeengine webui --open
```

或选择某个目录：

```bash
hermes lifeengine webui --life-dir ~/.hermes/lifeengine --open
```

打开：

```text
http://127.0.0.1:8765
```

页面包括：

- 像素 Agent 小人实时状态
- 今日 / 明日 / 本周 / 指定日期 schedule timeline
- 实时状态、睡眠债、恢复压力、资源
- Review 人类 item 列表
- 梦境卡片
- 最近流水 / trace

任意选择的 DB 默认只读；只有当前 Hermes profile DB 才允许 WebUI 的有限 operator action（call、tick、review safe apply 等），且这些 action 仍然通过 LifeEngineRuntime。


## v0.12.1 WebUI Live Observatory

This package includes the v0.12.1 WebUI enhancement. The design document is bundled under `docs/lifeengine_total_design_v0_12_1.md`.

Run:

```bash
hermes lifeengine webui --open
```

Highlights:

- SSE live snapshot updates.
- Clickable schedule timeline.
- Event detail drawer with transitions, schedule blocks, results, resources and journal references.
- Dream detail drawer.
- Trace explain drawer.
- Owner selector.
- Human-friendly display; raw JSON is kept inside detail/debug views only.
