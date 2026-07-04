"""LifeOps commit and dispatch helpers for LifeEngineRuntime.

本模块只做行为保持的结构搬迁：函数接收 ``LifeEngineRuntime`` 实例 ``rt``，
继续通过 ``rt.conn`` / ``rt.<method>`` 复用既有事务、savepoint、receipt 与
journal 边界。核心 mutation dispatcher 从 runtime.py 原样迁入，除 ``self``
重命名为 ``rt`` 外不改变分支顺序和提交语义。
"""

from __future__ import annotations

from typing import Any

from . import emotion, persona, venture
from .autonomy import apply_autonomy_goal_step, apply_autonomy_schedule_event
from .canon import ensure_control, get_active_canon
from .constants import DEFAULT_AGENT_ID
from .db import savepoint, transaction
from .dream import create_dream_entry, run_dream_cycle
from .events import (
    complete_event,
    create_event,
    create_schedule_block,
    set_realtime_state,
    transition_event,
    update_schedule_block_status,
)
from .execution import apply_serendipity_event
from .goals import (
    apply_event_goal_contributions,
    create_event_dependency,
    create_goal,
    create_life_arc,
    create_milestone,
    create_reflection,
    decompose_event,
    link_event_to_goal,
    recompute_parent_event_progress,
    update_goal_progress,
)
from .impromptu import record_impromptu_activity
from .jsonutil import dumps
from .meals import create_meal_record
from .memory import create_memory
from .proactive import (
    create_proactive_intent,
    evaluate_proactive_intent,
    expire_intents,
    mark_outbox_sent,
    reconsider_waiting_proactive_intents,
    suppress_intent,
)
from .receipts import create_commit_receipt
from .reply_gate import (
    call_override,
    create_delayed_reply,
    record_reply_gate_decision,
    release_delayed_replies,
)
from .resources import apply_delta, define_resource, release_reservation, reserve
from .sleep import (
    create_sleep_plan,
    end_sleep_session,
    interrupt_sleep_session,
    plan_core_sleep,
    skip_sleep_plan,
    start_sleep_session,
    wake_sleep_session,
)
from .trace import Trace, append_audit, append_journal, new_id
from .validators import validate_life_ops


def commit_ops(rt, ops: list[dict[str, Any]], owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
               source: str = "life_commit", session_id: str | None = None, turn_id: str | None = None) -> dict[str, Any]:
    try:
        with transaction(rt.conn):
            control = ensure_control(rt.conn, owner_kind, owner_id)
            trace = Trace(rt.conn, owner_kind, owner_id, "life_commit", session_id=session_id, turn_id=turn_id,
                          engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                          input_obj={"ops": ops, "source": source}).start()
            try:
                out = rt._commit_ops_locked(ops, owner_kind, owner_id, source, session_id, turn_id, trace, control=control)
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
        rt._record_failed_lifeops(owner_kind, owner_id, ops, source, session_id, turn_id, exc)
        raise

def _record_failed_lifeops(rt, owner_kind: str, owner_id: str, ops: list[dict[str, Any]], source: str,
                           session_id: str | None, turn_id: str | None, exc: Exception) -> dict[str, Any]:
    with transaction(rt.conn):
        try:
            control = ensure_control(rt.conn, owner_kind, owner_id)
        except Exception:
            control = {"engine_state": None, "active_canon_version": None}
        trace = Trace(
            rt.conn, owner_kind, owner_id, "life_commit_failed", session_id=session_id, turn_id=turn_id,
            engine_state=control.get("engine_state"), canon_version=control.get("active_canon_version"),
            input_obj={"ops": ops, "source": source},
        ).start()
        error = f"{type(exc).__name__}: {exc}"
        trace.end(status="error", output_obj={"ok": False, "error": error, "ops_count": len(ops or [])}, error=error)
        audit_id = append_audit(rt.conn, owner_kind, owner_id, "life_commit_failed", "error", error, {"ops": ops, "source": source}, trace_id=trace.id)
        try:
            fail_id = new_id("failedops")
            rt.conn.execute(
                "INSERT INTO failed_lifeops_audits(id, owner_kind, owner_id, session_id, turn_id, source, trace_id, error, ops_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (fail_id, owner_kind, owner_id, session_id, turn_id, source, trace.id, error, dumps(ops or [])),
            )
        except Exception:
            fail_id = None
        return {"ok": False, "trace_id": trace.id, "audit_id": audit_id, "failed_lifeops_audit_id": fail_id, "error": error}

