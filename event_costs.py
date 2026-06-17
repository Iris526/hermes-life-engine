"""Baseline estimate of what an activity costs the body, by type and duration.

The agent is expected to judge each event's resource cost itself and pass
``resource_costs`` when it creates an event (the life_event tool guidance says
so). This module is the *fallback*: when no cost is given for a recognized
activity type, the engine fills a sane baseline so no real activity is silently
free, and exposes the same estimate so the agent has a reference to adjust from.

Rules of the road:
- Only **recognized activity types** get an auto-estimate. Unknown/marker types
  (``other``, system events, custom subtypes) stay free — the engine never
  guesses a cost it has no basis for.
- An explicit ``resource_costs`` from the caller/agent *always* wins, including
  an explicit ``{}`` meaning "this one really is free".
- Costs are signed deltas, same convention as event ``resource_costs``:
  negative drains, positive restores. They are applied on completion through
  ``apply_delta`` like any other cost, so the doctor invariant is unaffected.
"""

from __future__ import annotations

from typing import Any

# (energy_per_hour, mood_per_hour) by event_type. Negative energy drains;
# positive restores. Mood is small and mostly positive for inherently pleasant
# activities. These are deliberately gentle baselines, not the last word — the
# agent overrides per event with its own read of how draining/rewarding it is.
_INTENSITY: dict[str, tuple[float, float]] = {
    "work": (-12.0, 0.0),
    "commission": (-12.0, 1.0),
    "study": (-10.0, 0.0),
    "creative": (-9.0, 2.0),
    "fitness": (-10.0, 2.0),
    "exercise": (-10.0, 2.0),
    "chore": (-6.0, 0.0),
    "chores": (-6.0, 0.0),
    "maintenance": (-6.0, 0.0),
    "temple_chores": (-6.0, 0.0),
    "routine": (-5.0, 0.0),
    "errand": (-6.0, 0.0),
    "purchase": (-4.0, 1.0),
    "shopping": (-5.0, 2.0),
    "travel": (-7.0, 0.0),
    "walk": (-4.0, 2.0),
    "social": (-4.0, 4.0),
    "relationship": (-3.0, 5.0),
    "meal": (3.0, 4.0),
    "rest": (10.0, 2.0),
    "recovery": (12.0, 2.0),
    "diary": (-2.0, 1.0),
    "proactive_note": (-2.0, 1.0),
    "reflection": (-2.0, 1.0),
}

_DEFAULT_DURATION_HOURS = 0.75  # ~45 min when no schedule is known
_MIN_HOURS = 0.1
_MAX_HOURS = 4.0


def estimate_event_cost(event_type: str | None = None, duration_minutes: float | None = None) -> dict[str, float]:
    """Return a baseline ``resource_costs`` dict, or ``{}`` for unrecognized types."""
    et = (event_type or "other").strip().lower()
    intensity = _INTENSITY.get(et)
    if intensity is None:
        return {}
    energy_per_hr, mood_per_hr = intensity
    try:
        hours = float(duration_minutes) / 60.0 if duration_minutes else _DEFAULT_DURATION_HOURS
    except (TypeError, ValueError):
        hours = _DEFAULT_DURATION_HOURS
    hours = max(_MIN_HOURS, min(hours, _MAX_HOURS))
    costs: dict[str, float] = {}
    energy = round(energy_per_hr * hours, 1)
    mood = round(mood_per_hr * hours, 1)
    if energy:
        costs["energy"] = energy
    if mood:
        costs["mood"] = mood
    return costs


def fill_costs_if_absent(
    resource_costs: dict[str, Any] | None,
    *,
    owner_kind: str,
    event_type: str | None,
    duration_minutes: float | None,
) -> dict[str, Any]:
    """Estimate costs only when the caller gave none, for an agent activity.

    ``None`` means "unspecified, estimate it"; an explicit ``{}`` means "free"
    and is preserved. Only agent-owned events are estimated.
    """
    if resource_costs is not None:
        return resource_costs
    if owner_kind != "agent":
        return {}
    return estimate_event_cost(event_type, duration_minutes)
