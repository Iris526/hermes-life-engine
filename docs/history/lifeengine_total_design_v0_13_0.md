# LifeEngine v0.13.0 — Game UI Observatory Refactor

## Purpose

v0.13.0 replaces the old developer-dashboard WebUI with a game-like RPG observatory.

The WebUI is no longer a table dump. It is a **game HUD for an Agent life runtime**:

- center stage: what the Agent is doing now
- visible pixel avatar state
- status bars and current mode
- schedule as a quest/timeline log
- inventory/collections as a bag/cabinet board
- review as an inbox, not raw JSON
- workspace/Agent markdown docs as an in-game library
- trace/debug as an optional debug window

## Design Principles

1. Main visual first: the Agent should be the protagonist of the page.
2. State should look like an RPG HUD: energy, focus, mood, fatigue, sleep debt.
3. Collections are presented like inventory/cabinets, not database rows.
4. Raw JSON is hidden behind detail drawers.
5. Hermes workspace markdown is readable in a library panel.
6. Runtime writes still go through LifeEngine tools and LifeOps; the WebUI is not a SQL editor.

## Game UI Inspiration

The design follows common game HUD conventions: status/health/resource bars, item/inventory panels, task logs, and context-sensitive windows. It uses a desktop/RPG window metaphor for side panels and an agent stage for diegetic state display.

## New WebUI Layout

### Top HUD

- brand / LifeEngine title
- LifeEngine DB selector
- live stream state

### Left HUD

- Agent portrait
- stat bars
- quick operator buttons: heartbeat, call, recovery sleep
- mini log

### Center Stage

- scene title
- pixel sprite
- speech bubble
- current event / mode / reply status

### Right Quest Panel

- today / tomorrow / week / selected day
- schedule blocks as quest cards

### Bottom Hotbar

- Bag / collections
- Closet
- Review
- Dreams
- Hermes workspace
- Settings
- Trace

### Detail Drawer

Clicking events, dreams, trace rows, or documents opens a right-side game window drawer.

## Runtime Bridge

New endpoints:

- `GET /api/workspace/docs`
- `GET /api/workspace/file?path=...`

Existing endpoints remain:

- `/api/snapshot`
- `/api/schedule`
- `/api/event/{id}`
- `/api/dream/{id}`
- `/api/trace/explain/{id}`
- `/api/stream`

## Workspace Library

The WebUI scans safe markdown files from inferred Hermes/LifeEngine roots:

- `$HERMES_HOME`
- selected LifeEngine profile parent
- selected lifeengine DB directory
- current working directory

It lists markdown docs such as:

- `SOUL.md`
- `AGENT.md`
- `AGENTS.md`
- `README.md`
- other `.md` files one level deep

File reads are restricted to safe text/config suffixes and small file sizes.

## Status Mapping

- asleep / napping -> sleep sprite
- dreaming -> dream sprite
- waiting_to_reply / delayed replies -> reply sprite
- uninterruptible_event -> battle sprite
- work/study/creative/maintenance -> work sprite
- travel/health/fitness -> walk sprite
- meal -> eat sprite
- high fatigue / recovery pressure -> tired sprite
- idle -> idle sprite

## Version

- Plugin version: `0.13.0`
- DB schema version: unchanged from runtime baseline
- WebUI: FastAPI + static frontend + SSE
