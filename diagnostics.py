"""LifeEngine runtime diagnostics facade helpers.

本模块承载 LifeEngineRuntime 的 doctor / maintenance / startup_check 实现。
当前切片只做行为保持的结构搬迁：函数仍接收 ``LifeEngineRuntime`` 实例
``rt``，并继续通过 ``rt.conn`` / ``rt.<method>`` 复用既有事务与运行时边界。
"""

from __future__ import annotations

from typing import Any

from .canon import ensure_control, get_active_canon, update_control
from .constants import DEFAULT_AGENT_ID, PLUGIN_VERSION, SETUP_STATES
from .db import _SCHEMA_VERSION, reference_table_names, transaction
from .delivery import delivery_config_status
from .heartbeat import heartbeat_installation_status
from .invariants import run_doctor as run_invariant_doctor
from .jsonutil import dumps, loads
from .resources import reconcile_resources
from .settings_check import check_required_settings
from .trace import Trace, append_audit, new_id, verify_journal_hash_chain


class _DoctorCheckList(list):
    """List of doctor checks that also supports dict-like keyed access."""
    def _names(self):
        return [c.get("name") for c in self if isinstance(c, dict)]

    def keys(self):
        names = self._names()
        if "resource_ledger" in names and "resources" not in names:
            names.append("resources")
        if "deep_invariants" in names and "event_lifecycle" not in names:
            names.append("event_lifecycle")
        return names

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, str):
            wanted = "resource_ledger" if key == "resources" else key
            for c in self:
                if isinstance(c, dict) and c.get("name") == wanted:
                    return c
            if key == "event_lifecycle":
                for c in self:
                    if isinstance(c, dict) and c.get("name") == "deep_invariants":
                        data = c.get("data") or {}
                        checks = data.get("checks") or data.get("invariant_checks") or {}
                        if isinstance(checks, dict) and "event_lifecycle" in checks:
                            return checks["event_lifecycle"]
                        return c
            raise KeyError(key)
        return super().__getitem__(key)



def run_maintenance(rt, action: str = "install_check", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID, **payload: Any) -> dict[str, Any]:
    if action in {"all", "release_check"}:
        install = rt.upgrade("check", owner_kind, owner_id, **payload)
        migrations = rt.upgrade("status", owner_kind, owner_id)
        cron = rt.upgrade("cron_test", owner_kind, owner_id, **payload)
        ok = bool(install.get("ok") and migrations.get("ok") and cron.get("ok"))
        return {"ok": ok, "install_check": install, "migration_status": migrations, "heartbeat_cron_test": cron}
    return rt.upgrade(action, owner_kind, owner_id, **payload)



