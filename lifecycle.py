"""Lifecycle policy for LifeEngine events and schedule blocks.

v0.9.1 centralizes state-machine rules that were previously split between
runtime validation and low-level writers. Validators should call this before a
LifeTransaction is created so rejected lifecycle mutations leave no tx/op rows.
"""

from __future__ import annotations

from typing import Any


EVENT_STATUSES = {
    "draft", "planned", "scheduled", "ready", "in_progress", "partial", "completed",
    "postponed", "rescheduled", "cancelled", "failed", "abandoned", "archived",
    "discarded", "skipped", "paused", "missed",
}

TERMINAL_EVENT_STATUSES = {
    "completed", "cancelled", "failed", "abandoned", "archived", "discarded",
}

COMPLETABLE_EVENT_STATUSES = {"planned", "scheduled", "ready", "in_progress", "partial"}

# These are intentionally conservative. Use replacement events / revisions for
# semantics that would otherwise require reopening a closed fact.
ALLOWED_EVENT_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"planned", "discarded", "cancelled"},
    "planned": {"scheduled", "cancelled", "abandoned", "in_progress", "postponed", "completed"},
    "scheduled": {"ready", "rescheduled", "cancelled", "in_progress", "completed", "missed", "postponed"},
    "ready": {"in_progress", "skipped", "postponed", "cancelled", "completed"},
    "in_progress": {"partial", "completed", "failed", "paused", "postponed"},
    "partial": {"scheduled", "completed", "abandoned", "postponed", "failed"},
    "postponed": {"rescheduled", "cancelled", "abandoned", "scheduled"},
    "rescheduled": {"scheduled", "cancelled", "postponed"},
    "missed": {"rescheduled", "cancelled", "archived", "postponed"},
    "skipped": {"rescheduled", "cancelled", "archived"},
    "paused": {"scheduled", "in_progress", "abandoned", "cancelled"},
    "failed": {"rescheduled", "abandoned", "archived"},
    "completed": {"archived"},
    "cancelled": {"archived"},
    "abandoned": {"archived"},
    "discarded": {"archived"},
    "archived": set(),
}

EVENT_TRANSITIONS = ALLOWED_EVENT_TRANSITIONS

SCHEDULE_BLOCK_STATUSES = {
    "planned", "locked", "ready", "in_progress", "completed", "skipped", "cancelled",
    "rescheduled", "missed",
}

TERMINAL_SCHEDULE_STATUSES = {"completed", "skipped", "cancelled", "rescheduled", "missed"}

ALLOWED_SCHEDULE_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"locked", "ready", "in_progress", "completed", "skipped", "cancelled", "rescheduled", "missed"},
    "locked": {"ready", "in_progress", "completed", "skipped", "cancelled", "rescheduled", "missed"},
    "ready": {"in_progress", "completed", "skipped", "cancelled", "rescheduled", "missed"},
    "in_progress": {"completed", "skipped", "cancelled", "rescheduled", "missed"},
    "completed": set(),
    "skipped": set(),
    "cancelled": set(),
    "rescheduled": set(),
    "missed": set(),
}

SCHEDULE_TRANSITIONS = ALLOWED_SCHEDULE_TRANSITIONS
SCHEDULE_BLOCK_TRANSITIONS = ALLOWED_SCHEDULE_TRANSITIONS

SCHEDULABLE_EVENT_STATUSES = {
    "draft", "planned", "scheduled", "ready", "partial", "postponed", "rescheduled", "missed", "skipped", "paused",
}


class LifecycleError(ValueError):
    pass


def normalize_status(status: Any) -> str:
    return str(status or "").strip().lower()


def assert_valid_event_status(status: Any) -> str:
    s = normalize_status(status)
    if s not in EVENT_STATUSES:
        raise LifecycleError(f"invalid event status: {status}")
    return s


def assert_valid_schedule_status(status: Any) -> str:
    s = normalize_status(status)
    if s not in SCHEDULE_BLOCK_STATUSES:
        raise LifecycleError(f"invalid schedule block status: {status}")
    return s


def assert_event_transition(old_status: Any, new_status: Any, *, allow_same: bool = True) -> tuple[str, str]:
    old = assert_valid_event_status(old_status)
    new = assert_valid_event_status(new_status)
    if allow_same and old == new:
        return old, new
    if new not in ALLOWED_EVENT_TRANSITIONS.get(old, set()):
        raise LifecycleError(f"invalid event status transition {old} -> {new}")
    return old, new


def assert_schedule_transition(old_status: Any, new_status: Any, *, allow_same: bool = True) -> tuple[str, str]:
    old = assert_valid_schedule_status(old_status)
    new = assert_valid_schedule_status(new_status)
    if allow_same and old == new:
        return old, new
    if new not in ALLOWED_SCHEDULE_TRANSITIONS.get(old, set()):
        raise LifecycleError(f"invalid schedule block status transition {old} -> {new}")
    return old, new


def assert_event_completable(status: Any) -> str:
    s = assert_valid_event_status(status)
    if s not in COMPLETABLE_EVENT_STATUSES:
        raise LifecycleError(f"cannot complete event from status {s}")
    return s


def assert_event_schedulable(status: Any) -> str:
    s = assert_valid_event_status(status)
    if s not in SCHEDULABLE_EVENT_STATUSES:
        raise LifecycleError(f"cannot schedule event from status {s}")
    return s


def event_transition_allowed(old_status: Any, new_status: Any) -> bool:
    try:
        assert_event_transition(old_status, new_status)
        return True
    except LifecycleError:
        try:
            old = assert_valid_event_status(old_status)
            new = assert_valid_event_status(new_status)
            return new == "completed" and old in COMPLETABLE_EVENT_STATUSES
        except LifecycleError:
            return False


def schedule_transition_allowed(old_status: Any, new_status: Any) -> bool:
    try:
        assert_schedule_transition(old_status, new_status)
        return True
    except LifecycleError:
        return False
