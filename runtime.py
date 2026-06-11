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
    get_autonomy_sleep_context,
    list_autonomy_sleep_adjustments,
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
    patch_canon_draft,
    render_canon_summary,
    render_draft_summary,
)
from .constants import DEFAULT_AGENT_ID, DEFAULT_USER_ID, MUTATION_BLOCKING_STATES, SETUP_STATES, PLUGIN_VERSION
from .db import connect, transaction, _SCHEMA_VERSION
from .doctor import run_doctor
from .dream import (
    collect_open_dream_repair_ops,
    create_dream_entry,
    dream_status,
    get_dream_entry,
    get_dream_repair_policy,
    get_dream_run,
    list_dream_entries,
    list_dream_findings,
    list_dream_repair_runs,
    list_dream_runs,
    record_dream_repair_run,
    run_dream_audit,
    run_dream_cycle,
    set_dream_repair_policy,
)
from .events import (
    claim_wake_job,
    complete_event,
    create_event,
    create_schedule_block,
    due_schedule_blocks,
    due_wake_jobs,
    finish_wake_job,
    get_event,
    get_realtime_state,
    list_events,
    event_transitions,
    schedule_transitions,
    set_realtime_state,
    transition_event,
    update_schedule_block_status,
)
from .execution import (
    apply_serendipity_event,
    get_execution_decision,
    get_execution_sleep_context,
    list_execution_decisions,
    list_execution_sleep_adjustments,
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
from .review import build_human_review, list_review_runs, get_review_run, dismiss_review_item, plan_review_item_action, record_review_action_run, mark_review_item_resolved, list_review_action_runs, get_review_action_run, get_review_action_policy, set_review_action_policy, validate_review_action_policy, select_review_items_for_batch, record_review_batch_run, list_review_batch_runs, get_review_batch_run, plan_review_action_undo, apply_review_action_undo, plan_review_batch_undo, apply_review_batch_undo, list_review_undo_runs, get_review_undo_run, decide_managed_review_loop, record_managed_review_loop_run, list_managed_review_loop_runs, get_managed_review_loop_run, get_managed_review_loop_state, begin_managed_review_acceptance_run, record_managed_review_acceptance_scenario, finish_managed_review_acceptance_run, list_managed_review_acceptance_runs, get_managed_review_acceptance_run, record_managed_review_stress_run, list_managed_review_stress_runs, get_managed_review_stress_run, build_managed_review_observability_report, list_managed_review_observability_reports, get_managed_review_observability_report, build_managed_review_release_readiness_report, list_managed_review_release_readiness_reports, get_managed_review_release_readiness_report
from .reply_gate import (
    assess_reply_gate,
    call_override,
    create_delayed_reply,
    list_call_overrides,
    list_delayed_replies,
    list_delayed_reply_digests,
    record_reply_gate_decision,
    release_delayed_replies,
    reply_gate_doctor,
    reply_gate_status,
)

from .sleep_dream_acceptance import (
    get_sleep_reply_dream_acceptance,
    list_sleep_reply_dream_acceptance,
    run_sleep_reply_dream_acceptance,
)

from .sleep_autonomy_execution_acceptance import (
    get_sleep_autonomy_execution_acceptance,
    list_sleep_autonomy_execution_acceptance,
    run_sleep_autonomy_execution_acceptance,
)

from .sleep_reply_dream_policy import (
    apply_preset as apply_srd_policy_preset,
    explain_policy as explain_srd_policy,
    get_policy as get_srd_policy,
    list_policy_audits as list_srd_policy_audits,
    list_suggestions as list_srd_policy_suggestions,
    reset_policy as reset_srd_policy,
    set_policy as set_srd_policy,
    suggestions as compute_srd_policy_suggestions,
    record_conflict_report as record_srd_policy_conflict_report,
    list_conflict_reports as list_srd_policy_conflict_reports,
    export_policy as export_srd_policy,
    import_policy as import_srd_policy,
    inspect_policy_export as inspect_srd_policy_export,
    list_policy_exports as list_srd_policy_exports,
    list_policy_imports as list_srd_policy_imports,
)

from .sleep_reply_dream_policy_acceptance import (
    run_sleep_reply_dream_policy_acceptance,
    list_sleep_reply_dream_policy_acceptance,
    get_sleep_reply_dream_policy_acceptance,
)

from .sleep_reply_dream_conversation_acceptance import (
    get_sleep_reply_dream_conversation_acceptance,
    list_sleep_reply_dream_conversation_acceptance,
    run_sleep_reply_dream_conversation_acceptance,
)

from .sleep import (
    create_sleep_plan,
    plan_core_sleep,
    start_sleep_session,
    end_sleep_session,
    wake_sleep_session,
    interrupt_sleep_session,
    skip_sleep_plan,
    sleep_status,
    list_sleep_plans,
    list_sleep_sessions,
    get_sleep_plan,
    get_sleep_session,
    get_active_sleep_session,
    sleep_interruptions,
    sleep_doctor,
)
from .sleep_effects import (
    get_sleep_day_state,
    list_sleep_day_states,
    plan_recovery_sleep_if_needed,
    record_post_sleep_day_state,
)
from .trace import Trace, append_audit, append_journal, new_id, verify_journal_hash_chain

from .schedule_view import list_schedule as list_human_schedule, list_unscheduled_events, explain_schedule_semantics, _tz_from_canon
from .settings_check import check_required_settings, latest_required_settings_check, required_settings_spec, default_setting_suggestions

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
    acceptance_suite,
    api_freeze_snapshot,
    api_freeze_status,
    backup_database,
    concurrency_smoke,
    export_profile_archive,
    get_acceptance_report,
    inspect_profile_export,
    integration_check,
    large_db_smoke,
    list_acceptance_reports,
    list_acceptance_runs,
    list_backups,
    list_maintenance_runs,
    list_profile_exports,
    mandatory_gate_patch,
    migration_history,
    record_package_manifest,
    rebuild_memory_indexes,
    release_readiness,
    run_tick_script_test,
    run_upgrade_check,
    stage_profile_import,
    stage_restore_plan,
    surface_snapshot,
    v1_rc_checklists,
    verify_memory_indexes,
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
            realtime_state = get_realtime_state(self.conn, owner_kind, owner_id)
            sleep_plans = list_sleep_plans(self.conn, owner_kind, owner_id, limit=5)
            sleep_sessions = list_sleep_sessions(self.conn, owner_kind, owner_id, limit=5)
            dreams = dream_status(self.conn, owner_kind, owner_id) if owner_kind == "agent" else {}
            required = check_required_settings(self.conn, owner_kind, owner_id, canon, persist=False) if owner_kind == "agent" else {"ok": True, "missing": []}
            schedule = list_human_schedule(self.conn, owner_kind, owner_id, period="today", tz_name=_tz_from_canon(canon), limit=20) if owner_kind == "agent" else {"items": []}
            out = {"control": c, "canon": canon, "realtime_state": realtime_state, "sleep_plans": sleep_plans, "sleep_sessions": sleep_sessions, "dreams": dreams, "resources": resources, "inventory": inventory, "goals": goals, "life_arcs": arcs, "pending_confirmations": confirmations, "pending_proactive": pending, "proactive_outbox": proactive_outbox if owner_kind == "agent" else [], "proactive_states": proactive_states if owner_kind == "agent" else [], "recent_autonomy": autonomy, "recent_execution": execution, "recent_serendipity": serendipity, "required_settings": required, "today_schedule": schedule.get("summary", {})}
            out["rendered"] = _render_status_page(out)
            return out

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
                brief = _brief_draft(draft)
                return {"ok": True, "draft": brief, "rendered": _render_setup_result(brief)}
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
                return {"ok": True, "canon": committed, "rendered": _render_canon_commit(committed)}
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
                    trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                    raise
        except Exception as exc:
            # v0.99/v0.10.0: validation failures inside the main transaction
            # rollback the trace row as well.  Record a separate durable failure
            # trace/audit after rollback so rejected LifeOps remain explainable
            # without creating life_transactions/life_ops/life_journal facts.
            self._record_failed_lifeops(owner_kind, owner_id, ops, source, session_id, turn_id, exc)
            raise

    def _record_failed_lifeops(self, owner_kind: str, owner_id: str, ops: list[dict[str, Any]], source: str,
                               session_id: str | None, turn_id: str | None, exc: Exception) -> dict[str, Any]:
        with transaction(self.conn):
            try:
                control = ensure_control(self.conn, owner_kind, owner_id)
            except Exception:
                control = {"engine_state": None, "active_canon_version": None}
            trace = Trace(
                self.conn, owner_kind, owner_id, "life_commit_failed", session_id=session_id, turn_id=turn_id,
                engine_state=control.get("engine_state"), canon_version=control.get("active_canon_version"),
                input_obj={"ops": ops, "source": source},
            ).start()
            error = f"{type(exc).__name__}: {exc}"
            trace.end(status="error", output_obj={"ok": False, "error": error, "ops_count": len(ops or [])}, error=error)
            audit_id = append_audit(self.conn, owner_kind, owner_id, "life_commit_failed", "error", error, {"ops": ops, "source": source}, trace_id=trace.id)
            try:
                fail_id = new_id("failedops")
                self.conn.execute(
                    "INSERT INTO failed_lifeops_audits(id, owner_kind, owner_id, session_id, turn_id, source, trace_id, error, ops_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (fail_id, owner_kind, owner_id, session_id, turn_id, source, trace.id, error, dumps(ops or [])),
                )
            except Exception:
                fail_id = None
            return {"ok": False, "trace_id": trace.id, "audit_id": audit_id, "failed_lifeops_audit_id": fail_id, "error": error}

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
        if op_type == "UPDATE_REALTIME_STATE":
            return set_realtime_state(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k not in {"source", "lease_expires_at_ts"}})
        if op_type == "PLAN_CORE_SLEEP":
            return plan_core_sleep(self.conn, owner_kind, owner_id, source=payload.get("source") or source, canon_version=canon_version, **{k: v for k, v in payload.items() if k not in {"source", "target_bedtime_ts", "target_wake_time_ts", "alarm_time_ts"}})
        if op_type == "START_SLEEP_SESSION":
            return start_sleep_session(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k not in {"source", "actual_start_ts"}})
        if op_type == "END_SLEEP_SESSION":
            return end_sleep_session(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k not in {"source", "actual_end_ts"}})
        if op_type == "COMPLETE_EVENT":
            result = complete_event(self.conn, owner_kind, owner_id, payload["event_id"], payload.get("summary", "completed"), payload.get("resource_deltas"), source)
            result["goal_updates"] = apply_event_goal_contributions(self.conn, owner_kind, owner_id, payload["event_id"], source)
            return result
        if op_type == "CREATE_SLEEP_PLAN":
            return create_sleep_plan(self.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "START_SLEEP_SESSION":
            return start_sleep_session(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "WAKE_SLEEP_SESSION":
            return wake_sleep_session(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "INTERRUPT_SLEEP_SESSION":
            return interrupt_sleep_session(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "RECORD_REPLY_GATE_DECISION":
            return record_reply_gate_decision(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "CREATE_DELAYED_REPLY":
            return create_delayed_reply(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "RELEASE_DELAYED_REPLIES":
            return release_delayed_replies(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "CALL_OVERRIDE":
            return call_override(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "RUN_DREAM":
            return run_dream_cycle(self.conn, owner_kind, owner_id, source=payload.get("source") or source, trace_id=payload.get("trace_id"), **{k: v for k, v in payload.items() if k not in {"source", "trace_id"}})
        if op_type == "CREATE_DREAM_ENTRY":
            return create_dream_entry(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "RESOURCE_DEFINE":
            p = dict(payload)
            reset_account = "initial" in p or bool(p.pop("reset_account", False))
            return define_resource(self.conn, owner_kind, owner_id, canon_version=canon_version, reset_account=reset_account, **p)
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
        if op_type == "CREATE_SLEEP_PLAN":
            return create_sleep_plan(self.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "START_SLEEP_SESSION":
            return start_sleep_session(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "END_SLEEP_SESSION":
            return end_sleep_session(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
        if op_type == "SKIP_SLEEP_PLAN":
            return skip_sleep_plan(self.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
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
                return {"ok": True, "events": list_events(self.conn, owner_kind, owner_id, payload.get("status"), int(payload.get("limit", 20)), payload.get("event_category"))}
        if action == "get":
            with transaction(self.conn):
                event = get_event(self.conn, payload["event_id"])
                return {"ok": True, "event": event, "transitions": event_transitions(self.conn, owner_kind, owner_id, payload["event_id"])}
        if action == "transitions":
            with transaction(self.conn):
                return {"ok": True, "event_id": payload.get("event_id"), "transitions": event_transitions(self.conn, owner_kind, owner_id, payload["event_id"])}
        if action == "schedule_transitions":
            with transaction(self.conn):
                return {"ok": True, "schedule_block_id": payload.get("schedule_block_id"), "transitions": schedule_transitions(self.conn, owner_kind, owner_id, payload["schedule_block_id"])}
        if action == "state":
            with transaction(self.conn):
                return {"ok": True, "realtime_state": get_realtime_state(self.conn, owner_kind, owner_id)}
        if action == "update_state":
            return self.commit_ops([{"type": "UPDATE_REALTIME_STATE", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        if action == "create":
            return self.commit_ops([{"type": "CREATE_EVENT", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        if action == "schedule":
            return self.commit_ops([{"type": "CREATE_SCHEDULE_BLOCK", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        if action == "transition":
            return self.commit_ops([{"type": "UPDATE_EVENT_STATUS", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        if action == "complete":
            return self.commit_ops([{"type": "COMPLETE_EVENT", "payload": payload}], owner_kind, owner_id, "life_event_tool", session_id, turn_id)
        raise ValueError(f"Unknown event action: {action}")


    # ----- Sleep / Reply / Dream policy UX --------------------------------
    def policy(self, action: str = "get", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
               session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        # Acceptance scenarios call other runtime methods that own their own
        # transactions, so do not wrap them in the policy read/write transaction.
        if action in {"acceptance", "policy_acceptance", "srd_policy_acceptance"}:
            return run_sleep_reply_dream_policy_acceptance(self, owner_kind, owner_id)
        if action in {"acceptance_runs", "policy_acceptance_runs"}:
            with transaction(self.conn):
                return {"ok": True, "runs": list_sleep_reply_dream_policy_acceptance(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
        if action in {"acceptance_get", "policy_acceptance_get"}:
            with transaction(self.conn):
                return {"ok": True, "run": get_sleep_reply_dream_policy_acceptance(self.conn, payload.get("acceptance_run_id") or payload.get("run_id"))}
        with transaction(self.conn):
            if action in {"get", "status", "state", "summary"}:
                pol = get_srd_policy(self.conn, owner_kind, owner_id)
                exp = explain_srd_policy(pol)
                return {"ok": True, "policy": pol, "explanation": exp}
            if action in {"explain"}:
                pol = get_srd_policy(self.conn, owner_kind, owner_id)
                return {"ok": True, "explanation": explain_srd_policy(pol)}
            if action in {"set", "patch", "update"}:
                patch = payload.get("policy_patch") or payload.get("patch") or {}
                if isinstance(patch, str):
                    patch = loads(patch, {})
                if not isinstance(patch, dict):
                    raise ValueError("policy patch must be an object")
                return set_srd_policy(self.conn, owner_kind, owner_id, policy_patch=patch, updated_by=payload.get("updated_by") or "life_policy_tool", source="life_policy_tool")
            if action in {"preset", "profile"}:
                preset = payload.get("preset") or payload.get("profile") or "balanced"
                return apply_srd_policy_preset(self.conn, owner_kind, owner_id, str(preset), updated_by=payload.get("updated_by") or "life_policy_tool")
            if action in {"reset", "defaults"}:
                return reset_srd_policy(self.conn, owner_kind, owner_id, updated_by=payload.get("updated_by") or "life_policy_tool")
            if action in {"suggest", "suggestions", "recommend", "review"}:
                return compute_srd_policy_suggestions(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 10)), record=bool(payload.get("record", True)))
            if action in {"suggestion_list", "list_suggestions"}:
                return {"ok": True, "suggestions": list_srd_policy_suggestions(self.conn, owner_kind, owner_id, status=payload.get("status"), limit=int(payload.get("limit", 20)))}
            if action in {"audits", "history"}:
                return {"ok": True, "audits": list_srd_policy_audits(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"conflicts", "check_conflicts", "validate", "conflict_report"}:
                return {"ok": True, "validation": record_srd_policy_conflict_report(self.conn, owner_kind, owner_id)}
            if action in {"conflict_reports", "list_conflicts"}:
                return {"ok": True, "reports": list_srd_policy_conflict_reports(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"export", "export_policy"}:
                return export_srd_policy(self.conn, owner_kind, owner_id, destination=payload.get("destination"))
            if action in {"exports", "list_exports"}:
                return {"ok": True, "exports": list_srd_policy_exports(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"inspect_import", "inspect_export"}:
                return {"ok": True, "inspection": inspect_srd_policy_export(payload.get("path") or payload.get("archive_path") or payload.get("export_path"))}
            if action in {"import", "import_policy"}:
                return import_srd_policy(self.conn, owner_kind, owner_id, path=payload.get("path") or payload.get("archive_path") or payload.get("export_path"), apply=bool(payload.get("apply", False)), updated_by=payload.get("updated_by") or "life_policy_tool")
            if action in {"imports", "list_imports"}:
                return {"ok": True, "imports": list_srd_policy_imports(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            raise ValueError(f"Unknown policy action: {action}")

    def sleep_tool(self, action: str, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                   session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action in {"status", "state"}:
            with transaction(self.conn):
                return {"ok": True, "sleep": sleep_status(self.conn, owner_kind, owner_id)}
        if action in {"day_state", "effects"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_day_state": get_sleep_day_state(self.conn, owner_kind, owner_id, payload.get("date") or payload.get("date_key"))}
        if action in {"day_states", "effects_list"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_day_states": list_sleep_day_states(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 14)))}
        if action in {"recovery_plan", "plan_recovery"}:
            with transaction(self.conn):
                return plan_recovery_sleep_if_needed(self.conn, owner_kind, owner_id, date_key=payload.get("date") or payload.get("date_key"), threshold=int(payload.get("threshold", 60)), duration_minutes=int(payload.get("duration_minutes", 30)), source="life_sleep_tool")
        if action in {"record_effects", "all_nighter"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_day_state": record_post_sleep_day_state(self.conn, owner_kind, owner_id, sleep_session_id=payload.get("sleep_session_id"), sleep_plan_id=payload.get("sleep_plan_id"), date_key=payload.get("date") or payload.get("date_key"), source="life_sleep_tool")}
        if action in {"plans", "list_plans"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_plans": list_sleep_plans(self.conn, owner_kind, owner_id, status=payload.get("status"), limit=int(payload.get("limit", 20)))}
        if action in {"sessions", "list_sessions"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_sessions": list_sleep_sessions(self.conn, owner_kind, owner_id, status=payload.get("status"), limit=int(payload.get("limit", 20)))}
        if action == "get_plan":
            with transaction(self.conn):
                return {"ok": True, "sleep_plan": get_sleep_plan(self.conn, payload["sleep_plan_id"])}
        if action == "get_session":
            with transaction(self.conn):
                return {"ok": True, "sleep_session": get_sleep_session(self.conn, payload["sleep_session_id"])}
        if action == "plan_day":
            policy = get_srd_policy(self.conn, owner_kind, owner_id)["effective_policy"]
            sleep_policy = policy.get("sleep", {})
            bedtime_default = (sleep_policy.get("bedtime_window") or ["23:30"])[0]
            wake_default = (sleep_policy.get("wake_window") or ["07:00"])[0]
            core_payload = {
                "date_key": payload.get("date") or payload.get("date_key"),
                "target_bedtime": payload.get("bedtime") or payload.get("target_bedtime") or bedtime_default,
                "target_wake_time": payload.get("wake_time") or payload.get("target_wake_time") or wake_default,
                "timezone": payload.get("timezone_name") or payload.get("timezone") or "UTC",
                "alarm_time": payload.get("alarm_at") or payload.get("alarm_time"),
                "alarm_enabled": bool(payload.get("alarm_at") or payload.get("alarm_enabled", sleep_policy.get("alarm_policy") == "prefer_alarm")),
                "source": payload.get("source") or "life_sleep_tool",
                "sleep_policy": sleep_policy,
            }
            return self.commit_ops([{"type": "PLAN_CORE_SLEEP", "payload": core_payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action == "plan":
            return self.commit_ops([{"type": "CREATE_SLEEP_PLAN", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action == "nap":
            payload.setdefault("sleep_type", "nap")
            payload.setdefault("forced_daily", False)
            payload.setdefault("title", "小憩/补觉")
            return self.commit_ops([{"type": "CREATE_SLEEP_PLAN", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action in {"start", "sleep"}:
            return self.commit_ops([{"type": "START_SLEEP_SESSION", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action in {"wake", "end"}:
            return self.commit_ops([{"type": "WAKE_SLEEP_SESSION", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action == "skip":
            return self.commit_ops([{"type": "SKIP_SLEEP_PLAN", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        raise ValueError(f"Unknown sleep action: {action}")



    # ----- DreamRun / DreamAudit / DreamEntry -----------------------------
    def dream(self, action: str = "status", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
              session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        with transaction(self.conn):
            if action in {"status", "state"}:
                out = dream_status(self.conn, owner_kind, owner_id)
                out["repair_policy"] = get_dream_repair_policy(self.conn, owner_kind, owner_id)
                return out
            if action in {"repair_policy", "policy"}:
                return {"ok": True, "repair_policy": get_dream_repair_policy(self.conn, owner_kind, owner_id)}
            if action in {"set_repair_policy", "policy_set"}:
                return {"ok": True, "repair_policy": set_dream_repair_policy(self.conn, owner_kind, owner_id, mode=payload.get("mode", "manual"), safe_finding_types=payload.get("safe_finding_types"), auto_apply_limit=int(payload.get("auto_apply_limit", 10)), updated_by="life_dream_tool")}
            if action in {"list", "runs"}:
                return {"ok": True, "runs": list_dream_runs(self.conn, owner_kind, owner_id, status=payload.get("status"), limit=int(payload.get("limit", 20)))}
            if action in {"entries", "dreams"}:
                return {"ok": True, "entries": list_dream_entries(self.conn, owner_kind, owner_id, dream_run_id=payload.get("dream_run_id"), limit=int(payload.get("limit", 20)))}
            if action in {"findings", "audit_findings"}:
                return {"ok": True, "findings": list_dream_findings(self.conn, owner_kind, owner_id, dream_run_id=payload.get("dream_run_id"), severity=payload.get("severity"), limit=int(payload.get("limit", 50)))}
            if action in {"get", "get_run"}:
                return {"ok": True, "dream_run": get_dream_run(self.conn, payload["dream_run_id"])}
            if action in {"get_entry", "entry"}:
                return {"ok": True, "dream_entry": get_dream_entry(self.conn, payload["dream_entry_id"])}
            if action in {"run", "cycle", "dream"}:
                op_payload = dict(payload)
                op_payload.setdefault("source", "life_dream_tool")
                return self._commit_ops_locked([{"type": "RUN_DREAM", "payload": op_payload}], owner_kind, owner_id, "life_dream_tool", session_id, turn_id, trace=None, control=ensure_control(self.conn, owner_kind, owner_id))
            if action in {"create_entry", "entry_create"}:
                op_payload = dict(payload)
                op_payload.setdefault("source", "life_dream_tool")
                return self._commit_ops_locked([{"type": "CREATE_DREAM_ENTRY", "payload": op_payload}], owner_kind, owner_id, "life_dream_tool", session_id, turn_id, trace=None, control=ensure_control(self.conn, owner_kind, owner_id))
            if action == "audit":
                dream_run_id = payload.get("dream_run_id")
                if not dream_run_id:
                    return run_dream_cycle(self.conn, owner_kind, owner_id, sleep_session_id=payload.get("sleep_session_id"), create_share_intent=False, source="life_dream_audit")
                run = get_dream_run(self.conn, dream_run_id)
                session = None
                if run.get("sleep_session_id"):
                    row = self.conn.execute("SELECT * FROM sleep_sessions WHERE id=?", (run.get("sleep_session_id"),)).fetchone()
                    session = dict(row) if row else None
                return run_dream_audit(self.conn, owner_kind, owner_id, dream_run_id, sleep_session=session)
            if action in {"repair_plan", "repair_preview"}:
                return collect_open_dream_repair_ops(self.conn, owner_kind, owner_id, dream_run_id=payload.get("dream_run_id"), finding_ids=payload.get("finding_ids"), limit=int(payload.get("limit", 50)), policy_mode=payload.get("policy_mode"))
            if action in {"repair", "apply_repairs"}:
                plan = collect_open_dream_repair_ops(self.conn, owner_kind, owner_id, dream_run_id=payload.get("dream_run_id"), finding_ids=payload.get("finding_ids"), limit=int(payload.get("limit", 50)))
                ops = plan.get("ops") or []
                finding_ids = [f.get("id") for f in (plan.get("findings") or []) if f.get("id")]
                if payload.get("dry_run") or not ops:
                    run = record_dream_repair_run(self.conn, owner_kind, owner_id, dream_run_id=payload.get("dream_run_id"), mode="dry_run" if payload.get("dry_run") else "noop", finding_ids=finding_ids, proposed_ops=ops, status="planned" if ops else "noop", output=plan)
                    return {"ok": True, "repair_run": run, "plan": plan, "commit": None}
                try:
                    commit = self._commit_ops_locked(ops, owner_kind, owner_id, "dream_audit_repair", session_id=session_id, turn_id=turn_id, trace=None, control=ensure_control(self.conn, owner_kind, owner_id))
                    run = record_dream_repair_run(self.conn, owner_kind, owner_id, dream_run_id=payload.get("dream_run_id"), mode="apply", finding_ids=finding_ids, proposed_ops=ops, status="applied", transaction_id=commit.get("transaction_id"), receipt_id=(commit.get("receipt") or {}).get("receipt_id"), output={"repaired_count": len(finding_ids)})
                    return {"ok": True, "repair_run": run, "plan": plan, "commit": commit}
                except Exception as exc:
                    run = record_dream_repair_run(self.conn, owner_kind, owner_id, dream_run_id=payload.get("dream_run_id"), mode="apply", finding_ids=finding_ids, proposed_ops=ops, status="failed", error=f"{type(exc).__name__}: {exc}", output=plan)
                    return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "repair_run": run, "plan": plan}
            if action in {"repairs", "repair_runs"}:
                return {"ok": True, "repairs": list_dream_repair_runs(self.conn, owner_kind, owner_id, dream_run_id=payload.get("dream_run_id"), limit=int(payload.get("limit", 20)))}
            raise ValueError(f"unknown dream action: {action}")

    # ----- ReplyGate / delayed replies / call override ---------------------
    def reply(self, action: str = "status", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
              session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action in {"status", "state"}:
            with transaction(self.conn):
                return {"ok": True, "reply_gate": reply_gate_status(self.conn, owner_kind, owner_id)}
        if action in {"assess", "gate", "check"}:
            with transaction(self.conn):
                control = ensure_control(self.conn, owner_kind, owner_id)
                trace = Trace(self.conn, owner_kind, owner_id, "reply_gate_assess", session_id=session_id, turn_id=turn_id, input_obj=payload).start()
                try:
                    out = assess_reply_gate(self.conn, owner_kind, owner_id, control,
                                            message_text=payload.get("message_text") or payload.get("text"),
                                            session_id=session_id, turn_id=turn_id, user_id=payload.get("user_id"),
                                            force_call=bool(payload.get("force_call", False)), trace_id=trace.id, source="life_reply_tool")
                    trace.end(output_obj=out)
                    return out
                except Exception as exc:
                    trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                    raise
        if action in {"defer", "queue"}:
            return self.commit_ops([{"type": "CREATE_DELAYED_REPLY", "payload": {**payload, "source": "life_reply_tool"}}], owner_kind, owner_id, "life_reply_tool", session_id, turn_id)
        if action in {"release", "release_pending"}:
            return self.commit_ops([{"type": "RELEASE_DELAYED_REPLIES", "payload": {**payload, "source": "life_reply_tool"}}], owner_kind, owner_id, "life_reply_tool", session_id, turn_id)
        if action in {"queue_list", "delayed", "list"}:
            with transaction(self.conn):
                return {"ok": True, "delayed_replies": list_delayed_replies(self.conn, owner_kind, owner_id, status=payload.get("status"), limit=int(payload.get("limit", 20)))}
        if action in {"digests", "digest_list"}:
            with transaction(self.conn):
                return {"ok": True, "delayed_reply_digests": list_delayed_reply_digests(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
        if action in {"calls", "call_overrides"}:
            with transaction(self.conn):
                return {"ok": True, "call_overrides": list_call_overrides(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
        if action == "doctor":
            with transaction(self.conn):
                return {"ok": True, "reply_gate_doctor": reply_gate_doctor(self.conn, owner_kind, owner_id)}
        if action in {"call", "override", "wake"}:
            return self.call(owner_kind, owner_id, session_id=session_id, turn_id=turn_id, **payload)
        raise ValueError(f"Unknown reply action: {action}")

    def call(self, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
             session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        return self.commit_ops([{"type": "CALL_OVERRIDE", "payload": {**payload, "source": "life_call_tool"}}], owner_kind, owner_id, "life_call_tool", session_id, turn_id)

    def assess_incoming_message(self, *, session_id: str | None = None, turn_id: str | None = None,
                                sender_id: str | None = None, platform: str | None = None,
                                text: str | None = None, force_call: bool = False) -> dict[str, Any]:
        scope = resolve_owner_scope({}, {"session_id": session_id, "turn_id": turn_id, "sender_id": sender_id, "platform": platform})
        owner_kind, owner_id = scope.owner_kind, scope.owner_id
        with transaction(self.conn):
            control = ensure_control(self.conn, owner_kind, owner_id)
            trace = Trace(self.conn, owner_kind, owner_id, "reply_gate_incoming", session_id=session_id, turn_id=turn_id, input_obj={"text": (text or "")[:500], "sender_id": sender_id, "platform": platform, "force_call": force_call}).start()
            try:
                out = assess_reply_gate(self.conn, owner_kind, owner_id, control, message_text=text, session_id=session_id, turn_id=turn_id, user_id=sender_id, force_call=force_call, trace_id=trace.id, source="incoming_message")
                decision = (out.get("decision") or {}).get("decision")
                gates = control.get("module_gates") or {}
                if decision == "defer" and str(gates.get("reply_gate", "advisory")).lower() in {"auto", "strict"}:
                    delayed = create_delayed_reply(self.conn, owner_kind, owner_id, message_text=text or "", user_id=sender_id, session_id=session_id, turn_id=turn_id, gate_decision_id=(out.get("decision") or {}).get("id"), reason=(out.get("decision") or {}).get("reason") or "ReplyGate deferred incoming message", source="incoming_message")
                    out["delayed_reply"] = delayed
                elif decision == "call_override":
                    out["call_override"] = call_override(self.conn, owner_kind, owner_id, reason="incoming call override", user_id=sender_id, session_id=session_id, turn_id=turn_id, message_text=text, trace_id=trace.id, source="incoming_message")
                trace.end(output_obj={"decision": decision, "deferred": bool(out.get("delayed_reply")), "called": bool(out.get("call_override"))})
                return out
            except Exception as exc:
                trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
                raise

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
                            if job.get("reason") == "sleep_plan_start" and job.get("target_id"):
                                with trace.span("sleep_start", {"wake_job_id": job["id"], "sleep_plan_id": job.get("target_id")}):
                                    commit = self._commit_ops_locked([{"type": "START_SLEEP_SESSION", "payload": {"sleep_plan_id": job["target_id"], "now": now, "source": "heartbeat", "reason": "scheduled sleep start"}}], owner_kind, owner_id, "sleep_heartbeat", session_id=None, turn_id=tick_id, trace=trace, control=control)
                                completed.append({"wake_job_id": job["id"], "sleep_plan_id": job["target_id"], "sleep_start_commit": commit})
                            elif job.get("reason") == "sleep_plan_wake" and job.get("target_id"):
                                with trace.span("sleep_wake", {"wake_job_id": job["id"], "sleep_plan_id": job.get("target_id")}):
                                    commit = self._commit_ops_locked([{"type": "WAKE_SLEEP_SESSION", "payload": {"sleep_plan_id": job["target_id"], "now": now, "wake_cause": "alarm_or_natural", "source": "heartbeat", "reason": "scheduled sleep wake"}}], owner_kind, owner_id, "sleep_heartbeat", session_id=None, turn_id=tick_id, trace=trace, control=control)
                                    dream_commit = None
                                    try:
                                        gates = control.get("module_gates") or {}
                                        if str(gates.get("dream", "auto")).lower() in {"auto", "daily", "on", "manual_ok"}:
                                            sleep_session_id = None
                                            for item in commit.get("results", []):
                                                res = item.get("result") or {}
                                                sess = res.get("sleep_session") if isinstance(res, dict) else None
                                                if isinstance(sess, dict) and sess.get("id"):
                                                    sleep_session_id = sess.get("id")
                                                    break
                                            if sleep_session_id:
                                                with trace.span("dream_run", {"sleep_session_id": sleep_session_id}):
                                                    dream_commit = self._commit_ops_locked([{"type": "RUN_DREAM", "payload": {"sleep_session_id": sleep_session_id, "trigger": "sleep_wake", "source": "heartbeat_dream", "trace_id": trace.id}}], owner_kind, owner_id, "dream_heartbeat", session_id=None, turn_id=tick_id, trace=trace, control=control)
                                    except Exception as dream_exc:
                                        append_audit(self.conn, owner_kind, owner_id, "dream_heartbeat_failed", "warning", str(dream_exc), {"sleep_plan_id": job.get("target_id")}, trace.id)
                                completed.append({"wake_job_id": job["id"], "sleep_plan_id": job["target_id"], "sleep_wake_commit": commit, "dream_commit": dream_commit})
                            elif job.get("reason") == "schedule_block_end" and job.get("target_id"):
                                block_row = self.conn.execute(
                                    "SELECT * FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?",
                                    (job["target_id"], owner_kind, owner_id),
                                ).fetchone()
                                block = dict(block_row) if block_row else None
                                if block and block.get("block_type") == "sleep":
                                    processed.append({"wake_job_id": job["id"], "status": "ignored", "reason": "sleep block handled by sleep_plan_wake"})
                                elif block and block["status"] in {"planned", "locked", "ready", "in_progress"}:
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
                managed_review_result = self._run_managed_review_for_tick(owner_kind, owner_id, control, tick_id, trace, now, manual)
                delayed_release = {"released_count": 0}
                try:
                    state = get_realtime_state(self.conn, owner_kind, owner_id)
                    if state.get("reply_mode") == "immediate" or state.get("mode") in {"idle", "awake", "in_conversation"}:
                        delayed_release = release_delayed_replies(self.conn, owner_kind, owner_id, reason="released by heartbeat after agent became available", source="heartbeat", limit=20)
                except Exception as exc:
                    delayed_release = {"error": f"{type(exc).__name__}: {exc}"}
                out = {"completed": completed, "resource_recovery": recovered, "wake_jobs": processed, "truth_refresh": truth_refresh, "autonomy": autonomy_result, "proactive": proactive_result, "managed_review": managed_review_result, "delayed_reply_release": delayed_release}
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


    def sleep(self, action: str = "state", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
              session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action in {"state", "status"}:
            with transaction(self.conn):
                return {"ok": True, "realtime_state": get_realtime_state(self.conn, owner_kind, owner_id), "active_sleep_session": get_active_sleep_session(self.conn, owner_kind, owner_id), "policy": explain_srd_policy(get_srd_policy(self.conn, owner_kind, owner_id))}
        if action in {"policy", "policy_status"}:
            return self.policy("get", owner_kind, owner_id, session_id=session_id, turn_id=turn_id)
        if action in {"plans", "list_plans", "list"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_plans": list_sleep_plans(self.conn, owner_kind, owner_id, payload.get("status"), int(payload.get("limit", 20)))}
        if action in {"sessions", "list_sessions"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_sessions": list_sleep_sessions(self.conn, owner_kind, owner_id, payload.get("status"), int(payload.get("limit", 20)))}
        if action == "get_plan":
            with transaction(self.conn):
                return {"ok": True, "sleep_plan": get_sleep_plan(self.conn, payload["sleep_plan_id"])}
        if action == "get_session":
            with transaction(self.conn):
                sess = get_sleep_session(self.conn, payload["sleep_session_id"])
                return {"ok": True, "sleep_session": sess, "interruptions": sleep_interruptions(self.conn, owner_kind, owner_id, sess["id"])}
        if action in {"plan", "create_plan", "ensure_daily"}:
            return self.commit_ops([{"type": "CREATE_SLEEP_PLAN", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action in {"start", "sleep"}:
            return self.commit_ops([{"type": "START_SLEEP_SESSION", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action in {"wake", "wake_up"}:
            return self.commit_ops([{"type": "WAKE_SLEEP_SESSION", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action in {"interrupt", "call_interrupt"}:
            return self.commit_ops([{"type": "INTERRUPT_SLEEP_SESSION", "payload": payload}], owner_kind, owner_id, "life_sleep_tool", session_id, turn_id)
        if action == "doctor":
            with transaction(self.conn):
                return {"ok": True, "sleep_doctor": sleep_doctor(self.conn, owner_kind, owner_id)}
        raise ValueError(f"Unknown sleep action: {action}")

    def autonomy(self, action: str = "list", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                 session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        if action in {"list", "decisions"}:
            with transaction(self.conn):
                return {"ok": True, "decisions": list_autonomy_decisions(self.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
        if action == "get":
            with transaction(self.conn):
                return {"ok": True, "decision": get_autonomy_decision(self.conn, payload["decision_id"])}
        if action in {"sleep_context", "sleep", "sleep_status"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_context": get_autonomy_sleep_context(self.conn, owner_kind, owner_id, now=payload.get("now"))}
        if action in {"sleep_adjustments", "adjustments"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_adjustments": list_autonomy_sleep_adjustments(self.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
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
        if action in {"sleep_context", "sleep"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_context": get_execution_sleep_context(self.conn, owner_kind, owner_id)}
        if action in {"sleep_adjustments", "adjustments"}:
            with transaction(self.conn):
                return {"ok": True, "sleep_adjustments": list_execution_sleep_adjustments(self.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
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

    # ----- human review UX --------------------------------------------------
    def review(self, action: str = "summary", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
               session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        """Human-readable review/inbox aggregation and safe review action application."""
        with transaction(self.conn):
            if action in {"summary", "run", "get", "status", "page", "review"}:
                ensure_control(self.conn, owner_kind, owner_id)
                return build_human_review(
                    self.conn, owner_kind, owner_id,
                    include_doctor=bool(payload.get("include_doctor", True)),
                    limit=int(payload.get("limit", 5)),
                    persist=bool(payload.get("persist", True)),
                    source="life_review_tool",
                )
            if action in {"runs", "history", "list"}:
                return {"ok": True, "runs": list_review_runs(self.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
            if action in {"get_run", "explain"}:
                rid = payload.get("review_run_id") or payload.get("run_id") or payload.get("id")
                if not rid:
                    raise ValueError("review_run_id is required")
                run = get_review_run(self.conn, rid)
                if not run:
                    raise ValueError(f"review run not found: {rid}")
                return {"ok": True, "run": run}
            if action in {"dismiss", "resolve"}:
                item_id = payload.get("item_id") or payload.get("id")
                if not item_id:
                    raise ValueError("item_id is required")
                item = dismiss_review_item(self.conn, owner_kind, owner_id, item_id, reason=str(payload.get("reason") or action))
                return {"ok": True, "item": item}
            if action in {"preview_action", "plan_action", "action_plan", "preview"}:
                item_id = payload.get("item_id") or payload.get("id")
                if not item_id:
                    raise ValueError("item_id is required")
                plan = plan_review_item_action(self.conn, owner_kind, owner_id, item_id, choice=payload.get("choice"))
                run = record_review_action_run(self.conn, owner_kind, owner_id, item_id=item_id, review_run_id=(plan.get("item") or {}).get("review_run_id"), mode="preview", status="planned", input_obj=payload, plan=plan, output={"preview": True})
                return {"ok": True, "applied": False, "plan": plan, "action_run": run}
            if action in {"apply", "apply_action", "apply_item", "run_action", "do"}:
                item_id = payload.get("item_id") or payload.get("id")
                if not item_id:
                    raise ValueError("item_id is required")
                return self._apply_review_action_locked(owner_kind, owner_id, item_id, session_id=session_id, turn_id=turn_id, **{k: v for k, v in payload.items() if k not in {"item_id", "id"}})
            if action in {"action_runs", "actions", "applied"}:
                return {"ok": True, "action_runs": list_review_action_runs(self.conn, owner_kind, owner_id, item_id=payload.get("item_id"), limit=int(payload.get("limit", 20)))}
            if action in {"get_action", "action_get"}:
                run_id = payload.get("action_run_id") or payload.get("run_id") or payload.get("id")
                if not run_id:
                    raise ValueError("action_run_id is required")
                return {"ok": True, "action_run": get_review_action_run(self.conn, owner_kind, owner_id, run_id)}
            if action in {"policy", "action_policy", "get_policy", "policy_get"}:
                return {"ok": True, "review_action_policy": get_review_action_policy(self.conn, owner_kind, owner_id, create=True)}
            if action in {"set_policy", "policy_set", "patch_policy", "update_policy"}:
                out = set_review_action_policy(self.conn, owner_kind, owner_id, policy_patch=payload.get("policy_patch") or payload.get("patch") or {}, replace_policy=payload.get("replace_policy"), updated_by=payload.get("updated_by") or "life_review_tool")
                return out
            if action in {"validate_policy", "policy_validate"}:
                pol = payload.get("policy") or get_review_action_policy(self.conn, owner_kind, owner_id, create=True).get("policy")
                return validate_review_action_policy(pol)
            if action in {"batch_preview", "preview_all", "apply_all_preview", "dry_run_all"}:
                return self._apply_review_batch_locked(owner_kind, owner_id, session_id=session_id, turn_id=turn_id, **{**payload, "dry_run": True})
            if action in {"apply_all", "batch_apply", "apply_safe", "apply_section"}:
                return self._apply_review_batch_locked(owner_kind, owner_id, session_id=session_id, turn_id=turn_id, **payload)
            if action in {"batch_runs", "batches"}:
                return {"ok": True, "batch_runs": list_review_batch_runs(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"get_batch", "batch_get"}:
                batch_id = payload.get("batch_run_id") or payload.get("batch_id") or payload.get("id")
                if not batch_id:
                    raise ValueError("batch_run_id is required")
                return {"ok": True, "batch_run": get_review_batch_run(self.conn, owner_kind, owner_id, batch_id)}
            if action in {"managed_state", "agent_state", "managed_loop_state"}:
                return {"ok": True, "state": get_managed_review_loop_state(self.conn, owner_kind, owner_id)}
            if action in {"managed_runs", "agent_runs", "managed_loop_runs"}:
                return {"ok": True, "managed_runs": list_managed_review_loop_runs(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"get_managed_run", "managed_get", "agent_run_get"}:
                run_id = payload.get("managed_run_id") or payload.get("run_id") or payload.get("id")
                if not run_id:
                    raise ValueError("managed_run_id is required")
                return {"ok": True, "managed_run": get_managed_review_loop_run(self.conn, owner_kind, owner_id, run_id)}
            if action in {"managed_observability", "managed_observe", "managed_status_report", "observability"}:
                return build_managed_review_observability_report(self.conn, owner_kind, owner_id, persist=bool(payload.get("persist", True)), include_doctor=bool(payload.get("include_doctor", True)))
            if action in {"managed_observability_reports", "observability_reports"}:
                return {"ok": True, "reports": list_managed_review_observability_reports(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"get_managed_observability", "managed_observability_get", "get_observability"}:
                report_id = payload.get("report_id") or payload.get("id")
                if not report_id:
                    raise ValueError("report_id is required")
                return {"ok": True, "report": get_managed_review_observability_report(self.conn, owner_kind, owner_id, report_id)}
            if action in {"managed_release_readiness", "release_readiness", "managed_readiness"}:
                return build_managed_review_release_readiness_report(self.conn, owner_kind, owner_id, persist=bool(payload.get("persist", True)))
            if action in {"managed_release_readiness_reports", "release_readiness_reports", "readiness_reports"}:
                return {"ok": True, "reports": list_managed_review_release_readiness_reports(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"get_managed_release_readiness", "release_readiness_get", "get_readiness"}:
                report_id = payload.get("report_id") or payload.get("id")
                if not report_id:
                    raise ValueError("report_id is required")
                return {"ok": True, "report": get_managed_review_release_readiness_report(self.conn, owner_kind, owner_id, report_id)}
            if action in {"managed_acceptance", "agent_managed_acceptance", "managed_loop_acceptance"}:
                return self._run_managed_review_acceptance_locked(owner_kind, owner_id, stress_count=int(payload.get("stress_count", 12)))
            if action in {"managed_acceptance_runs", "agent_managed_acceptance_runs"}:
                return {"ok": True, "acceptance_runs": list_managed_review_acceptance_runs(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"get_managed_acceptance", "managed_acceptance_get"}:
                run_id = payload.get("acceptance_run_id") or payload.get("run_id") or payload.get("id")
                if not run_id:
                    raise ValueError("acceptance_run_id is required")
                return {"ok": True, "acceptance_run": get_managed_review_acceptance_run(self.conn, owner_kind, owner_id, run_id)}
            if action in {"managed_stress", "agent_managed_stress"}:
                return self._run_managed_review_stress_locked(owner_kind, owner_id, count=int(payload.get("count", 25)), limit=int(payload.get("limit", 10)))
            if action in {"managed_stress_runs", "agent_managed_stress_runs"}:
                return {"ok": True, "stress_runs": list_managed_review_stress_runs(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"get_managed_stress", "managed_stress_get"}:
                run_id = payload.get("stress_run_id") or payload.get("run_id") or payload.get("id")
                if not run_id:
                    raise ValueError("stress_run_id is required")
                return {"ok": True, "stress_run": get_managed_review_stress_run(self.conn, owner_kind, owner_id, run_id)}
            if action in {"managed_preview", "agent_preview", "agent_managed_preview"}:
                return self._run_agent_managed_review_locked(owner_kind, owner_id, trigger_source=str(payload.get("trigger_source") or "manual"), tick_id=payload.get("tick_id"), dry_run=True, force=bool(payload.get("force", False)), session_id=session_id, turn_id=turn_id)
            if action in {"managed_run", "agent_run", "agent_managed_run"}:
                return self._run_agent_managed_review_locked(owner_kind, owner_id, trigger_source=str(payload.get("trigger_source") or "manual"), tick_id=payload.get("tick_id"), dry_run=bool(payload.get("dry_run", False)), force=bool(payload.get("force", False)), session_id=session_id, turn_id=turn_id)
            if action in {"undo_preview", "preview_undo", "undo_plan", "plan_undo"}:
                action_run_id = payload.get("action_run_id") or payload.get("run_id") or payload.get("id")
                if not action_run_id:
                    raise ValueError("action_run_id is required")
                plan = plan_review_action_undo(self.conn, owner_kind, owner_id, action_run_id)
                run = apply_review_action_undo(self.conn, owner_kind, owner_id, action_run_id, dry_run=True)
                return {"ok": True, "undone": False, "plan": plan, "undo_run": run.get("undo_run")}
            if action in {"undo", "apply_undo", "rollback_action"}:
                action_run_id = payload.get("action_run_id") or payload.get("run_id") or payload.get("id")
                if not action_run_id:
                    raise ValueError("action_run_id is required")
                return apply_review_action_undo(self.conn, owner_kind, owner_id, action_run_id, dry_run=bool(payload.get("dry_run", False)), reason=str(payload.get("reason") or "review undo"))
            if action in {"batch_undo_preview", "preview_batch_undo", "batch_undo_plan"}:
                batch_id = payload.get("batch_run_id") or payload.get("batch_id") or payload.get("id")
                if not batch_id:
                    raise ValueError("batch_run_id is required")
                plan = plan_review_batch_undo(self.conn, owner_kind, owner_id, batch_id)
                run = apply_review_batch_undo(self.conn, owner_kind, owner_id, batch_id, dry_run=True)
                return {"ok": True, "undone": False, "plan": plan, "undo_run": run.get("undo_run")}
            if action in {"batch_undo", "undo_batch", "rollback_batch"}:
                batch_id = payload.get("batch_run_id") or payload.get("batch_id") or payload.get("id")
                if not batch_id:
                    raise ValueError("batch_run_id is required")
                return apply_review_batch_undo(self.conn, owner_kind, owner_id, batch_id, dry_run=bool(payload.get("dry_run", False)), safe_only=bool(payload.get("safe_only", True)), reason=str(payload.get("reason") or "review batch undo"))
            if action in {"undo_runs", "undos", "rollback_runs"}:
                return {"ok": True, "undo_runs": list_review_undo_runs(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))}
            if action in {"get_undo", "undo_get"}:
                undo_id = payload.get("undo_run_id") or payload.get("undo_id") or payload.get("id")
                if not undo_id:
                    raise ValueError("undo_run_id is required")
                return {"ok": True, "undo_run": get_review_undo_run(self.conn, owner_kind, owner_id, undo_id)}
            raise ValueError(f"Unknown review action: {action}")

    def _apply_review_action_locked(self, owner_kind: str, owner_id: str, item_id: str,
                                    session_id: str | None = None, turn_id: str | None = None,
                                    **payload: Any) -> dict[str, Any]:
        """Apply a review item action inside an existing transaction.

        This is intentionally conservative: safe automatic items can apply
        directly; ambiguous items return needs_choice/manual instead of guessing.
        Life-changing actions use LifeOps where the underlying surface supports it.
        """
        choice = payload.get("choice") or payload.get("decision")
        mode = str(payload.get("mode") or ("preview" if payload.get("dry_run") else "apply"))
        dry_run = bool(payload.get("dry_run") or mode in {"preview", "plan", "dry_run"})
        plan = plan_review_item_action(self.conn, owner_kind, owner_id, item_id, choice=choice)
        item = plan.get("item") or {}
        if dry_run:
            run = record_review_action_run(self.conn, owner_kind, owner_id, item_id=item_id, review_run_id=item.get("review_run_id"), mode="preview", status="planned", input_obj=payload, plan=plan, output={"dry_run": True})
            return {"ok": True, "applied": False, "plan": plan, "action_run": run}
        if not plan.get("supported", True):
            run = record_review_action_run(self.conn, owner_kind, owner_id, item_id=item_id, review_run_id=item.get("review_run_id"), mode="apply", status="failed", input_obj=payload, plan=plan, output={}, error=plan.get("message"))
            return {"ok": False, "applied": False, "plan": plan, "action_run": run, "error": plan.get("message")}
        if plan.get("requires_choice") or plan.get("application_type") in {"manual_choice", "manual_review"}:
            run = record_review_action_run(self.conn, owner_kind, owner_id, item_id=item_id, review_run_id=item.get("review_run_id"), mode="apply", status="needs_choice" if plan.get("requires_choice") else "manual", input_obj=payload, plan=plan, output={"message": plan.get("message")})
            return {"ok": True, "applied": False, "needs_choice": bool(plan.get("requires_choice")), "plan": plan, "action_run": run}
        try:
            output: dict[str, Any] = {}
            tx_id = None
            receipt_id = None
            app_type = plan.get("application_type")
            if app_type == "lifeops":
                commit = self._commit_ops_locked(plan.get("ops") or [], owner_kind, owner_id, "life_review_action", session_id=session_id, turn_id=turn_id, control=ensure_control(self.conn, owner_kind, owner_id))
                output = {"commit": commit}
                tx_id = commit.get("transaction_id")
                receipt_id = (commit.get("receipt") or {}).get("receipt_id")
            elif app_type == "direct" and plan.get("tool") == "life_sleep":
                output = plan_recovery_sleep_if_needed(self.conn, owner_kind, owner_id, threshold=int(payload.get("threshold", 60)), duration_minutes=int(payload.get("duration_minutes", 30)), source="life_review_action")
            elif app_type == "dream_repair":
                repair_plan = collect_open_dream_repair_ops(self.conn, owner_kind, owner_id, dream_run_id=plan.get("dream_run_id"), finding_ids=[plan.get("finding_id")] if plan.get("finding_id") else None, limit=50)
                ops = repair_plan.get("ops") or []
                if ops:
                    commit = self._commit_ops_locked(ops, owner_kind, owner_id, "life_review_dream_repair", session_id=session_id, turn_id=turn_id, control=ensure_control(self.conn, owner_kind, owner_id))
                    tx_id = commit.get("transaction_id")
                    receipt_id = (commit.get("receipt") or {}).get("receipt_id")
                    finding_ids = [f.get("id") for f in (repair_plan.get("findings") or []) if f.get("id")]
                    repair_run = record_dream_repair_run(self.conn, owner_kind, owner_id, dream_run_id=plan.get("dream_run_id"), mode="apply", finding_ids=finding_ids, proposed_ops=ops, status="applied", transaction_id=tx_id, receipt_id=receipt_id, output={"source": "life_review_action"})
                    output = {"repair_plan": repair_plan, "commit": commit, "repair_run": repair_run}
                else:
                    output = {"repair_plan": repair_plan, "commit": None}
            elif app_type == "confirmation":
                confirmation_id = plan.get("confirmation_id")
                if plan.get("action") == "reject":
                    c = mark_confirmation(self.conn, owner_kind, owner_id, confirmation_id, "rejected", resolved_by=payload.get("resolved_by") or "review", note=payload.get("note") or "rejected from /life review")
                    output = {"confirmation": c}
                else:
                    row = self.conn.execute("SELECT * FROM user_confirmations WHERE id=? AND owner_kind=? AND owner_id=?", (confirmation_id, owner_kind, owner_id)).fetchone()
                    if not row or row["status"] != "pending":
                        raise ValueError("confirmation not found or not pending")
                    ops = confirmed_ops(loads(row["proposed_ops_json"], []))
                    commit = self._commit_ops_locked(ops, owner_kind, owner_id, "user_confirmed_from_review", session_id=session_id or row["session_id"], turn_id=turn_id or row["turn_id"], control=ensure_control(self.conn, owner_kind, owner_id))
                    tx_id = commit.get("transaction_id")
                    receipt_id = (commit.get("receipt") or {}).get("receipt_id")
                    c = mark_confirmation(self.conn, owner_kind, owner_id, confirmation_id, "confirmed", resolved_by=payload.get("resolved_by") or "review", note=payload.get("note") or "confirmed from /life review", result_transaction_id=tx_id)
                    output = {"confirmation": c, "commit": commit}
            elif app_type == "policy_patch":
                if not payload.get("allow_policy_patch") and not payload.get("apply_policy_patch"):
                    output = {"message": "policy patch requires allow_policy_patch=true", "policy_patch": plan.get("policy_patch")}
                    run = record_review_action_run(self.conn, owner_kind, owner_id, item_id=item_id, review_run_id=item.get("review_run_id"), mode="apply", status="needs_choice", input_obj=payload, plan=plan, output=output)
                    return {"ok": True, "applied": False, "needs_choice": True, "plan": plan, "action_run": run, "output": output}
                output = set_srd_policy(self.conn, owner_kind, owner_id, policy_patch=plan.get("policy_patch") or {}, updated_by="life_review_action", source="life_review_action")
            elif app_type == "policy_suggestions":
                output = compute_srd_policy_suggestions(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 10)), record=True)
            else:
                output = {"message": plan.get("message"), "noop": True}
            run = record_review_action_run(self.conn, owner_kind, owner_id, item_id=item_id, review_run_id=item.get("review_run_id"), mode="apply", status="applied", input_obj=payload, plan=plan, output=output, transaction_id=tx_id, receipt_id=receipt_id)
            mark_review_item_resolved(self.conn, owner_kind, owner_id, item_id, action_run_id=run.get("id"), status="applied")
            return {"ok": True, "applied": True, "plan": plan, "output": output, "action_run": run}
        except Exception as exc:
            run = record_review_action_run(self.conn, owner_kind, owner_id, item_id=item_id, review_run_id=item.get("review_run_id"), mode="apply", status="failed", input_obj=payload, plan=plan, output={}, error=f"{type(exc).__name__}: {exc}")
            return {"ok": False, "applied": False, "plan": plan, "action_run": run, "error": f"{type(exc).__name__}: {exc}"}

    def _apply_review_batch_locked(self, owner_kind: str, owner_id: str,
                                   session_id: str | None = None, turn_id: str | None = None,
                                   **payload: Any) -> dict[str, Any]:
        """Preview/apply a safe batch of review items under review action policy."""
        policy_row = get_review_action_policy(self.conn, owner_kind, owner_id, create=True)
        policy = policy_row.get("policy") or {}
        review_run_id = payload.get("review_run_id") or payload.get("run_id")
        section = payload.get("section")
        raw_item_ids = payload.get("item_ids") or payload.get("items")
        if isinstance(raw_item_ids, str):
            item_ids = [x.strip() for x in raw_item_ids.split(",") if x.strip()]
        else:
            item_ids = list(raw_item_ids or []) if raw_item_ids else None
        safe_only = bool(payload.get("safe_only", policy.get("default_safe_only", True)))
        dry_run = bool(payload.get("dry_run", False) or payload.get("mode") in {"dry_run", "preview", "plan"})
        limit = int(payload.get("limit") or policy.get("max_batch_items") or 10)
        if not policy.get("allow_safe_batch", True) and not dry_run:
            plan = {"ok": False, "message": "review action policy disables safe batch apply", "policy": policy}
            run = record_review_batch_run(self.conn, owner_kind, owner_id, review_run_id=review_run_id, mode="apply", section=section, safe_only=safe_only, selected_item_ids=[], plan=plan, results=[], status="failed", error=plan["message"])
            return {"ok": False, "applied": False, "batch_run": run, "error": plan["message"]}
        if not review_run_id and not item_ids:
            review = build_human_review(self.conn, owner_kind, owner_id, include_doctor=bool(payload.get("include_doctor", True)), limit=int(payload.get("review_limit", 5)), persist=True, source="life_review_batch")
            review_run_id = review.get("review_run_id")
        selected = select_review_items_for_batch(self.conn, owner_kind, owner_id, review_run_id=review_run_id, section=section, item_ids=item_ids, safe_only=safe_only, limit=limit)
        item_ids_selected = [i.get("id") for i in selected if i.get("id")]
        plan = {
            "ok": True,
            "dry_run": dry_run,
            "review_run_id": review_run_id,
            "section": section,
            "safe_only": safe_only,
            "policy": policy,
            "selected_count": len(item_ids_selected),
            "selected_item_ids": item_ids_selected,
            "items": [{"item_id": i.get("id"), "item_type": i.get("item_type"), "title": i.get("title"), "plan": i.get("batch_plan")} for i in selected],
        }
        if dry_run:
            run = record_review_batch_run(self.conn, owner_kind, owner_id, review_run_id=review_run_id, mode="dry_run", section=section, safe_only=safe_only, selected_item_ids=item_ids_selected, plan=plan, results=[], status="planned")
            return {"ok": True, "applied": False, "plan": plan, "batch_run": run}
        results = []
        for item in selected:
            item_id = item.get("id")
            if not item_id:
                continue
            result = self._apply_review_action_locked(owner_kind, owner_id, item_id, session_id=session_id, turn_id=turn_id, mode="apply")
            result["item_id"] = item_id
            result["status"] = "applied" if result.get("applied") else ("skipped" if result.get("needs_choice") else "failed" if not result.get("ok") else "planned")
            results.append(result)
        status = "applied" if all(r.get("applied") for r in results) else ("skipped" if not results else "partial")
        run = record_review_batch_run(self.conn, owner_kind, owner_id, review_run_id=review_run_id, mode="apply", section=section, safe_only=safe_only, selected_item_ids=item_ids_selected, plan=plan, results=results, status=status)
        return {"ok": True, "applied": bool(results), "status": status, "plan": plan, "results": results, "batch_run": run}


    def _run_agent_managed_review_locked(self, owner_kind: str, owner_id: str, *, trigger_source: str = "manual",
                                         tick_id: str | None = None, dry_run: bool = False, force: bool = False,
                                         session_id: str | None = None, turn_id: str | None = None) -> dict[str, Any]:
        """Run the policy-gated agent-managed review loop inside a transaction.

        This is deliberately conservative. It only runs safe batch review actions
        selected by Review Action Policy, obeys daily limits and failure budget,
        and records a managed-loop run even when it no-ops.
        """
        if tick_id and not force:
            existing = self.conn.execute(
                "SELECT id FROM human_review_managed_loop_runs WHERE owner_kind=? AND owner_id=? AND tick_id=? ORDER BY created_at DESC LIMIT 1",
                (owner_kind, owner_id, tick_id),
            ).fetchone()
            if existing:
                run = get_managed_review_loop_run(self.conn, owner_kind, owner_id, existing["id"])
                return {"ok": True, "status": "duplicate_tick", "applied": False, "duplicate": True, "managed_run": run}
        dec = decide_managed_review_loop(self.conn, owner_kind, owner_id, trigger_source=trigger_source, force=force)
        policy = dec.get("policy") or {}
        decision = dec.get("decision") or {}
        state = dec.get("state") or {}
        limit = max(0, int(decision.get("remaining_actions") or 0))
        daily_limit = int(decision.get("daily_action_limit") or 0)
        failure_budget = int(decision.get("failure_budget") or 0)
        failure_before = int(decision.get("failure_count") or 0)
        daily_before = int(decision.get("daily_action_count") or 0)
        if not decision.get("allowed"):
            run = record_managed_review_loop_run(
                self.conn, owner_kind, owner_id, trigger_source=trigger_source, tick_id=tick_id,
                status="blocked", policy=policy, decision=decision, daily_action_count_before=daily_before,
                daily_action_limit=daily_limit, failure_count_before=failure_before, failure_budget=failure_budget,
                output={"state": state}, now=None,
            )
            return {"ok": True, "status": "blocked", "allowed": False, "decision": decision, "managed_run": run}
        if limit <= 0:
            run = record_managed_review_loop_run(
                self.conn, owner_kind, owner_id, trigger_source=trigger_source, tick_id=tick_id,
                status="skipped", policy=policy, decision={**decision, "reasons": [*decision.get("reasons", []), "no remaining action budget"]},
                daily_action_count_before=daily_before, daily_action_limit=daily_limit,
                failure_count_before=failure_before, failure_budget=failure_budget,
                output={"state": state}, now=None,
            )
            return {"ok": True, "status": "skipped", "allowed": True, "decision": decision, "managed_run": run}
        review = build_human_review(self.conn, owner_kind, owner_id, include_doctor=True, limit=5, persist=True, source="agent_managed_review_loop")
        sections = policy.get("agent_managed_sections") or [None]
        selected_ids: list[str] = []
        seen: set[str] = set()
        for section in sections:
            for item in select_review_items_for_batch(
                self.conn, owner_kind, owner_id, review_run_id=review.get("review_run_id"),
                section=None if section in {"all", "*"} else section,
                safe_only=bool(policy.get("agent_managed_safe_only", True)), limit=limit,
            ):
                if item.get("id") and item["id"] not in seen:
                    seen.add(item["id"])
                    selected_ids.append(item["id"])
                if len(selected_ids) >= limit:
                    break
            if len(selected_ids) >= limit:
                break
        decision = {**decision, "review_run_id": review.get("review_run_id"), "selected_item_ids": selected_ids, "dry_run": dry_run}
        if dry_run or not selected_ids:
            status = "planned" if selected_ids else "noop"
            run = record_managed_review_loop_run(
                self.conn, owner_kind, owner_id, trigger_source=trigger_source, tick_id=tick_id,
                status=status, policy=policy, decision=decision, review_run_id=review.get("review_run_id"),
                selected_count=len(selected_ids), applied_count=0, skipped_count=0, failed_count=0,
                daily_action_count_before=daily_before, daily_action_limit=daily_limit,
                failure_count_before=failure_before, failure_budget=failure_budget,
                output={"review": {"review_run_id": review.get("review_run_id"), "item_count": len(review.get("items") or [])}}, now=None,
            )
            return {"ok": True, "status": status, "applied": False, "decision": decision, "selected_item_ids": selected_ids, "managed_run": run, "review": review}
        batch = self._apply_review_batch_locked(
            owner_kind, owner_id, session_id=session_id, turn_id=turn_id,
            item_ids=selected_ids, safe_only=True, limit=limit, dry_run=False,
        )
        results = batch.get("results") or []
        applied_count = sum(1 for r in results if r.get("applied"))
        failed_count = sum(1 for r in results if (not r.get("ok")) or r.get("status") == "failed")
        skipped_count = max(0, len(selected_ids) - applied_count - failed_count)
        status = "applied" if failed_count == 0 and applied_count > 0 else ("partial" if applied_count > 0 else "failed" if failed_count else "noop")
        run = record_managed_review_loop_run(
            self.conn, owner_kind, owner_id, trigger_source=trigger_source, tick_id=tick_id,
            status=status, policy=policy, decision=decision, review_run_id=review.get("review_run_id"),
            batch_run_id=(batch.get("batch_run") or {}).get("id"), selected_count=len(selected_ids),
            applied_count=applied_count, skipped_count=skipped_count, failed_count=failed_count,
            daily_action_count_before=daily_before, daily_action_limit=daily_limit,
            failure_count_before=failure_before, failure_budget=failure_budget,
            output={"batch": batch, "review": {"review_run_id": review.get("review_run_id")}}, now=None,
        )
        return {"ok": True, "status": status, "applied": applied_count > 0, "decision": decision, "managed_run": run, "batch": batch, "review": review}


    def _run_managed_review_acceptance_locked(self, owner_kind: str, owner_id: str, *, stress_count: int = 12) -> dict[str, Any]:
        """Run synthetic acceptance scenarios for Agent Managed Review Loop.

        Uses a synthetic owner so the acceptance suite does not mutate the real
        agent's review queues, sleep state, or delayed replies.
        """
        synth_owner = f"{owner_id}-mgrev-{new_id('run')[:8]}"
        begin = begin_managed_review_acceptance_run(self.conn, owner_kind, owner_id)
        run_id = begin["id"]
        scenarios: list[dict[str, Any]] = []
        try:
            ensure_control(self.conn, owner_kind, synth_owner)
            # Scenario 1: default disabled should block and leave delayed reply pending.
            create_delayed_reply(self.conn, owner_kind, synth_owner, message_text="acceptance disabled", reason="managed acceptance")
            out1 = self._run_agent_managed_review_locked(owner_kind, synth_owner, trigger_source="manual", force=False)
            pending1 = self.conn.execute("SELECT COUNT(*) FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status='pending'", (owner_kind, synth_owner)).fetchone()[0]
            ok1 = out1.get("status") == "blocked" and int(pending1) >= 1
            scenarios.append(record_managed_review_acceptance_scenario(self.conn, owner_kind, owner_id, run_id, "MGR01_DISABLED_BY_DEFAULT", "passed" if ok1 else "failed", "Managed review is disabled by default and does not mutate queues.", {"result": out1, "pending_count": pending1, "synthetic_owner_id": synth_owner}))

            # Enable managed loop with strict limits.
            set_review_action_policy(self.conn, owner_kind, synth_owner, policy_patch={
                "allow_agent_managed_loop": True,
                "agent_managed_trigger_sources": ["manual", "heartbeat"],
                "agent_managed_daily_action_limit": 2,
                "agent_managed_failure_budget": 1,
                "agent_managed_sections": ["reply", "sleep", "dream", "proactive", "policy"],
                "agent_managed_safe_only": True,
            }, updated_by="managed_acceptance")

            # Scenario 2: applies safe delayed reply once enabled.
            create_delayed_reply(self.conn, owner_kind, synth_owner, message_text="acceptance release", reason="managed acceptance")
            out2 = self._run_agent_managed_review_locked(owner_kind, synth_owner, trigger_source="manual", force=False)
            released2 = self.conn.execute("SELECT COUNT(*) FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status='released'", (owner_kind, synth_owner)).fetchone()[0]
            ok2 = out2.get("status") in {"applied", "partial"} and int(released2) >= 1
            scenarios.append(record_managed_review_acceptance_scenario(self.conn, owner_kind, owner_id, run_id, "MGR02_SAFE_ITEM_APPLIED", "passed" if ok2 else "failed", "Managed review applies safe delayed-reply items when policy allows.", {"result": out2, "released_count": released2, "synthetic_owner_id": synth_owner}))

            # Scenario 3: daily limit blocks after budget is exhausted.
            create_delayed_reply(self.conn, owner_kind, synth_owner, message_text="limit one", reason="managed acceptance")
            create_delayed_reply(self.conn, owner_kind, synth_owner, message_text="limit two", reason="managed acceptance")
            out3 = self._run_agent_managed_review_locked(owner_kind, synth_owner, trigger_source="manual", force=False)
            out3b = self._run_agent_managed_review_locked(owner_kind, synth_owner, trigger_source="manual", force=False)
            reasons = " ".join((out3b.get("decision") or {}).get("reasons") or [])
            ok3 = out3b.get("status") in {"blocked", "skipped"} and ("limit" in reasons or "budget" in reasons)
            scenarios.append(record_managed_review_acceptance_scenario(self.conn, owner_kind, owner_id, run_id, "MGR03_DAILY_LIMIT", "passed" if ok3 else "failed", "Managed review respects daily action limit.", {"first": out3, "second": out3b, "synthetic_owner_id": synth_owner}))

            # Scenario 4: duplicate tick id is idempotent.
            synth2 = f"{owner_id}-mgrev-dup-{new_id('run')[:8]}"
            ensure_control(self.conn, owner_kind, synth2)
            set_review_action_policy(self.conn, owner_kind, synth2, policy_patch={
                "allow_agent_managed_loop": True,
                "agent_managed_trigger_sources": ["heartbeat"],
                "agent_managed_daily_action_limit": 5,
                "agent_managed_failure_budget": 2,
            }, updated_by="managed_acceptance")
            create_delayed_reply(self.conn, owner_kind, synth2, message_text="dup tick", reason="managed acceptance")
            tick_id = f"tick-{new_id('dup')}"
            out4a = self._run_agent_managed_review_locked(owner_kind, synth2, trigger_source="heartbeat", tick_id=tick_id, force=False)
            out4b = self._run_agent_managed_review_locked(owner_kind, synth2, trigger_source="heartbeat", tick_id=tick_id, force=False)
            runs4 = self.conn.execute("SELECT COUNT(*) FROM human_review_managed_loop_runs WHERE owner_kind=? AND owner_id=? AND tick_id=?", (owner_kind, synth2, tick_id)).fetchone()[0]
            ok4 = out4a.get("status") in {"applied", "noop"} and out4b.get("status") == "duplicate_tick" and int(runs4) == 1
            scenarios.append(record_managed_review_acceptance_scenario(self.conn, owner_kind, owner_id, run_id, "MGR04_DUPLICATE_TICK_IDEMPOTENCY", "passed" if ok4 else "failed", "Managed review heartbeat tick id is idempotent.", {"first": out4a, "second": out4b, "run_count_for_tick": runs4, "synthetic_owner_id": synth2}))

            # Scenario 5: stress batch applies no more than limit and stays traceable.
            stress = self._run_managed_review_stress_locked(owner_kind, owner_id, count=stress_count, limit=5, synthetic_suffix="acceptance")
            ok5 = stress.get("ok") and (stress.get("stress_run") or {}).get("applied_count", 0) <= 5 and (stress.get("stress_run") or {}).get("created_count", 0) == stress_count
            scenarios.append(record_managed_review_acceptance_scenario(self.conn, owner_kind, owner_id, run_id, "MGR05_STRESS_LIMITED_BATCH", "passed" if ok5 else "failed", "Managed review stress run respects batch limit and records stress trace.", {"stress": stress, "synthetic_owner_id": (stress.get("stress_run") or {}).get("output", {}).get("synthetic_owner_id")}))

            final = finish_managed_review_acceptance_run(self.conn, owner_kind, owner_id, run_id, output={"synthetic_owner_id": synth_owner, "scenario_ids": [s.get("id") for s in scenarios]})
            return {"ok": final.get("status") == "passed", "acceptance_run": final}
        except Exception as exc:
            final = finish_managed_review_acceptance_run(self.conn, owner_kind, owner_id, run_id, output={"synthetic_owner_id": synth_owner}, error=f"{type(exc).__name__}: {exc}")
            return {"ok": False, "acceptance_run": final, "error": f"{type(exc).__name__}: {exc}"}

    def _run_managed_review_stress_locked(self, owner_kind: str, owner_id: str, *, count: int = 25, limit: int = 10, synthetic_suffix: str | None = None) -> dict[str, Any]:
        """Stress managed review with many delayed replies on a synthetic owner."""
        import time
        count = max(0, min(int(count), 500))
        limit = max(1, min(int(limit), 100))
        synth_owner = f"{owner_id}-mgrev-stress-{synthetic_suffix or new_id('stress')[:8]}"
        start = time.time()
        error = None
        out: dict[str, Any] = {}
        try:
            ensure_control(self.conn, owner_kind, synth_owner)
            set_review_action_policy(self.conn, owner_kind, synth_owner, policy_patch={
                "allow_agent_managed_loop": True,
                "agent_managed_trigger_sources": ["manual"],
                "agent_managed_daily_action_limit": limit,
                "agent_managed_failure_budget": 2,
                "agent_managed_sections": ["reply"],
                "agent_managed_safe_only": True,
                "max_batch_items": limit,
            }, updated_by="managed_stress")
            for i in range(count):
                create_delayed_reply(self.conn, owner_kind, synth_owner, message_text=f"managed stress delayed reply {i+1}", reason="managed review stress")
            result = self._run_agent_managed_review_locked(owner_kind, synth_owner, trigger_source="manual", force=False)
            batch = result.get("batch") or {}
            run = result.get("managed_run") or {}
            applied = int(run.get("applied_count") or 0)
            selected = int(run.get("selected_count") or 0)
            failed = int(run.get("failed_count") or 0)
            released = self.conn.execute("SELECT COUNT(*) FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status='released'", (owner_kind, synth_owner)).fetchone()[0]
            pending = self.conn.execute("SELECT COUNT(*) FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status='pending'", (owner_kind, synth_owner)).fetchone()[0]
            out = {"managed_result": result, "batch_run_id": batch.get("batch_run", {}).get("id") if isinstance(batch, dict) else None, "synthetic_owner_id": synth_owner, "released_count": released, "pending_count": pending}
            status = "passed" if result.get("ok") and applied <= limit and released == applied else "failed"
            duration_ms = int((time.time() - start) * 1000)
            stress_run = record_managed_review_stress_run(self.conn, owner_kind, owner_id, stress_kind="delayed_reply_batch", input_obj={"count": count, "limit": limit}, status=status, output=out, created_count=count, selected_count=selected, applied_count=applied, failed_count=failed, duration_ms=duration_ms, error=None if status == "passed" else "stress invariant failed")
            return {"ok": status == "passed", "stress_run": stress_run, **out}
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            duration_ms = int((time.time() - start) * 1000)
            stress_run = record_managed_review_stress_run(self.conn, owner_kind, owner_id, stress_kind="delayed_reply_batch", input_obj={"count": count, "limit": limit}, status="failed", output=out, created_count=count, duration_ms=duration_ms, error=error)
            return {"ok": False, "stress_run": stress_run, "error": error}

    def _run_managed_review_for_tick(self, owner_kind: str, owner_id: str, control: dict[str, Any],
                                     tick_id: str, trace: Trace, now: str, manual: bool) -> dict[str, Any]:
        try:
            with trace.span("agent_managed_review_loop", {"tick_id": tick_id, "manual": manual}):
                return self._run_agent_managed_review_locked(
                    owner_kind, owner_id, trigger_source="heartbeat", tick_id=tick_id,
                    dry_run=False, force=False, session_id=None, turn_id=tick_id,
                )
        except Exception as exc:
            append_audit(self.conn, owner_kind, owner_id, "agent_managed_review_loop_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
            return {"ok": False, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}

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
        if action in {"sleep_autonomy_execution_acceptance", "sae_acceptance", "sleep_execution_acceptance"}:
            return run_sleep_autonomy_execution_acceptance(self, owner_kind, owner_id)
        if action in {"sleep_reply_dream_conversation_acceptance", "crd_acceptance", "conversation_acceptance", "srd_conversation_acceptance"}:
            return run_sleep_reply_dream_conversation_acceptance(self, owner_kind, owner_id)
        with transaction(self.conn):
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
            if action in {"cron_test", "heartbeat_test", "test_tick_script"}:
                script = payload.get("script_path")
                if not script:
                    script = str(write_tick_script())
                return run_tick_script_test(self.conn, owner_kind, owner_id, script_path=str(script), timeout=int(payload.get("timeout", 30)))
            if action in {"surface", "tool_surface", "command_surface"}:
                return {"ok": True, "surface": surface_snapshot()}
            if action in {"integration_check", "hermes_integration_check"}:
                return integration_check(self.conn, owner_kind, owner_id, include_details=bool(payload.get("include_details", False)))
            if action in {"api_freeze", "api_freeze_snapshot"}:
                return api_freeze_snapshot(self.conn, owner_kind, owner_id)
            if action in {"api_freeze_status", "api_freeze_snapshots"}:
                return api_freeze_status(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 10)))
            if action in {"release_readiness", "v1_rc_check"}:
                return release_readiness(self.conn, owner_kind, owner_id)
            if action in {"mandatory_gate_patch", "core_patch"}:
                return mandatory_gate_patch()
            if action in {"concurrency_smoke", "schedule_overlap_smoke", "heartbeat_idempotency_smoke", "lifeops_stress"}:
                return concurrency_smoke(self.conn, owner_kind, owner_id, action=action, workers=int(payload.get("workers", 4)), items=int(payload.get("items", payload.get("memories", 20))))
            if action in {"acceptance", "acceptance_suite", "v1_rc_acceptance"}:
                return acceptance_suite(self.conn, owner_kind, owner_id, report_path=payload.get("report_path"))
            if action in {"acceptance_reports"}:
                return list_acceptance_reports(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"acceptance_report"}:
                return get_acceptance_report(self.conn, str(payload.get("report_id") or payload.get("id") or ""))
            if action in {"acceptance_runs"}:
                return list_acceptance_runs(self.conn, owner_kind, owner_id, acceptance_run_id=payload.get("acceptance_run_id"), limit=int(payload.get("limit", 50)))
            if action in {"v1_rc_checklists", "v1_rc_checklist"}:
                return v1_rc_checklists(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"sleep_reply_dream_acceptance", "srd_acceptance", "sleep_dream_acceptance"}:
                return run_sleep_reply_dream_acceptance(self.conn, owner_kind, owner_id)
            if action in {"sleep_reply_dream_acceptance_runs", "srd_acceptance_runs"}:
                return list_sleep_reply_dream_acceptance(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"sleep_reply_dream_acceptance_get", "srd_acceptance_get"}:
                return get_sleep_reply_dream_acceptance(self.conn, str(payload.get("acceptance_run_id") or payload.get("id") or ""))
            if action in {"sleep_autonomy_execution_acceptance_runs", "sae_acceptance_runs"}:
                return list_sleep_autonomy_execution_acceptance(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"sleep_autonomy_execution_acceptance_get", "sae_acceptance_get"}:
                return get_sleep_autonomy_execution_acceptance(self.conn, str(payload.get("acceptance_run_id") or payload.get("id") or ""))
            if action in {"sleep_reply_dream_conversation_acceptance_runs", "crd_acceptance_runs", "srd_conversation_acceptance_runs"}:
                return list_sleep_reply_dream_conversation_acceptance(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 20)))
            if action in {"sleep_reply_dream_conversation_acceptance_get", "crd_acceptance_get", "srd_conversation_acceptance_get"}:
                return get_sleep_reply_dream_conversation_acceptance(self.conn, str(payload.get("acceptance_run_id") or payload.get("id") or ""))
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
                    "execution_decisions", "serendipity_events", "memory_vec", "life_invariant_checks", "schema_migrations", "install_checks", "final_gate_reports", "final_gate_feedback_queue", "trace_coverage_reports", "acceptance_reports", "api_freeze_snapshots", "event_state_transitions", "schedule_block_state_transitions", "action_state_transitions", "agent_realtime_state", "agent_state_snapshots", "dream_runs", "dream_audit_findings", "dream_entries", "dream_repair_runs",
    "sleep_day_states", "sleep_recovery_plans", "delayed_reply_digests", "dream_repair_policies",
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

                missing_event_transitions = self.conn.execute(
                    """SELECT COUNT(*) FROM events e WHERE e.owner_kind=? AND e.owner_id=?
                          AND NOT EXISTS (SELECT 1 FROM event_state_transitions t WHERE t.event_id=e.id)""",
                    (owner_kind, owner_id),
                ).fetchone()[0] if "event_state_transitions" in existing else 0
                add("event_transition_coverage", "ok" if missing_event_transitions == 0 else "error", "event transition coverage ok" if missing_event_transitions == 0 else f"{missing_event_transitions} event(s) without transition history", missing=missing_event_transitions)

                stuck_state = self.conn.execute(
                    """SELECT * FROM agent_realtime_state WHERE owner_kind=? AND owner_id=?
                          AND lease_expires_at_ts IS NOT NULL AND lease_expires_at_ts < unixepoch('now')
                          AND mode IN ('busy','asleep','napping','dreaming','uninterruptible_event','waiting_to_reply')""",
                    (owner_kind, owner_id),
                ).fetchall() if "agent_realtime_state" in existing else []
                add("realtime_state_lease", "warn" if stuck_state else "ok", "realtime state leases ok" if not stuck_state else f"{len(stuck_state)} expired realtime lease(s)", stuck=[dict(r) for r in stuck_state] if include_samples or stuck_state else [])

                if {"sleep_sessions", "dream_runs"}.issubset(existing):
                    missing_dreams = self.conn.execute(
                        """SELECT COUNT(*) FROM sleep_sessions s WHERE s.owner_kind=? AND s.owner_id=?
                              AND s.session_type='core_sleep' AND s.status IN ('completed','interrupted')
                              AND COALESCE(s.actual_duration_minutes,0) >= 90
                              AND NOT EXISTS (SELECT 1 FROM dream_runs d WHERE d.sleep_session_id=s.id)""",
                        (owner_kind, owner_id),
                    ).fetchone()[0]
                    stuck_dreams = self.conn.execute(
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
                    actions = self.conn.execute("SELECT * FROM actions WHERE event_id=?", (event_id,)).fetchall()
                    results = self.conn.execute("SELECT * FROM results WHERE event_id=?", (event_id,)).fetchall()
                    ledger = self.conn.execute("SELECT * FROM resource_ledger WHERE event_id=?", (event_id,)).fetchall()
                    schedule_blocks = self.conn.execute("SELECT * FROM schedule_blocks WHERE event_id=? ORDER BY start_ts, created_at", (event_id,)).fetchall()
                    block_ids = [r["id"] for r in schedule_blocks]
                    if block_ids:
                        q = ",".join("?" for _ in block_ids)
                        wake_jobs = self.conn.execute(f"SELECT * FROM wake_jobs WHERE target_id IN ({q}) ORDER BY wake_at, created_at", tuple(block_ids)).fetchall()
                    else:
                        wake_jobs = []
                    memories = self.conn.execute("SELECT * FROM memories WHERE event_id=? OR content LIKE ? ORDER BY created_at", (event_id, f"%{event_id}%")).fetchall()
                    goal_links = self.conn.execute("SELECT * FROM event_goal_links WHERE event_id=?", (event_id,)).fetchall()
                    dependencies = self.conn.execute("SELECT * FROM event_dependencies WHERE event_id=? OR depends_on_event_id=?", (event_id, event_id)).fetchall()
                    execution_decisions = self.conn.execute("SELECT * FROM execution_decisions WHERE event_id=? OR schedule_block_id IN (SELECT id FROM schedule_blocks WHERE event_id=?) ORDER BY created_at", (event_id, event_id)).fetchall()
                    execution_sleep_adjustments = self.conn.execute("SELECT * FROM execution_sleep_adjustments WHERE event_id=? OR schedule_block_id IN (SELECT id FROM schedule_blocks WHERE event_id=?) ORDER BY created_at", (event_id, event_id)).fetchall() if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='execution_sleep_adjustments'").fetchone() else []
                    serendipity = self.conn.execute("SELECT * FROM serendipity_events WHERE event_id=? OR trigger_event_id=? ORDER BY created_at", (event_id, event_id)).fetchall()
                    proactive_intents = self.conn.execute("SELECT * FROM proactive_intents WHERE trigger_event_id=? ORDER BY created_at", (event_id,)).fetchall()
                    diary_entries = self.conn.execute("SELECT * FROM diary_entries WHERE source_event_ids_json LIKE ? ORDER BY created_at", (f"%{event_id}%",)).fetchall()
                    journal = self.conn.execute("SELECT * FROM life_journal WHERE payload_json LIKE ? ORDER BY created_at", (f"%{event_id}%",)).fetchall()
                    event_state_transitions_rows = self.conn.execute("SELECT * FROM event_state_transitions WHERE event_id=? ORDER BY occurred_at_ts, occurred_at", (event_id,)).fetchall()
                    schedule_state_transitions_rows = self.conn.execute("SELECT * FROM schedule_block_state_transitions WHERE event_id=? ORDER BY occurred_at_ts, occurred_at", (event_id,)).fetchall()
                    action_state_transitions_rows = self.conn.execute("SELECT * FROM action_state_transitions WHERE event_id=? ORDER BY occurred_at_ts, occurred_at", (event_id,)).fetchall()
                    state_snapshots = self.conn.execute("SELECT * FROM agent_state_snapshots WHERE event_id=? ORDER BY created_at", (event_id,)).fetchall()
                    sleep_plans = self.conn.execute("SELECT * FROM sleep_plans WHERE event_id=? ORDER BY created_at", (event_id,)).fetchall() if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='sleep_plans'").fetchone() else []
                    sleep_sessions = self.conn.execute("SELECT * FROM sleep_sessions WHERE event_id=? ORDER BY COALESCE(actual_sleep_at_ts, unixepoch(created_at))", (event_id,)).fetchall() if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='sleep_sessions'").fetchone() else []
                    return {"ok": True, "event": dict(event) if event else None, "actions": [dict(a) for a in actions], "results": [dict(r) for r in results], "resource_ledger": [dict(l) for l in ledger], "schedule_blocks": [dict(r) for r in schedule_blocks], "wake_jobs": [dict(r) for r in wake_jobs], "memories": [dict(r) for r in memories], "goal_links": [dict(r) for r in goal_links], "dependencies": [dict(r) for r in dependencies], "execution_decisions": [dict(r) for r in execution_decisions], "execution_sleep_adjustments": [dict(r) for r in execution_sleep_adjustments], "serendipity": [dict(r) for r in serendipity], "proactive_intents": [dict(r) for r in proactive_intents], "diary_entries": [dict(r) for r in diary_entries], "event_state_transitions": [dict(r) for r in event_state_transitions_rows], "schedule_state_transitions": [dict(r) for r in schedule_state_transitions_rows], "action_state_transitions": [dict(r) for r in action_state_transitions_rows], "state_snapshots": [dict(r) for r in state_snapshots], "sleep_plans": [dict(r) for r in sleep_plans], "sleep_sessions": [dict(r) for r in sleep_sessions], "journal": [dict(j) for j in journal]}
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

    def schedule(self, action: str = "today", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                 session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        """Human-friendly schedule read/write interface.

        Read actions render timelines. Write actions are convenience wrappers
        over LifeOps and never mutate schedule tables directly.
        """
        action_l = str(action or "today").strip().lower()
        if action_l in {"schedule_event", "schedule", "arrange", "排期", "安排"} and (payload.get("event_id") or payload.get("title")):
            if not payload.get("event_id"):
                # Two-step convenience: create event first, then schedule the returned id.
                created = self.commit_ops([{"type": "CREATE_EVENT", "payload": {
                    "title": payload.get("title"),
                    "description": payload.get("description"),
                    "event_type": payload.get("event_type") or "other",
                    "event_category": payload.get("event_category") or payload.get("event_type") or "other",
                    "source": payload.get("source") or "life_schedule_tool",
                    "status": "planned",
                    "importance": int(payload.get("importance", 50)),
                    "priority": int(payload.get("priority", 50)),
                }}], owner_kind, owner_id, "life_schedule_tool", session_id, turn_id)
                event_id = (((created.get("results") or [{}])[0].get("result") or {}).get("id"))
                if not event_id:
                    return created
                payload = {**payload, "event_id": event_id}
            op = {"type": "CREATE_SCHEDULE_BLOCK", "payload": {
                "event_id": payload.get("event_id"),
                "start": payload.get("start") or payload.get("planned_start"),
                "end": payload.get("end") or payload.get("planned_end"),
                "block_type": payload.get("block_type") or "planned_event",
                "timezone_name": payload.get("timezone") or payload.get("timezone_name") or "UTC",
                "interruptibility": payload.get("interruptibility") or {},
            }}
            return self.commit_ops([op], owner_kind, owner_id, "life_schedule_tool", session_id, turn_id)
        if action_l in {"reschedule", "move", "改期", "重新排期"}:
            block_id = payload.get("schedule_block_id") or payload.get("block_id")
            if not block_id:
                raise ValueError("schedule_block_id is required for reschedule")
            with transaction(self.conn):
                row = self.conn.execute("SELECT * FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?", (block_id, owner_kind, owner_id)).fetchone()
                if not row:
                    raise ValueError(f"schedule block not found: {block_id}")
                event_id = row["event_id"]
                block_type = row["block_type"] or "planned_event"
                tz = row["timezone"] or payload.get("timezone") or "UTC"
            ops = [
                {"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": block_id, "status": "rescheduled", "reason": payload.get("reason") or "rescheduled by life_schedule"}},
                {"type": "CREATE_SCHEDULE_BLOCK", "payload": {"event_id": event_id, "start": payload.get("start"), "end": payload.get("end"), "block_type": block_type, "timezone_name": payload.get("timezone") or tz}},
            ]
            return self.commit_ops(ops, owner_kind, owner_id, "life_schedule_tool", session_id, turn_id)
        if action_l in {"cancel", "cancel_block", "取消"}:
            return self.commit_ops([{"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": payload.get("schedule_block_id") or payload.get("block_id"), "status": "cancelled", "reason": payload.get("reason") or "cancelled by life_schedule"}}], owner_kind, owner_id, "life_schedule_tool", session_id, turn_id)
        if action_l in {"complete", "complete_block", "done", "完成"}:
            return self.commit_ops([{"type": "UPDATE_SCHEDULE_BLOCK_STATUS", "payload": {"schedule_block_id": payload.get("schedule_block_id") or payload.get("block_id"), "status": "completed", "reason": payload.get("reason") or "completed by life_schedule"}}], owner_kind, owner_id, "life_schedule_tool", session_id, turn_id)

        with transaction(self.conn):
            canon = get_active_canon(self.conn, owner_kind, owner_id)
            period = payload.get("period") or action or "today"
            if period in {"list", "view", "show"}:
                period = payload.get("period") or "today"
            if str(period).lower() in {"unscheduled", "queue", "planned_events", "待排期", "未排期"}:
                return list_unscheduled_events(self.conn, owner_kind, owner_id, limit=int(payload.get("limit", 100)))
            if str(period).lower() in {"explain", "semantics", "help", "说明"}:
                return explain_schedule_semantics()
            date = payload.get("date")
            # Accept direct action as date, e.g. /life schedule 2026-06-11.
            if isinstance(action, str) and len(action) >= 8 and action[0].isdigit():
                date = action
                period = "day"
            return list_human_schedule(
                self.conn, owner_kind, owner_id, period=str(period or "today"), date=date,
                start=payload.get("start"), end=payload.get("end"), tz_name=payload.get("timezone") or _tz_from_canon(canon),
                include_completed=bool(payload.get("include_completed", True)), limit=int(payload.get("limit", 200)),
            )

    def required_settings(self, action: str = "check", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                          **payload: Any) -> dict[str, Any]:
        """Human/agent friendly Canon/settings interface.

        Read actions are human-readable.  Write actions only update CanonDraft;
        active Canon changes still require /life commit.
        """
        action = (action or "check").strip()
        with transaction(self.conn):
            canon = get_active_canon(self.conn, owner_kind, owner_id)
            if action in {"latest", "last"}:
                latest = latest_required_settings_check(self.conn, owner_kind, owner_id)
                return {"ok": True, "latest": latest, "rendered": latest.get("rendered") if latest else "还没有运行过必选设定检查。"}
            if action in {"requirements", "spec", "schema", "必选项", "规格"}:
                return required_settings_spec()
            if action in {"suggest_defaults", "suggestions", "defaults", "补全建议"}:
                check = check_required_settings(self.conn, owner_kind, owner_id, canon, persist=False, source="suggest_defaults")
                return default_setting_suggestions(check, str(payload.get("kind") or payload.get("preset") or "balanced"))
            if action in {"apply_default_draft", "complete_defaults", "apply_defaults", "写入默认草案"}:
                check = check_required_settings(self.conn, owner_kind, owner_id, canon, persist=False, source="apply_default_draft")
                sug = default_setting_suggestions(check, str(payload.get("kind") or payload.get("preset") or "balanced"))
                draft = patch_canon_draft(self.conn, owner_kind, owner_id, patch=sug["patch"], source=str(payload.get("source") or "life_config_defaults"))
                return {"ok": True, "suggestions": sug, "draft": draft, "rendered": sug["rendered"] + "\n\n" + render_draft_summary(draft)}
            if action in {"summary", "get", "canon", "show", "设定"}:
                required = check_required_settings(self.conn, owner_kind, owner_id, canon, persist=False, source="summary") if owner_kind == "agent" else {"ok": True}
                return {"ok": True, "canon": canon, "required_settings": required, "rendered": render_canon_summary(canon) + "\n\n" + (required.get("rendered") or "")}
            if action in {"missing", "check", "status", "required"}:
                return check_required_settings(self.conn, owner_kind, owner_id, canon, persist=bool(payload.get("persist", True)), source=str(payload.get("source") or action or "manual"))
            if action in {"draft", "草案"}:
                control = ensure_control(self.conn, owner_kind, owner_id)
                draft_id = control.get("draft_canon_id")
                draft = get_draft(self.conn, draft_id) if draft_id else begin_setup(self.conn, owner_kind, owner_id, reason="config_draft")
                return {"ok": True, "draft": draft, "rendered": render_draft_summary(draft)}
            if action in {"patch", "set", "write", "补充", "update"}:
                text = payload.get("text") or payload.get("statement")
                patch = payload.get("patch")
                if isinstance(patch, str):
                    patch = loads(patch, {})
                value = payload.get("value")
                draft = patch_canon_draft(
                    self.conn, owner_kind, owner_id,
                    path=payload.get("path"), value=value, section=payload.get("section"),
                    patch=patch if isinstance(patch, dict) else None, text=text,
                    source=str(payload.get("source") or "agent"),
                )
                return {"ok": True, "draft": draft, "rendered": render_draft_summary(draft)}
            if action in {"explain", "help", "说明"}:
                rendered = """LifeEngine 设定读写说明
======================
- 读取当前设定：/life config 或 life_config(action='summary')
- 检查缺失项：/life config check
- 补充自然语言设定：/life setup <设定> 或 life_config(action='patch', text='...')
- 补充结构化设定：life_config(action='patch', path='truth_sources.bindings.weather.authority', value='narrative_simulator')
- 所有补充先进入 CanonDraft，不会污染生活流水。
- 真正启用设定必须 /life commit。
- Agent 自己可以补全非关键默认值；人设、世界观、真相源建议让用户明确确认。
"""
                return {"ok": True, "rendered": rendered}
            return check_required_settings(self.conn, owner_kind, owner_id, canon, persist=bool(payload.get("persist", True)), source=str(payload.get("source") or action or "manual"))

    def interface(self, action: str = "catalog", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
                  session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
        """Unified safe Agent-facing interface router.

        This intentionally does not expose raw SQL. Read routes call existing
        domain APIs; write routes update CanonDraft or LifeOps-backed domain
        methods so receipts, validators, and trace remain intact.
        """
        from .interface import run as run_interface
        return run_interface(self, action, owner_kind, owner_id, session_id=session_id, turn_id=turn_id, **payload)

    def startup_check(self, owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID, *, source: str = "startup") -> dict[str, Any]:
        with transaction(self.conn):
            control = ensure_control(self.conn, owner_kind, owner_id)
            canon = get_active_canon(self.conn, owner_kind, owner_id)
            required = check_required_settings(self.conn, owner_kind, owner_id, canon, persist=True, source=source) if owner_kind == "agent" else {"ok": True}
            # Make self-life management opt-out rather than opt-in. Human/user-life
            # confirmation remains protected elsewhere.
            gates = dict(control.get("module_gates") or {})
            changed = False
            if gates.get("autonomy") in {None, "manual", "off"}:
                gates["autonomy"] = "full"
                changed = True
            if gates.get("managed_review_loop") in {None, "off", "manual"}:
                gates["managed_review_loop"] = "auto"
                changed = True
            if changed:
                update_control(self.conn, owner_kind, owner_id, module_gates_json=dumps(gates))
            # Ensure review policy permits agent-managed safe maintenance by default.
            try:
                from .review import get_review_action_policy, set_review_action_policy
                pol = get_review_action_policy(self.conn, owner_kind, owner_id, create=True).get("policy") or {}
                if not pol.get("allow_agent_managed_loop"):
                    set_review_action_policy(self.conn, owner_kind, owner_id, policy_patch={"allow_agent_managed_loop": True, "mode": "agent_managed_safe"}, updated_by="startup_check")
            except Exception:
                pass
            return {"ok": True, "control": ensure_control(self.conn, owner_kind, owner_id), "required_settings": required}

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
                sleep = sleep_status(self.conn, owner_kind, owner_id) if owner_kind == "agent" else {}
                reply_gate = reply_gate_status(self.conn, owner_kind, owner_id) if owner_kind == "agent" else {}
                dreams = dream_status(self.conn, owner_kind, owner_id) if owner_kind == "agent" else {}
                srd_policy = get_srd_policy(self.conn, owner_kind, owner_id) if owner_kind == "agent" else {}
                final_gate_feedback = consume_final_gate_feedback(self.conn, owner_kind, owner_id, limit=3)
                required = check_required_settings(self.conn, owner_kind, owner_id, canon, persist=False) if owner_kind == "agent" else {"ok": True}
                today_schedule = list_human_schedule(self.conn, owner_kind, owner_id, period="today", tz_name=_tz_from_canon(canon), limit=20) if owner_kind == "agent" else {"items": []}
                out = _render_life_context(control, canon, events, resources, memories, pending, scope, truth_sources, inventory=inventory, confirmations=confirmations, goals=goals, arcs=arcs, autonomy=autonomy, proactive_outbox=proactive_outbox if owner_kind == "agent" else [], proactive_states=proactive_states if owner_kind == "agent" else [], execution=execution, serendipity=serendipity, sleep=sleep, reply_gate=reply_gate, dreams=dreams, srd_policy=srd_policy, final_gate_feedback=final_gate_feedback, required_settings=required, today_schedule=today_schedule)
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
                         sleep: dict[str, Any] | None = None,
                         reply_gate: dict[str, Any] | None = None,
                         dreams: dict[str, Any] | None = None,
                         srd_policy: dict[str, Any] | None = None,
                         final_gate_feedback: list[dict[str, Any]] | None = None,
                         required_settings: dict[str, Any] | None = None,
                         today_schedule: dict[str, Any] | None = None) -> str:
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
    compact_autonomy = [{"id": d["id"], "mode": d.get("mode"), "status": d.get("status"), "reason": d.get("reason"), "selected_goal_id": d.get("selected_goal_id"), "sleep": (d.get("score") or {}).get("sleep", {}), "proposed_ops": (d.get("proposed_ops") or [])[:2]} for d in (autonomy or [])[:3]]
    compact_outbox = [{"id": o.get("id"), "intent_id": o.get("intent_id"), "target_user_id": o.get("target_user_id"), "status": o.get("status"), "draft_text": (o.get("draft_text") or "")[:240]} for o in (proactive_outbox or [])[:3]]
    compact_proactive_states = [{"user_id": ps.get("user_id"), "state": ps.get("state"), "pending_intent_ids": (ps.get("pending_intent_ids") or [])[:3], "daily_sent_count": ps.get("daily_sent_count")} for ps in (proactive_states or [])[:3]]
    compact_execution = [{"id": d.get("id"), "decision_type": d.get("decision_type"), "status": d.get("status"), "reason": d.get("reason"), "event_id": d.get("event_id"), "proposed_ops": (d.get("proposed_ops") or [])[:2]} for d in (execution or [])[:3]]
    compact_serendipity = [{"id": s.get("id"), "title": s.get("title"), "type": s.get("serendipity_type"), "trigger_event_id": s.get("trigger_event_id"), "intensity": s.get("intensity")} for s in (serendipity or [])[:3]]
    compact_sleep = {
        "realtime_state": (sleep or {}).get("realtime_state", {}),
        "planned": (sleep or {}).get("planned", [])[:3],
        "active_plans": (sleep or {}).get("active_plans", [])[:3],
        "recent_sessions": (sleep or {}).get("recent_sessions", [])[:3],
    }
    compact_reply_gate = {
        "realtime_state": (reply_gate or {}).get("realtime_state", {}),
        "pending_delayed_replies": [{"id": r.get("id"), "preview": r.get("message_preview"), "reason": r.get("reason"), "status": r.get("status")} for r in (reply_gate or {}).get("pending_delayed_replies", [])[:5]],
        "recent_decisions": [{"id": d.get("id"), "decision": d.get("decision"), "reason": d.get("reason"), "mode": d.get("mode")} for d in (reply_gate or {}).get("recent_decisions", [])[:3]],
    }
    compact_dreams = {
        "recent_runs": [{"id": r.get("id"), "status": r.get("status"), "sleep_session_id": r.get("sleep_session_id"), "findings_count": r.get("findings_count"), "created_entry_id": r.get("created_entry_id"), "proactive_intent_id": r.get("proactive_intent_id")} for r in (dreams or {}).get("recent_runs", [])[:3]],
        "recent_entries": [{"id": e.get("id"), "summary": e.get("summary"), "share_text": (e.get("share_text") or "")[:240], "truth_layer": e.get("truth_layer")} for e in (dreams or {}).get("recent_entries", [])[:3]],
        "recent_findings": [{"id": f.get("id"), "type": f.get("finding_type"), "severity": f.get("severity"), "message": (f.get("message") or "")[:180]} for f in (dreams or {}).get("recent_findings", [])[:3]],
    }
    compact_policy_explanation = explain_srd_policy(srd_policy or get_srd_policy_dummy()) if srd_policy else {}
    compact_srd_policy = {
        "profile": ((srd_policy or {}).get("effective_policy") or {}).get("profile"),
        "summary": compact_policy_explanation.get("lines", [])[:6] if compact_policy_explanation else [],
        "agent_rules": compact_policy_explanation.get("agent_rules", [])[:5] if compact_policy_explanation else [],
    }
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
        "sleep": compact_sleep,
        "reply_gate": compact_reply_gate,
        "dreams": compact_dreams,
        "sleep_reply_dream_policy": compact_srd_policy,
        "internal_final_gate_feedback": compact_final_gate_feedback,
        "pending_user_confirmations": compact_confirmations,
        "protocol": "Use LifeEngine tools to commit any new durable life facts before final answer. Use life_truth to resolve or observe Canon-bound external facts before planning. For User Life, use life_confirmation propose/confirm before writing uncertain user facts. Use life_inventory for entity resources such as wardrobe, supplies, books, and meals. Use life_goal for long-term goals, life arcs, and decomposing large events into child events. Use life_autonomy for manual autonomous planning; heartbeat may run autonomy only when the autonomy module gate permits it. Autonomy is sleep-aware: if SleepDayState shows all-nighter, high sleep debt, fatigue, or focus penalty, prefer recovery sleep, light work, postponement, or low-intensity goal steps. Use life_proactive for proactive intents/outbox: create an intent when the agent wants to share/help/report, evaluate it against delivery policy, and only mark sent after actual delivery. Use life_execution to inspect or manually run narrative execution decisions; heartbeat uses this simulator for due schedule blocks. Use life_sleep to plan core sleep, naps, start/wake sleep sessions, and inspect planned vs actual sleep. Use life_dream to inspect/run DreamRun: dreams are dream_symbolic, run DreamAudit after sleep, consolidate recent memories, and create a shareable proactive intent after waking. Use life_policy to inspect and tune Sleep/Reply/Dream policy profiles, templates, and suggestions. Use life_reply to inspect ReplyGate/delayed replies and life_call for emergency wake/interrupt. If reply_gate shows pending_delayed_replies, answer or summarize them once the agent is available. Final audit is advisory by default: if internal_final_gate_feedback is present, do not show it to the user. Use it internally to call life_commit for missing durable facts, or rephrase as intention/plan/draft. Final audit checks CommitReceipt and canonical state.",
    }
    return "\n<LIFEENGINE_CONTEXT>\n" + pretty(compact) + "\n</LIFEENGINE_CONTEXT>"



def _render_status_page(out: dict[str, Any]) -> str:
    control = out.get("control") or {}
    realtime = out.get("realtime_state") or {}
    req = out.get("required_settings") or {}
    sched = out.get("today_schedule") or {}
    gates = control.get("module_gates") or {}
    lines = ["LifeEngine 状态", "=============="]
    lines.append(f"运行：{control.get('engine_state')}；实时：{realtime.get('mode') or 'unknown'}；Canon v{control.get('active_canon_version') or '-'}")
    lines.append(f"自治：{gates.get('autonomy')}；自主管理 Review：{gates.get('managed_review_loop', 'auto')}；FinalGate：{gates.get('final_audit')}")
    if req.get("ok") is False:
        lines.append(f"必选设定：还缺 {req.get('missing_count')} 项，运行 /life config 查看。")
    else:
        lines.append("必选设定：已满足。")
    lines.append(f"今日日程：{sched.get('count', 0)} 个时间块。查看：/life schedule")
    if out.get("resources", {}).get("accounts"):
        acc = out["resources"]["accounts"][:5]
        lines.append("资源：" + "，".join([f"{a.get('resource_key')}={a.get('current_value')}" for a in acc]))
    lines.append("常用：/life review 看待办；/life schedule 看日程；/life setup 补设定；/life run 推进一次。")
    return "\n".join(lines)


def _render_setup_result(brief: dict[str, Any]) -> str:
    lines = ["LifeEngine 设定草案", "================"]
    lines.append(f"草案：{brief.get('id')}；状态：{brief.get('status')}；已记录 {brief.get('statement_count')} 条设定输入。")
    extracted = brief.get("extracted") or {}
    if extracted:
        lines.append("已识别：")
        for key in ["identity", "worldview", "truth_sources", "resources", "sleep", "dream", "autonomy", "proactive"]:
            if extracted.get(key):
                lines.append(f"- {key}: 已记录")
    qs = brief.get("unresolved_questions") or []
    if qs:
        lines.append("还建议补充：")
        for q in qs[:8]:
            lines.append(f"- {q}")
    lines.append("确认无误后用 /life commit 提交；想继续补充就直接 /life setup <自然语言设定>。")
    return "\n".join(lines)


def _render_canon_commit(committed: dict[str, Any]) -> str:
    mig = committed.get("migration") or {}
    return "\n".join([
        "LifeEngine 设定已提交",
        "====================",
        f"Canon 版本：v{committed.get('version')}；状态：{committed.get('status')}",
        f"迁移类型：{mig.get('migration_type') or mig.get('type') or 'recorded'}",
        "如果这是第一次设定，LifeEngine 会进入 active；如果是重构设定，建议先 /life review，再 /life resume。",
    ])


def format_result(obj: Any) -> str:
    if isinstance(obj, dict):
        for key in ("rendered", "human", "message"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