def run_doctor(rt, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
               level: str = "full", include_samples: bool = False, write_audit: bool = True) -> dict[str, Any]:
    with transaction(rt.conn):
        control = ensure_control(rt.conn, owner_kind, owner_id)
        trace = Trace(
            rt.conn,
            owner_kind,
            owner_id,
            "doctor",
            engine_state=control.get("engine_state"),
            canon_version=control.get("active_canon_version"),
            input_obj={"level": level, "include_samples": include_samples},
        ).start()
        checks: list[dict[str, Any]] = []
        deep_result: dict[str, Any] | None = None

        def add(name: str, status: str, message: str = "", data: Any | None = None, **extra: Any) -> None:
            item = {"name": name, "status": status, "ok": status != "error", "message": message}
            if data is not None:
                item["data"] = data
            item.update(extra)
            checks.append(item)

        try:
            try:
                sqlite_version, vec_version = rt.conn.execute("SELECT sqlite_version(), vec_version()").fetchone()
                add("sqlite_vec", "ok", f"sqlite={sqlite_version}, sqlite-vec={vec_version}", sqlite_version=sqlite_version, vec_version=vec_version)
            except Exception as exc:
                add("sqlite_vec", "error", f"sqlite-vec is not loaded: {type(exc).__name__}: {exc}")

            user_version = int(rt.conn.execute("PRAGMA user_version").fetchone()[0])
            add(
                "schema_version",
                "ok" if user_version == _SCHEMA_VERSION else "error",
                f"user_version={user_version}, expected={_SCHEMA_VERSION}",
                current=user_version,
                expected=_SCHEMA_VERSION,
            )

            try:
                migration_rows = rt.conn.execute(
                    "SELECT * FROM schema_migrations ORDER BY created_at DESC LIMIT 5"
                ).fetchall()
                add(
                    "schema_migrations",
                    "ok" if migration_rows else "warn",
                    f"recorded_migrations={len(migration_rows)}",
                    migrations=[dict(r) for r in migration_rows] if include_samples else [dict(r) for r in migration_rows[:1]],
                )
            except Exception as exc:
                add("schema_migrations", "error", f"schema_migrations unavailable: {type(exc).__name__}: {exc}")

            # Derived from the schema (db.reference_table_names) so this never
            # drifts — it used to be a hand-maintained list parallel to doctor.py's.
            required_tables = reference_table_names()
            existing = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()}
            missing = sorted(required_tables - existing)
            add("required_tables", "ok" if not missing else "error", "all required tables present" if not missing else f"missing: {', '.join(missing)}", missing=missing if include_samples else missing[:3])

            state = control.get("engine_state")
            canon_version = control.get("active_canon_version")
            if owner_kind == "agent" and state == "active" and not canon_version:
                add("control_state", "error", "agent LifeEngine is active without an active Life Canon", data=control if include_samples else None)
            elif state in SETUP_STATES:
                add("control_state", "warn", f"LifeEngine is in setup state ({state}); mutations should be CanonDraft-only", state=state, draft=control.get("draft_canon_id"))
            elif state in {"paused", "paused_setup", "read_only", "disabled", "migrating", "archived"}:
                add("control_state", "warn" if state != "paused" else "ok", f"LifeEngine state is {state}; heartbeat/mutations should be gated", state=state)
            else:
                add("control_state", "ok", f"engine_state={state}, canon_version={canon_version}", state=state, canon_version=canon_version)

            gates = loads(control.get("module_gates_json") or "{}")
            final_audit = gates.get("final_audit")
            add(
                "final_audit_gate",
                "ok" if final_audit in {"advisory", "strict", "trace", "repair", "warn"} else "warn",
                f"final_audit={final_audit}",
                final_audit=final_audit,
            )
            heartbeat_mode = control.get("heartbeat_mode") or gates.get("heartbeat")
            autonomy_mode = gates.get("autonomy")
            if autonomy_mode in {"low_spontaneity", "full", "auto"} and heartbeat_mode == "off":
                add("autonomy_heartbeat", "warn", "autonomy is enabled but heartbeat is off; autonomous behavior will only run manually", autonomy=autonomy_mode, heartbeat=heartbeat_mode)
            else:
                add("autonomy_heartbeat", "ok", f"autonomy={autonomy_mode}, heartbeat={heartbeat_mode}", autonomy=autonomy_mode, heartbeat=heartbeat_mode)

            hb_status = heartbeat_installation_status()
            add(
                "heartbeat_script",
                "ok" if hb_status.get("ok") else "warn",
                "heartbeat cron script is installed and current" if hb_status.get("ok") else "heartbeat cron script is not installed or stale",
                data=hb_status if include_samples or not hb_status.get("ok") else {"script": hb_status.get("script"), "hermes_found": hb_status.get("hermes_found")},
            )

            hash_check = verify_journal_hash_chain(rt.conn, owner_kind, owner_id)
            add("journal_hash_chain", "ok" if hash_check.get("ok") else "error", hash_check.get("message", ""), data=hash_check if include_samples or not hash_check.get("ok") else {"checked_entries": hash_check.get("checked_entries")})

            # Read-only resource reconcile for concise doctor output.
            res_check = reconcile_resources(rt.conn, owner_kind, owner_id, record=False)
            mismatches = res_check.get("mismatches") or []
            add(
                "resource_ledger",
                "ok" if res_check.get("ok") else "error",
                f"checked {res_check.get('checked', 0)} resources" if not mismatches else f"{len(mismatches)} resource mismatches",
                checked=res_check.get("checked", 0),
                mismatches=mismatches if include_samples or mismatches else [],
            )

            if "persona_traits" in existing:
                persona_oob = rt.conn.execute(
                    "SELECT COUNT(*) FROM persona_traits WHERE owner_kind=? AND owner_id=? AND (value < -1.0 OR value > 1.0)",
                    (owner_kind, owner_id),
                ).fetchone()[0]
                add("persona", "ok" if persona_oob == 0 else "warn",
                    "persona traits in bounds" if persona_oob == 0 else f"{persona_oob} persona trait(s) out of [-1,1]",
                    out_of_bounds=persona_oob)

            running_jobs = rt.conn.execute(
                "SELECT id,reason,wake_at,status,running_at FROM wake_jobs WHERE owner_kind=? AND owner_id=? AND status='running' ORDER BY running_at LIMIT 5",
                (owner_kind, owner_id),
            ).fetchall()
            pending_jobs = rt.conn.execute(
                "SELECT COUNT(*) FROM wake_jobs WHERE owner_kind=? AND owner_id=? AND status='pending'",
                (owner_kind, owner_id),
            ).fetchone()[0]
            add("wake_jobs", "warn" if running_jobs else "ok", f"pending={pending_jobs}, running={len(running_jobs)}", pending=pending_jobs, running=len(running_jobs), samples=[dict(r) for r in running_jobs] if include_samples else [])

            missing_event_transitions = rt.conn.execute(
                """SELECT COUNT(*) FROM events e WHERE e.owner_kind=? AND e.owner_id=?
                      AND NOT EXISTS (SELECT 1 FROM event_state_transitions t WHERE t.event_id=e.id)""",
                (owner_kind, owner_id),
            ).fetchone()[0] if "event_state_transitions" in existing else 0
            add("event_transition_coverage", "ok" if missing_event_transitions == 0 else "error", "event transition coverage ok" if missing_event_transitions == 0 else f"{missing_event_transitions} event(s) without transition history", missing=missing_event_transitions)

            stuck_state = rt.conn.execute(
                """SELECT * FROM agent_realtime_state WHERE owner_kind=? AND owner_id=?
                      AND lease_expires_at_ts IS NOT NULL AND lease_expires_at_ts < unixepoch('now')
                      AND mode IN ('busy','asleep','napping','dreaming','uninterruptible_event','waiting_to_reply')""",
                (owner_kind, owner_id),
            ).fetchall() if "agent_realtime_state" in existing else []
            add("realtime_state_lease", "warn" if stuck_state else "ok", "realtime state leases ok" if not stuck_state else f"{len(stuck_state)} expired realtime lease(s)", stuck=[dict(r) for r in stuck_state] if include_samples or stuck_state else [])

            if {"sleep_sessions", "dream_runs"}.issubset(existing):
                missing_dreams = rt.conn.execute(
                    """SELECT COUNT(*) FROM sleep_sessions s WHERE s.owner_kind=? AND s.owner_id=?
                          AND s.session_type='core_sleep' AND s.status IN ('completed','interrupted')
                          AND COALESCE(s.actual_duration_minutes,0) >= 90
                          AND NOT EXISTS (SELECT 1 FROM dream_runs d WHERE d.sleep_session_id=s.id)""",
                    (owner_kind, owner_id),
                ).fetchone()[0]
                stuck_dreams = rt.conn.execute(
                    "SELECT COUNT(*) FROM dream_runs WHERE owner_kind=? AND owner_id=? AND status='running' AND started_at < datetime('now','-30 minutes')",
                    (owner_kind, owner_id),
                ).fetchone()[0]
                add(
                    "dreams",
                    "warn" if (missing_dreams or stuck_dreams) else "ok",
                    "dream runs ok" if not (missing_dreams or stuck_dreams) else f"missing={missing_dreams}, stuck={stuck_dreams}",
                    missing_dreams=missing_dreams,
                    stuck_dreams=stuck_dreams,
                )

            if owner_kind == "user":
                pending_conf = rt.conn.execute(
                    "SELECT COUNT(*) FROM user_confirmations WHERE owner_kind=? AND owner_id=? AND status='pending'",
                    (owner_kind, owner_id),
                ).fetchone()[0]
                add("user_confirmations", "warn" if pending_conf else "ok", f"pending={pending_conf}", pending=pending_conf)
            if owner_kind == "agent":
                queued_outbox = rt.conn.execute(
                    "SELECT COUNT(*) FROM proactive_outbox WHERE agent_id=? AND status='queued'",
                    (owner_id,),
                ).fetchone()[0]
                delivering_outbox = rt.conn.execute(
                    "SELECT COUNT(*) FROM proactive_outbox WHERE agent_id=? AND status='delivering'",
                    (owner_id,),
                ).fetchone()[0]
                pending_intents = rt.conn.execute(
                    "SELECT COUNT(*) FROM proactive_intents WHERE agent_id=? AND status IN ('generated','queued')",
                    (owner_id,),
                ).fetchone()[0]
                add(
                    "proactive_queue",
                    "warn" if queued_outbox > 20 or delivering_outbox else "ok",
                    f"pending_intents={pending_intents}, queued_outbox={queued_outbox}, delivering_outbox={delivering_outbox}",
                    pending_intents=pending_intents,
                    queued_outbox=queued_outbox,
                    delivering_outbox=delivering_outbox,
                )
                delivery_cfg = delivery_config_status()
                recent_failed_deliveries = rt.conn.execute(
                    "SELECT COUNT(*) FROM proactive_deliveries WHERE agent_id=? AND status='failed' AND created_at >= datetime('now','-24 hours')",
                    (owner_id,),
                ).fetchone()[0]
                if queued_outbox and not delivery_cfg.get("enabled"):
                    delivery_status = "error"
                    delivery_msg = "queued proactive outbox exists but no delivery adapter is configured"
                elif recent_failed_deliveries:
                    delivery_status = "warn"
                    delivery_msg = f"{recent_failed_deliveries} proactive delivery attempt(s) failed in the last 24h"
                else:
                    delivery_status = "ok"
                    delivery_msg = "proactive delivery configured" if delivery_cfg.get("enabled") else "delivery disabled and no queued outbox"
                add(
                    "proactive_delivery",
                    delivery_status,
                    delivery_msg,
                    config=delivery_cfg,
                    queued_outbox=queued_outbox,
                    delivering_outbox=delivering_outbox,
                    recent_failed_deliveries=recent_failed_deliveries,
                )

            try:
                from pathlib import Path
                plugin_dir = Path(__file__).resolve().parent
                pycache_count = len(list(plugin_dir.rglob("__pycache__")))
                add("package_hygiene", "warn" if pycache_count else "ok", "runtime package has no __pycache__ directories" if not pycache_count else f"found {pycache_count} __pycache__ directories", pycache_count=pycache_count)
            except Exception as exc:
                add("package_hygiene", "warn", f"could not inspect package hygiene: {exc}")

            try:
                deep = run_invariant_doctor(rt.conn, owner_kind, owner_id)
                deep_result = deep
                add(
                    "deep_invariants",
                    "ok" if deep.get("ok") else "error",
                    f"status={deep.get('status')}, issues={len(deep.get('issues') or [])}",
                    data=deep if include_samples or not deep.get("ok") else {"check_id": deep.get("check_id"), "status": deep.get("status")},
                    check_id=deep.get("check_id"),
                )
            except Exception as exc:
                add("deep_invariants", "error", f"invariant doctor failed: {type(exc).__name__}: {exc}")

            worst = "ok"
            if any(c["status"] == "error" for c in checks):
                worst = "error"
            elif any(c["status"] == "warn" for c in checks):
                worst = "warn"

            public_status = "warning" if worst == "warn" else worst
            invariant_checks = (deep_result or {}).get("checks") if isinstance(deep_result, dict) else None
            out = {
                "ok": worst != "error",
                "status": public_status,
                "version": PLUGIN_VERSION,
                "schema_version": _SCHEMA_VERSION,
                "owner": {"kind": owner_kind, "id": owner_id},
                # Hybrid doctor checks support list iteration and dict-like keyed access.
                "checks": _DoctorCheckList(checks),
                # Deep invariant checks remain available as a programmatic mapping.
                "invariant_checks": invariant_checks or {},
                "runtime_checks": _DoctorCheckList(checks),
            }
            if write_audit:
                append_audit(rt.conn, owner_kind, owner_id, "doctor", "info" if worst == "ok" else worst, f"LifeEngine doctor status={worst}", out, trace.id)
                rt.conn.execute(
                    "INSERT INTO install_checks(id, owner_kind, owner_id, check_type, status, payload_json) VALUES(?,?,?,?,?,?)",
                    (new_id("installcheck"), owner_kind, owner_id, "doctor", public_status, dumps({"checks": len(checks), "schema_version": _SCHEMA_VERSION, "plugin_version": PLUGIN_VERSION})),
                )
            trace.end(status="ok" if worst != "error" else "error", output_obj={"status": worst, "checks": len(checks)})
            return out
        except Exception as exc:
            trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
            raise



