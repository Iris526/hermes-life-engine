"""Hermes integration acceptance and v1.0 API-freeze helpers.

v0.9.6 keeps LifeEngine embedded and framework-adapter friendly.  These helpers
are deliberately maintenance-only: they inspect registration, public schemas,
CLI/slash surfaces, package shape, and the optional Hermes core patch proposal
without creating Agent life facts.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import DEFAULT_MODULE_GATES, ENGINE_STATES, PLUGIN_VERSION
from .db import _SCHEMA_VERSION
from .jsonutil import dumps
from .trace import append_audit, new_id
from .validators import ALLOWED_OPS, USER_ALLOWED_FACT_SOURCES, AGENT_NARRATIVE_SOURCES


@dataclass
class FakeHermesContext:
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)
    slash_commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    cli_commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    skills: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register_tool(self, name: str, toolset: str, schema: dict, handler, **kwargs: Any) -> None:
        self.tools[name] = {
            "name": name,
            "toolset": toolset,
            "schema": schema,
            "handler": getattr(handler, "__name__", repr(handler)),
            "description": kwargs.get("description", ""),
            "emoji": kwargs.get("emoji", ""),
        }

    def register_hook(self, hook_name: str, callback) -> None:
        self.hooks.append(hook_name)

    def register_command(self, name: str, handler, description: str = "", args_hint: str = "") -> None:
        clean = name.lower().strip().lstrip("/")
        self.slash_commands[clean] = {"name": clean, "handler": getattr(handler, "__name__", repr(handler)), "description": description, "args_hint": args_hint}

    def register_cli_command(self, name: str, help: str, setup_fn, handler_fn=None, description: str = "") -> None:
        self.cli_commands[name] = {"name": name, "help": help, "setup_fn": getattr(setup_fn, "__name__", repr(setup_fn)), "handler_fn": getattr(handler_fn, "__name__", repr(handler_fn)), "description": description}

    def register_skill(self, name: str, path: Path, description: str = "") -> None:
        self.skills[name] = {"name": name, "path": str(path), "description": description}


def _plugin_dir() -> Path:
    return Path(__file__).resolve().parent


def _package_root() -> Path:
    return _plugin_dir().parent


def _read_manifest_text() -> str:
    return (_plugin_dir() / "plugin.yaml").read_text(encoding="utf-8")


def _extract_manifest_list(key: str) -> list[str]:
    text = _read_manifest_text().splitlines()
    out: list[str] = []
    in_block = False
    for line in text:
        if line.startswith(f"{key}:"):
            in_block = True
            continue
        if in_block and line and not line.startswith(" "):
            break
        if in_block:
            stripped = line.strip()
            if stripped.startswith("-"):
                out.append(stripped[1:].strip())
    return out


def _manifest_scalar(key: str) -> str | None:
    for line in _read_manifest_text().splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip('"\'')
    return None


def _register_in_fake_context() -> FakeHermesContext:
    package_name = __package__ or "lifeengine"
    plugin = sys.modules.get(package_name)
    if plugin is None:
        try:
            plugin = importlib.import_module(package_name)
        except ModuleNotFoundError:
            # Hermes can load directory plugins directly from their __init__.py
            # without adding $HERMES_HOME/plugins to sys.path. In that mode this
            # module is already running, but a top-level import("lifeengine")
            # used only by the integration smoke test may fail. Fall back to a
            # file-location import of the current plugin package.
            spec = importlib.util.spec_from_file_location(
                package_name,
                _plugin_dir() / "__init__.py",
                submodule_search_locations=[str(_plugin_dir())],
            )
            if spec is None or spec.loader is None:
                raise
            plugin = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = plugin
            spec.loader.exec_module(plugin)
    ctx = FakeHermesContext()
    plugin.register(ctx)
    return ctx


def _check(name: str, ok: bool, message: str = "", severity: str = "error", **data: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "status": "ok" if ok else severity, "severity": severity, "message": message, **data}


def _status(checks: list[dict[str, Any]]) -> str:
    if any((not c.get("ok")) and c.get("severity") == "error" for c in checks):
        return "failed"
    if any(not c.get("ok") for c in checks):
        return "warning"
    return "ok"


def run_hermes_integration_smoke(conn, owner_kind: str, owner_id: str, *, include_details: bool = False) -> dict[str, Any]:
    """Register the plugin against a fake Hermes context and validate public surfaces."""
    checks: list[dict[str, Any]] = []
    ctx: FakeHermesContext | None = None
    try:
        ctx = _register_in_fake_context()
        manifest_tools = set(_extract_manifest_list("provides_tools"))
        manifest_hooks = set(_extract_manifest_list("provides_hooks"))
        registered_tools = set(ctx.tools)
        registered_hooks = set(ctx.hooks)
        checks.append(_check("manifest_version", _manifest_scalar("version") == PLUGIN_VERSION, f"manifest={_manifest_scalar('version')}, constants={PLUGIN_VERSION}", manifest_version=_manifest_scalar("version"), plugin_version=PLUGIN_VERSION))
        checks.append(_check("tool_registration", manifest_tools == registered_tools, f"manifest={len(manifest_tools)}, registered={len(registered_tools)}", missing=sorted(manifest_tools - registered_tools), extra=sorted(registered_tools - manifest_tools)))
        checks.append(_check("hook_registration", manifest_hooks.issubset(registered_hooks), f"manifest hooks={sorted(manifest_hooks)}, registered={sorted(registered_hooks)}", missing=sorted(manifest_hooks - registered_hooks), registered=sorted(registered_hooks)))
        checks.append(_check("slash_command", "life" in ctx.slash_commands, "/life slash command registered", commands=sorted(ctx.slash_commands)))
        checks.append(_check("cli_command", "lifeengine" in ctx.cli_commands, "hermes lifeengine CLI command registered", commands=sorted(ctx.cli_commands)))
        checks.append(_check("skill_registration", "lifeengine" in ctx.skills, "lifeengine skill registered", skills=sorted(ctx.skills)))
        schema_issues: list[dict[str, Any]] = []
        for name, meta in sorted(ctx.tools.items()):
            schema = meta.get("schema") or {}
            params = schema.get("parameters") or {}
            if schema.get("name") != name:
                schema_issues.append({"tool": name, "issue": "schema.name mismatch", "schema_name": schema.get("name")})
            if not schema.get("description"):
                schema_issues.append({"tool": name, "issue": "missing description"})
            if params.get("type") != "object":
                schema_issues.append({"tool": name, "issue": "parameters.type must be object", "parameters": params})
        checks.append(_check("tool_schema_shape", not schema_issues, "all tool schemas have names/descriptions/object parameters" if not schema_issues else f"{len(schema_issues)} schema issue(s)", issues=schema_issues[:50]))
        core_hooks = {"pre_llm_call", "transform_llm_output"}
        checks.append(_check("lifeengine_required_hooks", core_hooks.issubset(registered_hooks), "core hooks registered", missing=sorted(core_hooks - registered_hooks)))
        # Smoke argparse construction without mutating durable life state.
        try:
            from .cli import setup_cli_parser
            import argparse
            parser = argparse.ArgumentParser(prog="hermes lifeengine")
            setup_cli_parser(parser)
            parser.parse_args(["upgrade", "integration_smoke"])
            parser.parse_args(["upgrade", "api_freeze"])
            parser.parse_args(["upgrade", "core_patch_draft"])
            checks.append(_check("cli_v096_actions", True, "v0.9.6 CLI maintenance actions parse"))
        except Exception as exc:
            checks.append(_check("cli_v096_actions", False, f"{type(exc).__name__}: {exc}"))
    except Exception as exc:
        checks.append(_check("register_call", False, f"register(ctx) failed: {type(exc).__name__}: {exc}"))
    status = _status(checks)
    run_id = new_id("integration")
    output = {
        "ok": status != "failed",
        "status": status,
        "plugin_version": PLUGIN_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "checks": checks,
    }
    if include_details and ctx is not None:
        output["registered"] = {
            "tools": sorted(ctx.tools),
            "hooks": sorted(ctx.hooks),
            "slash_commands": sorted(ctx.slash_commands),
            "cli_commands": sorted(ctx.cli_commands),
            "skills": sorted(ctx.skills),
        }
    conn.execute(
        "INSERT INTO integration_test_runs(id, owner_kind, owner_id, test_type, status, checks_json, output_json) VALUES(?,?,?,?,?,?,?)",
        (run_id, owner_kind, owner_id, "hermes_plugin_registration", status, dumps(checks), dumps(output)),
    )
    output["integration_test_run_id"] = run_id
    append_audit(conn, owner_kind, owner_id, "life_integration_smoke", "info" if output["ok"] else "error", f"integration_smoke status={status}", output)
    return output


def list_integration_runs(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM integration_test_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "integration_runs": [dict(r) for r in rows]}


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def create_api_freeze_snapshot(conn, owner_kind: str, owner_id: str, *, status: str = "freeze_candidate", include_schemas: bool = True) -> dict[str, Any]:
    """Record a machine-readable v1.0 freeze candidate for public surfaces."""
    ctx = _register_in_fake_context()
    tool_schemas = {name: meta["schema"] for name, meta in sorted(ctx.tools.items())}
    # Large schemas are useful for freeze snapshots, but callers can suppress
    # them for compact release-readiness reports.
    snapshot: dict[str, Any] = {
        "plugin_version": PLUGIN_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "tools": sorted(ctx.tools),
        "hooks": sorted(set(ctx.hooks)),
        "slash_commands": sorted(ctx.slash_commands),
        "cli_commands": sorted(ctx.cli_commands),
        "skills": sorted(ctx.skills),
        "life_ops": sorted(ALLOWED_OPS),
        "engine_states": sorted(ENGINE_STATES),
        "default_module_gates": DEFAULT_MODULE_GATES,
        "user_allowed_fact_sources": sorted(USER_ALLOWED_FACT_SOURCES),
        "agent_narrative_sources": sorted(AGENT_NARRATIVE_SOURCES),
        "db_tables": sorted(str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()),
    }
    if include_schemas:
        snapshot["tool_schemas"] = tool_schemas
    digest = hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()
    snap_id = new_id("apifreeze")
    status = str(status or "freeze_candidate")
    conn.execute(
        "INSERT INTO api_freeze_snapshots(id, owner_kind, owner_id, plugin_version, schema_version, status, snapshot_sha256, snapshot_json) VALUES(?,?,?,?,?,?,?,?)",
        (snap_id, owner_kind, owner_id, PLUGIN_VERSION, _SCHEMA_VERSION, status, digest, dumps(snapshot)),
    )
    out = {"ok": True, "snapshot_id": snap_id, "status": status, "snapshot_sha256": digest, "plugin_version": PLUGIN_VERSION, "schema_version": _SCHEMA_VERSION, "counts": {"tools": len(ctx.tools), "hooks": len(set(ctx.hooks)), "life_ops": len(ALLOWED_OPS), "tables": len(snapshot["db_tables"])}}
    if include_schemas:
        out["snapshot"] = snapshot
    append_audit(conn, owner_kind, owner_id, "life_api_freeze_snapshot", "info", f"API freeze snapshot created: {snap_id}", out)
    return out


def list_api_freeze_snapshots(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM api_freeze_snapshots WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "api_freeze_snapshots": [dict(r) for r in rows]}


def get_api_freeze_snapshot(conn, snapshot_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM api_freeze_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    return {"ok": bool(row), "snapshot": dict(row) if row else None}


def mandatory_final_gate_patch_text() -> str:
    """Return an optional Hermes core patch draft.

    This is intentionally not applied by LifeEngine.  It is a review artifact
    for maintainers who want fail-closed final response gating beyond the normal
    plugin hook isolation policy.
    """
    return f"""# Optional Hermes Core Patch Draft: Mandatory Final Gate

