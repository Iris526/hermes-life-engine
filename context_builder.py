"""Reply-path context builder helpers for LifeEngineRuntime.

本模块只做行为保持的结构搬迁：函数接收 ``LifeEngineRuntime`` 实例 ``rt``，
继续通过 ``rt.conn`` / runtime 公开方法复用既有事务、追踪和上下文注入边界。
"""

from __future__ import annotations

from typing import Any

from . import emotion, persona
from .autonomy import list_autonomy_decisions
from .behavior_mapping import (
    BehaviorMappingError,
    ensure_default_behavior_mappings,
    list_behavior_mappings,
    resolve_behavior,
)
from .canon import (
    append_setup_statement,
    begin_setup,
    ensure_control,
    get_active_canon,
    get_draft,
    update_control,
)
from .confirmations import list_confirmations
from .constants import DEFAULT_AGENT_ID, SETUP_STATES
from .context_policy import (
    list_context_runs,
    record_context_injection,
    render_context_policy_explanation,
    render_progressive_context,
)
from .conversation import interaction_time_context, record_turn_interaction, temporal_grounding
from .db import transaction
from .dream import consume_first_reply_dream_share, dream_status
from .events import get_event, get_realtime_state, list_events
from .execution import list_execution_decisions, list_serendipity_events
from .final_gate import consume_final_gate_feedback
from .goals import list_goals, list_life_arcs
from .jsonutil import dumps, pretty
from .memory import search_memories
from .owner_scope import OwnerScope, resolve_owner_scope
from .proactive import list_outbox, list_proactive_intents, list_proactive_states
from .reply_gate import reply_gate_status
from .resources import list_resources
from .schedule_view import _tz_from_canon, list_schedule as list_human_schedule
from .settings_check import check_required_settings
from .sleep import sleep_status
from .sleep_reply_dream_policy import get_policy as get_srd_policy
from .trace import Trace
from .truth_sources import list_truth_sources


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

