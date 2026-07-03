"""轴二-2 gate schema: GATE_SPECS is the single source of truth.

Pins the registry so the "杂物抽屉" (37 keys / 18 dead, zero write-validation,
numeric config mixed into the mode value-space) cannot silently grow back:

- DEFAULT_MODULE_GATES is DERIVED from GATE_SPECS (cannot hand-drift).
- every gate names a real runtime `consumer` (a dead gate cannot exist here).
- the 18 audit-confirmed dead keys stay deleted.
- context_budget_chars is a numeric config, kept out of the mode value-space and
  in sync with context_policy's bounds.
- set_module_gate validates writes (unknown key / bad value are rejected).
"""
from __future__ import annotations

import os
import tempfile

import pytest

from lifeengine.constants import (
    DEFAULT_AGENT_ID,
    DEFAULT_MODULE_GATES,
    GATE_SPECS,
    GateSpec,
    validate_gate_value,
)

# The 18 keys the 2026-07-02 audit confirmed had NO runtime read. They must not
# reappear in the registry (regression guard for "只加不删" drift).
_AUDIT_DEAD_KEYS = frozenset({
    "memory", "vector_memory", "events", "resources", "sleep", "diary",
    "dream_repair", "user_life", "relationship_memory", "human_surface",
    "schedule_view", "collections", "behavior_mapping", "srd_policy",
    "world_model", "social_world", "execution", "serendipity",
})

# Every (key, value) the codebase/tests actually write via life_control module.
# Validation must accept all of them (allowed-set may not be too tight).
_OBSERVED_WRITES = {
    "autonomy": ["full", "low_spontaneity", "off"],
    "daily_rhythm": ["off"],
    "dream": ["auto"],
    "final_audit": ["repair", "strict", "trace"],
    "inter_agent": ["auto", "off", "on"],
    "life_author": ["off"],
    "managed_review_loop": ["off"],
    "meals": ["off"],
    "passive_metabolism": ["off"],
    "proactive": ["auto_send", "off", "pending_only"],
    "reply_gate": ["auto"],
    "context_mode": ["micro", "slim", "balanced", "debug"],
}


def test_default_module_gates_is_derived_from_specs():
    assert DEFAULT_MODULE_GATES == {k: s.default for k, s in GATE_SPECS.items()}
    # No accidental duplication: every default is a plain str.
    assert all(isinstance(v, str) for v in DEFAULT_MODULE_GATES.values())


def test_every_gate_names_a_consumer():
    """A gate with no runtime consumer is dead code and must not be here."""
    for key, spec in GATE_SPECS.items():
        assert isinstance(spec, GateSpec)
        assert spec.consumer.strip(), f"{key} has no consumer (dead gate?)"
        assert spec.kind in ("mode", "numeric"), f"{key} has unknown kind {spec.kind}"


def test_mode_gates_wellformed():
    for key, spec in GATE_SPECS.items():
        if spec.kind != "mode":
            continue
        assert spec.allowed, f"{key} mode gate has no allowed vocabulary"
        allowed_lower = {a.lower() for a in spec.allowed}
        assert spec.default.lower() in allowed_lower, (
            f"{key} default {spec.default!r} not in allowed {spec.allowed}"
        )
        assert spec.numeric_range is None


def test_numeric_gates_wellformed():
    for key, spec in GATE_SPECS.items():
        if spec.kind != "numeric":
            continue
        assert spec.numeric_range is not None, key
        lo, hi = spec.numeric_range
        assert lo < hi
        n = int(spec.default)
        assert lo <= n <= hi, f"{key} default {n} outside [{lo}, {hi}]"
        assert spec.allowed == (), f"{key} numeric gate must not carry a mode vocab"


def test_context_budget_range_tracks_context_policy():
    """context_budget_chars bounds/default must mirror the actual consumer."""
    from lifeengine.context_policy import (
        DEFAULT_BUDGET_CHARS,
        MAX_BUDGET_CHARS,
        MIN_BUDGET_CHARS,
    )

    spec = GATE_SPECS["context_budget_chars"]
    assert spec.numeric_range == (MIN_BUDGET_CHARS, MAX_BUDGET_CHARS)
    assert int(spec.default) == DEFAULT_BUDGET_CHARS


def test_audit_dead_keys_stay_deleted():
    present = _AUDIT_DEAD_KEYS & set(GATE_SPECS)
    assert not present, f"audit-confirmed dead gate(s) reappeared: {sorted(present)}"


def test_observed_writes_are_accepted():
    for key, values in _OBSERVED_WRITES.items():
        assert key in GATE_SPECS, f"{key} is written in the codebase but not in GATE_SPECS"
        for v in values:
            assert validate_gate_value(key, v) == v


def test_validate_rejects_unknown_key():
    with pytest.raises(ValueError):
        validate_gate_value("not_a_real_gate", "auto")


def test_validate_rejects_bad_mode_value():
    with pytest.raises(ValueError):
        validate_gate_value("dream", "banana")


def test_validate_numeric_bounds_and_type():
    assert validate_gate_value("context_budget_chars", " 6000 ") == "6000"  # trims + canonicalizes
    with pytest.raises(ValueError):
        validate_gate_value("context_budget_chars", "abc")
    with pytest.raises(ValueError):
        validate_gate_value("context_budget_chars", "999999")


def test_set_module_gate_rejects_unknown_key_end_to_end(monkeypatch):
    from lifeengine.runtime import LifeEngineRuntime

    monkeypatch.setenv("HERMES_HOME", tempfile.mkdtemp(prefix="le_gate_schema_"))
    rt = LifeEngineRuntime()
    try:
        rt.setup("gate schema test agent")
        rt.commit_canon()
        rt.control("resume")
        with pytest.raises(ValueError):
            rt.control("module", key="execution", value="auto")  # a now-deleted dead key
        # A valid write still works.
        rt.control("module", key="dream", value="off")
    finally:
        rt.close()