LifeEngine v{PLUGIN_VERSION} works as a normal Hermes plugin using `pre_llm_call`
and `transform_llm_output`.  Current Hermes plugin hooks isolate callback
exceptions so one plugin cannot break the agent loop.  That is correct for normal
plugins, but a strict life-state kernel may want a fail-closed final gate.

This draft proposes a tiny host-level seam.  It is **not** required for the
embedded plugin to run.

## Desired contract

- Register hook name: `mandatory_final_gate`.
- Called after normal `transform_llm_output`, before the final response is
  persisted or delivered.
- Return shapes:
  - `{{"action":"allow"}}`
  - `{{"action":"replace", "response_text":"..."}}`
  - `{{"action":"block", "response_text":"...", "reason":"..."}}`
- If a mandatory gate callback raises, host returns a safe block response instead
  of silently delivering the original response.

## Sketch

```diff
diff --git a/hermes_cli/plugins.py b/hermes_cli/plugins.py
@@
 VALID_HOOKS = {{
@@
     "transform_llm_output",
+    "mandatory_final_gate",
@@
 }}
+
+def invoke_mandatory_hook(hook_name: str, **kwargs):
+    callbacks = _plugin_manager._hooks.get(hook_name, [])
+    results = []
+    for cb in callbacks:
+        try:
+            ret = cb(**kwargs)
+            if ret is not None:
+                results.append(ret)
+        except Exception as exc:
+            return [{{
+                "action": "block",
+                "response_text": "LifeEngine final gate failed closed before delivery.",
+                "reason": f"{{type(exc).__name__}}: {{exc}}",
+            }}]
+    return results
```

