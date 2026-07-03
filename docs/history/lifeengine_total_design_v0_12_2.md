# LifeEngine WebUI v0.12.2 — Product Refactor

## Goal

v0.12.2 turns the WebUI from a developer-heavy database monitor into a human-readable Agent observatory.

The main problems fixed:

1. The reference portrait must not be distorted or flattened.
2. The pixel Agent must be clearly visible and stateful.
3. The default UI must be readable by humans, not raw JSON.
4. Schedule, review, events and dreams must be browsable without memorizing internal tools.

## Visual model

The WebUI now uses two Agent visual layers:

1. **Portrait identity layer**
   - Uses the user-provided image as the visual identity source.
   - Rendered in a fixed 9:16 frame.
   - Uses `object-fit: cover` and `object-position: center 12%` to preserve aspect ratio and avoid squashing.

2. **Pixel runtime layer**
   - Uses a generated chibi pixel avatar derived from the reference image.
   - Key traits: twin buns, long dark hair, white blouse, black skirt, red ribbons, talisman/pendant.
   - It has state-specific sprites: idle, walk, work, battle, sleep, dream, reply, eat, tired, recover.

## Human-readable views

The UI is organized into tabs:

- Overview
- Schedule
- Review
- Events
- Dreams
- Trace

Raw JSON is no longer the main presentation. It is available only inside collapsible debug blocks in detail drawers.

## Runtime state mapping

LifeEngine runtime state maps to pixel Agent state:

| Runtime state | Sprite |
|---|---|
| asleep / napping | sleep |
| dreaming | dream |
| waiting_to_reply / delayed replies | reply |
| uninterruptible_event | battle |
| work / study / creative / maintenance | work |
| health / fitness / travel | walk |
| meal | eat |
| high sleep debt / fatigue | tired / recover |
| otherwise | idle |

## Schedule display

Schedule remains grounded in LifeEngine data:

- `ScheduleBlock` is the arranged time block.
- `Event` is the thing being done.
- One Event may have multiple ScheduleBlocks.
- Sleep uses Event + ScheduleBlock + SleepSession.

The WebUI timeline shows:

- planned time
- actual time when present
- block status
- event status
- event category / type
- location
- interruptibility

## Detail drawers

Event detail shows:

- event summary
- planned vs actual
- category and type
- state transitions
- schedule blocks
- results
- resource changes
- sleep/execution adjustments
- journal references

Dream detail shows:

- dream entry
- `truth_layer = dream_symbolic`
- share text
- dream runs
- audit findings

Trace detail shows:

- transaction / ops / receipts / journal, with raw JSON folded.

## Safety

The WebUI remains an observatory first.

Operator actions are limited to the active Hermes profile DB. Arbitrary selected DBs are read-only. Write actions still route through `LifeEngineRuntime`; the WebUI never directly mutates LifeEngine state tables.

## Version

- Plugin version: `0.12.2`
- DB schema version: `39`
- WebUI stack: FastAPI + static HTML/CSS/JS + SSE
