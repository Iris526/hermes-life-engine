from __future__ import annotations

import argparse
import json
import os
import shutil
from contextlib import redirect_stdout
from io import StringIO

from lifeengine.cli import handle_cli, setup_cli_parser, slash_life
from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.tools import life_final_gate


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v093"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.9.3 FinalGate repair test Agent。")
    rt.commit_canon()
    rt.control("resume")


def test_final_gate_default_is_advisory_and_strict_can_block(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        msg = rt.audit_final_output("我今天中午吃了咖喱饭。", session_id="s1", turn_id="t1")
        assert msg is None
        reports = rt.final_gate("reports")
        assert reports["reports"]
        latest = reports["reports"][0]
        assert latest["status"] == "advisory"
        assert latest["unsupported"]
        assert latest["suggested_ops"]
        rt.control("module", key="final_audit", value="strict")
        blocked = rt.audit_final_output("我今天中午吃了咖喱饭。", session_id="s2", turn_id="t2")
        assert blocked
        assert "缺少证据" in blocked
        assert "建议 LifeOps 草案" in blocked
    finally:
        rt.close()


def test_final_gate_trace_mode_records_but_does_not_block(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.control("module", key="final_audit", value="trace")
        msg = rt.audit_final_output("我今天中午吃了咖喱饭。", session_id="s1", turn_id="t1")
        assert msg is None
        reports = rt.final_gate("reports")
        assert reports["reports"]
        assert reports["reports"][0]["status"] == "advisory"
    finally:
        rt.close()


def test_final_gate_repair_mode_uses_soft_repair_language(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.control("module", key="final_audit", value="repair")
        msg = rt.audit_final_output("我明天计划去买裙子。", session_id="s1", turn_id="t1")
        assert msg is None
        reports = rt.final_gate("reports")
        assert reports["reports"]
        assert reports["reports"][0]["repair"].get("advisory")
    finally:
        rt.close()


def test_life_final_gate_tool_and_cli_slash(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
    finally:
        rt.close()

    out = json.loads(life_final_gate({"action": "check", "response_text": "我今天中午吃了咖喱饭。", "mode": "repair"}))
    assert out["ok"] is True
    assert out["report"]["unsupported"]
    assert "建议 LifeOps" in out["repair_message"]

    slash = json.loads(slash_life("final_gate reports"))
    assert slash["ok"] is True
    assert slash["reports"]

    parser = argparse.ArgumentParser()
    setup_cli_parser(parser)
    args = parser.parse_args(["final_gate", "reports"])
    buf = StringIO()
    with redirect_stdout(buf):
        handle_cli(args)
    cli_out = json.loads(buf.getvalue())
    assert cli_out["ok"] is True


def test_v093_schema_and_registration_surface(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION and _SCHEMA_VERSION >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()}
        assert "final_gate_reports" in tables
    finally:
        rt.close()

    from lifeengine import register

    class FakeCtx:
        def __init__(self):
            self.tools = []
            self.hooks = []
            self.commands = []
            self.cli = []
            self.skills = []
        def register_tool(self, **kwargs):
            self.tools.append(kwargs["name"])
        def register_hook(self, name, cb):
            self.hooks.append(name)
        def register_command(self, name, handler, **kwargs):
            self.commands.append(name)
        def register_cli_command(self, **kwargs):
            self.cli.append(kwargs["name"])
        def register_skill(self, *args, **kwargs):
            self.skills.append(args[0])

    ctx = FakeCtx()
    register(ctx)
    assert "life_final_gate" in ctx.tools