def _project_completed_event_safe(rt, owner_kind: str, owner_id: str, event_id: str, *,
                                  summary: str | None, source: str,
                                  trace_id: str | None = None,
                                  rumor_authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """降级执行已完成事件的社会投影。

    输入来自 COMPLETE_EVENT 的 LifeOps 写路径；输出始终是可序列化的投影结果。
    该方法会调用 social_projector 的局部 savepoint 投影，成功时返回真实结果；
    失败时写 warning audit 并返回 projection_failed，不让辅助社交事实阻断事件
    完成、目标推进和 LifeOps receipt。调用方仍可通过 event_id 之后补投影。
    """
    try:
        from .social_projector import project_completed_event
        return project_completed_event(
            rt.conn, owner_kind, owner_id, event_id,
            summary=summary,
            source=source,
            rumor_authoring=rumor_authoring,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        append_audit(
            rt.conn, owner_kind, owner_id,
            "social_projection_failed", "warning", error,
            {"projection_kind": "event_completed", "event_id": event_id, "summary": summary, "source": source},
            trace_id,
        )
        return {"projected": False, "reason": "projection_failed", "event_id": event_id, "error": error}

def _commit_ops_locked(rt, ops: list[dict[str, Any]], owner_kind: str, owner_id: str,
                       source: str, session_id: str | None = None, turn_id: str | None = None,
                       trace: Trace | None = None, control: dict[str, Any] | None = None) -> dict[str, Any]:
    """在已打开的 SQLite 事务里原子提交一组 LifeOps。

    输入是一组已经由工具、heartbeat、autonomy 或 review 生成的 LifeOps；
    输出是 transaction、receipt 和逐 op 结果。函数会写 life_transactions、
    life_ops、领域表、journal、receipt 和 turn_commits。关键不变量是：即使
    外层 heartbeat/review 捕获异常并继续，单次 LifeOps transaction 也必须
    全成或全不成，不能留下 pending transaction、半截 op 或半截生活事实。
    """
    control = control or ensure_control(rt.conn, owner_kind, owner_id)
    normalized_ops = validate_life_ops(rt.conn, owner_kind, owner_id, control, ops, source)
    tx_id = new_id("tx")
    trace_id = trace.id if trace is not None else None
    with savepoint(rt.conn, f"lifeops_{tx_id}"):
        rt.conn.execute(
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
            rt.conn.execute(
                """INSERT INTO life_ops(id, transaction_id, owner_kind, owner_id, op_type, payload_json, status, validator_report_json)
                       VALUES(?,?,?,?,?,?,?,?)""",
                (op_id, tx_id, owner_kind, owner_id, op_type, dumps(payload), "pending", dumps(validator_report)),
            )
            if trace is not None:
                with trace.span(f"op:{op_type}", payload):
                    result = rt._apply_op(owner_kind, owner_id, op_type, payload, source, control.get("active_canon_version"), trace_id=trace_id)
            else:
                result = rt._apply_op(owner_kind, owner_id, op_type, payload, source, control.get("active_canon_version"), trace_id=trace_id)
            rt.conn.execute("UPDATE life_ops SET status='committed', result_json=? WHERE id=?", (dumps(result), op_id))
            append_journal(rt.conn, owner_kind, owner_id, op_type.lower(), {"op_id": op_id, "payload": payload, "result": result}, source, transaction_id=tx_id, op_id=op_id, canon_version=control.get("active_canon_version"))
            results.append({"op_id": op_id, "type": op_type, "payload": payload, "result": result})
        receipt = create_commit_receipt(rt.conn, owner_kind, owner_id, tx_id, trace_id, session_id, turn_id, results)
        rt.conn.execute("UPDATE life_transactions SET status='committed', committed_at=datetime('now'), receipt_id=?, receipt_json=? WHERE id=?", (receipt["receipt_id"], dumps(receipt), tx_id))
        if session_id and turn_id:
            rt.conn.execute(
                "INSERT OR IGNORE INTO turn_commits(id, owner_kind, owner_id, session_id, turn_id, transaction_id, receipt_id) VALUES(?,?,?,?,?,?,?)",
                (new_id("turncommit"), owner_kind, owner_id, session_id, turn_id, tx_id, receipt["receipt_id"]),
            )
    return {"ok": True, "transaction_id": tx_id, "receipt": receipt, "results": results, "trace_id": trace_id}

def _apply_op(rt, owner_kind: str, owner_id: str, op_type: str, payload: dict[str, Any], source: str,
              canon_version: int | None, trace_id: str | None = None) -> Any:
    """执行单个已通过校验的 LifeOp。

    输入来自 `_commit_ops_locked` 的标准化 op；输出是写入 receipt/journal 的
    领域结果。调用方已经持有 LifeOps savepoint，本函数可以写数据库和审计。
    对于事件完成后的社会投影这类派生事实，失败会降级成结果字段和 warning
    audit，不能破坏核心 LifeOp 的原子提交边界。
    """
    if op_type == "CREATE_EVENT":
        event = create_event(rt.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
        if payload.get("goal_id"):
            try:
                link = link_event_to_goal(rt.conn, owner_kind, owner_id, payload["goal_id"], event["id"], role=payload.get("goal_role", "supports"), weight=float(payload.get("goal_weight", payload.get("weight", 1.0))), source=payload.get("source") or source)
                event["goal_link"] = link
            except Exception as exc:
                event["goal_link_error"] = str(exc)
        return event
    elif op_type == "UPDATE_EVENT_STATUS":
        return transition_event(rt.conn, owner_kind, owner_id, payload["event_id"], payload["status"], payload.get("reason"), source)
    elif op_type == "CREATE_SCHEDULE_BLOCK":
        return create_schedule_block(rt.conn, owner_kind, owner_id, **payload)
    elif op_type == "UPDATE_SCHEDULE_BLOCK_STATUS":
        return update_schedule_block_status(rt.conn, owner_kind, owner_id, payload["schedule_block_id"], payload["status"], payload.get("reason"), source)
    elif op_type == "UPDATE_REALTIME_STATE":
        return set_realtime_state(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k not in {"source", "lease_expires_at_ts"}})
    elif op_type == "PLAN_CORE_SLEEP":
        return plan_core_sleep(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, canon_version=canon_version, **{k: v for k, v in payload.items() if k not in {"source", "target_bedtime_ts", "target_wake_time_ts", "alarm_time_ts"}})
    elif op_type == "START_SLEEP_SESSION":
        return start_sleep_session(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k not in {"source", "actual_start_ts"}})
    elif op_type == "END_SLEEP_SESSION":
        return end_sleep_session(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k not in {"source", "actual_end_ts"}})
    elif op_type == "COMPLETE_EVENT":
        result = complete_event(rt.conn, owner_kind, owner_id, payload["event_id"], payload.get("summary", "completed"), payload.get("resource_deltas"), source)
        result["goal_updates"] = apply_event_goal_contributions(rt.conn, owner_kind, owner_id, payload["event_id"], source)
        result["social_projection"] = rt._project_completed_event_safe(
            owner_kind, owner_id, payload["event_id"],
            summary=payload.get("summary", "completed"),
            source=f"social_projector:{source}",
            trace_id=trace_id,
            rumor_authoring=payload.get("social_projection_authoring") if isinstance(payload.get("social_projection_authoring"), dict) else None,
        )
        return result
    elif op_type == "CREATE_SLEEP_PLAN":
        return create_sleep_plan(rt.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WAKE_SLEEP_SESSION":
        return wake_sleep_session(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "INTERRUPT_SLEEP_SESSION":
        return interrupt_sleep_session(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "RECORD_REPLY_GATE_DECISION":
        return record_reply_gate_decision(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "CREATE_DELAYED_REPLY":
        return create_delayed_reply(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "RELEASE_DELAYED_REPLIES":
        return release_delayed_replies(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "CALL_OVERRIDE":
        return call_override(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "RUN_DREAM":
        return run_dream_cycle(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, trace_id=payload.get("trace_id"), **{k: v for k, v in payload.items() if k not in {"source", "trace_id"}})
    elif op_type == "CREATE_DREAM_ENTRY":
        return create_dream_entry(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "RESOURCE_DEFINE":
        p = dict(payload)
        reset_account = "initial" in p or bool(p.pop("reset_account", False))
        return define_resource(rt.conn, owner_kind, owner_id, canon_version=canon_version, reset_account=reset_account, **p)
    elif op_type == "RESOURCE_DELTA":
        return apply_delta(rt.conn, owner_kind, owner_id, **payload)
    elif op_type == "RESOURCE_RESERVE":
        return reserve(rt.conn, owner_kind, owner_id, **payload)
    elif op_type == "RESOURCE_RELEASE":
        return release_reservation(rt.conn, owner_kind, owner_id, payload["reservation_id"])
    elif op_type == "CREATE_MEMORY":
        return create_memory(rt.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
    elif op_type == "CREATE_DIARY":
        return rt._create_diary(owner_kind, owner_id, canon_version=canon_version, **payload)
    elif op_type == "CREATE_MEAL_RECORD":
        return create_meal_record(rt.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "CREATE_LIFE_ARC":
        return create_life_arc(rt.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
    elif op_type == "CREATE_GOAL":
        return create_goal(rt.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
    elif op_type == "UPDATE_GOAL_PROGRESS":
        return update_goal_progress(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "CREATE_GOAL_MILESTONE":
        return create_milestone(rt.conn, owner_kind, owner_id, **payload)
    elif op_type == "LINK_EVENT_TO_GOAL":
        return link_event_to_goal(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "CREATE_EVENT_DEPENDENCY":
        return create_event_dependency(rt.conn, owner_kind, owner_id, **payload)
    elif op_type == "DECOMPOSE_EVENT":
        return decompose_event(rt.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "CREATE_REFLECTION":
        return create_reflection(rt.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "RECOMPUTE_EVENT_PROGRESS":
        return recompute_parent_event_progress(rt.conn, owner_kind, owner_id, payload["event_id"], source)
    elif op_type == "AUTONOMY_CREATE_GOAL_STEP":
        return apply_autonomy_goal_step(rt.conn, owner_kind, owner_id, canon_version=canon_version, **payload)
    elif op_type == "AUTONOMY_SCHEDULE_EVENT":
        return apply_autonomy_schedule_event(rt.conn, owner_kind, owner_id, **payload)
    elif op_type == "CREATE_SERENDIPITY_EVENT":
        return apply_serendipity_event(rt.conn, owner_kind, owner_id, canon_version=canon_version, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "CREATE_PROACTIVE_INTENT":
        return create_proactive_intent(rt.conn, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "EVALUATE_PROACTIVE_INTENT":
        return evaluate_proactive_intent(rt.conn, owner_id, payload.get("intent_id"), control=ensure_control(rt.conn, "agent", owner_id), target_user_id=payload.get("target_user_id"), manual=bool(payload.get("manual", False)), trace_id=payload.get("trace_id"), draft_text=payload.get("draft_text"), draft_texts_by_intent_id=payload.get("draft_texts_by_intent_id"), allow_authoring=bool(payload.get("allow_authoring", False)), temporal_grounding_facts=payload.get("temporal_grounding_facts"))
    elif op_type == "RECONSIDER_WAITING_PROACTIVE_INTENTS":
        return reconsider_waiting_proactive_intents(
            rt.conn,
            owner_id,
            control=ensure_control(rt.conn, "agent", owner_id),
            trace_id=payload.get("trace_id"),
            limit=int(payload.get("limit", 10)),
            allow_authoring=bool(payload.get("allow_authoring", False)),
            draft_texts_by_intent_id=payload.get("draft_texts_by_intent_id"),
            temporal_grounding_facts=payload.get("temporal_grounding_facts"),
        )
    elif op_type == "MARK_PROACTIVE_SENT":
        return mark_outbox_sent(rt.conn, owner_id, payload["outbox_id"], result=payload.get("result") or {}, manual=bool(payload.get("manual", True)))
    elif op_type == "SUPPRESS_PROACTIVE_INTENT":
        return suppress_intent(rt.conn, owner_id, payload["intent_id"], payload.get("reason") or "manual suppress")
    elif op_type == "EXPIRE_PROACTIVE_INTENTS":
        return expire_intents(rt.conn, owner_id)
    elif op_type == "SKIP_SLEEP_PLAN":
        return skip_sleep_plan(rt.conn, owner_kind, owner_id, source=payload.get("source") or source, **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "RECORD_IMPROMPTU_ACTIVITY":
        return record_impromptu_activity(rt.conn, owner_kind, owner_id, canon_version=canon_version,
                                         source=payload.get("source") or source,
                                         **{k: v for k, v in payload.items() if k not in {"source"}})
    elif op_type == "PERSONA_DRIFT":
        return persona.apply_persona_drift(
            rt.conn, owner_kind, owner_id,
            signals=payload.get("signals"),
            canon=get_active_canon(rt.conn, owner_kind, owner_id),
            tick_id=payload.get("tick_id"),
            trace_id=payload.get("trace_id"),
            source=payload.get("source") or source,
            gain=float(payload.get("gain", 1.0)),
        )
    elif op_type == "MOOD_REACTION":
        return emotion.record_mood_reaction(
            rt.conn, owner_kind, owner_id, canon_version=canon_version,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type in ("CREATE_VENTURE", "CREATE_RECURRING_ACTIVITY"):  # old op-type accepted for back-compat
        return venture.create_venture(
            rt.conn, owner_kind, owner_id, canon_version=canon_version,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type in ("UPDATE_VENTURE", "UPDATE_RECURRING_ACTIVITY"):  # old op-type accepted for back-compat
        return venture.update_venture(
            rt.conn, owner_kind, owner_id, payload["activity_id"], canon_version=canon_version,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k not in {"source", "activity_id"}})
    elif op_type == "WORLD_UPSERT_PROFILE":
        from . import world_model as _world
        return _world.upsert_world_profile(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WORLD_UPSERT_REGION":
        from . import world_model as _world
        return _world.upsert_region(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WORLD_UPSERT_PLACE":
        from . import world_model as _world
        return _world.upsert_place(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WORLD_UPSERT_LORE":
        from . import world_model as _world
        return _world.upsert_lore_entry(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WORLD_UPSERT_FACTION_PRESENCE":
        from . import world_model as _world
        return _world.upsert_faction_presence(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WORLD_UPSERT_ROUTE":
        from . import world_model as _world
        return _world.upsert_route(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WORLD_UPSERT_CONDITION":
        from . import world_model as _world
        return _world.upsert_condition(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WORLD_UPSERT_CHRONICLE_EVENT":
        from . import world_model as _world
        return _world.upsert_chronicle_event(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "WORLD_ARCHIVE_OBJECT":
        from . import world_model as _world
        return _world.archive_object(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_DEFINE_SLOT":
        from . import social_world as _social
        return _social.upsert_slot_definition(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_CREATE_ENTITY":
        from . import social_world as _social
        return _social.create_entity(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_LINK_AFFILIATION":
        from . import social_world as _social
        return _social.link_affiliation(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_SET_EDGE":
        from . import social_world as _social
        return _social.upsert_social_edge(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_REPUTATION_EVENT":
        from . import social_world as _social
        return _social.apply_reputation_event(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_RECORD_EVALUATION":
        from . import social_world as _social
        return _social.record_evaluation(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_RECORD_RUMOR":
        from . import social_world as _social
        return _social.record_rumor(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_RUMOR_DECAY":
        from . import social_world as _social
        return _social.apply_rumor_decay(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_RECORD_RUMOR_EXPOSURE":
        from . import social_world as _social
        return _social.record_rumor_exposure(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_RECORD_REQUEST":
        from . import social_world as _social
        return _social.record_social_request(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    elif op_type == "SOCIAL_REQUEST_TRANSITION":
        from . import social_world as _social
        return _social.transition_social_request(
            rt.conn, owner_kind, owner_id,
            source=payload.get("source") or source,
            **{k: v for k, v in payload.items() if k != "source"})
    raise ValueError(f"Unknown LifeOp type: {op_type}")

