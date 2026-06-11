from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sqlite3
from contextlib import redirect_stdout
from io import StringIO

from lifeengine.cli import handle_cli, setup_cli_parser, slash_life
from lifeengine.db import (
    _SCHEMA_VERSION,
    _create_schema_v1,
    _create_schema_v2,
    _create_schema_v3,
    _create_schema_v4,
    _create_schema_v5,
    _create_schema_v6,
    _create_schema_v7,
    _create_schema_v8,
    _create_schema_v9,
    _create_schema_v10,
    _create_schema_v11,
    _load_sqlite_vec,
)
from lifeengine.heartbeat import heartbeat_installation_status, install_heartbeat_cron, run_tick_script_once
from lifeengine.paths import db_path
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v092"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent；v0.9.2 install and upgrade hardening。")
    rt.commit_canon()
    rt.control("resume")


def test_fresh_install_records_schema_migration_and_install_check(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        version = rt.conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == _SCHEMA_VERSION and version >= 29
        rows = rt.conn.execute("SELECT * FROM schema_migrations WHERE id='schema_v14'").fetchall()
        assert len(rows) == 1
        activate(rt)
        doctor = rt.doctor(include_samples=True)
        assert doctor["ok"] is True
        install_rows = rt.conn.execute("SELECT * FROM install_checks WHERE check_type='doctor'").fetchall()
        assert install_rows
    finally:
        rt.close()


def test_incremental_upgrade_from_v11_creates_v092_tables(tmp_path):
    home = fresh_home(tmp_path)
    db_file = db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    _load_sqlite_vec(conn)
    for fn in [
        _create_schema_v1,
        _create_schema_v2,
        _create_schema_v3,
        _create_schema_v4,
        _create_schema_v5,
        _create_schema_v6,
        _create_schema_v7,
        _create_schema_v8,
        _create_schema_v9,
        _create_schema_v10,
        _create_schema_v11,
    ]:
        fn(conn)
    conn.execute("PRAGMA user_version=11")
    conn.commit()
    conn.close()

    rt = LifeEngineRuntime()
    try:
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 29
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()}
        assert {"schema_migrations", "install_checks"}.issubset(tables)
        assert rt.conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE id='schema_v14'").fetchone()[0] == 1
    finally:
        rt.close()


def test_heartbeat_install_status_and_run_script(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
    finally:
        rt.close()

    gc.collect()

    installed = install_heartbeat_cron(schedule="every 5m", deliver="local", name="lifeengine-heartbeat", run_command=False)
    assert installed["ok"] is True
    assert "--no-agent" in installed["command"]
    assert "lifeengine_tick.py" in installed["command"]

    status = heartbeat_installation_status()
    assert status["ok"] is True
    assert status["script_exists"] is True
    assert status["script_current"] is True

    run = run_tick_script_once(timeout=20)
    assert run["ok"] is True
    # A healthy no-agent heartbeat should be silent on success.
    assert run["stdout"] == ""


def test_slash_command_smoke_path(tmp_path):
    fresh_home(tmp_path)
    setup = slash_life("setup 你是一个 v0.9.2 测试 Agent。")
    assert "LifeEngine 设定草案" in setup
    committed = slash_life("commit")
    assert "LifeEngine 设定已提交" in committed or "ok" in committed
    resumed = slash_life("resume")
    assert "LifeEngine" in resumed or "ok" in resumed
    resource = slash_life("resource add energy")
    assert "resource" in resource.lower() or "资源" in resource
    doctor = slash_life("doctor")
    assert "checks" in doctor or "Doctor" in doctor or "ok" in doctor


def test_cli_parser_and_handler_smoke_path(tmp_path):
    fresh_home(tmp_path)
    parser = argparse.ArgumentParser()
    setup_cli_parser(parser)

    def run_cli_text(argv: list[str]) -> str:
        buf = StringIO()
        args = parser.parse_args(argv)
        with redirect_stdout(buf):
            handle_cli(args)
        return buf.getvalue()

    assert "LifeEngine 设定草案" in run_cli_text(["setup", "CLI", "smoke", "agent"])
    assert "LifeEngine 设定已提交" in run_cli_text(["commit"])
    assert "LifeEngine" in run_cli_text(["control", "resume"]) or "ok" in run_cli_text(["control", "resume"])
    hb_text = run_cli_text(["heartbeat", "status"])
    hb = json.loads(hb_text)
    assert hb["command"].startswith("hermes cron create")
    assert "checks" in run_cli_text(["doctor"]) or "ok" in run_cli_text(["doctor"])


def test_life_upgrade_tool_and_plugin_registration_smoke(tmp_path):
    fresh_home(tmp_path)
    from lifeengine import register
    from lifeengine.tools import life_upgrade, life_doctor

    out = json.loads(life_upgrade({"action": "check", "include_details": True}))
    assert out["ok"] is True
    assert out["db_user_version"] == _SCHEMA_VERSION

    doctor = json.loads(life_doctor({"level": "quick"}))
    assert "checks" in doctor

    class StubCtx:
        def __init__(self):
            self.tools = []
            self.hooks = []
            self.commands = []
            self.cli = []
            self.skills = []
        def register_tool(self, **kwargs):
            self.tools.append(kwargs["name"])
        def register_hook(self, name, fn):
            self.hooks.append(name)
        def register_command(self, name, handler, **kwargs):
            self.commands.append(name)
        def register_cli_command(self, **kwargs):
            self.cli.append(kwargs["name"])
        def register_skill(self, *args, **kwargs):
            self.skills.append(args[0])

    ctx = StubCtx()
    register(ctx)
    assert "life_upgrade" in ctx.tools
    assert "life_doctor" in ctx.tools
    assert "life" in ctx.commands
    assert "lifeengine" in ctx.cli


def test_plugin_registers_v092_tooling_surface():
    from lifeengine import register

    class FakeCtx:
        def __init__(self):
            self.tools = []
            self.hooks = []
            self.commands = []
            self.cli_commands = []
            self.skills = []
        def register_tool(self, **kwargs):
            self.tools.append(kwargs)
        def register_hook(self, name, cb):
            self.hooks.append(name)
        def register_command(self, name, handler, description="", args_hint=""):
            self.commands.append((name, args_hint))
        def register_cli_command(self, **kwargs):
            self.cli_commands.append(kwargs["name"])
        def register_skill(self, name, path, description=""):
            self.skills.append(name)

    ctx = FakeCtx()
    register(ctx)
    tool_names = {t["name"] for t in ctx.tools}
    assert "life_upgrade" in tool_names
    assert "life_doctor" in tool_names
    assert "pre_llm_call" in ctx.hooks
    assert "transform_llm_output" in ctx.hooks
    assert ctx.commands and ctx.commands[0][0] == "life"
    assert "advanced" in ctx.commands[0][1]
    assert "lifeengine" in ctx.cli_commands