```diff
diff --git a/agent/conversation_loop.py b/agent/conversation_loop.py
@@
 # after transform_llm_output and before final delivery/persistence
+try:
+    from hermes_cli.plugins import invoke_mandatory_hook
+    for gate_result in invoke_mandatory_hook(
+        "mandatory_final_gate",
+        response_text=final_response,
+        session_id=agent.session_id,
+        task_id=effective_task_id,
+        turn_id=turn_id,
+        model=agent.model,
+        platform=getattr(agent, "platform", None) or "",
+    ):
+        if isinstance(gate_result, dict):
+            action = gate_result.get("action")
+            if action in {{"replace", "block"}} and gate_result.get("response_text"):
+                final_response = str(gate_result["response_text"])
+                break
+except Exception as exc:
+    final_response = "LifeEngine mandatory final gate failed closed before delivery."
```

## LifeEngine plugin behavior if this hook exists

LifeEngine can register the same audit callback for both:

- `transform_llm_output` for current Hermes compatibility.
- `mandatory_final_gate` for fail-closed hosts.
"""


def write_core_patch_draft(conn, owner_kind: str, owner_id: str, *, destination: str | None = None) -> dict[str, Any]:
    patch_id = new_id("patchdraft")
    root = Path(destination).expanduser() if destination else _package_root() / "docs" / "patches"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"lifeengine_mandatory_final_gate_patch_{PLUGIN_VERSION.replace('.', '_')}.md"
    text = mandatory_final_gate_patch_text()
    path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO core_patch_drafts(id, owner_kind, owner_id, patch_type, plugin_version, patch_path, patch_sha256, status, notes) VALUES(?,?,?,?,?,?,?,?,?)",
        (patch_id, owner_kind, owner_id, "mandatory_final_gate", PLUGIN_VERSION, str(path), digest, "draft", "Optional host patch; not applied automatically"),
    )
    out = {"ok": True, "patch_id": patch_id, "patch_type": "mandatory_final_gate", "patch_path": str(path), "patch_sha256": digest, "status": "draft"}
    append_audit(conn, owner_kind, owner_id, "life_core_patch_draft", "info", f"Core patch draft written: {path}", out)
    return out


def list_core_patch_drafts(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM core_patch_drafts WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "core_patch_drafts": [dict(r) for r in rows]}

# ---- Compatibility aliases used by runtime/CLI action names -----------------

def run_integration_check(conn, owner_kind: str, owner_id: str, *, write_audit: bool = True, include_details: bool = False) -> dict[str, Any]:
    # write_audit is retained for action-level API symmetry; the underlying smoke
    # always records its own audit row.
    return run_hermes_integration_smoke(conn, owner_kind, owner_id, include_details=include_details)


def public_surface(conn) -> dict[str, Any]:
    ctx = _register_in_fake_context()
    return {
        "plugin_version": PLUGIN_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "manifest_version": _manifest_scalar("version"),
        "tools": sorted(ctx.tools),
        "hooks": sorted(set(ctx.hooks)),
        "slash_commands": sorted(ctx.slash_commands),
        "cli_commands": sorted(ctx.cli_commands),
        "skills": sorted(ctx.skills),
        "life_ops": sorted(ALLOWED_OPS),
        "engine_states": sorted(ENGINE_STATES),
        "default_module_gates": DEFAULT_MODULE_GATES,
        "minimal_human_commands": [
            "/life", "/life help", "/life setup <设定>", "/life commit",
            "/life pause", "/life resume", "/life run", "/life review",
            "/life doctor", "/life backup", "/life advanced",
        ],
        "advanced_commands": [
            "truth", "resource", "inventory", "goal", "autonomy", "proactive",
            "execution", "confirmation", "trace", "final_gate", "upgrade",
            "heartbeat", "module", "acceptance", "release_readiness",
        ],
        "db_tables": sorted(str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()),
    }


def create_core_patch_draft(conn, owner_kind: str, owner_id: str, *, patch_name: str = "mandatory_final_gate", destination: str | None = None) -> dict[str, Any]:
    # patch_name is reserved for future patch families; v0.9.6 currently emits
    # the mandatory final gate draft.
    return write_core_patch_draft(conn, owner_kind, owner_id, destination=destination)


def release_readiness(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    integration = run_hermes_integration_smoke(conn, owner_kind, owner_id, include_details=True)
    freeze = create_api_freeze_snapshot(conn, owner_kind, owner_id, include_schemas=False)
    patch = write_core_patch_draft(conn, owner_kind, owner_id)
    ok = bool(integration.get("ok") and freeze.get("ok") and patch.get("ok"))
    out = {
        "ok": ok,
        "status": "ok" if ok else "error",
        "integration_test_run_id": integration.get("integration_test_run_id"),
        "api_freeze_snapshot_id": freeze.get("snapshot_id"),
        "api_freeze_sha256": freeze.get("snapshot_sha256"),
        "core_patch_id": patch.get("patch_id"),
        "core_patch_path": patch.get("patch_path"),
        "next": [
            "Run /life doctor --samples on the target profile.",
            "Run /life trace verify after migration/import.",
            "Review the optional mandatory final gate patch before v1.0 if fail-closed host semantics are required.",
        ],
        "integration": integration,
    }
    append_audit(conn, owner_kind, owner_id, "life_release_readiness", "info" if ok else "error", f"release readiness status={out['status']}", out)
    return out