def _ensure_context_mounts_table(rt) -> None:
    # Safety net: the table is normally created by schema migration v48
    # (see db._create_schema_v48). This CREATE IF NOT EXISTS keeps older
    # DBs working if they somehow connect before the migration runs.
    rt.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_context_session_mounts (
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          session_id TEXT NOT NULL,
          platform TEXT,
          mounted INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY(owner_kind, owner_id, session_id)
        )
        """
    )

def set_context_mount(rt, owner_kind: str, owner_id: str, session_id: str | None,
                      *, platform: str | None = None, mounted: bool = True) -> dict[str, Any]:
    if not session_id:
        raise ValueError("session_id is required for LifeEngine context mount switches")
    _ensure_context_mounts_table(rt)
    rt.conn.execute(
        """
        INSERT INTO prompt_context_session_mounts(owner_kind, owner_id, session_id, platform, mounted)
        VALUES(?,?,?,?,?)
        ON CONFLICT(owner_kind, owner_id, session_id) DO UPDATE SET
          platform=excluded.platform,
          mounted=excluded.mounted,
          updated_at=datetime('now')
        """,
        (owner_kind, owner_id, session_id, platform, 1 if mounted else 0),
    )
    return {"ok": True, "owner_kind": owner_kind, "owner_id": owner_id, "session_id": session_id, "platform": platform, "mounted": bool(mounted)}

def context_mount_status(rt, owner_kind: str, owner_id: str, session_id: str | None,
                         *, platform: str | None = None) -> dict[str, Any]:
    default_mounted = str(platform or "").strip().lower() in {"qq", "qqbot"}
    row = None
    if session_id:
        _ensure_context_mounts_table(rt)
        row = rt.conn.execute(
            "SELECT mounted, platform, updated_at FROM prompt_context_session_mounts WHERE owner_kind=? AND owner_id=? AND session_id=?",
            (owner_kind, owner_id, session_id),
        ).fetchone()
    if row is not None:
        mounted = bool(row["mounted"] if hasattr(row, "keys") else row[0])
        source = "session_switch"
        row_platform = row["platform"] if hasattr(row, "keys") else row[1]
        updated_at = row["updated_at"] if hasattr(row, "keys") else row[2]
    else:
        mounted = default_mounted
        source = "platform_default" if default_mounted else "default_off"
        row_platform = platform
        updated_at = None
    return {"ok": True, "owner_kind": owner_kind, "owner_id": owner_id, "session_id": session_id, "platform": platform or row_platform, "mounted": mounted, "source": source, "updated_at": updated_at}

def should_mount_context_for_turn(rt, session_id: str | None, turn_id: str | None = None,
                                  sender_id: str | None = None, platform: str | None = None) -> bool:
    scope = resolve_owner_scope({}, {"session_id": session_id, "turn_id": turn_id, "sender_id": sender_id, "platform": platform})
    with transaction(rt.conn):
        return bool(rt.context_mount_status(scope.owner_kind, scope.owner_id, session_id, platform=platform).get("mounted"))

def _resolve_behavior_for_context(rt, owner_kind: str, owner_id: str, user_message: str) -> dict[str, Any] | None:
    """Lightweight behavior-mapping resolve for context injection.

    Uses user_message as behavior_text to match a private truth-source
    mapping. On match, returns narrative_label + agent_instruction (no
    private source URLs) so the LLM can narrate consistently without
    exposing hidden sources. Unmatched or empty messages return None.
    """
    if owner_kind != "agent" or not user_message or not str(user_message).strip():
        return None
    try:
        resolved = resolve_behavior(rt.conn, owner_kind, owner_id, behavior_text=user_message, include_private=False, source="context_inject", write_run=False)
    except BehaviorMappingError:
        return None
    except Exception:
        return None
    if not resolved or not resolved.get("ok"):
        return None
    return {
        "behavior_key": resolved.get("behavior_key"),
        "narrative_label": resolved.get("narrative_label"),
        "agent_instruction": resolved.get("agent_instruction"),
        "run_id": resolved.get("run_id"),
    }

def _inner_life_capsule(rt, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """把 agent 当前可说出的内心与社会世界压缩进本轮上下文。

    输入是 owner；输出包含自我叙事、观点、正在推进的 campaign、该回访的用户生活
    事项，以及紧凑的社会世界摘要（声望/评价/流言）。调用方是
    `build_context_for_turn()`；函数只读数据库，不写事实。失败逐段吞掉，避免上下文
    注入影响主对话。
    """
    cap: dict[str, Any] = {}
    try:
        from . import opinions as _opinions
        nar = _opinions.latest_self_narrative(rt.conn, owner_id)
        if nar:
            cap["self_narrative"] = nar
        stances = _opinions.opinion_phrases(rt.conn, owner_id, limit=4)
        if stances:
            cap["opinions"] = stances
    except Exception:
        pass
    try:
        from . import campaigns as _campaigns
        active = _campaigns.list_campaigns(rt.conn, owner_kind, owner_id, status="active", limit=1)
        if active:
            c = active[0]
            phases = c.get("phases") or []
            cp = int(c.get("current_phase") or 0)
            phase_title = phases[cp].get("title") if 0 <= cp < len(phases) else None
            cap["working_toward"] = {"title": c.get("title"), "phase": phase_title,
                                     "progress": round(float(c.get("progress") or 0), 2)}
    except Exception:
        pass
    try:
        from . import relationship as _relationship
        due = _relationship.notes_due_for_followup(rt.conn, owner_id, _relationship.resolve_primary_user(rt.conn, owner_id), limit=1)
        if due:
            cap["meant_to_ask_you_about"] = str(due[0].get("content") or "")[:160]
    except Exception:
        pass
    try:
        from . import social_world as _social
        reps = _social.list_reputation_accounts(rt.conn, owner_kind, owner_id, limit=4)
        rumors = _social.list_rumors(rt.conn, owner_kind, owner_id, limit=3)
        evaluations = _social.list_evaluations(rt.conn, owner_kind, owner_id, limit=3)
        compact_social: dict[str, Any] = {}
        if reps:
            compact_social["reputation"] = [
                {"subject": r.get("subject_entity_id"), "audience": r.get("audience_entity_id"),
                 "axis": r.get("axis"), "value": r.get("value")}
                for r in reps
            ]
        if evaluations:
            compact_social["evaluations"] = [
                {"subject": e.get("subject_entity_id"), "axis": e.get("axis"),
                 "score": e.get("score"), "truth_layer": e.get("truth_layer")}
                for e in evaluations
            ]
        if rumors:
            compact_social["rumors"] = [
                {"content": (r.get("content") or "")[:120], "channel": r.get("channel"),
                 "truth_layer": r.get("truth_layer"), "heat": r.get("heat")}
                for r in rumors
            ]
        if compact_social:
            cap["social_world"] = compact_social
    except Exception:
        pass
    return cap

def build_context_for_turn(rt, session_id: str | None, turn_id: str | None, user_message: str,
                           sender_id: str | None = None, platform: str | None = None,
                           model: str | None = None) -> str:
    """构建单轮 prompt 的 LifeEngine 上下文胶囊。

    输入来自 pre-LLM hook；输出是可注入 prompt 的文本。除既有 context
    run 记录外，本函数会幂等记录本轮聊天时间判定，并推进用户活动 TTL，
    避免把“用户一小时前在吃饭”继续当成正在发生。各段读取失败会降级为空
    胶囊，避免上下文注入阻断正常回复。
    """
    scope = resolve_owner_scope({}, {"session_id": session_id, "turn_id": turn_id, "sender_id": sender_id, "platform": platform})
    owner_kind, owner_id = scope.owner_kind, scope.owner_id
    with transaction(rt.conn):
        control = ensure_control(rt.conn, owner_kind, owner_id)
        trace = Trace(rt.conn, owner_kind, owner_id, "pre_llm_call", session_id=session_id, turn_id=turn_id,
                      engine_state=control["engine_state"], canon_version=control.get("active_canon_version"),
                      input_obj={"user_message": user_message, "scope": scope.__dict__, "platform": platform, "model": model}).start()
        behavior_replacement = None
        try:
            if control["engine_state"] in SETUP_STATES:
                if user_message and not user_message.strip().startswith("/"):
                    draft = append_setup_statement(rt.conn, owner_kind, owner_id, user_message, "user")
                else:
                    draft_id = control.get("draft_canon_id")
                    draft = get_draft(rt.conn, draft_id) if draft_id else begin_setup(rt.conn, owner_kind, owner_id)
                out = _render_setup_context(control, draft, scope)
                trace.end(output_obj={"mode": "setup", "draft_id": draft["id"]})
                return out
            canon = get_active_canon(rt.conn, owner_kind, owner_id)
            # Each query is isolated so a single failure degrades gracefully
            # instead of taking down the whole context injection (issue #7).
            def _safe(fn, default):
                try:
                    return fn()
                except Exception:
                    return default
            if user_message and not user_message.strip().startswith("/"):
                _safe(lambda: record_turn_interaction(
                    rt.conn, owner_kind, owner_id, session_id=session_id, turn_id=turn_id,
                    user_id=sender_id, platform=platform, text=user_message,
                    source="context_preflight",
                ), None)
            interaction_time = _safe(lambda: interaction_time_context(
                rt.conn, owner_kind, owner_id, session_id=session_id, turn_id=turn_id,
                user_id=sender_id,
            ), {})
            # Sharp temporal anchor: exact local time + day phase, precise gap
            # since the last exchange, and each daily window's status vs now.
            # Facts the model reasons over — so it notices hours passed instead
            # of continuing a stale thread (e.g. offering dinner at 3am).
            temporal = _safe(lambda: temporal_grounding(
                rt.conn, owner_kind, owner_id, canon=canon,
                session_id=session_id, turn_id=turn_id,
            ), {})
            memories = _safe(lambda: search_memories(rt.conn, owner_kind, owner_id, user_message or "", 5), [])
            events = _safe(lambda: list_events(rt.conn, owner_kind, owner_id, limit=8), [])
            resources = _safe(lambda: list_resources(rt.conn, owner_kind, owner_id), {"accounts": []})
            goals = _safe(lambda: list_goals(rt.conn, owner_kind, owner_id, limit=5), [])
            arcs = _safe(lambda: list_life_arcs(rt.conn, owner_kind, owner_id, limit=5), [])
            truth_sources = _safe(lambda: list_truth_sources(rt.conn, owner_kind, owner_id, 5), [])
            try:
                ensure_default_behavior_mappings(rt.conn, owner_kind, owner_id)
                behavior_mappings = list_behavior_mappings(rt.conn, owner_kind, owner_id, include_sources=False, limit=8) if owner_kind == "agent" else []
            except Exception:
                behavior_mappings = []
            confirmations = _safe(lambda: list_confirmations(rt.conn, owner_kind, owner_id, limit=5) if owner_kind == "user" else [], [])
            pending = _safe(lambda: list_proactive_intents(rt.conn, owner_id, status="queued", limit=3) if owner_kind == "agent" else [], [])
            proactive_outbox = _safe(lambda: list_outbox(rt.conn, owner_id, status="queued", limit=3) if owner_kind == "agent" else [], [])
            proactive_states = _safe(lambda: list_proactive_states(rt.conn, owner_id, limit=3) if owner_kind == "agent" else [], [])
            autonomy = _safe(lambda: list_autonomy_decisions(rt.conn, owner_kind, owner_id, limit=3) if owner_kind == "agent" else [], [])
            execution = _safe(lambda: list_execution_decisions(rt.conn, owner_kind, owner_id, limit=3), [])
            serendipity = _safe(lambda: list_serendipity_events(rt.conn, owner_kind, owner_id, limit=3), [])
            sleep = _safe(lambda: sleep_status(rt.conn, owner_kind, owner_id) if owner_kind == "agent" else {}, {})
            reply_gate = _safe(lambda: reply_gate_status(rt.conn, owner_kind, owner_id) if owner_kind == "agent" else {}, {})
            if owner_kind == "agent" and reply_gate:
                try:
                    latest_decision = rt.conn.execute(
                        "SELECT decision, mode, reason, created_at FROM reply_gate_decisions WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT 1",
                        (owner_kind, owner_id),
                    ).fetchone()
                    if latest_decision:
                        reply_gate["latest_decision"] = {"decision": latest_decision["decision"], "mode": latest_decision["mode"], "reason": latest_decision["reason"]}
                except Exception:
                    pass
            dreams = _safe(lambda: dream_status(rt.conn, owner_kind, owner_id) if owner_kind == "agent" else {}, {})
            pending_dream_share = None
            if owner_kind == "agent" and str(platform or "").strip().lower() not in {"feishu", "lark", "dingtalk", "wecom", "slack", "teams"}:
                pending_dream_share = _safe(lambda: consume_first_reply_dream_share(rt.conn, owner_kind, owner_id, source="context_first_reply"), None)
                if pending_dream_share:
                    dreams = _safe(lambda: dream_status(rt.conn, owner_kind, owner_id), dreams or {})
            srd_policy = _safe(lambda: get_srd_policy(rt.conn, owner_kind, owner_id) if owner_kind == "agent" else {}, {})
            final_gate_feedback = _safe(lambda: consume_final_gate_feedback(rt.conn, owner_kind, owner_id, limit=3), [])
            required = _safe(lambda: check_required_settings(rt.conn, owner_kind, owner_id, canon, persist=False) if owner_kind == "agent" else {"ok": True}, {"ok": True})
            today_schedule = _safe(lambda: list_human_schedule(rt.conn, owner_kind, owner_id, period="today", tz_name=_tz_from_canon(canon), limit=20) if owner_kind == "agent" else {"items": []}, {"items": []})
            resolved_behavior = _resolve_behavior_for_context(rt, owner_kind, owner_id, user_message)
            persona_capsule = _safe(lambda: persona.render_persona_capsule(persona.ensure_persona(rt.conn, owner_kind, owner_id, canon)) if owner_kind == "agent" else {}, {})
            mood_capsule = _safe(lambda: (lambda m: {"value": m, "band": emotion.mood_band(m), "note": emotion.mood_bias(m).get("note")})(emotion.current_mood(rt.conn, owner_kind, owner_id)) if owner_kind == "agent" else {}, {})
            inner_life = _safe(lambda: _inner_life_capsule(rt, owner_kind, owner_id) if owner_kind == "agent" else {}, {})
            realtime = _safe(lambda: get_realtime_state(rt.conn, owner_kind, owner_id), {})
            active_event = _safe(lambda: get_event(rt.conn, realtime.get("active_event_id")), {}) if realtime.get("active_event_id") else {}
            from . import world_model as _world_model
            world_model = _safe(lambda: _world_model.summary(rt.conn, owner_kind, owner_id, limit=8), {})
            world_context = _safe(lambda: _world_model.effective_context(
                rt.conn, owner_kind, owner_id,
                location=(active_event.get("location") or {}),
                limit=8,
            ), {})
            social_world = inner_life.get("social_world") if isinstance(inner_life, dict) else {}
            if social_world:
                inner_life = {k: v for k, v in inner_life.items() if k != "social_world"}
            context_data = {
                "_canon": canon,
                "owner_scope": scope.__dict__,
                "engine_state": control["engine_state"],
                "canon_version": control.get("active_canon_version"),
                "module_gates": control.get("module_gates"),
                "persona": persona_capsule,
                "mood": mood_capsule,
                "inner_life": inner_life,
                "world_model": world_model or {},
                "world_context": world_context or {},
                "social_world": social_world or {},
                "canon_brief": {"identity": (canon or {}).get("identity"), "worldview": (canon or {}).get("worldview"), "truth_sources": (canon or {}).get("truth_sources")},
                "realtime": realtime,
                "interaction_time": interaction_time,
                "time": temporal,
                "resources": [{"resource_key": a["resource_key"], "current_value": a["current_value"], "unit": a.get("unit"), "state": a.get("state")} for a in (resources.get("accounts", [])[:20])],
                "events": [{"id": e["id"], "title": e["title"], "status": e["status"], "event_category": e.get("event_category"), "event_type": e.get("event_type"), "planned_start": e.get("planned_start"), "planned_end": e.get("planned_end"), "progress": e.get("progress")} for e in events[:8]],
                "memories": [{"id": m["id"], "type": m["memory_type"], "content": m["content"][:220]} for m in memories[:5]],
                "pending_proactive": pending,
                "truth_sources": truth_sources or {},
                "behavior_mappings": [{"id": m.get("id"), "behavior_key": m.get("behavior_key"), "public_label": m.get("public_label") or m.get("narrative_label") or m.get("display_name"), "source_count": len(m.get("sources") or [])} for m in (behavior_mappings or [])[:5]],
                "resolved_behavior": resolved_behavior,
                "confirmations": confirmations or [],
                "goals": [{"id": g["id"], "title": g["title"], "status": g["status"], "progress": g["progress"], "priority": g["priority"]} for g in (goals or [])[:5]],
                "arcs": [{"id": a["id"], "title": a["title"], "status": a["status"], "progress": a.get("progress")} for a in (arcs or [])[:3]],
                "autonomy": autonomy or [],
                "proactive_outbox": proactive_outbox if owner_kind == "agent" else [],
                "proactive_states": proactive_states if owner_kind == "agent" else [],
                "execution": execution or [],
                "serendipity": serendipity or [],
                "sleep": sleep or {},
                "reply_gate": reply_gate or {},
                "dreams": dreams or {},
                "pending_dream_share": pending_dream_share or {},
                "srd_policy": srd_policy or {},
                "final_gate_feedback": final_gate_feedback or [],
                # Compact summary only: the full items/missing lists (with
                # titles/messages/suggestions) can run >1.5k chars and would
                # blow the slim budget, collapsing the whole capsule to the
                # minimal fallback. The model fetches details via life_config.
                "required_settings": {
                    "ok": (required or {}).get("ok", True),
                    "missing_count": (required or {}).get("missing_count", 0),
                    "missing_keys": [m.get("key") for m in ((required or {}).get("missing") or [])][:8],
                },
                "today_schedule": today_schedule or {},
            }
            out, context_meta = render_progressive_context(context_data, user_message, control)
            context_run = record_context_injection(rt.conn, owner_kind, owner_id, session_id=session_id, turn_id=turn_id, user_message=user_message, meta=context_meta, trace_id=trace.id)
            trace.end(output_obj={"mode": control["engine_state"], "context_run_id": context_run.get("id"), "context_chars": context_meta.get("output_chars"), "domains": context_meta.get("domains")})
            return out
        except Exception as exc:
            trace.end(status="error", error=f"{type(exc).__name__}: {exc}")
            raise

def context(rt, action: str = "policy", owner_kind: str = "agent", owner_id: str = DEFAULT_AGENT_ID,
            session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
    """Prompt/context slimming and progressive-disclosure inspection.

    This is read/config only. Runtime correctness is still guaranteed by
    LifeOps, validators, receipts, and trace, not prompt text.
    """
    action_l = str(action or "policy").strip().lower()
    with transaction(rt.conn):
        control = ensure_control(rt.conn, owner_kind, owner_id)
        if action_l in {"policy", "summary", "explain"}:
            return {"ok": True, "policy": control.get("module_gates") or {}, "rendered": render_context_policy_explanation(control)}
        if action_l in {"runs", "history", "list"}:
            return {"ok": True, "runs": list_context_runs(rt.conn, owner_kind, owner_id, int(payload.get("limit", 20)))}
        if action_l in {"set", "mode"}:
            mode = str(payload.get("mode") or payload.get("value") or "slim")
            if mode not in {"micro", "slim", "balanced", "debug"}:
                raise ValueError("context mode must be micro/slim/balanced/debug")
            gates = dict(control.get("module_gates") or {})
            gates["context_mode"] = mode
            if payload.get("budget_chars"):
                gates["context_budget_chars"] = str(int(payload["budget_chars"]))
            update_control(rt.conn, owner_kind, owner_id, module_gates_json=dumps(gates))
            c = ensure_control(rt.conn, owner_kind, owner_id)
            return {"ok": True, "control": c, "rendered": render_context_policy_explanation(c)}
        if action_l in {"mount", "on", "enable"}:
            out = rt.set_context_mount(owner_kind, owner_id, session_id, platform=payload.get("platform"), mounted=True)
            out["rendered"] = f"LifeEngine context mounted for session {session_id}."
            return out
        if action_l in {"unmount", "off", "disable"}:
            out = rt.set_context_mount(owner_kind, owner_id, session_id, platform=payload.get("platform"), mounted=False)
            out["rendered"] = f"LifeEngine context unmounted for session {session_id}."
            return out
        if action_l in {"mount_status", "status"}:
            out = rt.context_mount_status(owner_kind, owner_id, session_id, platform=payload.get("platform"))
            out["rendered"] = f"LifeEngine context mount: {'on' if out.get('mounted') else 'off'} ({out.get('source')})."
            return out
        raise ValueError(f"Unknown context action: {action}")
