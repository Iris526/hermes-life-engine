"""Regression: startup_check must respect an explicit self-life gate choice.

It used to force autonomy/managed_review_loop back to full/auto on every startup,
silently overriding a user who deliberately turned them off/manual. The opt-out
default now only fills a genuinely-absent gate.
"""
from __future__ import annotations

import os
import tempfile

from lifeengine.canon import ensure_control, update_control
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.jsonutil import dumps
from lifeengine.runtime import LifeEngineRuntime

OWNER_KIND = "agent"
OWNER_ID = DEFAULT_AGENT_ID


def _gates(rt):
    return ensure_control(rt.conn, OWNER_KIND, OWNER_ID).get("module_gates") or {}


def test_startup_check_keeps_explicit_off_autonomy(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", tempfile.mkdtemp(prefix="le_startup_gate_"))
    rt = LifeEngineRuntime()
    try:
        rt.setup("startup gate test agent")
        rt.commit_canon()
        rt.control("resume")
        # User deliberately turns self-life management OFF.
        gates = dict(_gates(rt))
        gates["autonomy"] = "off"
        gates["managed_review_loop"] = "off"
        with rt.conn:
            update_control(rt.conn, OWNER_KIND, OWNER_ID, module_gates_json=dumps(gates))

        rt.startup_check()

        after = _gates(rt)
        assert after["autonomy"] == "off", "explicit autonomy=off must survive startup_check"
        assert after["managed_review_loop"] == "off", "explicit managed_review_loop=off must survive startup_check"
    finally:
        rt.close()


def test_startup_check_defaults_a_missing_gate_on(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", tempfile.mkdtemp(prefix="le_startup_gate2_"))
    rt = LifeEngineRuntime()
    try:
        rt.setup("startup gate default test agent")
        rt.commit_canon()
        rt.control("resume")
        # Simulate an old agent whose module_gates lack the key entirely.
        gates = {k: v for k, v in _gates(rt).items() if k not in ("autonomy", "managed_review_loop")}
        with rt.conn:
            update_control(rt.conn, OWNER_KIND, OWNER_ID, module_gates_json=dumps(gates))

        rt.startup_check()

        after = _gates(rt)
        assert after["autonomy"] == "full", "an absent autonomy gate should default ON (opt-out)"
        assert after["managed_review_loop"] == "auto"
    finally:
        rt.close()
