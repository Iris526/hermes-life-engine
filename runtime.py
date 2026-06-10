"""Framework-independent LifeEngine runtime.

v0.9 convergence rule: every durable mutation goes through LifeOps. Read-only
queries may use direct store access; mutation helper tools and heartbeat/autonomy
translate intent into LifeOps so validation, receipts, trace, journal, and
final-gate see one shape.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .autonomy import (
    plan_autonomy,
    get_autonomy_decision,
    list_autonomy_decisions,
    update_autonomy_decision_result,
    apply_autonomy_goal_step,
    apply_autonomy_schedule_event,
)
from .canon import (
    append_setup_statement,
    begin_setup,
    commit_draft,
    ensure_control,
    get_active_canon,
    get_draft,
    set_engine_state,
    set_module_gate,
    update_control,
)
from .constants import DEFAULT_AGENT_ID, DEFAULT_USER_ID, MUTATION_BLOCKING_STATES, SETUP_STATES, PLUGIN_VERSION
from .db import connect, transaction, _SCHEMA_VERSION
from .doctor import run_doctor
from .events import (
    claim_wake_job,
    complete_event,
    create_event,
    create_schedule_block,
    due_schedule_blocks,
    due_wake_jobs,
    finish_wake_job,
    list_events,
    transition_event,
    update_schedule_block_status,
)
from .execution import (
    apply_serendipity_event,
    get_execution_decision,
    list_execution_decisions,
    list_serendipity_events,
    simulate_schedule_block_execution,
    update_execution_decision_result,
)
from .goals import (
    create_life_arc,
    create_goal,
    update_goal_progress,
    link_event_to_goal,
    create_event_dependency,
    decompose_event,
    create_reflection,
    create_milestone,
    apply_event_goal_contributions,
    list_life_arcs,
    list_goals,
    list_reflections,
    list_event_dependencies,
    compute_goal_progress,
    recompute_parent_event_progress,
)
from .jsonutil import dumps, loads, pretty
from .maintenance import (
    command_smoke,
    heartbeat_install_plan,
    heartbeat_script_check,
    migration_status,
    record_command_smoke,
    record_cron_install,
    run_install_check,
)
from .heartbeat import heartbeat_installation_status, write_tick_script
from .confirmations import confirmed_ops, get_confirmation, list_confirmations, mark_confirmation, propose_confirmation
from .concurrency import (
    run_parallel_commit_smoke,
    run_parallel_schedule_overlap_smoke,
    run_parallel_heartbeat_idempotency_smoke,
    run_lifeops_stress_smoke,
    list_concurrency_runs,
    list_stress_runs,
)
from .invariants import run_doctor as run_invariant_doctor
from .inventory import (
    create_inventory_item,
    create_meal_record,
    inventory_delta,
    list_inventory,
    list_inventory_movements,
    list_meals,
    update_inventory_item,
)
from .memory import create_memory, search_memories
from .migration import create_branch, list_migrations
from .owner_scope import OwnerScope, resolve_owner_scope
from .proactive import (
    create_proactive_intent,
    evaluate_proactive_intent,
    expire_intents,
    get_proactive_intent,
    list_outbox,
    list_proactive_intents,
    list_proactive_states,
    mark_outbox_sent,
    suppress_intent,
    ensure_proactive_state,
)
from .receipts import create_commit_receipt
from .resources import apply_delta, define_resource, list_resources, reconcile_resources, release_reservation, reserve
from .trace import Trace, append_audit, append_journal, new_id, verify_journal_hash_chain
from .truth_sources import (
    list_truth_sources,
    observe_truth_source,
    resolve_truth_source,
    truth_binding_statement,
)
from .final_gate import (
    build_repair_message,
    consume_final_gate_feedback,
    detect_life_claims,
    enqueue_final_gate_feedback,
    evaluate_final_response,
    final_gate_intervention_count,
    get_final_gate_report,
    list_final_gate_reports,
    write_final_gate_report,
)
from .upgrade import (
    backup_database,
    export_profile_archive,
    inspect_profile_export,
    large_db_smoke,
    list_backups,
    list_maintenance_runs,
    list_profile_exports,
    migration_history,
    record_package_manifest,
    rebuild_memory_indexes,
    run_tick_script_test,
    run_upgrade_check,
    stage_profile_import,
    stage_restore_plan,
    verify_memory_indexes,
)
from .integration import (
    create_api_freeze_snapshot,
    create_core_patch_draft,
    get_api_freeze_snapshot,
    list_api_freeze_snapshots,
    list_core_patch_drafts,
    public_surface,
    release_readiness,
    run_integration_check,
)
from .acceptance import (
    get_acceptance_report,
    latest_v1_rc_checklists,
    list_acceptance_reports,
    list_acceptance_scenarios,
    run_acceptance_suite,
)
from .validators import validate_life_ops


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_owner(args: dict[str, Any] | None = None, *, owner_kind: str | None = None, owner_id: str | None = None,
                  sender_id: str | None = None, agent_id: str | None = None, user_id: str | None = None,
                  **kwargs: Any) -> tuple[str, str]:
    scope = resolve_owner_scope(
        args or {},
        {**kwargs, "sender_id": sender_id, "agent_id": agent_id, "user_id": user_id},
        owner_kind=owner_kind,
        owner_id=owner_id,
    )
    return scope.owner_kind, scope.owner_id


class LifeEngineRuntime:
    def __init__(self) -> None:
        self.conn = connect()

    def close(self) -> None:
        self.conn.close()

    # ----- control / setup -------------------------------------------------
    def status(self, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID) -> dict[str, Any]:
        with transaction(self.conn):
            c = ensure_control(self.conn, owner_kind, owner_id)
            canon = get_active_canon(self.conn, owner_kind, owner_id)
            resources = list_resources(self.conn, owner_kind, owner_id)
            goals = list_goals(self.conn, owner_kind, owner_id, limit=5)
            arcs = list_life_arcs(self.conn, owner_kind, owner_id, limit=5)
            inventory = list_inventory(self.conn, owner_kind, owner_id, limit=10)
            confirmations = list_confirmations(self.conn, owner_kind, owner_id, limit=5) if owner_kind == "user" else []
            pending = list_proactive_intents(self.conn, owner_id, limit=5) if owner_kind == "agent" else []
            proactive_outbox = list_outbox(self.conn, owner_id, status="queued", limit=5) if owner_kind == "agent" else []
            proactive_states = list_proactive_states(self.conn, owner_id, limit=5) if owner_kind == "agent" else []
            autonomy = list_autonomy_decisions(self.conn, owner_kind, owner_id, limit=5) if owner_kind == "agent" else []
            execution = list_execution_decisions(self.conn, owner_kind, owner_id, limit=5)
            serendipity = list_serendipity_events(self.conn, owner_kind, owner_id, limit=5)
            return {"control": c, "canon": canon, "resources": resources, "inventory": inventory, "goals": goals, "life_arcs": arcs, "pending_confirmations": confirmations, "pending_proactive": pending, "proactive_outbox": proactive_outbox if owner_kind == "agent" else [], "proactive_states": proactive_states if owner_kind == "agent" else [], "recent_autonomy": autonomy, "recent_execution": execution, "recent_serendipity": serendipity}

    def control(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID, **kwargs: Any) -> dict[str, Any]:
        with transaction(self.conn):
            trace = Trace(self.conn, owner_kind, owner_id, "control", input_obj={"action": action, "kwargs": kwargs}).start()
            try:
                ensure_control(self.conn, owner_kind, owner_id)
                if action == "setup":
                    draft = begin_setup(self.conn, owner_kind, owner_id, kwargs.get("reason", "manual setup"))
                    trace.end(output_obj={"draft_id": draft["id"]})
                    return {"ok": True, "draft": _brief_draft(draft)}
                if action == "pause":
                    c = set_engine_state(self.conn, owner_kind, owner_id, "paused", kwargs.get("reason"))
                    trace.end(output_obj={"state": c["engine_state"]})
                    return {"ok": True, "control": c}
                if action == "resume":
                    c0 = ensure_control(self.conn, owner_kind, owner_id)
                    if not c0.get("active_canon_version") and owner_kind == "agent":
                        c = set_engine_state(self.conn, owner_kind, owner_id, "setup_required", "resume requires canon")
                        trace.end(status="blocked", output_obj={"reason": "no active canon"})
                        return {"ok": False, "error": "No active Life Canon. Run /life setup then /life commit.", "control": c}
                    c = set_engine_state(self.conn, owner_kind, owner_id, "active", kwargs.get("reason") or "resume")
                    trace.end(output_obj={"state": c["engine_state"]})
                    return {"ok": True, "control": c}
                if action == "disable":
                    c = set_engine_state(self.conn, owner_kind, owner_id, "disabled", kwargs.get("reason") or "disabled")
                    trace.end(output_obj={"state": c["engine_state"]})
                    return {"ok": True, "control": c}
                if action == "readonly":
                    c = set_engine_state(self.conn, owner_kind, owner_id, "read_only", kwargs.get("reason") or "read_only")
                    trace.end(output_obj={"state": c["engine_state"]})
                    return {"ok": True, "control": c}
                if action == "module":
                    c = set_module_gate(self.conn, owner_kind, owner_id, str(kwargs["key"]), str(kwargs["value"]))
                    trace.end(output_obj={"module_gates": c.get("module_gates")})
                    return {"ok": True, "control": c}
                if action == "heartbeat":
                    mode = str(kwargs.get("mode", "manual"))
                    update_control(self.conn, owner_kind, owner_id, heartbeat_mode=mode)
                    c = ensure_control(self.conn, owner_kind, owner_id)
                    trace.end(output_obj={"heartbeat_mode": mode})
                    return {"ok": True, "control": c}
                raise ValueError(f"Unknown control action: {action}")
            except Exception as exc:
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                raise

    def setup(self, text: str | None = None, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID) -> dict[str, Any]:
        with transaction(self.conn):
            trace = Trace(self.conn, owner_kind, owner_id, "setup", input_obj={"text": text}).start()
            try:
                if text:
                    draft = append_setup_statement(self.conn, owner_kind, owner_id, text, "user")
                else:
                    draft = begin_setup(self.conn, owner_kind, owner_id, "manual setup")
                trace.end(output_obj={"draft_id": draft["id"]})
                return {"ok": True, "draft": _brief_draft(draft)}
            except Exception as exc:
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                raise

    def commit_canon(self, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                     draft_id: str | None = None, activate: bool = True) -> dict[str, Any]:
        with transaction(self.conn):
            trace = Trace(self.conn, owner_kind, owner_id, "canon_commit", input_obj={"draft_id": draft_id, "activate": activate}).start()
            try:
                committed = commit_draft(self.conn, owner_kind, owner_id, draft_id, activate)
                trace.end(output_obj={"version": committed["version"]})
                return {"ok": True, "canon": committed}
            except Exception as exc:
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                raise

    def branch(self, name: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID) -> dict[str, Any]:
        with transaction(self.conn):
            control = ensure_control(self.conn, owner_kind, owner_id)
            b = create_branch(self.conn, owner_kind, owner_id, name, control.get("active_canon_version"))
            return {"ok": True, "branch": b}

    # ----- LifeOps transaction --------------------------------------------
    def commit_ops(self, ops: list[dict[str, Any]], owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                   source: str = "life_commit", session_id: str | None = None, turn_id: str | None = None) -> dict[str, Any]:
        try:
            with transaction(self.conn):
                control = ensure_control(self.conn, owner_kind, owner_id)
                trace = Trace(self.conn, owner_kind, owner_id, "life_commit", session_id=session_id, turn_id=turn_id,
                              engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                              input_obj={"ops": ops, "source": source}).start()
                try:
                    out = self._commit_ops_locked(ops, owner_kind, owner_id, source, session_id, turn_id, trace, control=control)
                    trace.end(output_obj=out)
                    return out
                except Exception as exc:
                    # This trace row is part of the transaction and will roll
                    # back with invalid LifeOps.  A durable failure trace is
                    # written just outside the transaction below.
                    trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                    raise
        except Exception as exc:
            self._record_failed_commit_trace(ops, owner_kind, owner_id, source, session_id, turn_id, exc)
            raise

    def _record_failed_commit_trace(self, ops: list[dict[str, Any]], owner_kind: str, owner_id: str,
                                    source: str, session_id: str | None, turn_id: str | None,
                                    exc: Exception) -> None:
        """Persist validation/apply failures that rolled back the main tx.

        Without this out-of-band diagnostic trace, a rejected LifeOps batch could
        disappear from trace history because the trace row lived in the same
        transaction that was rolled back.  This does not create life facts.
        """
        try:
            with transaction(self.conn):
                control = ensure_control(self.conn, owner_kind, owner_id)
                trace = Trace(self.conn, owner_kind, owner_id, "life_commit_failed", session_id=session_id, turn_id=turn_id,
                              engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                              input_obj={"ops": ops, "source": source}).start()
                payload = {"source": source, "error_type": type(exc).__name__, "error": str(exc), "op_count": len(ops or [])}
                append_audit(self.conn, owner_kind, owner_id, "life_commit_failed", "warning", "LifeOps commit rejected before durable mutation", payload, trace.id)
                try:
                    self.conn.execute(
                        "INSERT INTO failed_lifeops_audits(id, owner_kind, owner_id, session_id, turn_id, source, trace_id, error, ops_json) VALUES(?,?,?,?,?,?,?,?,?)",
                        (new_id("failedops"), owner_kind, owner_id, session_id, turn_id, source, trace.id, f"{type(exc).__name__}: {exc}", dumps(ops or [])),
                    )
                except Exception:
                    pass
                trace.end(status="error", output_obj=payload, error=f"{type(exc).__name__}: {exc}")
        except Exception:
            # Never mask the original validation/apply exception.
            pass

    def _commit_ops_locked(self, ops: list[dict[str, Any]], owner_kind: str, owner_id: str,
                           source: str, session_id: str | None = None, turn_id: str | None = None,
                           trace: Trace | None = None, control: dict[str, Any] | None = None) -> dict[str, Any]:
        """Commit LifeOps inside an already-open SQLite transaction.

        This is used by both public tools and heartbeat/autonomy.  It preserves
        the invariant that background life mutations also produce LifeOps,
        receipts, journal entries, and trace evidence.
        """
        control = control or ensure_control(self.conn, owner_kind, owner_id)
        normalized_ops = validate_life_ops(self.conn, owner_kind, owner_id, control, ops, source)
        tx_id = new_id("tx")
        trace_id = trace.id if trace is not None else None
        self.conn.execute(
            """INSERT INTO life_transactions(id, owner_kind, owner_id, source, session_id, turn_id,
                   trace_id, canon_version, status) VALUES(?,?,?,?,?,?,?,?,?)""",
            (tx_id, owner_kind, owner_id, source, session_id, turn_id, trace_id, control.get("active_canon_version"), "pending"),
        )
        results: list[dict[str, Any]] = []
        for op in normalized_ops:
            op_id = new_id("op")
            op_type = op["type"]
            payload = op["payload"]
            validator_report = op.get("validator_report") or {"ok": True}
            self.conn.execute(
                """INSERT INTO life_ops(id, transaction_id, owner_kind, owner_id, op_type, payload_json, status, validator_report_json)
                       VALUES(?,?,?,?,?,?,?,?)""",
                (op_id, tx_id, owner_kind, owner_id, op_type, dumps(payload), "pending", dumps(validator_report)),
            )
            if trace is not None:
                with trace.span(f"op:{op_type}", payload):
                    result = self._apply_op(owner_kind, owner_id, op_type, payload, source, control.get("active_canon_version"))
            else:
                result = self._apply_op(owner_kind, owner_id, op_type, payload, source, control.get("active_canon_version"))
            self.conn.execute("UPDATE life_ops SET status='committed', result_json=? WHERE id=?", (dumps(result), op_id))
            append_journal(self.conn, owner_kind, owner_id, op_type.lower(), {"op_id": op_id, "payload": payload, "result": result}, source, transaction_id=tx_id, op_id=op_id, canon_version=control.get("active_canon_version"))
            results.append({"op_id": op_id, "type": op_type, "payload": payload, "result": result})
        receipt = create_commit_receipt(self.conn, owner_kind, owner_id, tx_id, trace_id, session_id, turn_id, results)
        self.conn.execute("UPDATE life_transactions SET status='committed', committed_at=datetime('now'), receipt_id=?, receipt_json=? WHERE id=?", (receipt["receipt_id"], dumps(receipt), tx_id))
        if session_id and turn_id:
            self.conn.execute(
                "INSERT OR IGNORE INTO turn_commits(id, owner_kind, owner_id, session_id, turn_id, transaction_id, receipt_id) VALUES(?,?,?,?,?,?,?)",
                (new_id("turncommit"), owner_kind, owner_id, session_id, turn_id, tx_id, receipt["receipt_id"]),
            )
        return {"ok": True, "transaction_id": tx_id, "receipt": receipt, "results": results, "trace_id": trace_id}

    def _apply_op(self, owner_kind: str, owner_id: str, op_type: str, payload: dict[str, Any], source: str, canon_version: int | None) -> Any:
        if op_type == "CREATE_EVENT":
            event = create_event(self.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
            if payload.get("goal_id"):
                try:
                    link = link_event_to_goal(self.conn, owner_kind, owner_id, payload["goal_id"], event["id"], role=payload.get("goal_role", "supports"), weight=float(payload.get("goal_weight", payload.get("weight", 1.0))), source=payload.get("source") or source)
                    event["goal_link"] = link
                except Exception as exc:
                    event["goal_link_error"] = str(exc)
            return event
        if op_type == "UPDATE_EVENT_STATUS":
            return transition_event(self.conn, owner_kind, owner_id, payload["event_id"], payload["status"], payload.get("reason"), source)
        if op_type == "CREATE_SCHEDULE_BLOCK":
            return create_schedule_block(self.conn, owner_kind, owner_id, **payload)
        if op_type == "UPDATE_SCHEDULE_BLOCK_STATUS":
            return update_schedule_block_status(self.conn, owner_kind, owner_id, payload["schedule_block_id"], payload["status"], payload.get("reason"), source)
        if op_type == "COMPLETE_EVENT":
            result = complete_event(self.conn, owner_kind, owner_id, payload["event_id"], payload.get("summary", "completed"), payload.get("resource_deltas"), source)
            result["goal_updates"] = apply_event_goal_contributions(self.conn, owner_kind, owner_id, payload["event_id"], source)
            return result
        if op_type == "RESOURCE_DEFINE":
            return define_resource(self.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
        if op_type == "RESOURCE_DELTA":
            return apply_delta(self.conn, owner_kind, owner_id, **payload)
        if op_type == "RESOURCE_RESERVE":
            return reserve(self.conn, owner_kind, owner_id, **payload)
        if op_type == "RESOURCE_RELEASE":
            return release_reservation(self.conn, owner_kind, owner_id, payload["reservation_id"])
        if op_type == "CREATE_MEMORY":
            return create_memory(self.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
        if op_type == "CREATE_DIARY":
            return self._create_diary(owner_kind, owner_id, canon_version=canon_version, **payload)
        if op_type == "CREATE_INVENTORY_ITEM":
            return create_inventory_item(self.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "UPDATE_INVENTORY_ITEM":
            return update_inventory_item(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "INVENTORY_DELTA":
            return inventory_delta(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "INVENTORY_MOVE":
            p = dict(payload)
            p.setdefault("operation", p.pop("movement_type", "move"))
            return inventory_delta(self.conn, owner_kind, owner_id, source=p.get("source") or source, **{k: v for k, v in p.items() if k != "source"})
        if op_type == "CREATE_MEAL_RECORD":
            return create_meal_record(self.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "CREATE_LIFE_ARC":
            return create_life_arc(self.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
        if op_type == "CREATE_GOAL":
            return create_goal(self.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
        if op_type == "UPDATE_GOAL_PROGRESS":
            return update_goal_progress(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "CREATE_GOAL_MILESTONE":
            return create_milestone(self.conn, owner_kind, owner_id, **payload)
        if op_type == "LINK_EVENT_TO_GOAL":
            return link_event_to_goal(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "CREATE_EVENT_DEPENDENCY":
            return create_event_dependency(self.conn, owner_kind, owner_id, **payload)
        if op_type == "DECOMPOSE_EVENT":
            return decompose_event(self.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "CREATE_REFLECTION":
            return create_reflection(self.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "RECOMPUTE_EVENT_PROGRESS":
            return recompute_parent_event_progress(self.conn, owner_kind, owner_id, payload["event_id"], source)
        if op_type == "AUTONOMY_CREATE_GOAL_STEP":
            return apply_autonomy_goal_step(self.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
        if op_type == "AUTONOMY_SCHEDULE_EVENT":
            return apply_autonomy_schedule_event(self.conn, owner_kind, owner_id, **payload)
        if op_type == "CREATE_SERENDIPITY_EVENT":
            return apply_serendipity_event(self.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "CREATE_PROACTIVE_INTENT":
            return create_proactive_intent(self.conn, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "EVALUATE_PROACTIVE_INTENT":
            return evaluate_proactive_intent(self.conn, owner_id, payload.get("intent_id"), control=ensure_control(self.conn, "agent", owner_id), target_user_id=payload.get("target_user_id"), manual=bool(payload.get("manual", False)), trace_id=payload.get("trace_id"), draft_text=payload.get("draft_text"))
        if op_type == "MARK_PROACTIVE_SENT":
            return mark_outbox_sent(self.conn, owner_id, payload["outbox_id"], result=payload.get("result") or {}, manual=bool(payload.get("manual", True)))
        if op_type == "SUPPRESS_PROACTIVE_INTENT":
            return suppress_intent(self.conn, owner_id, payload["intent_id"], payload.get("reason") or "manual suppress")
        if op_type == "EXPIRE_PROACTIVE_INTENTS":
            return expire_intents(self.conn, owner_id)
        raise ValueError(f"Unknown LifeOp type: {op_type}")

    # ----- query / mutation convenience -----------------------------------
    def resources(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                  session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action == "list":
            with transaction(self.conn):
                return {"ok": True, "resources": list_resources(self.conn, owner_kind, owner_id)}
        if action == "define":
            return self.commit_ops([{"type": "RESOURCE_DEFINE", "payload": payload}], owner_kind, owner_id, "life_resource_tool", session_id, turn_id)
        if action == "delta":
            return self.commit_ops([{"type": "RESOURCE_DELTA", "payload": payload}], owner_kind, owner_id, "life_resource_tool", session_id, turn_id)
        if action == "reserve":
            return self.commit_ops([{"type": "RESOURCE_RESERVE", "payload": payload}], owner_kind, owner_id, "life_resource_tool", session_id, turn_id)
        if action == "release":
            return self.commit_ops([{"type": "RESOURCE_RELEASE", "payload": payload}], owner_kind, owner_id, "life_resource_tool", session_id, turn_id)
        if action == "reconcile":
            with transaction(self.conn):
                return {"ok": True, "reconcile": reconcile_resources(self.conn, owner_kind, owner_id)}
        raise ValueError(f"Unknown resource action: {action}")

    def memory(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
               session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action == "remember":
            return self.commit_ops([{"type": "CREATE_MEMORY", "payload": payload}], owner_kind, owner_id, "life_memory_tool", session_id, turn_id)
        if action == "search":
            with transaction(self.conn):
                return {"ok": True, "memories": search_memories(self.conn, owner_kind, owner_id, payload.get("query", ""), int(payload.get("limit", 10)))}
        raise ValueError(f"Unknown memory action: {action}")

    def event_tool(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                   session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action == "list":
            with transaction(self.conn):
                return {"ok": True, "events": list_events(self.conn, owner_kind, owner_id, payload.get("status"), int(payload.get("limit", 20)))}
        if action == "create":
            return self.commit_ops([{"type": "CREATE_EVENT", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        if action == "schedule":
            return self.commit_ops([{"type": "CREATE_SCHEDULE_BLOCK", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        if action == "transition":
            return self.commit_ops([{"type": "UPDATE_EVENT_STATUS", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        if action == "complete":
            return self.commit_ops([{"type": "COMPLETE_EVENT", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        raise ValueError(f"Unknown event action: {action}")


    # ----- truth sources ---------------------------------------------------
    def truth(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
              session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        with transaction(self.conn):
            control = ensure_control(self.conn, owner_kind, owner_id)
            trace = Trace(self.conn, owner_kind, owner_id, "truth_source", session_id=session_id, turn_id=turn_id,
                          engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                          input_obj={"action": action, "payload": payload}).start()
            try:
                if action == "list":
                    result = {"ok": True, "truth_sources": list_truth_sources(self.conn, owner_kind, owner_id, int(payload.get("limit", 10)))}
                elif action == "resolve":
                    read = resolve_truth_source(
                        self.conn, owner_kind, owner_id, payload.get("domain"), payload.get("parameters") or {},
                        trace_id=trace.id, allow_stale=bool(payload.get("allow_stale", False)),
                    )
                    result = {"ok": True, "truth_read": read}
                elif action == "observe":
                    read = observe_truth_source(
                        self.conn, owner_kind, owner_id, payload.get("domain"), payload.get("result") or {},
                        authority=payload.get("authority"), parameters=payload.get("parameters") or {},
                        source=payload.get("source") or "tool_observation", trace_id=trace.id,
                        ttl_minutes=payload.get("ttl_minutes"),
                    )
                    result = {"ok": True, "truth_read": read}
                elif action == "bind":
                    # Binding changes are CanonDraft edits, never active-state mutation.
                    stmt = truth_binding_statement(
                        payload.get("domain"), payload.get("authority"), payload.get("value"),
                        payload.get("parameters") or {}, payload.get("freshness_ttl_minutes"), payload.get("fallback"),
                    )
                    draft = append_setup_statement(self.conn, owner_kind, owner_id, stmt, "truth_bind")
                    result = {"ok": True, "draft": _brief_draft(draft), "statement": stmt}
                else:
                    raise ValueError(f"Unknown truth action: {action}")
                trace.end(output_obj=result)
                return result
            except Exception as exc:
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                raise

    # ----- heartbeat -------------------------------------------------------
    def tick(self, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
             now: str | None = None, manual: bool = True) -> dict[str, Any]:
        now = now or now_iso()
        with transaction(self.conn):
            control = ensure_control(self.conn, owner_kind, owner_id)
            tick_id = new_id("tick")
            trace = Trace(self.conn, owner_kind, owner_id, "heartbeat", tick_id=tick_id,
                          engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                          input_obj={"now": now, "manual": manual}).start()
            self.conn.execute(
                "INSERT INTO heartbeat_runs(id, owner_kind, owner_id, tick_id, mode, status) VALUES(?,?,?,?,?,?)",
                (new_id("hbrun"), owner_kind, owner_id, tick_id, "manual" if manual else control.get("heartbeat_mode", "manual"), "running"),
            )
            try:
                if control["engine_state"] != "active":
                    out = {"reason": f"state={control['engine_state']}"}
                    append_journal(self.conn, owner_kind, owner_id, "heartbeat_noop", {"now": now, **out}, "heartbeat", canon_version=control.get("active_canon_version"))
                    trace.end(status="noop", output_obj=out)
                    self.conn.execute("UPDATE heartbeat_runs SET status='noop', ended_at=datetime('now'), output_json=? WHERE tick_id=?", (dumps(out), tick_id))
                    return {"ok": True, "status": "noop", "reason": f"LifeEngine is {control['engine_state']}"}
                gates = control.get("module_gates") or {}
                if gates.get("heartbeat") == "off" and not manual:
                    out = {"reason": "heartbeat off"}
                    trace.end(status="noop", output_obj=out)
                    self.conn.execute("UPDATE heartbeat_runs SET status='noop', ended_at=datetime('now'), output_json=? WHERE tick_id=?", (dumps(out), tick_id))
                    return {"ok": True, "status": "noop", "reason": "heartbeat off"}
                truth_refresh = self._refresh_truth_sources_for_heartbeat(owner_kind, owner_id, trace.id)
                completed: list[dict[str, Any]] = []
                jobs = due_wake_jobs(self.conn, owner_kind, owner_id, now)
                processed: list[dict[str, Any]] = []
                with trace.span("due_wake_jobs", {"count": len(jobs)}):
                    for job in jobs:
                        claimed = claim_wake_job(self.conn, owner_kind, owner_id, job["id"], tick_id)
                        if not claimed:
                            continue
                        try:
                            if job.get("reason") == "schedule_block_end" and job.get("target_id"):
                                block_row = self.conn.execute(
                                    "SELECT * FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?",
                                    (job["target_id"], owner_kind, owner_id),
                                ).fetchone()
                                block = dict(block_row) if block_row else None
                                if block and block["status"] in {"planned", "locked", "ready", "in_progress"}:
                                    with trace.span("execution_simulate", {"wake_job_id": job["id"], "block_id": block["id"], "event_id": block.get("event_id")}):
                                        decision = simulate_schedule_block_execution(
                                            self.conn, owner_kind, owner_id, control, tick_id=tick_id, trace_id=trace.id,
                                            wake_job_id=job["id"], block=block, now=now, manual=manual,
                                        )
                                    ops = decision.get("proposed_ops") or []
                                    commit = None
                                    if ops:
                                        with trace.span("execution_commit", {"decision_id": decision["id"], "op_count": len(ops)}):
                                            commit = self._commit_ops_locked(ops, owner_kind, owner_id, "execution_simulator", session_id=None, turn_id=tick_id, trace=trace, control=control)
                                        decision = update_execution_decision_result(
                                            self.conn, decision["id"], status="committed",
                                            result_transaction_id=commit.get("transaction_id"),
                                            result_receipt_id=(commit.get("receipt") or {}).get("receipt_id"),
                                        )
                                    completed.append({"wake_job_id": job["id"], "block_id": block["id"], "execution_decision": decision, "commit": commit})
                            finish_wake_job(self.conn, owner_kind, owner_id, job["id"], "done")
                            processed.append({"wake_job_id": job["id"], "status": "done"})
                        except Exception as job_exc:
                            finish_wake_job(self.conn, owner_kind, owner_id, job["id"], "failed", f"{type(job_exc).__name__}: {job_exc}")
                            processed.append({"wake_job_id": job["id"], "status": "failed", "error": str(job_exc)})
                            append_audit(self.conn, owner_kind, owner_id, "heartbeat_wake_job_failed", "warning", str(job_exc), {"wake_job_id": job["id"]}, trace.id)
                recovered = self._apply_resource_recovery(owner_kind, owner_id)
                autonomy_result = self._run_autonomy_for_tick(owner_kind, owner_id, control, tick_id, trace, now, manual)
                proactive_result = self._run_proactive_for_tick(owner_kind, owner_id, control, tick_id, trace, now)
                out = {"completed": completed, "resource_recovery": recovered, "wake_jobs": processed, "truth_refresh": truth_refresh, "autonomy": autonomy_result, "proactive": proactive_result}
                append_journal(self.conn, owner_kind, owner_id, "heartbeat_tick", {"now": now, **out}, "heartbeat", canon_version=control.get("active_canon_version"))
                trace.end(output_obj=out)
                self.conn.execute("UPDATE heartbeat_runs SET status='done', ended_at=datetime('now'), output_json=? WHERE tick_id=?", (dumps(out), tick_id))
                return {"ok": True, "status": "done", **out}
            except Exception as exc:
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                self.conn.execute("UPDATE heartbeat_runs SET status='failed', ended_at=datetime('now'), error=? WHERE tick_id=?", (f"{type(exc).__name__}: {exc}", tick_id))
                raise

    def _refresh_truth_sources_for_heartbeat(self, owner_kind: str, owner_id: str, trace_id: str | None = None) -> list[dict[str, Any]]:
        canon = get_active_canon(self.conn, owner_kind, owner_id)
        bindings = ((canon.get("truth_sources") or {}).get("bindings") or {})
        out: list[dict[str, Any]] = []
        # Always record clock so heartbeat has a traceable time truth.
        domains = ["time"]
        for domain, binding in bindings.items():
            if isinstance(binding, dict) and binding.get("refresh_on_heartbeat"):
                domains.append(domain)
        for domain in dict.fromkeys(domains):
            try:
                out.append(resolve_truth_source(self.conn, owner_kind, owner_id, domain, {}, trace_id=trace_id))
            except Exception as exc:
                append_audit(self.conn, owner_kind, owner_id, "truth_refresh_failed", "warning", str(exc), {"domain": domain}, trace_id)
                out.append({"domain": domain, "status": "error", "error": str(exc)})
        return out

    def _apply_resource_recovery(self, owner_kind: str, owner_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM resource_definitions WHERE owner_kind=? AND owner_id=?",
            (owner_kind, owner_id),
        ).fetchall()
        out = []
        for r in rows:
            rules = loads(r["rules_json"], {}) or {}
            delta = rules.get("heartbeat_recovery")
            if delta is None:
                continue
            try:
                applied = apply_delta(self.conn, owner_kind, owner_id, r["key"], float(delta), "recover", "heartbeat recovery", "heartbeat")
                out.append(applied)
            except Exception as exc:
                out.append({"resource_key": r["key"], "error": str(exc)})
        return out

    def _run_autonomy_for_tick(self, owner_kind: str, owner_id: str, control: dict[str, Any],
                               tick_id: str, trace: Trace, now: str, manual: bool) -> dict[str, Any]:
        gates = control.get("module_gates") or {}
        mode = str(gates.get("autonomy", "manual") or "manual")
        # A manual /life tick should not accidentally force manual autonomy;
        # explicit life_autonomy action=run does that.  Heartbeat only runs
        # autonomy automatically when mode is planned_only/low_spontaneity/full.
        heartbeat_manual = bool(manual)
        planner_manual = False
        try:
            with trace.span("autonomy_plan", {"mode": mode, "heartbeat_manual": heartbeat_manual}):
                decision = plan_autonomy(self.conn, owner_kind, owner_id, control, tick_id=tick_id, trace_id=trace.id, manual=planner_manual, now=now)
            ops = decision.get("proposed_ops") or []
            if not ops:
                return {"decision": decision, "commit": None}
            with trace.span("autonomy_commit", {"decision_id": decision["id"], "op_count": len(ops)}):
                commit = self._commit_ops_locked(ops, owner_kind, owner_id, "autonomy", session_id=None, turn_id=tick_id, trace=trace, control=control)
            updated = update_autonomy_decision_result(
                self.conn, decision["id"], status="committed",
                result_transaction_id=commit.get("transaction_id"),
                result_receipt_id=(commit.get("receipt") or {}).get("receipt_id"),
            )
            return {"decision": updated, "commit": commit}
        except Exception as exc:
            append_audit(self.conn, owner_kind, owner_id, "autonomy_failed", "warning", str(exc), {"mode": mode}, trace.id)
            return {"decision": None, "commit": None, "error": f"{type(exc).__name__}: {exc}"}

    def _run_proactive_for_tick(self, owner_kind: str, owner_id: str, control: dict[str, Any],
                                tick_id: str, trace: Trace, now: str) -> dict[str, Any]:
        if owner_kind != "agent":
            return {"evaluated": [], "reason": "not agent"}
        gates = control.get("module_gates") or {}
        mode = str(gates.get("proactive", "pending_only") or "pending_only")
        if mode == "off":
            return {"evaluated": [], "reason": "proactive off"}
        try:
            with trace.span("proactive_evaluate", {"mode": mode}):
                commit = self._commit_ops_locked([
                    {"type": "EXPIRE_PROACTIVE_INTENTS", "payload": {}},
                    {"type": "EVALUATE_PROACTIVE_INTENT", "payload": {"manual": False, "trace_id": trace.id}},
                ], owner_kind, owner_id, "proactive_heartbeat", session_id=None, turn_id=tick_id, trace=trace, control=control)
            return {"commit": commit}
        except Exception as exc:
            append_audit(self.conn, owner_kind, owner_id, "proactive_failed", "warning", str(exc), {"mode": mode}, trace.id)
            return {"error": f"{type(exc).__name__}: {exc}"}

    def autonomy(self, action: str = "list", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                 session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action in {"list", "decisions"}:
            with transaction(self.conn):
                return {"ok": True, "decisions": list_autonomy_decisions(self.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
        if action == "get":
            with transaction(self.conn):
                return {"ok": True, "decision": get_autonomy_decision(self.conn, payload["decision_id"])}
        if action in {"plan", "propose"}:
            with transaction(self.conn):
                control = ensure_control(self.conn, owner_kind, owner_id)
                trace = Trace(self.conn, owner_kind, owner_id, "autonomy", session_id=session_id, turn_id=turn_id,
                              engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                              input_obj={"action": action, "payload": payload}).start()
                try:
                    decision = plan_autonomy(self.conn, owner_kind, owner_id, control, tick_id=payload.get("tick_id"), trace_id=trace.id, manual=True, now=payload.get("now"))
                    trace.end(output_obj={"decision_id": decision["id"], "ops": len(decision.get("proposed_ops") or [])})
                    return {"ok": True, "decision": decision}
                except Exception as exc:
                    trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                    raise
        if action in {"run", "act", "commit"}:
            with transaction(self.conn):
                control = ensure_control(self.conn, owner_kind, owner_id)
                trace = Trace(self.conn, owner_kind, owner_id, "autonomy", session_id=session_id, turn_id=turn_id,
                              engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                              input_obj={"action": action, "payload": payload}).start()
                try:
                    decision = plan_autonomy(self.conn, owner_kind, owner_id, control, tick_id=payload.get("tick_id"), trace_id=trace.id, manual=True, now=payload.get("now"))
                    ops = decision.get("proposed_ops") or []
                    commit = None
                    if ops:
                        commit = self._commit_ops_locked(ops, owner_kind, owner_id, "autonomy", session_id=session_id, turn_id=turn_id, trace=trace, control=control)
                        decision = update_autonomy_decision_result(self.conn, decision["id"], status="committed", result_transaction_id=commit.get("transaction_id"), result_receipt_id=(commit.get("receipt") or {}).get("receipt_id"))
                    trace.end(output_obj={"decision_id": decision["id"], "committed": bool(commit)})
                    return {"ok": True, "decision": decision, "commit": commit}
                except Exception as exc:
                    trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                    raise
        raise ValueError(f"Unknown autonomy action: {action}")

    def execution(self, action: str = "list", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                  session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action in {"list", "decisions"}:
            with transaction(self.conn):
                return {"ok": True, "decisions": list_execution_decisions(self.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
        if action == "get":
            with transaction(self.conn):
                return {"ok": True, "decision": get_execution_decision(self.conn, payload["decision_id"])}
        if action in {"serendipity", "serendipity_list"}:
            with transaction(self.conn):
                return {"ok": True, "serendipity": list_serendipity_events(self.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
        if action in {"run", "simulate", "execute"}:
            with transaction(self.conn):
                control = ensure_control(self.conn, owner_kind, owner_id)
                trace = Trace(self.conn, owner_kind, owner_id, "execution", session_id=session_id, turn_id=turn_id,
                              engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                              input_obj={"action": action, "payload": payload}).start()
                try:
                    block_id = payload.get("schedule_block_id") or payload.get("block_id")
                    if block_id:
                        block_row = self.conn.execute("SELECT * FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?", (block_id, owner_kind, owner_id)).fetchone()
                    else:
                        block_row = self.conn.execute(
                            """SELECT * FROM schedule_blocks WHERE owner_kind=? AND owner_id=? AND status IN ('planned','locked','ready','in_progress')
                                 ORDER BY COALESCE(end_ts,start_ts,unixepoch(created_at)) ASC LIMIT 1""",
                            (owner_kind, owner_id),
                        ).fetchone()
                    if not block_row:
                        trace.end(status="blocked", output_obj={"reason": "no schedule block"})
                        return {"ok": False, "error": "schedule block not found"}
                    block = dict(block_row)
                    decision = simulate_schedule_block_execution(
                        self.conn, owner_kind, owner_id, control, tick_id=payload.get("tick_id"), trace_id=trace.id,
                        wake_job_id=payload.get("wake_job_id"), block=block, now=payload.get("now"), manual=True,
                    )
                    ops = decision.get("proposed_ops") or []
                    commit = None
                    if ops and action in {"run", "execute"}:
                        commit = self._commit_ops_locked(ops, owner_kind, owner_id, "execution_simulator", session_id=session_id, turn_id=turn_id, trace=trace, control=control)
                        decision = update_execution_decision_result(
                            self.conn, decision["id"], status="committed",
                            result_transaction_id=commit.get("transaction_id"),
                            result_receipt_id=(commit.get("receipt") or {}).get("receipt_id"),
                        )
                    trace.end(output_obj={"decision_id": decision["id"], "committed": bool(commit)})
                    return {"ok": True, "decision": decision, "commit": commit}
                except Exception as exc:
                    trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                    raise
        raise ValueError(f"Unknown execution action: {action}")


    # ----- user confirmation flow -----------------------------------------
    def confirmation(self, action: str, owner_kind: str = "user", owner_id: str = DEFAULT_USER_ID,
                     session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action == "list":
            with transaction(self.conn):
                return {"ok": True, "confirmations": list_confirmations(self.conn, owner_kind, owner_id, status=payload.get("status", "pending"), limit=int(payload.get("limit", 20)))}
        if action == "get":
            with transaction(self.conn):
                return {"ok": True, "confirmation": get_confirmation(self.conn, owner_kind, owner_id, payload["confirmation_id"])}
        if action == "propose":
            with transaction(self.conn):
                c = propose_confirmation(self.conn, owner_kind, owner_id, payload.get("ops") or payload.get("proposed_ops") or [], payload.get("reason") or "requires user confirmation", session_id, turn_id)
                return {"ok": True, "confirmation": c}
        if action == "reject":
            with transaction(self.conn):
                c = mark_confirmation(self.conn, owner_kind, owner_id, payload["confirmation_id"], "rejected", resolved_by=payload.get("resolved_by") or "user", note=payload.get("note"))
                return {"ok": True, "confirmation": c}
        if action == "confirm":
            # Commit LifeOps through the normal transaction service, then mark
            # the confirmation resolved.  This intentionally avoids a nested
            # SQLite BEGIN while keeping the durable fact write in LifeOps.
            row = self.conn.execute("SELECT * FROM user_confirmations WHERE id=? AND owner_kind=? AND owner_id=?", (payload["confirmation_id"], owner_kind, owner_id)).fetchone()
            if not row:
                raise ValueError(f"confirmation not found: {payload['confirmation_id']}")
            if row["status"] != "pending":
                raise ValueError(f"confirmation is not pending: {row['status']}")
            ops = confirmed_ops(loads(row["proposed_ops_json"], []))
            commit = self.commit_ops(ops, owner_kind, owner_id, "user_confirmed", session_id or row["session_id"], turn_id or row["turn_id"])
            with transaction(self.conn):
                c = mark_confirmation(self.conn, owner_kind, owner_id, payload["confirmation_id"], "confirmed", resolved_by=payload.get("resolved_by") or "user", note=payload.get("note"), result_transaction_id=commit.get("transaction_id"))
            return {"ok": True, "confirmation": c, "commit": commit}
        raise ValueError(f"Unknown confirmation action: {action}")

    # ----- inventory / entity resources -----------------------------------
    def inventory(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                  session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action == "list":
            with transaction(self.conn):
                return {"ok": True, "items": list_inventory(self.conn, owner_kind, owner_id, category=payload.get("category"), status=payload.get("status", "active"), limit=int(payload.get("limit", 50)))}
        if action == "movements":
            with transaction(self.conn):
                return {"ok": True, "movements": list_inventory_movements(self.conn, owner_kind, owner_id, item_id=payload.get("item_id"), limit=int(payload.get("limit", 50)))}
        if action == "meals":
            with transaction(self.conn):
                return {"ok": True, "meals": list_meals(self.conn, owner_kind, owner_id, meal_type=payload.get("meal_type"), limit=int(payload.get("limit", 30)))}
        if action in {"add", "create"}:
            return self.commit_ops([{"type": "CREATE_INVENTORY_ITEM", "payload": payload}], owner_kind, owner_id, "life_inventory_tool", session_id, turn_id)
        if action == "update":
            return self.commit_ops([{"type": "UPDATE_INVENTORY_ITEM", "payload": payload}], owner_kind, owner_id, "life_inventory_tool", session_id, turn_id)
        if action in {"delta", "consume", "discard", "move"}:
            p = dict(payload)
            if action == "consume":
                p.setdefault("operation", "consume")
                if "quantity_delta" not in p and "quantity" in p:
                    p["quantity_delta"] = -abs(float(p["quantity"]))
                p.pop("quantity", None)
            elif action == "discard":
                p.setdefault("operation", "discard")
                if "quantity_delta" not in p and "quantity" in p:
                    p["quantity_delta"] = -abs(float(p["quantity"]))
                p.pop("quantity", None)
            elif action == "move":
                p.setdefault("operation", "move")
                p.setdefault("quantity_delta", 0)
            return self.commit_ops([{"type": "INVENTORY_DELTA", "payload": p}], owner_kind, owner_id, "life_inventory_tool", session_id, turn_id)
        if action == "meal":
            return self.commit_ops([{"type": "CREATE_MEAL_RECORD", "payload": payload}], owner_kind, owner_id, "life_inventory_tool", session_id, turn_id)
        raise ValueError(f"Unknown inventory action: {action}")

    # ----- goals / life arcs / decomposition -------------------------------
    def goals(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
              session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        """Goal, life arc, dependency, decomposition, and reflection API."""
        if action in {"list", "goals"}:
            with transaction(self.conn):
                return {"ok": True, "goals": list_goals(self.conn, owner_kind, owner_id, status=payload.get("status"), arc_id=payload.get("arc_id"), limit=int(payload.get("limit", 20)))}
        if action in {"arcs", "list_arcs"}:
            with transaction(self.conn):
                arcs = list_life_arcs(self.conn, owner_kind, owner_id, status=payload.get("status"), limit=int(payload.get("limit", 20)))
                return {"ok": True, "arcs": arcs, "life_arcs": arcs}
        if action in {"dependencies", "list_dependencies"}:
            with transaction(self.conn):
                return {"ok": True, "dependencies": list_event_dependencies(self.conn, owner_kind, owner_id, parent_event_id=payload.get("parent_event_id"), child_event_id=payload.get("child_event_id") or payload.get("event_id"), limit=int(payload.get("limit", 50)))}
        if action == "progress" and payload.get("goal_id") and payload.get("progress") is None and payload.get("progress_delta") is None:
            with transaction(self.conn):
                return {"ok": True, "progress": compute_goal_progress(self.conn, owner_kind, owner_id, payload["goal_id"])}
        if action in {"create", "add", "create_goal"}:
            return self.commit_ops([{"type": "CREATE_GOAL", "payload": payload}], owner_kind, owner_id, "life_goal_tool", session_id, turn_id)
        if action in {"progress", "update_progress"}:
            return self.commit_ops([{"type": "UPDATE_GOAL_PROGRESS", "payload": payload}], owner_kind, owner_id, "life_goal_tool", session_id, turn_id)
        if action in {"arc", "create_arc"}:
            return self.commit_ops([{"type": "CREATE_LIFE_ARC", "payload": payload}], owner_kind, owner_id, "life_goal_tool", session_id, turn_id)
        if action in {"link", "link_event"}:
            return self.commit_ops([{"type": "LINK_EVENT_TO_GOAL", "payload": payload}], owner_kind, owner_id, "life_goal_tool", session_id, turn_id)
        if action in {"dependency", "add_dependency", "depend"}:
            p = dict(payload)
            if "event_id" not in p and p.get("child_event_id"):
                p["event_id"] = p["child_event_id"]
            if "depends_on_event_id" not in p and p.get("parent_event_id"):
                p["depends_on_event_id"] = p["parent_event_id"]
            return self.commit_ops([{"type": "CREATE_EVENT_DEPENDENCY", "payload": p}], owner_kind, owner_id, "life_goal_tool", session_id, turn_id)
        if action in {"decompose", "decompose_event", "decomposition"}:
            p = dict(payload)
            if "children" not in p and p.get("child_events"):
                p["children"] = p.pop("child_events")
            return self.commit_ops([{"type": "DECOMPOSE_EVENT", "payload": p}], owner_kind, owner_id, "life_goal_tool", session_id, turn_id)
        if action in {"recompute_event", "recompute_event_progress"}:
            return self.commit_ops([{"type": "RECOMPUTE_EVENT_PROGRESS", "payload": {"event_id": payload["event_id"]}}], owner_kind, owner_id, "life_goal_tool", session_id, turn_id)
        if action in {"reflect", "reflection"}:
            return self.commit_ops([{"type": "CREATE_REFLECTION", "payload": payload}], owner_kind, owner_id, "life_goal_tool", session_id, turn_id)
        if action in {"reflections", "list_reflections"}:
            with transaction(self.conn):
                return {"ok": True, "reflections": list_reflections(self.conn, owner_kind, owner_id, target_kind=payload.get("target_kind"), target_id=payload.get("target_id"), limit=int(payload.get("limit", 20)))}
        raise ValueError(f"Unknown goals action: {action}")

    def goal(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.goals(*args, **kwargs)

    # ----- diary / proactive / trace --------------------------------------
    def diary(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
              session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action == "write":
            return self.commit_ops([{"type": "CREATE_DIARY", "payload": payload}], owner_kind, owner_id, "life_diary_tool", session_id, turn_id)
        if action == "list":
            with transaction(self.conn):
                rows = self.conn.execute(
                    "SELECT * FROM diary_entries WHERE owner_kind=? AND owner_id=? ORDER BY date DESC, created_at DESC LIMIT ?",
                    (owner_kind, owner_id, int(payload.get("limit", 10))),
                ).fetchall()
                return {"ok": True, "diary": [dict(r) for r in rows]}
        raise ValueError(f"Unknown diary action: {action}")

    def _create_diary(self, owner_kind: str, owner_id: str, canon_version: int | None = None,
                      diary_type: str = "daily", date: str | None = None, content: str | None = None,
                      source_event_ids: list[str] | None = None, source_result_ids: list[str] | None = None,
                      source_resource_ledger_ids: list[str] | None = None, privacy: str = "agent_private") -> dict[str, Any]:
        date = date or now_iso()[:10]
        if not content:
            events = list_events(self.conn, owner_kind, owner_id, limit=5)
            content = "今日 LifeEngine 日记：" + ("；".join([f"{e['title']}({e['status']})" for e in events]) if events else "今天还没有足够的已提交生活事件。")
        diary_id = new_id("diary")
        self.conn.execute(
            """INSERT INTO diary_entries(id, owner_kind, owner_id, diary_type, date, source_event_ids_json,
                   source_result_ids_json, source_resource_ledger_ids_json, canon_version, content, privacy)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (diary_id, owner_kind, owner_id, diary_type, date, dumps(source_event_ids or []), dumps(source_result_ids or []),
             dumps(source_resource_ledger_ids or []), canon_version, content, privacy),
        )
        append_journal(self.conn, owner_kind, owner_id, "diary_created", {"diary_id": diary_id, "date": date}, "diary", canon_version=canon_version)
        return dict(self.conn.execute("SELECT * FROM diary_entries WHERE id=?", (diary_id,)).fetchone())

    def _create_proactive(self, agent_id: str, **payload: Any) -> dict[str, Any]:
        intent_id = new_id("proactive")
        self.conn.execute(
            """INSERT INTO proactive_intents(id, agent_id, target_type, target_id, trigger_event_id,
                   trigger_result_id, intent_type, summary, emotional_tone, importance, urgency, novelty,
                   relationship_relevance, privacy_level, status, delivery_policy_json, expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (intent_id, agent_id, payload.get("target_type", "self_journal"), payload.get("target_id"),
             payload.get("trigger_event_id"), payload.get("trigger_result_id"), payload.get("intent_type", "share_interesting"),
             payload.get("summary", ""), payload.get("emotional_tone"), int(payload.get("importance", 50)),
             int(payload.get("urgency", 50)), int(payload.get("novelty", 50)), int(payload.get("relationship_relevance", 50)),
             payload.get("privacy_level", "safe_to_share"), payload.get("status", "generated"), dumps(payload.get("delivery_policy", {})), payload.get("expires_at")),
        )
        append_journal(self.conn, "agent", agent_id, "proactive_intent_created", {"intent_id": intent_id}, "proactive")
        return dict(self.conn.execute("SELECT * FROM proactive_intents WHERE id=?", (intent_id,)).fetchone())

    def proactive(self, action: str = "list", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                  session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if owner_kind != "agent":
            raise ValueError("proactive operations are only valid for agent self-life")
        if action in {"list", "intents"}:
            with transaction(self.conn):
                return {"ok": True, "intents": list_proactive_intents(self.conn, owner_id, status=payload.get("status"), limit=int(payload.get("limit", 20)))}
        if action == "get":
            with transaction(self.conn):
                return {"ok": True, "intent": get_proactive_intent(self.conn, payload["intent_id"])}
        if action in {"create", "intent"}:
            return self.commit_ops([{ "type": "CREATE_PROACTIVE_INTENT", "payload": payload }], owner_kind, owner_id, "life_proactive_tool", session_id, turn_id)
        if action in {"evaluate", "queue"}:
            p = dict(payload)
            p.setdefault("manual", False)
            return self.commit_ops([{ "type": "EVALUATE_PROACTIVE_INTENT", "payload": p }], owner_kind, owner_id, "life_proactive_tool", session_id, turn_id)
        if action in {"send", "mark_sent", "sent"}:
            p = dict(payload)
            return self.commit_ops([{ "type": "MARK_PROACTIVE_SENT", "payload": p }], owner_kind, owner_id, "life_proactive_tool", session_id, turn_id)
        if action in {"suppress", "cancel"}:
            return self.commit_ops([{ "type": "SUPPRESS_PROACTIVE_INTENT", "payload": payload }], owner_kind, owner_id, "life_proactive_tool", session_id, turn_id)
        if action in {"expire", "expire_due"}:
            return self.commit_ops([{ "type": "EXPIRE_PROACTIVE_INTENTS", "payload": payload }], owner_kind, owner_id, "life_proactive_tool", session_id, turn_id)
        if action == "outbox":
            with transaction(self.conn):
                return {"ok": True, "outbox": list_outbox(self.conn, owner_id, status=payload.get("status"), limit=int(payload.get("limit", 20)))}
        if action in {"state", "states"}:
            with transaction(self.conn):
                if payload.get("target_user_id") or payload.get("user_id"):
                    return {"ok": True, "state": ensure_proactive_state(self.conn, owner_id, payload.get("target_user_id") or payload.get("user_id"))}
                return {"ok": True, "states": list_proactive_states(self.conn, owner_id, limit=int(payload.get("limit", 20)))}
        raise ValueError(f"Unknown proactive action: {action}")

    def upgrade(self, action: str = "check", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID, **payload: Any) -> dict[str, Any]:
        """Install/upgrade/maintenance helper. Does not create durable life facts.

        Backup/export/import/restore/smoke actions intentionally run outside
        the outer BEGIN IMMEDIATE transaction because sqlite3.Connection.backup()
        can block when invoked from the same connection while a write
        transaction is open.  The helper functions use autocommit writes for
        their diagnostic rows.
        """
        if action in {"backup", "backup_db"}:
            return backup_database(
                self.conn,
                owner_kind,
                owner_id,
                reason=str(payload.get("reason") or "manual"),
                destination=payload.get("destination"),
            )
        if action in {"export", "export_profile"}:
            return export_profile_archive(
                self.conn,
                owner_kind,
                owner_id,
                destination=payload.get("destination"),
                include_package_manifest=bool(payload.get("include_package_manifest", True)),
            )
        if action in {"import", "import_profile", "stage_import"}:
            return stage_profile_import(self.conn, owner_kind, owner_id, archive_path=str(payload.get("archive_path") or payload.get("path") or ""))
        if action in {"restore", "stage_restore", "restore_plan"}:
            return stage_restore_plan(self.conn, owner_kind, owner_id, archive_path=str(payload.get("archive_path") or payload.get("path") or ""))
        if action in {"large_smoke", "large_db_smoke", "smoke"}:
            return large_db_smoke(self.conn, owner_kind, owner_id, memories=int(payload.get("memories", 250)))
        if action in {"cron_test", "heartbeat_test", "test_tick_script"}:
            script = payload.get("script_path")
            if not script:
                script = str(write_tick_script())
            return run_tick_script_test(self.conn, owner_kind, owner_id, script_path=str(script), timeout=int(payload.get("timeout", 30)))
        if action in {"concurrency_smoke", "parallel_commit_smoke"}:
            return run_parallel_commit_smoke(self.conn, owner_kind, owner_id, workers=int(payload.get("workers", 8)))
        if action in {"schedule_overlap_smoke", "parallel_schedule_overlap"}:
            return run_parallel_schedule_overlap_smoke(self.conn, owner_kind, owner_id, workers=int(payload.get("workers", 6)))
        if action in {"heartbeat_idempotency_smoke", "parallel_heartbeat_smoke"}:
            return run_parallel_heartbeat_idempotency_smoke(self.conn, owner_kind, owner_id, workers=int(payload.get("workers", 6)))
        if action in {"lifeops_stress", "stress_smoke"}:
            return run_lifeops_stress_smoke(self.conn, owner_kind, owner_id, items=int(payload.get("items", payload.get("events", 200))))
        if action in {"acceptance", "acceptance_suite", "acceptance_scenarios", "v1_rc_check", "v1_rc_acceptance"}:
            return run_acceptance_suite(self, owner_kind, owner_id, report_path=payload.get("report_path") or payload.get("path"), include_details=bool(payload.get("include_details", True)))
        with transaction(self.conn):
            if action in {"integration_check", "integration_smoke", "integration_acceptance", "hermes_integration", "surface_check"}:
                return run_integration_check(self.conn, owner_kind, owner_id, write_audit=bool(payload.get("write_audit", True)), include_details=bool(payload.get("include_details", False)))
            if action in {"surface", "public_surface", "api_surface"}:
                return {"ok": True, "surface": public_surface(self.conn)}
            if action in {"api_freeze", "api_freeze_snapshot", "freeze_snapshot"}:
                return create_api_freeze_snapshot(self.conn, owner_kind, owner_id, include_schemas=bool(payload.get("include_schemas", True)))
            if action in {"api_freeze_status", "freeze_status", "freeze_snapshots"}:
                if payload.get("snapshot_id"):
                    return get_api_freeze_snapshot(self.conn, str(payload.get("snapshot_id")))
                return list_api_freeze_snapshots(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"mandatory_gate_patch", "core_patch", "core_patch_draft"}:
                return create_core_patch_draft(self.conn, owner_kind, owner_id, patch_name=str(payload.get("patch_name") or "mandatory_final_gate"))
            if action in {"core_patches", "patches", "list_core_patches"}:
                return list_core_patch_drafts(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"release_readiness", "release_check", "all"}:
                return release_readiness(self.conn, owner_kind, owner_id)
            if action in {"acceptance_reports", "list_acceptance_reports"}:
                return list_acceptance_reports(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"acceptance_report", "get_acceptance_report"}:
                report_id = payload.get("report_id") or payload.get("id")
                if not report_id:
                    raise ValueError("report_id is required")
                return get_acceptance_report(self.conn, str(report_id))
            if action in {"acceptance_runs", "acceptance_scenario_runs", "list_acceptance_scenarios"}:
                return list_acceptance_scenarios(self.conn, owner_kind, owner_id, acceptance_run_id=payload.get("acceptance_run_id"), limit=int(payload.get("limit", 50)))
            if action in {"v1_rc_checklists", "v1_rc_checklist"}:
                return latest_v1_rc_checklists(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"status", "migrations", "migration_status"}:
                return {"ok": True, "migrations": migration_history(self.conn)}
            if action in {"check", "install_check", "upgrade_check", "run"}:
                return run_upgrade_check(
                    self.conn,
                    owner_kind,
                    owner_id,
                    include_details=bool(payload.get("include_details", False)),
                    write_audit=bool(payload.get("write_audit", True)),
                )
            if action in {"backup", "backup_db"}:
                return backup_database(
                    self.conn,
                    owner_kind,
                    owner_id,
                    reason=str(payload.get("reason") or "manual"),
                    destination=payload.get("destination"),
                )
            if action in {"backups", "list_backups"}:
                return list_backups(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"rebuild_memory", "rebuild_indexes", "rebuild"}:
                return rebuild_memory_indexes(self.conn, owner_kind, owner_id)
            if action in {"verify_memory", "verify_indexes", "verify_memory_indexes"}:
                return verify_memory_indexes(self.conn, owner_kind, owner_id)
            if action in {"export", "export_profile"}:
                return export_profile_archive(
                    self.conn,
                    owner_kind,
                    owner_id,
                    destination=payload.get("destination"),
                    include_package_manifest=bool(payload.get("include_package_manifest", True)),
                )
            if action in {"exports", "list_exports"}:
                return list_profile_exports(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"inspect_export", "inspect_import"}:
                return inspect_profile_export(str(payload.get("archive_path") or payload.get("path") or ""))
            if action in {"import", "import_profile", "stage_import"}:
                return stage_profile_import(self.conn, owner_kind, owner_id, archive_path=str(payload.get("archive_path") or payload.get("path") or ""))
            if action in {"restore", "stage_restore", "restore_plan"}:
                return stage_restore_plan(self.conn, owner_kind, owner_id, archive_path=str(payload.get("archive_path") or payload.get("path") or ""))
            if action in {"package", "package_manifest", "package_check", "checksum"}:
                return record_package_manifest(self.conn, owner_kind, owner_id, root=payload.get("root"))
            if action in {"large_smoke", "large_db_smoke", "smoke"}:
                return large_db_smoke(self.conn, owner_kind, owner_id, memories=int(payload.get("memories", 250)))
            if action in {"maintenance", "maintenance_runs"}:
                return list_maintenance_runs(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"concurrency_runs", "list_concurrency_runs"}:
                return list_concurrency_runs(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"stress_runs", "list_stress_runs"}:
                return list_stress_runs(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"cron_test", "heartbeat_test", "test_tick_script"}:
                script = payload.get("script_path")
                if not script:
                    script = str(write_tick_script())
                return run_tick_script_test(self.conn, owner_kind, owner_id, script_path=str(script), timeout=int(payload.get("timeout", 30)))
            raise ValueError("unknown upgrade action")

    def maintenance(self, action: str = "install_check", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID, **payload: Any) -> dict[str, Any]:
        """Backward-compatible alias for v0.9.2 upgrade checks."""
        if action in {"all", "release_check"}:
            install = self.upgrade("check", owner_kind, owner_id, **payload)
            migrations = self.upgrade("status", owner_kind, owner_id)
            cron = self.upgrade("cron_test", owner_kind, owner_id, **payload)
            ok = bool(install.get("ok") and migrations.get("ok") and cron.get("ok"))
            return {"ok": ok, "install_check": install, "migration_status": migrations, "heartbeat_cron_test": cron}
        return self.upgrade(action, owner_kind, owner_id, **payload)


    def doctor(self, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
               level: str = "full", include_samples: bool = False, write_audit: bool = True) -> dict[str, Any]:
        """Run LifeEngine diagnostics for release-hardening and installs.

        The doctor does not repair life state.  It checks embedded dependencies,
        schema shape, control/canon state, resource ledger drift, wake-job
        stuckness, proactive/user queues, and the deeper invariant pass.  The
        deep pass writes a ``life_invariant_checks`` row so operators can audit
        which profile was considered healthy at a point in time.
        """
        with transaction(self.conn):
            control = ensure_control(self.conn, owner_kind, owner_id)
            trace = Trace(
                self.conn,
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
                    sqlite_version, vec_version = self.conn.execute("SELECT sqlite_version(), vec_version()").fetchone()
                    add("sqlite_vec", "ok", f"sqlite={sqlite_version}, sqlite-vec={vec_version}", sqlite_version=sqlite_version, vec_version=vec_version)
                except Exception as exc:
                    add("sqlite_vec", "error", f"sqlite-vec is not loaded: {type(exc).__name__}: {exc}")

                user_version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
                add(
                    "schema_version",
                    "ok" if user_version == _SCHEMA_VERSION else "error",
                    f"user_version={user_version}, expected={_SCHEMA_VERSION}",
                    current=user_version,
                    expected=_SCHEMA_VERSION,
                )

                try:
                    migration_rows = self.conn.execute(
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

                required_tables = [
                    "controls", "canon_versions", "canon_drafts", "life_transactions", "life_ops",
                    "life_journal", "trace_runs", "trace_spans", "commit_receipts",
                    "resource_definitions", "resource_accounts", "resource_ledger",
                    "events", "schedule_blocks", "wake_jobs", "truth_source_reads",
                    "inventory_items", "goals", "autonomy_decisions", "proactive_intents",
                    "execution_decisions", "serendipity_events", "memory_vec", "life_invariant_checks", "schema_migrations", "install_checks", "final_gate_reports", "concurrency_test_runs", "stress_test_runs", "integration_test_runs", "api_freeze_snapshots", "core_patch_drafts", "acceptance_scenario_runs", "acceptance_reports", "v1_rc_checklists",
                ]
                existing = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()}
                missing = [t for t in required_tables if t not in existing]
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
                    "ok" if final_audit in {"strict", "trace", "repair"} else "warn",
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

                hash_check = verify_journal_hash_chain(self.conn, owner_kind, owner_id)
                add("journal_hash_chain", "ok" if hash_check.get("ok") else "error", hash_check.get("message", ""), data=hash_check if include_samples or not hash_check.get("ok") else {"checked_entries": hash_check.get("checked_entries")})

                # Read-only resource reconcile for concise doctor output.
                res_check = reconcile_resources(self.conn, owner_kind, owner_id, record=False)
                mismatches = res_check.get("mismatches") or []
                add(
                    "resource_ledger",
                    "ok" if res_check.get("ok") else "error",
                    f"checked {res_check.get('checked', 0)} resources" if not mismatches else f"{len(mismatches)} resource mismatches",
                    checked=res_check.get("checked", 0),
                    mismatches=mismatches if include_samples or mismatches else [],
                )

                running_jobs = self.conn.execute(
                    "SELECT id,reason,wake_at,status,running_at FROM wake_jobs WHERE owner_kind=? AND owner_id=? AND status='running' ORDER BY running_at LIMIT 5",
                    (owner_kind, owner_id),
                ).fetchall()
                pending_jobs = self.conn.execute(
                    "SELECT COUNT(*) FROM wake_jobs WHERE owner_kind=? AND owner_id=? AND status='pending'",
                    (owner_kind, owner_id),
                ).fetchone()[0]
                add("wake_jobs", "warn" if running_jobs else "ok", f"pending={pending_jobs}, running={len(running_jobs)}", pending=pending_jobs, running=len(running_jobs), samples=[dict(r) for r in running_jobs] if include_samples else [])

                if owner_kind == "user":
                    pending_conf = self.conn.execute(
                        "SELECT COUNT(*) FROM user_confirmations WHERE owner_kind=? AND owner_id=? AND status='pending'",
                        (owner_kind, owner_id),
                    ).fetchone()[0]
                    add("user_confirmations", "warn" if pending_conf else "ok", f"pending={pending_conf}", pending=pending_conf)
                if owner_kind == "agent":
                    queued_outbox = self.conn.execute(
                        "SELECT COUNT(*) FROM proactive_outbox WHERE agent_id=? AND status='queued'",
                        (owner_id,),
                    ).fetchone()[0]
                    pending_intents = self.conn.execute(
                        "SELECT COUNT(*) FROM proactive_intents WHERE agent_id=? AND status IN ('generated','queued')",
                        (owner_id,),
                    ).fetchone()[0]
                    add("proactive_queue", "warn" if queued_outbox > 20 else "ok", f"pending_intents={pending_intents}, queued_outbox={queued_outbox}", pending_intents=pending_intents, queued_outbox=queued_outbox)

                try:
                    from pathlib import Path
                    plugin_dir = Path(__file__).resolve().parent
                    pycache_count = len(list(plugin_dir.rglob("__pycache__")))
                    add("package_hygiene", "warn" if pycache_count else "ok", "runtime package has no __pycache__ directories" if not pycache_count else f"found {pycache_count} __pycache__ directories", pycache_count=pycache_count)
                except Exception as exc:
                    add("package_hygiene", "warn", f"could not inspect package hygiene: {exc}")

                try:
                    deep = run_invariant_doctor(self.conn, owner_kind, owner_id)
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
                    append_audit(self.conn, owner_kind, owner_id, "doctor", "info" if worst == "ok" else worst, f"LifeEngine doctor status={worst}", out, trace.id)
                    self.conn.execute(
                        "INSERT INTO install_checks(id, owner_kind, owner_id, check_type, status, payload_json) VALUES(?,?,?,?,?,?)",
                        (new_id("installcheck"), owner_kind, owner_id, "doctor", public_status, dumps({"checks": len(checks), "schema_version": _SCHEMA_VERSION, "plugin_version": PLUGIN_VERSION})),
                    )
                trace.end(status="ok" if worst != "error" else "error", output_obj={"status": worst, "checks": len(checks)})
                return out
            except Exception as exc:
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                raise

    def traces(self, action: str = "latest", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID, **payload: Any) -> dict[str, Any]:
        with transaction(self.conn):
            if action == "latest":
                rows = self.conn.execute(
                    "SELECT * FROM trace_runs WHERE owner_kind=? AND owner_id=? ORDER BY started_at DESC LIMIT ?",
                    (owner_kind, owner_id, int(payload.get("limit", 10))),
                ).fetchall()
                return {"ok": True, "traces": [dict(r) for r in rows]}
            if action == "explain":
                trace_id = payload.get("trace_id")
                tx_id = payload.get("transaction_id")
                event_id = payload.get("event_id")
                if trace_id:
                    run = self.conn.execute("SELECT * FROM trace_runs WHERE id=?", (trace_id,)).fetchone()
                    spans = self.conn.execute("SELECT * FROM trace_spans WHERE trace_id=? ORDER BY started_at", (trace_id,)).fetchall()
                    return {"ok": True, "trace": dict(run) if run else None, "spans": [dict(s) for s in spans]}
                if tx_id:
                    tx = self.conn.execute("SELECT * FROM life_transactions WHERE id=?", (tx_id,)).fetchone()
                    ops = self.conn.execute("SELECT * FROM life_ops WHERE transaction_id=? ORDER BY created_at", (tx_id,)).fetchall()
                    journal = self.conn.execute("SELECT * FROM life_journal WHERE transaction_id=? ORDER BY created_at", (tx_id,)).fetchall()
                    receipt = self.conn.execute("SELECT * FROM commit_receipts WHERE transaction_id=?", (tx_id,)).fetchone()
                    facts = self.conn.execute("SELECT * FROM commit_receipt_facts WHERE transaction_id=? ORDER BY created_at", (tx_id,)).fetchall()
                    return {"ok": True, "transaction": dict(tx) if tx else None, "ops": [dict(o) for o in ops], "journal": [dict(j) for j in journal], "receipt": dict(receipt) if receipt else None, "facts": [dict(f) for f in facts], "receipt_facts": [dict(f) for f in facts]}
                if event_id:
                    event = self.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
                    actions = self.conn.execute("SELECT * FROM actions WHERE event_id=? ORDER BY created_at", (event_id,)).fetchall()
                    results = self.conn.execute("SELECT * FROM results WHERE event_id=? ORDER BY created_at", (event_id,)).fetchall()
                    ledger = self.conn.execute("SELECT * FROM resource_ledger WHERE event_id=? ORDER BY created_at", (event_id,)).fetchall()
                    schedules = self.conn.execute("SELECT * FROM schedule_blocks WHERE event_id=? ORDER BY start_ts, created_at", (event_id,)).fetchall()
                    schedule_ids = [r["id"] for r in schedules]
                    wake_jobs = []
                    if schedule_ids:
                        placeholders = ",".join("?" for _ in schedule_ids)
                        wake_jobs = self.conn.execute(f"SELECT * FROM wake_jobs WHERE target_id IN ({placeholders}) ORDER BY wake_at_ts, created_at", schedule_ids).fetchall()
                    memories = self.conn.execute("SELECT * FROM memories WHERE event_id=? ORDER BY created_at", (event_id,)).fetchall()
                    goal_links = self.conn.execute("SELECT * FROM event_goal_links WHERE event_id=? ORDER BY created_at", (event_id,)).fetchall()
                    dependencies = self.conn.execute("SELECT * FROM event_dependencies WHERE event_id=? OR depends_on_event_id=? ORDER BY created_at", (event_id, event_id)).fetchall()
                    execution_decisions = self.conn.execute("SELECT * FROM execution_decisions WHERE event_id=? OR schedule_block_id IN (SELECT id FROM schedule_blocks WHERE event_id=?) ORDER BY created_at", (event_id, event_id)).fetchall()
                    serendipity = self.conn.execute("SELECT * FROM serendipity_events WHERE event_id=? OR trigger_event_id=? ORDER BY created_at", (event_id, event_id)).fetchall()
                    proactive = self.conn.execute("SELECT * FROM proactive_intents WHERE trigger_event_id=? ORDER BY created_at", (event_id,)).fetchall()
                    diary = self.conn.execute("SELECT * FROM diary_entries WHERE source_event_ids_json LIKE ? ORDER BY created_at", (f"%{event_id}%",)).fetchall()
                    journal = self.conn.execute("SELECT * FROM life_journal WHERE payload_json LIKE ? ORDER BY created_at", (f"%{event_id}%",)).fetchall()
                    return {
                        "ok": True,
                        "event": dict(event) if event else None,
                        "actions": [dict(a) for a in actions],
                        "results": [dict(r) for r in results],
                        "resource_ledger": [dict(l) for l in ledger],
                        "schedule_blocks": [dict(s) for s in schedules],
                        "wake_jobs": [dict(w) for w in wake_jobs],
                        "memories": [dict(m) for m in memories],
                        "goal_links": [dict(g) for g in goal_links],
                        "dependencies": [dict(d) for d in dependencies],
                        "execution_decisions": [dict(d) for d in execution_decisions],
                        "serendipity": [dict(x) for x in serendipity],
                        "proactive_intents": [dict(p) for p in proactive],
                        "diary_entries": [dict(d) for d in diary],
                        "journal": [dict(j) for j in journal],
                    }
                raise ValueError("explain requires trace_id, transaction_id, or event_id")
            if action == "verify":
                result = verify_journal_hash_chain(self.conn, owner_kind, owner_id)
                self.conn.execute(
                    "INSERT INTO trace_integrity_checks(id, owner_kind, owner_id, status, checked_entries, first_bad_journal_id, message) VALUES(?,?,?,?,?,?,?)",
                    (new_id("integrity"), owner_kind, owner_id, "ok" if result.get("ok") else "failed", int(result.get("checked_entries", 0)), result.get("first_bad_journal_id"), result.get("message")),
                )
                return result
            if action == "migrations":
                canon_migrations = list_migrations(self.conn, owner_kind, owner_id, int(payload.get("limit", 10)))
                schema_rows = self.conn.execute("SELECT * FROM schema_migrations ORDER BY created_at DESC LIMIT ?", (int(payload.get("limit", 10)),)).fetchall()
                return {"ok": True, "migrations": canon_migrations, "canon_migrations": canon_migrations, "schema_migrations": [dict(r) for r in schema_rows]}
            if action == "doctor":
                return run_invariant_doctor(self.conn, owner_kind, owner_id)
            if action == "receipts":
                rows = self.conn.execute(
                    "SELECT * FROM commit_receipts WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
                    (owner_kind, owner_id, int(payload.get("limit", 20))),
                ).fetchall()
                return {"ok": True, "receipts": [dict(r) for r in rows]}
            if action == "audit":
                rows = self.conn.execute(
                    "SELECT * FROM audit_log WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
                    (owner_kind, owner_id, int(payload.get("limit", 20))),
                ).fetchall()
                return {"ok": True, "audit": [dict(r) for r in rows]}
            raise ValueError(f"Unknown trace action: {action}")

    # ----- hook helpers ----------------------------------------------------
    def build_context_for_turn(self, session_id: str | None, turn_id: str | None, user_message: str,
                               sender_id: str | None = None, platform: str | None = None,
                               model: str | None = None) -> str:
        scope = resolve_owner_scope({}, {"session_id": session_id, "turn_id": turn_id, "sender_id": sender_id, "platform": platform})
        owner_kind, owner_id = scope.owner_kind, scope.owner_id
        with transaction(self.conn):
            control = ensure_control(self.conn, owner_kind, owner_id)
            trace = Trace(self.conn, owner_kind, owner_id, "pre_llm_call", session_id=session_id, turn_id=turn_id,
                          engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                          input_obj={"user_message": user_message, "scope": scope.__dict__, "platform": platform, "model": model}).start()
            try:
                if control["engine_state"] in SETUP_STATES:
                    if user_message and not user_message.strip().startswith("/"):
                        draft = append_setup_statement(self.conn, owner_kind, owner_id, user_message, "user")
                    else:
                        draft_id = control.get("draft_canon_id")
                        draft = get_draft(self.conn, draft_id) if draft_id else begin_setup(self.conn, owner_kind, owner_id)
                    out = _render_setup_context(control, draft, scope)
                    trace.end(output_obj={"mode": "setup", "draft_id": draft["id"]})
                    return out
                canon = get_active_canon(self.conn, owner_kind, owner_id)
                memories = search_memories(self.conn, owner_kind, owner_id, user_message or "", 5)
                events = list_events(self.conn, owner_kind, owner_id, limit=8)
                resources = list_resources(self.conn, owner_kind, owner_id)
                goals = list_goals(self.conn, owner_kind, owner_id, limit=5)
                arcs = list_life_arcs(self.conn, owner_kind, owner_id, limit=5)
                truth_sources = list_truth_sources(self.conn, owner_kind, owner_id, 5)
                inventory = list_inventory(self.conn, owner_kind, owner_id, limit=8)
                confirmations = list_confirmations(self.conn, owner_kind, owner_id, limit=5) if owner_kind == "user" else []
                pending = list_proactive_intents(self.conn, owner_id, status="queued", limit=3) if owner_kind == "agent" else []
                proactive_outbox = list_outbox(self.conn, owner_id, status="queued", limit=3) if owner_kind == "agent" else []
                proactive_states = list_proactive_states(self.conn, owner_id, limit=3) if owner_kind == "agent" else []
                autonomy = list_autonomy_decisions(self.conn, owner_kind, owner_id, limit=3) if owner_kind == "agent" else []
                execution = list_execution_decisions(self.conn, owner_kind, owner_id, limit=3)
                serendipity = list_serendipity_events(self.conn, owner_kind, owner_id, limit=3)
                final_gate_feedback = consume_final_gate_feedback(self.conn, owner_kind, owner_id, limit=3)
                out = _render_life_context(control, canon, events, resources, memories, pending, scope, truth_sources, inventory=inventory, confirmations=confirmations, goals=goals, arcs=arcs, autonomy=autonomy, proactive_outbox=proactive_outbox if owner_kind == "agent" else [], proactive_states=proactive_states if owner_kind == "agent" else [], execution=execution, serendipity=serendipity, final_gate_feedback=final_gate_feedback)
                trace.end(output_obj={"mode": control["engine_state"], "memories": len(memories), "events": len(events)})
                return out
            except Exception as exc:
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                raise

    def audit_final_output(self, response_text: str, session_id: str | None = None, turn_id: str | None = None,
                           model: str | None = None, platform: str | None = None, sender_id: str | None = None) -> str | None:
        """Final answer audit hook.

        v0.9.5 default: advisory.  The hook records unsupported hard claims and
        queues internal feedback for the next model turn, but does not replace
        the user's visible answer unless the operator explicitly sets
        final_audit to strict/repair/warn.
        """
        scope = resolve_owner_scope({}, {"session_id": session_id, "turn_id": turn_id, "sender_id": sender_id, "platform": platform})
        owner_kind, owner_id = scope.owner_kind, scope.owner_id
        with transaction(self.conn):
            control = ensure_control(self.conn, owner_kind, owner_id)
            trace = Trace(self.conn, owner_kind, owner_id, "final_audit", session_id=session_id, turn_id=turn_id,
                          engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                          input_obj={"response_text": response_text[:2000], "scope": scope.__dict__}).start()
            try:
                if control["engine_state"] in SETUP_STATES:
                    trace.end(status="ok", output_obj={"mode": "setup_passthrough"})
                    return None
                gates = control.get("module_gates") or {}
                mode = str(gates.get("final_audit", "advisory") or "advisory").lower()
                if mode == "trace":
                    # Back-compat alias.  Advisory is clearer: record, inform the
                    # next agent turn, but do not replace the user-visible reply.
                    mode = "advisory"
                if mode == "off" or control["engine_state"] != "active":
                    trace.end(status="ok", output_obj={"mode": "off_or_inactive"})
                    return None

                report = evaluate_final_response(self.conn, owner_kind, owner_id, response_text, session_id, turn_id)
                if not report.get("claims"):
                    trace.end(status="ok", output_obj={"claims": 0})
                    return None
                if report.get("ok") and not report.get("advisory"):
                    report = write_final_gate_report(self.conn, owner_kind, owner_id, session_id, turn_id, mode, "passed", response_text, report, trace.id)
                    trace.end(status="ok", output_obj={"claims": len(report.get("claims") or []), "report_id": report.get("report_id")})
                    return None

                if report.get("ok") and report.get("advisory"):
                    status = "advisory"
                elif mode == "advisory":
                    status = "advisory"
                elif mode == "warn":
                    status = "warned"
                else:
                    attempts = final_gate_intervention_count(self.conn, owner_kind, owner_id, session_id, turn_id)
                    status = "released_after_budget" if attempts >= 3 else ("repaired" if mode == "repair" else "blocked")

                report = write_final_gate_report(self.conn, owner_kind, owner_id, session_id, turn_id, mode, status, response_text, report, trace.id)
                enqueue_final_gate_feedback(self.conn, owner_kind, owner_id, report, session_id, turn_id)
                append_audit(self.conn, owner_kind, owner_id, "uncommitted_life_claim", "warning",
                             "Final response contained life claims requiring advisory follow-up",
                             {"report_id": report.get("report_id"), "status": status, "claims": report.get("claims"), "unsupported": report.get("unsupported"), "advisory": report.get("advisory"), "response_preview": response_text[:500]}, trace.id)
                trace.end(status=status, output_obj={"report_id": report.get("report_id"), "claims": report.get("claims"), "unsupported": report.get("unsupported"), "advisory": report.get("advisory")})

                # Default behavior: never show raw gate diagnostics to the user.
                if status in {"advisory", "released_after_budget"}:
                    return None
                if mode == "warn":
                    return response_text + build_repair_message(report, mode="warn")
                if mode in {"strict", "repair"}:
                    return build_repair_message(report, mode=mode)
                return None
            except Exception as exc:
                # Fail open: a diagnostic system must not replace the user's
                # answer with an internal error.  The trace/audit record remains.
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                append_audit(self.conn, owner_kind, owner_id, "final_gate_error", "error", str(exc), {"response_preview": response_text[:500]}, trace.id)
                return None

    def final_gate(self, action: str = "check", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                   session_id: str | None = None, turn_id: str | None = None, **payload) -> dict[str, Any]:
        with transaction(self.conn):
            if action in {"check", "audit", "simulate"}:
                response_text = payload.get("response_text") or payload.get("text") or ""
                mode = payload.get("mode") or (ensure_control(self.conn, owner_kind, owner_id).get("module_gates") or {}).get("final_audit", "strict")
                report = evaluate_final_response(self.conn, owner_kind, owner_id, response_text, session_id, turn_id)
                if payload.get("write_report", True):
                    status = "passed" if report.get("ok") and not report.get("advisory") else "advisory" if mode in {"advisory", "trace"} or report.get("advisory") else "blocked"
                    report = write_final_gate_report(self.conn, owner_kind, owner_id, session_id, turn_id, mode, status, response_text, report, None)
                    if not report.get("ok") or report.get("advisory"):
                        enqueue_final_gate_feedback(self.conn, owner_kind, owner_id, report, session_id, turn_id)
                return {"ok": True, "report": report, "repair_message": "" if report.get("ok") and mode not in {"strict", "repair", "warn"} else build_repair_message(report, mode)}
            if action in {"reports", "list"}:
                return {"ok": True, "reports": list_final_gate_reports(self.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
            if action in {"get", "explain"}:
                report_id = payload.get("report_id") or payload.get("id")
                if not report_id:
                    raise ValueError("report_id is required")
                report = get_final_gate_report(self.conn, report_id)
                if not report:
                    raise ValueError(f"FinalGate report not found: {report_id}")
                return {"ok": True, "report": report}
            raise ValueError(f"Unknown final_gate action: {action}")


def _detect_life_claims(text: str) -> list[str]:
    patterns = [
        r"我(?:今天|明天|昨天|刚才|中午|晚上|早上|下午|周末|下周|6\s*月|\d+\s*月).*?(?:吃|买|去|做|学|练|睡|计划|准备|推迟|取消|完成|花|用了|写了)",
        r"I\s+(?:ate|bought|went|will|plan|planned|finished|postponed|cancelled|slept|studied|spent|wrote)",
        r"我的(?:钱包|衣柜|精力|心情|计划|日程|资源|日记|目标|生活弧线).*?(?:。|$)",
    ]
    claims = []
    for p in patterns:
        for m in re.finditer(p, text, flags=re.IGNORECASE | re.DOTALL):
            claims.append(m.group(0)[:240])
    return claims[:5]


def _brief_draft(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": draft["id"],
        "status": draft["status"],
        "base_version": draft.get("base_version"),
        "extracted": draft.get("extracted", {}),
        "unresolved_questions": draft.get("unresolved_questions", []),
        "conflicts": draft.get("conflicts", []),
        "statement_count": len(draft.get("raw_user_statements", [])),
    }


def _render_setup_context(control: dict[str, Any], draft: dict[str, Any], scope: OwnerScope | None = None) -> str:
    return (
        "\n<LIFEENGINE_SETUP_CONTEXT>\n"
        f"engine_state: {control['engine_state']}\n"
        f"owner_scope: {pretty(scope.__dict__ if scope else {})}\n"
        "LifeEngine is in setup mode. Do not create life events, resources ledger deltas, diary entries, or memories except CanonDraft.\n"
        "User natural-language settings in this turn are being stored as CanonDraft input.\n"
        "Current CanonDraft brief:\n"
        f"{pretty(_brief_draft(draft))}\n"
        "Ask only for missing settings or summarize what has been captured.\n"
        "</LIFEENGINE_SETUP_CONTEXT>"
    )


def _render_life_context(control: dict[str, Any], canon: dict[str, Any], events: list[dict[str, Any]],
                         resources: dict[str, Any], memories: list[dict[str, Any]], pending: list[dict[str, Any]],
                         scope: OwnerScope | None = None, truth_sources: dict[str, Any] | None = None,
                         inventory: list[dict[str, Any]] | None = None, confirmations: list[dict[str, Any]] | None = None,
                         goals: list[dict[str, Any]] | None = None, arcs: list[dict[str, Any]] | None = None,
                         autonomy: list[dict[str, Any]] | None = None,
                         proactive_outbox: list[dict[str, Any]] | None = None, proactive_states: list[dict[str, Any]] | None = None,
                         execution: list[dict[str, Any]] | None = None, serendipity: list[dict[str, Any]] | None = None,
                         final_gate_feedback: list[dict[str, Any]] | None = None) -> str:
    compact_accounts = [
        {"key": a["resource_key"], "value": a["current_value"], "unit": a.get("unit"), "state": a.get("state")}
        for a in resources.get("accounts", [])[:20]
    ]
    compact_events = [
        {"id": e["id"], "title": e["title"], "status": e["status"], "planned_start": e.get("planned_start"), "progress": e.get("progress")}
        for e in events[:8]
    ]
    compact_mem = [{"id": m["id"], "type": m["memory_type"], "content": m["content"][:300]} for m in memories[:5]]
    compact_inventory = [{"id": i["id"], "name": i["name"], "category": i["category"], "quantity": i["quantity"], "unit": i.get("unit"), "condition": i.get("condition"), "location": i.get("location")} for i in (inventory or [])[:8]]
    compact_confirmations = [{"id": c["id"], "reason": c.get("reason"), "status": c.get("status"), "proposed_ops": c.get("proposed_ops", [])[:2]} for c in (confirmations or [])[:5]]
    compact_goals = [{"id": g["id"], "title": g["title"], "status": g["status"], "progress": g["progress"], "priority": g["priority"], "target_date": g.get("target_date")} for g in (goals or [])[:5]]
    compact_arcs = [{"id": a["id"], "title": a["title"], "status": a["status"], "stage": a.get("current_stage"), "progress": a.get("progress"), "goal_id": a.get("goal_id")} for a in (arcs or [])[:5]]
    compact_autonomy = [{"id": d["id"], "mode": d.get("mode"), "status": d.get("status"), "reason": d.get("reason"), "selected_goal_id": d.get("selected_goal_id"), "proposed_ops": (d.get("proposed_ops") or [])[:2]} for d in (autonomy or [])[:3]]
    compact_outbox = [{"id": o.get("id"), "intent_id": o.get("intent_id"), "target_user_id": o.get("target_user_id"), "status": o.get("status"), "draft_text": (o.get("draft_text") or "")[:240]} for o in (proactive_outbox or [])[:3]]
    compact_proactive_states = [{"user_id": ps.get("user_id"), "state": ps.get("state"), "pending_intent_ids": (ps.get("pending_intent_ids") or [])[:3], "daily_sent_count": ps.get("daily_sent_count")} for ps in (proactive_states or [])[:3]]
    compact_execution = [{"id": d.get("id"), "decision_type": d.get("decision_type"), "status": d.get("status"), "reason": d.get("reason"), "event_id": d.get("event_id"), "proposed_ops": (d.get("proposed_ops") or [])[:2]} for d in (execution or [])[:3]]
    compact_serendipity = [{"id": s.get("id"), "title": s.get("title"), "type": s.get("serendipity_type"), "trigger_event_id": s.get("trigger_event_id"), "intensity": s.get("intensity")} for s in (serendipity or [])[:3]]
    compact_final_gate_feedback = [{"id": f.get("id"), "report_id": f.get("report_id"), "message": (f.get("message") or "")[:500]} for f in (final_gate_feedback or [])[:3]]
    compact = {
        "owner_scope": scope.__dict__ if scope else {},
        "engine_state": control["engine_state"],
        "canon_version": control.get("active_canon_version"),
        "module_gates": control.get("module_gates"),
        "life_canon_snapshot": canon,
        "resources": compact_accounts,
        "recent_or_relevant_events": compact_events,
        "recalled_memories": compact_mem,
        "pending_proactive_intents": pending,
        "truth_sources": truth_sources or {},
        "inventory": compact_inventory,
        "goals": compact_goals,
        "life_arcs": compact_arcs,
        "recent_autonomy_decisions": compact_autonomy,
        "proactive_outbox": compact_outbox,
        "proactive_states": compact_proactive_states,
        "recent_execution_decisions": compact_execution,
        "recent_serendipity": compact_serendipity,
        "internal_final_gate_feedback": compact_final_gate_feedback,
        "pending_user_confirmations": compact_confirmations,
        "protocol": "Use LifeEngine tools to commit any new durable life facts before final answer. Use life_truth to resolve or observe Canon-bound external facts before planning. For User Life, use life_confirmation propose/confirm before writing uncertain user facts. Use life_inventory for entity resources such as wardrobe, supplies, books, and meals. Use life_goal for long-term goals, life arcs, and decomposing large events into child events. Use life_autonomy for manual autonomous planning; heartbeat may run autonomy only when the autonomy module gate permits it. Use life_proactive for proactive intents/outbox: create an intent when the agent wants to share/help/report, evaluate it against delivery policy, and only mark sent after actual delivery. Use life_execution to inspect or manually run narrative execution decisions; heartbeat uses this simulator for due schedule blocks. Final audit is advisory by default: if internal_final_gate_feedback is present, do not show it to the user. Use it internally to call life_commit for missing durable facts, or rephrase as intention/plan/draft. Final audit checks CommitReceipt and canonical state.",
    }
    return "\n<LIFEENGINE_CONTEXT>\n" + pretty(compact) + "\n</LIFEENGINE_CONTEXT>"


def format_result(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