def run_startup_check(rt, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID, *, source: str = "startup") -> dict[str, Any]:
    with transaction(rt.conn):
        control = ensure_control(rt.conn, owner_kind, owner_id)
        canon = get_active_canon(rt.conn, owner_kind, owner_id)
        required = check_required_settings(rt.conn, owner_kind, owner_id, canon, persist=True, source=source) if owner_kind == "agent" else {"ok": True}
        # Make self-life management opt-out rather than opt-in: default it ON
        # only when the user has NOT set it. Previously this reverted an
        # explicit "off"/"manual" back to full/auto on every startup, silently
        # overriding a deliberate choice (audit: startup_check 静默覆盖). New
        # agents already get "full"/"auto" from DEFAULT_MODULE_GATES, so this
        # now only fills a genuinely-absent key.
        gates = dict(control.get("module_gates") or {})
        changed = False
        if gates.get("autonomy") is None:
            gates["autonomy"] = "full"
            changed = True
        if gates.get("managed_review_loop") is None:
            gates["managed_review_loop"] = "auto"
            changed = True
        if changed:
            update_control(rt.conn, owner_kind, owner_id, module_gates_json=dumps(gates))
        # Ensure review policy permits agent-managed safe maintenance by default.
        try:
            from .review import get_review_action_policy, set_review_action_policy
            pol = get_review_action_policy(rt.conn, owner_kind, owner_id, create=True).get("policy") or {}
            if not pol.get("allow_agent_managed_loop"):
                set_review_action_policy(rt.conn, owner_kind, owner_id, policy_patch={"allow_agent_managed_loop": True, "mode": "agent_managed_safe"}, updated_by="startup_check")
        except Exception:
            pass
        return {"ok": True, "control": ensure_control(rt.conn, owner_kind, owner_id), "required_settings": required}



__all__ = ["_DoctorCheckList", "run_doctor", "run_maintenance", "run_startup_check"]
