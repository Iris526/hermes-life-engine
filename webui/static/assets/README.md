# Avatar assets — a pluggable skin

LifeEngine is character-agnostic. The bundled sprites/portrait here are just the
**default skin** (the host character "明灯"); the engine does not depend on any
specific character — the agent identity lives in Canon, and the avatar is a
host-provided resource.

## Files (the default set)

- `sprite-<state>.webp` — animated (2-frame) pose per realtime state:
  `idle, work, walk, sleep, dream, eat, reply, battle, tired, recover`
  (`sprite-<state>.png` is the static fallback).
- `default-agent-reference.jpg` — left-panel portrait / 立绘.
- `agent-chibi-icon.png` — dialogue-box portrait.

All poses are normalized to the same head size on a uniform 760×820 canvas,
bottom-anchored, so every state renders at a consistent relative size.

## Reskinning (override without touching code)

The WebUI serves avatar assets through `GET /api/avatar/<name>`, which prefers a
**host override** over the bundled default:

```
$HERMES_HOME/lifeengine/avatar/<name>   ← host override (wins if present)
<bundled>/webui/static/assets/<name>    ← default (明灯)
```

To give the host its own character, drop replacement files with the same names
into `~/.hermes/lifeengine/avatar/` (or `$HERMES_HOME/lifeengine/avatar/`). No
rebuild, no code change. Keep the same canvas/head conventions for consistent
sizing. Allowed types: png/jpg/jpeg/webp/gif.

## Scene backgrounds (maps)

Per-activity map backgrounds are `bg-<scene>.webp` (observatory, workshop,
night_room, dream_space, message_room, combat_alley, city_walk, meal_corner,
recovery_room). The stage loads `GET /api/avatar/bg-<scene>.webp` per state, so
hosts can reskin maps the same way (drop overrides into the avatar dir). If a
`bg-<scene>` is missing, the stage falls back to the built-in CSS scene.
