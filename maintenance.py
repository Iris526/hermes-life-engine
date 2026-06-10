"""Install/upgrade maintenance checks for LifeEngine v0.9.2.

This module is deliberately boring: it does not mutate Life state.  It records
install, migration, command-smoke, and heartbeat-script diagnostics so v1.0 can
ship with observable upgrade behavior rather than relying on manual filesystem
inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .constants import PLUGIN_VERSION
from .db import _SCHEMA_VERSION
from .heartbeat import write_tick_script, cron_create_command
from .jsonutil import dumps
from .paths import hermes_home
from .trace import append_audit, new_id


def _table_names(conn) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()}


def _check(name: str, ok: bool, message: str = "", severity: str = "error", **data: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "status": "ok" if ok else severity, "severity": severity, "message": message, **data}


def _status(checks: list[dict[str, Any]]) -> str:
    if any((not c.get("ok")) and c.get("severity") == "error" for c in checks):
        return "failed"
    if any((not c.get("ok")) for c in checks):
        return "warning"
    return "ok"


def migration_status(conn) -> dict[str, Any]:
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    names = _table_names(conn)
    rows: list[dict[str, Any]] = []
    if "schema_migrations" in names:
        rows = [dict(r) for r in conn.execute("SELECT * FROM schema_migrations ORDER BY created_at DESC, to_version DESC LIMIT 20").fetchall()]
    return {
        "ok": user_version == _SCHEMA_VERSION,
        "plugin_version": PLUGIN_VERSION,
        "schema_version": user_version,
        "target_schema_version": _SCHEMA_VERSION,
        "migration_table_present": "schema_migrations" in names,
        "migrations": rows,
    }


def run_install_check(conn, owner_kind: str, owner_id: str, *, write_audit: bool = True) -> dict[str, Any]:
    plugin_dir = Path(__file__).resolve().parent
    package_root = plugin_dir.parent
    checks: list[dict[str, Any]] = []

    required_files = [
        plugin_dir / "plugin.yaml",
        plugin_dir / "__init__.py",
        plugin_dir / "runtime.py",
        plugin_dir / "db.py",
        plugin_dir / "schemas.py",
        package_root / "requirements.txt",
        package_root / "install.sh",
    ]
    missing = [str(p) for p in required_files if not p.exists()]
    checks.append(_check("plugin_layout", not missing, "plugin layout ok" if not missing else "missing required files", missing=missing))

    req = package_root / "requirements.txt"
    req_text = req.read_text(encoding="utf-8") if req.exists() else ""
    checks.append(_check("sqlite_vec_requirement", "sqlite-vec" in req_text, "sqlite-vec declared in requirements.txt" if "sqlite-vec" in req_text else "sqlite-vec missing from requirements.txt"))

    try:
        sqlite_version, vec_version = conn.execute("SELECT sqlite_version(), vec_version()").fetchone()
        checks.append(_check("sqlite_vec_loaded", True, f"sqlite={sqlite_version}, sqlite-vec={vec_version}", sqlite_version=sqlite_version, vec_version=vec_version))
    except Exception as exc:
        checks.append(_check("sqlite_vec_loaded", False, f"sqlite-vec is not loaded: {type(exc).__name__}: {exc}"))

    mig = migration_status(conn)
    checks.append(_check("schema_current", mig["ok"], f"user_version={mig['schema_version']}, target={mig['target_schema_version']}", migration_status=mig))

    names = _table_names(conn)
    checks.append(_check("schema_migrations_table", "schema_migrations" in names, "schema_migrations table present"))
    checks.append(_check("install_checks_table", "install_checks" in names, "install_checks table present"))

    scripts = hermes_home() / "scripts"
    try:
        scripts.mkdir(parents=True, exist_ok=True)
        writable = os.access(str(scripts), os.W_OK)
    except Exception:
        writable = False
    checks.append(_check("scripts_dir_writable", writable, str(scripts), severity="warning"))

    pycache = [str(p.relative_to(package_root)) for p in package_root.rglob("__pycache__")]
    pytest_cache = [str(p.relative_to(package_root)) for p in package_root.rglob(".pytest_cache")]
    caches = pycache + pytest_cache
    checks.append(_check("package_cache_clean", not caches, "no cache dirs in release package" if not caches else "cache dirs found", severity="warning", caches=caches))

    status = _status(checks)
    out = {
        "ok": status != "failed",
        "status": status,
        "plugin_version": PLUGIN_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "package_root": str(package_root),
        "hermes_home": str(hermes_home()),
        "checks": checks,
    }
    try:
        conn.execute(
            "INSERT INTO install_checks(id, owner_kind, owner_id, check_type, status, payload_json) VALUES(?,?,?,?,?,?)",
            (new_id("installcheck"), owner_kind, owner_id, "install_check", status, dumps(out)),
        )
    except Exception:
        pass
    if write_audit:
        append_audit(conn, owner_kind, owner_id, "life_install_check", "info" if out["ok"] else "warning", f"install_check status={status}", out)
    return out


def command_smoke(owner_kind: str, owner_id: str) -> dict[str, Any]:
    """Run lightweight parser/slash smoke tests without changing durable life facts."""
    checks: list[dict[str, Any]] = []
    try:
        from .cli import setup_cli_parser, slash_life
        parser = argparse.ArgumentParser(prog="hermes lifeengine")
        setup_cli_parser(parser)
        for argv in (["status"], ["trace", "verify"], ["doctor"], ["heartbeat", "install"]):
            ns = parser.parse_args(list(argv))
            checks.append(_check(f"cli_parse:{' '.join(argv)}", True, "parsed", action=getattr(ns, "lifeengine_action", None)))
        # Slash command smoke: status and unknown usage should return readable text.
        status_text = slash_life("status")
        usage_text = slash_life("definitely_unknown_command")
        checks.append(_check("slash_status", "control" in status_text or "engine_state" in status_text, "slash /life status returned payload", sample=status_text[:240]))
        checks.append(_check("slash_usage", "Usage:" in usage_text, "unknown slash command returns usage", sample=usage_text[:240]))
    except SystemExit as exc:
        checks.append(_check("command_smoke", False, f"argparse exited: {exc}"))
    except Exception as exc:
        checks.append(_check("command_smoke", False, f"{type(exc).__name__}: {exc}"))
    status = _status(checks)
    return {"ok": status != "failed", "status": status, "owner_kind": owner_kind, "owner_id": owner_id, "checks": checks}


def record_command_smoke(conn, owner_kind: str, owner_id: str, result: dict[str, Any]) -> None:
    try:
        conn.execute(
            "INSERT INTO install_checks(id, owner_kind, owner_id, check_type, status, payload_json) VALUES(?,?,?,?,?,?)",
            (new_id("cmdsmoke"), owner_kind, owner_id, "command_smoke", result.get("status", "unknown"), dumps(result)),
        )
    except Exception:
        pass


def heartbeat_script_check(timeout: int = 30) -> dict[str, Any]:
    script = write_tick_script()
    proc = subprocess.run([sys.executable, str(script)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return {
        "ok": proc.returncode == 0,
        "script": str(script),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def record_cron_install(conn, owner_kind: str, owner_id: str, result: dict[str, Any]) -> None:
    try:
        conn.execute(
            "INSERT INTO install_checks(id, owner_kind, owner_id, check_type, status, payload_json) VALUES(?,?,?,?,?,?)",
            (new_id("croncheck"), owner_kind, owner_id, "heartbeat_cron", "ok" if result.get("ok") else "failed", dumps(result)),
        )
    except Exception:
        pass


def heartbeat_install_plan(schedule: str = "every 5m", deliver: str = "local", name: str = "lifeengine-heartbeat") -> dict[str, Any]:
    script = write_tick_script()
    cmd = cron_create_command(schedule, deliver, name)
    return {"ok": True, "script": str(script), "command": " ".join(cmd), "argv": cmd, "scheduled": False}
