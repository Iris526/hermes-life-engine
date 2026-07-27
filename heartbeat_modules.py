"""Heartbeat per-tick module runners.

本模块承载 LifeEngine heartbeat registry 中逐 tick 执行的 runner。当前切片只
做行为保持的结构搬迁：runner 仍接收 ``LifeEngineRuntime`` 实例 ``rt``，并继续
通过 ``rt.conn`` / ``rt._commit_ops_locked`` 复用既有事务与 LifeOps 边界。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import persona, venture
from .autonomy import plan_autonomy, update_autonomy_decision_result
from .canon import get_active_canon
from .constants import TICK_BASELINE_MIN
from .db import savepoint
from .events import get_event, get_realtime_state
from .heartbeat_authoring import owner_authoring_pack_key
from .jsonutil import dumps, loads
from .meals import plan_meal_autonomy, plan_meal_settlement
from .schedule_view import _tz_from_canon
from .time_utils import to_epoch as _to_epoch
from .trace import Trace, append_audit, append_journal, new_id


# ----- 私有 helper：只服务 heartbeat runner -----

def _project_venture_sale_settlement_safe(rt, owner_kind: str, owner_id: str, occurrence_id: str, *,
                                          source: str, trace_id: str | None = None,
                                          rumor_authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """降级执行经营结算 occurrence 的社会投影。

    输入来自 heartbeat 进销存结算或补偿扫描；输出始终是可序列化的投影结果。
    social_projector 自身负责 savepoint 原子性，本方法负责失败审计和错误降级。
    这样库存/收入结算不会因流言、评价等辅助事实失败而回滚，同时失败的
    occurrence 不会留下半截 projection run，后续 heartbeat 可再次补投影。
    """
    try:
        from .social_projector import project_venture_sale_settlement
        return project_venture_sale_settlement(
            rt.conn, owner_kind, owner_id, occurrence_id,
            source=source,
            rumor_authoring=rumor_authoring,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        append_audit(
            rt.conn, owner_kind, owner_id,
            "social_projection_failed", "warning", error,
            {"projection_kind": "venture_sale_settled", "occurrence_id": occurrence_id, "source": source},
            trace_id,
        )
        return {"projected": False, "reason": "projection_failed", "occurrence_id": occurrence_id, "error": error}

def _activity_window(date_key: str, start_time: str | None, end_time: str | None, tz_name: str) -> tuple[str | None, str | None]:
    """Build today's start/end ISO for an activity's HH:MM window in its tz."""
    if not start_time or not end_time:
        return None, None
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        sh, sm = (int(x) for x in str(start_time).split(":")[:2])
        eh, em = (int(x) for x in str(end_time).split(":")[:2])
        y, mo, d = (int(x) for x in date_key.split("-"))
        tz = ZoneInfo(tz_name or "UTC")
        start = _dt(y, mo, d, sh, sm, tzinfo=tz)
        end = _dt(y, mo, d, eh, em, tzinfo=tz)
        return start.isoformat(), end.isoformat()
    except Exception:
        return None, None

def _window_end_ts(date_key: str, end_time: str | None, tz_name: str) -> int | None:
    """Epoch ts of a venture's window end on date_key (for passive settlement).
    None when there's no window — then passive settlement fires promptly."""
    if not end_time:
        return None
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        eh, em = (int(x) for x in str(end_time).split(":")[:2])
        y, mo, d = (int(x) for x in date_key.split("-"))
        return int(_dt(y, mo, d, eh, em, tzinfo=ZoneInfo(tz_name or "UTC")).timestamp())
    except Exception:
        return None

def _schedule_campaign_event(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                             tick_id: str, trace: Trace, now: str, tz_name: str,
                             ev_id: str, duration_minutes: int) -> None:
    """Place a campaign event into the next free slot (一人不能分身)."""
    from .impromptu import _next_free_slot
    from .time_utils import to_epoch as _to_epoch
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    now_ts = int(_to_epoch(now))
    dur = max(60, int(duration_minutes) * 60)
    fs, fe = _next_free_slot(rt.conn, owner_kind, owner_id, after_ts=now_ts, duration_s=dur, exclude_ids=set())
    tzinfo = ZoneInfo(tz_name)
    start_iso = _dt.fromtimestamp(fs, tz=tzinfo).isoformat()
    end_iso = _dt.fromtimestamp(fe, tz=tzinfo).isoformat()
    rt._commit_ops_locked([{"type": "CREATE_SCHEDULE_BLOCK", "payload": {"event_id": ev_id, "start": start_iso, "end": end_iso, "block_type": "campaign", "timezone_name": tz_name, "interruptibility": {"level": "soft_interruptible", "max_delay_minutes": 30}}}], owner_kind, owner_id, "campaign", session_id=None, turn_id=tick_id, trace=trace, control=control)

def _account_value(rt, owner_kind: str, owner_id: str, key: str) -> float:
    row = rt.conn.execute(
        "SELECT current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
        (owner_kind, owner_id, key),
    ).fetchone()
    return float(row["current_value"]) if row and row["current_value"] is not None else 0.0

def _venture_replenish_event(rt, *, owner_kind: str, owner_id: str, control: dict[str, Any], tick_id: str,
                             trace: Trace, now: str, act: dict[str, Any], target_key: str, order_qty: float,
                             costs: dict[str, Any], title: str, desc: str, dur: int, unit_cost: float, tag: str) -> bool:
    """Create one replenishment event (买工具/买原料/进货/制作) for target_key and
    record a pending order. Deduped per (venture, target resource) so the
    goods, each material, and each tool replenish independently. Returns True
    if an order was created."""
    from .time_utils import parse_datetime
    from datetime import timedelta as _td
    if order_qty <= 0:
        return False
    if rt.conn.execute(
        "SELECT 1 FROM venture_restock_orders WHERE owner_kind=? AND owner_id=? AND activity_id=? AND goods_name=? AND status='pending'",
        (owner_kind, owner_id, act["id"], target_key),
    ).fetchone():
        return False
    from .time_utils import to_epoch as _to_epoch
    from .impromptu import _next_free_slot
    from datetime import datetime as _dt
    base = parse_datetime(now)
    atz = act.get("timezone") or "UTC"
    try:
        from zoneinfo import ZoneInfo
        tzinfo = ZoneInfo(atz)
    except Exception:
        tzinfo = None
    # place each replenishment event in the next free slot from now+30min so
    # multiple orders (笔/原料/制作…) serialize instead of overlapping (the
    # schedule overlap guard would otherwise reject all but the first block).
    after_ts = int(_to_epoch((base + _td(minutes=30)).isoformat()))
    fs, fe = _next_free_slot(rt.conn, owner_kind, owner_id, after_ts=after_ts, duration_s=max(60, int(dur) * 60), exclude_ids=set())
    if tzinfo is not None:
        r_start = _dt.fromtimestamp(fs, tz=tzinfo).isoformat()
        r_end = _dt.fromtimestamp(fe, tz=tzinfo).isoformat()
    else:
        r_start = (base + _td(minutes=30)).isoformat()
        r_end = (base + _td(minutes=30 + dur)).isoformat()
    ev_payload = {
        "title": title, "description": desc,
        "event_type": "work", "event_category": "work",
        "status": "planned", "importance": 60, "priority": 60,
        "resource_costs": costs,
        "source": "venture_restock",
        "tags": ["营生", tag, act["id"]],
        "attributes": {"recurring_activity_id": act["id"], "venture_restock": True, "target": target_key},
    }
    if act.get("location"):
        ev_payload["location"] = {"name": act.get("location"), "kind": "flexible"}
    # Atomic: event + schedule + restock order must commit together. A prior bug
    # swallowed schedule failures then still inserted a pending order, so stock
    # never replenished (event had no wake job) while the order blocked retries.
    from .db import savepoint
    with savepoint(rt.conn, f"venture_restock_{act['id']}_{target_key}"):
        with trace.span("venture_replenish", {"activity_id": act["id"], "target": target_key, "qty": order_qty}):
            c1 = rt._commit_ops_locked([{"type": "CREATE_EVENT", "payload": ev_payload}], owner_kind, owner_id, "venture_restock", session_id=None, turn_id=tick_id, trace=trace, control=control)
        rev_id = (((c1.get("results") or [{}])[0].get("result") or {}).get("id"))
        if not rev_id:
            raise RuntimeError("venture restock event create returned no id")
        rt._commit_ops_locked([{"type": "CREATE_SCHEDULE_BLOCK", "payload": {"event_id": rev_id, "start": r_start, "end": r_end, "block_type": "venture_restock", "timezone_name": atz, "interruptibility": {"level": "soft_interruptible", "max_delay_minutes": 60}}}], owner_kind, owner_id, "venture_restock", session_id=None, turn_id=tick_id, trace=trace, control=control)
        rt.conn.execute(
            "INSERT INTO venture_restock_orders(id, owner_kind, owner_id, activity_id, event_id, goods_name, quantity, unit_cost, status) VALUES(?,?,?,?,?,?,?,?, 'pending')",
            (new_id("restock"), owner_kind, owner_id, act["id"], rev_id, target_key, order_qty, unit_cost),
        )
    return True

# ----- registry runner：按 runtime._HEARTBEAT_MODULES 顺序调用 -----

def run_autonomy(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                           tick_id: str, trace: Trace, now: str, manual: bool,
                           authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """运行 heartbeat 内的自主规划并提交候选 LifeOps。

    输入来自 `tick()` 的控制状态、逻辑时间和事务外 authoring 包；输出是 planner
    decision 与可选 commit。调用方式只由 heartbeat 触发。副作用是写 autonomy
    decision，并在有 proposed_ops 时提交 LifeOps。`authoring` 中的
    `autonomy_goal_step` 是唯一允许的生成式文案来源，本函数传
    `allow_authoring=False`，确保事务内不再访问宿主模型。
    """
    gates = control.get("module_gates") or {}
    mode = str(gates.get("autonomy", "manual") or "manual")
    # A manual /life tick should not accidentally force manual autonomy;
    # explicit life_autonomy action=run does that.  Heartbeat only runs
    # autonomy automatically when mode is planned_only/low_spontaneity/full.
    heartbeat_manual = bool(manual)
    planner_manual = False
    try:
        with trace.span("autonomy_plan", {"mode": mode, "heartbeat_manual": heartbeat_manual}):
            decision = plan_autonomy(
                rt.conn, owner_kind, owner_id, control, tick_id=tick_id,
                trace_id=trace.id, manual=planner_manual, now=now,
                authored_goal_step=(authoring or {}).get("autonomy_goal_step"),
                allow_authoring=False,
            )
        ops = decision.get("proposed_ops") or []
        if not ops:
            return {"decision": decision, "commit": None}
        with trace.span("autonomy_commit", {"decision_id": decision["id"], "op_count": len(ops)}):
            commit = rt._commit_ops_locked(ops, owner_kind, owner_id, "autonomy", session_id=None, turn_id=tick_id, trace=trace, control=control)
        updated = update_autonomy_decision_result(
            rt.conn, decision["id"], status="committed",
            result_transaction_id=commit.get("transaction_id"),
            result_receipt_id=(commit.get("receipt") or {}).get("receipt_id"),
        )
        return {"decision": updated, "commit": commit}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "autonomy_failed", "warning", str(exc), {"mode": mode}, trace.id)
        return {"decision": None, "commit": None, "error": f"{type(exc).__name__}: {exc}"}

def run_persona_drift(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                tick_id: str, trace: Trace, now: str, minutes_elapsed: float) -> dict[str, Any]:
    """Nudge the living persona from recent experience (v0.14.0).

    Gated by ``personality_drift``. Drift is committed as a PERSONA_DRIFT
    LifeOp so it lands in the transaction / receipt / journal / trace chain.
    """
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    mode = str(gates.get("personality_drift", "auto") or "auto").lower()
    if mode in {"off", "disabled", "manual", "false"}:
        return {"status": "skipped", "reason": f"gate={mode}"}
    try:
        with trace.span("persona_drift", {"tick_id": tick_id}):
            signals = persona.compute_drift_signals(
                rt.conn, owner_kind, owner_id,
                window_minutes=max(float(minutes_elapsed), float(TICK_BASELINE_MIN)), now=now,
            )
            commit = rt._commit_ops_locked(
                [{"type": "PERSONA_DRIFT", "payload": {"signals": signals, "tick_id": tick_id, "trace_id": trace.id, "source": "heartbeat"}}],
                owner_kind, owner_id, "persona_drift", session_id=None, turn_id=tick_id, trace=trace, control=control,
            )
        return {"status": "ok", "signals": signals, "commit": commit}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "persona_drift_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def run_reflection(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                             tick_id: str, trace: Trace, now: str,
                             authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """运行 heartbeat 的每日反思落库。

    输入来自 `tick()` 的控制状态、逻辑时间和事务外 authoring 包；输出是写入的
    opinions/self_narrative 或 skipped/degraded/error。副作用是写观点、记忆和
    journal。生成式反思只能来自 `authoring["reflection"]`，本函数在事务内禁止
    补调模型；没有预生成结果时返回 degraded，等待下一轮宿主可用时再反思。
    """
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    mode = str(gates.get("reflection", "auto") or "auto").strip().lower()
    if mode in {"off", "disabled", "manual", "false"}:
        return {"status": "skipped", "reason": f"gate={mode}"}
    try:
        from . import opinions
        with trace.span("reflection", {"tick_id": tick_id}):
            return opinions.run_reflection(
                rt.conn, owner_id, owner_kind=owner_kind, now=now,
                trace_id=trace.id,
                authored_reflection=(authoring or {}).get("reflection"),
                allow_authoring=False,
            )
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "reflection_failed", "warning", str(exc), {}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def settle_meals(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                           tick_id: str, trace: Trace, now: str) -> dict[str, Any]:
    """Three-meals-a-day accountability: any meal whose window has passed
    without a record is settled as 'skipped' (with a reason + a small vitals
    penalty), committed as CREATE_MEAL_RECORD ops. Gated by `meals`.
    """
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    mode = str(gates.get("meals", "auto") or "auto").lower()
    if mode in {"off", "disabled", "false"}:
        return {"status": "skipped", "reason": f"gate={mode}"}
    try:
        canon = get_active_canon(rt.conn, owner_kind, owner_id)
        tz_name = _tz_from_canon(canon) or "UTC"
        # agent-local now + offset for meal-time math
        local_iso, offset = now, "+00:00"
        try:
            from zoneinfo import ZoneInfo
            from .time_utils import parse_datetime
            dt = parse_datetime(now)
            if dt is not None:
                loc = dt.astimezone(ZoneInfo(tz_name))
                local_iso = loc.isoformat()
                offset = loc.strftime("%z")
                offset = offset[:3] + ":" + offset[3:] if offset else "+00:00"
        except Exception:
            pass
        # 1) the agent may autonomously derive extra meals (下午茶/夜宵/brunch)
        derived = []
        if str(gates.get("autonomy", "full") or "full").lower() not in {"off", "disabled", "manual"}:
            try:
                persona_traits = persona.get_persona(rt.conn, owner_kind, owner_id)
                d_ops = plan_meal_autonomy(rt.conn, owner_kind, owner_id, now=local_iso, canon=canon,
                                           persona=persona_traits, tz_offset=offset)
                if d_ops:
                    with trace.span("meal_autonomy", {"count": len(d_ops)}):
                        rt._commit_ops_locked(d_ops, owner_kind, owner_id, "autonomy_meal",
                                                session_id=None, turn_id=tick_id, trace=trace, control=control)
                    derived = [o["payload"]["meal_type"] for o in d_ops if o["type"] == "CREATE_MEAL_RECORD"]
            except Exception as exc:
                append_audit(rt.conn, owner_kind, owner_id, "meal_autonomy_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        # 2) settle any base meal whose window passed without being eaten/covered
        ops = plan_meal_settlement(rt.conn, owner_kind, owner_id, now=local_iso, canon=canon, tz_offset=offset)
        settled = []
        if ops:
            with trace.span("meal_settlement", {"count": len(ops)}):
                rt._commit_ops_locked(ops, owner_kind, owner_id, "heartbeat_meals",
                                        session_id=None, turn_id=tick_id, trace=trace, control=control)
            settled = [o["payload"]["meal_type"] for o in ops]
        return {"status": "ok", "derived": derived, "settled_count": len(settled), "skipped": settled, "meals": settled}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "meal_settlement_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def materialize_recurring(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                    tick_id: str, trace: Trace, now: str) -> dict[str, Any]:
    """Materialize each active recurring activity (营生) due today into a
    concrete scheduled event, once per day (idempotent). Income/cost settles
    through normal event completion. Gated by `venture`."""
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    mode = str(gates.get("venture", gates.get("recurring_activities", "auto")) or "auto").lower()
    if mode in {"off", "disabled", "false"}:
        return {"status": "skipped", "reason": f"gate={mode}"}
    try:
        canon = get_active_canon(rt.conn, owner_kind, owner_id)
        tz_name = _tz_from_canon(canon) or "UTC"
        from .time_utils import parse_datetime
        dt = parse_datetime(now)
        local = dt
        try:
            from zoneinfo import ZoneInfo
            if dt is not None:
                local = dt.astimezone(ZoneInfo(tz_name))
        except Exception:
            local = dt
        if local is None:
            return {"status": "skipped", "reason": "unparseable now"}
        date_key = local.date().isoformat()
        weekday = local.weekday()
        due = venture.due_activities(rt.conn, owner_kind, owner_id, date_key, weekday)
        materialized = []
        for act in due:
            # opportunity-triggered ventures don't run on a fixed cadence —
            # they arrive stochastically (see _roll_opportunities_for_tick).
            if (act.get("trigger_kind") or "scheduled") == "opportunity":
                continue
            atz = act.get("timezone") or tz_name
            start_iso, end_iso = _activity_window(date_key, act.get("start_time"), act.get("end_time"), atz)
            op_model = act.get("operation_model") or "active"
            passive = op_model in {"self_service", "staffed"}
            # active occupies the agent's time → its block must not overlap an
            # existing one (一人不能分身), so shift to the next free slot if the
            # preferred window is taken. passive ventures (self_service /
            # staffed) run without her, so they get NO occupying block.
            if start_iso and end_iso and not passive:
                try:
                    from .impromptu import _next_free_slot
                    from .time_utils import to_epoch as _to_epoch
                    from datetime import datetime as _dt
                    from zoneinfo import ZoneInfo
                    s_ts, e_ts = _to_epoch(start_iso), _to_epoch(end_iso)
                    dur = max(60, int(e_ts) - int(s_ts))
                    fs, fe = _next_free_slot(rt.conn, owner_kind, owner_id, after_ts=int(s_ts), duration_s=dur, exclude_ids=set())
                    if fs != int(s_ts):
                        tzinfo = ZoneInfo(atz)
                        start_iso = _dt.fromtimestamp(fs, tz=tzinfo).isoformat()
                        end_iso = _dt.fromtimestamp(fe, tz=tzinfo).isoformat()
                except Exception:
                    pass
            costs = dict(act.get("resource_costs") or {})
            # Supply-chain income is settled from sales, so drop money keys to
            # avoid double-counting (non-supply keeps money as the pay).
            if act.get("supply_chain"):
                costs = {k: v for k, v in costs.items() if not str(k).startswith("money")}
            # Passive ventures run without the agent present, so they cost her
            # no effort (vitals); only money/stock effects remain.
            if passive:
                costs = {k: v for k, v in costs.items() if str(k) not in {"energy", "focus", "mood", "fatigue", "stamina"}}
            ev_payload = {
                "title": act["title"],
                "description": act.get("description") or "由营生(周期活动)自动铺出的当日事项。",
                "event_type": act.get("activity_type") or "work",
                "event_category": act.get("event_category") or "work",
                "activity_domain": act.get("activity_domain"),
                "status": "planned",
                "importance": int(act.get("importance") or 55),
                "priority": int(act.get("priority") or 55),
                "resource_costs": costs,
                "source": "recurring_activity",
                "tags": (act.get("tags") or []) + ["营生", "recurring", act["id"]],
                "attributes": {"recurring_activity_id": act["id"], "generated_by": "recurring_activity", "operation_model": op_model},
            }
            if act.get("location"):
                ev_payload["location"] = {"name": act.get("location"), "kind": act.get("location_kind") or "fixed"}
            # Atomicity: the occurrence row is the idempotency guard
            # (due_activities skips activities that already have one for the
            # day). If the event were created but record_occurrence then
            # failed, the next tick would re-materialize a duplicate. Wrap the
            # event + block + occurrence in one savepoint so they all commit or
            # all roll back together — an un-guarded orphan event can't survive.
            with savepoint(rt.conn, f"recurring_{act['id']}_{date_key}"):
                with trace.span("recurring_materialize", {"activity_id": act["id"]}):
                    c1 = rt._commit_ops_locked([{"type": "CREATE_EVENT", "payload": ev_payload}], owner_kind, owner_id, "recurring_activity", session_id=None, turn_id=tick_id, trace=trace, control=control)
                ev_id = (((c1.get("results") or [{}])[0].get("result") or {}).get("id"))
                bid = None
                if ev_id and start_iso and end_iso and not passive:
                    c2 = rt._commit_ops_locked([{"type": "CREATE_SCHEDULE_BLOCK", "payload": {"event_id": ev_id, "start": start_iso, "end": end_iso, "block_type": "recurring_activity", "timezone_name": atz, "interruptibility": {"level": "soft_interruptible", "max_delay_minutes": 30}}}], owner_kind, owner_id, "recurring_activity", session_id=None, turn_id=tick_id, trace=trace, control=control)
                    bid = (((c2.get("results") or [{}])[0].get("result") or {}).get("id"))
                venture.record_occurrence(rt.conn, owner_kind, owner_id, act["id"], date_key, ev_id, bid)
            materialized.append({"activity_id": act["id"], "title": act["title"], "event_id": ev_id, "schedule_block_id": bid})
        return {"status": "ok", "date_key": date_key, "count": len(materialized), "materialized": materialized}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "recurring_materialize_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def run_campaigns(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                            tick_id: str, trace: Trace, now: str,
                            authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """v0.18.0 P3: materialize each active campaign (资料片) day by day — spawn
    the current phase's themed events (one-time beats on first entry +
    per-day spawns), auto-advance phases by elapsed time, escalate via the
    per-phase data, and resolve at the arc's end. Idempotent per
    (campaign, phase, day); conflict-arbitrated like venture. Gated by
    `campaigns`. 若事务外 authoring 包已预生成 auto-seed blueprint，则本函数只在
    事务内二次确认“无 active campaign 且 idle 窗口仍成立”后创建 campaign，绝不在
    写锁内调用 LifeAuthor。"""
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    mode = str(gates.get("campaigns", "auto") or "auto").lower()
    if mode in {"off", "disabled", "false"}:
        return {"status": "skipped", "reason": f"gate={mode}"}
    try:
        from . import campaigns as _campaigns
        canon = get_active_canon(rt.conn, owner_kind, owner_id)
        tz_name = _tz_from_canon(canon) or "UTC"
        out = []
        owner_seed = ((authoring or {}).get("campaign_autoseeds_by_owner") or {}).get(
            owner_authoring_pack_key(owner_kind, owner_id)
        )
        if isinstance(owner_seed, dict):
            blueprint = owner_seed.get("blueprint")
            if isinstance(blueprint, dict) and (blueprint.get("phases") or []):
                try:
                    idle_days = int(owner_seed.get("idle_days") or _campaigns.autoseed_idle_days(canon))
                except Exception:
                    idle_days = _campaigns.autoseed_idle_days(canon)
                eligibility = _campaigns.autoseed_eligibility(
                    rt.conn, owner_kind, owner_id, control, now=now, idle_days=idle_days,
                )
                if eligibility.get("eligible"):
                    try:
                        with savepoint(rt.conn, f"campaign_autoseed_{tick_id}"):
                            camp = _campaigns.create_campaign(
                                rt.conn, owner_kind, owner_id,
                                title=str(blueprint.get("title") or "近来想做的一件大事"),
                                phases=blueprint["phases"],
                                description=blueprint.get("description"),
                                theme={"seed_brief": owner_seed.get("brief", "")},
                                importance=int(blueprint.get("importance", 60)),
                                timezone=tz_name,
                                start_date=None,
                                now=now,
                                source="campaign_seed",
                            )
                            append_journal(
                                rt.conn, owner_kind, owner_id, "campaign_auto_seeded",
                                {
                                    "campaign_id": camp.get("id"),
                                    "title": camp.get("title"),
                                    "idle_days": max(1, int(idle_days)),
                                    "last_campaign_at": eligibility.get("last_campaign_at"),
                                },
                                "campaign",
                            )
                        out.append({"campaign_id": camp.get("id"), "autoseeded": True})
                    except Exception as seed_exc:
                        append_audit(
                            rt.conn, owner_kind, owner_id, "campaign_autoseed_failed",
                            "warning", str(seed_exc), {"tick_id": tick_id}, trace.id,
                        )
        for camp in _campaigns.list_campaigns(rt.conn, owner_kind, owner_id, status="active"):
            ctz = camp.get("timezone") or tz_name
            date_key = _campaigns._local_date_key(now, ctz)
            plan = _campaigns.plan_today(rt.conn, camp, date_key)
            if plan["resolve"]:
                _campaigns.resolve_campaign(rt.conn, camp["id"], now=now)
                append_journal(rt.conn, owner_kind, owner_id, "campaign_resolved", {"campaign_id": camp["id"], "title": camp.get("title")}, "campaign")
                out.append({"campaign_id": camp["id"], "resolved": True})
                continue
            if plan["already_done"]:
                continue
            # Atomicity: record_phase_occurrence is this phase-day's idempotency
            # guard (plan_today reports already_done from it). Wrap the spawns +
            # occurrence + progress in one savepoint so a failure after spawning
            # can't leave events on the schedule without the guard — which would
            # re-spawn duplicates on the next tick.
            spawned_ids = []
            with savepoint(rt.conn, f"campaign_{camp['id']}_{plan['phase_idx']}_{date_key}"):
                for ev in plan["spawn"]:
                    duration_minutes = int(ev.get("duration_minutes") or 60)
                    ev_payload = {k: v for k, v in ev.items() if k != "duration_minutes"}
                    ev_payload.setdefault("status", "planned")
                    ev_payload["source"] = "campaign"
                    with trace.span("campaign_materialize", {"campaign_id": camp["id"], "phase": plan["phase_idx"]}):
                        c1 = rt._commit_ops_locked([{"type": "CREATE_EVENT", "payload": ev_payload}], owner_kind, owner_id, "campaign", session_id=None, turn_id=tick_id, trace=trace, control=control)
                    ev_id = (((c1.get("results") or [{}])[0].get("result") or {}).get("id"))
                    if not ev_id:
                        raise RuntimeError("campaign event create returned no id")
                    spawned_ids.append(ev_id)
                    # Schedule failures must roll the whole phase-day savepoint so
                    # occurrence is not recorded without runnable blocks.
                    _schedule_campaign_event(rt, owner_kind, owner_id, control, tick_id, trace, now, ctz, ev_id, duration_minutes)
                _campaigns.record_phase_occurrence(rt.conn, camp["id"], owner_kind, owner_id, plan["phase_idx"], date_key, spawned_ids)
                _campaigns.update_phase_progress(rt.conn, camp["id"], plan["phase_idx"], plan["progress"])
            out.append({"campaign_id": camp["id"], "phase": plan["phase_idx"], "spawned": len(spawned_ids), "progress": plan["progress"]})
        return {"status": "ok", "campaigns": out}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "campaign_materialize_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def run_world_evolution(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                  tick_id: str, trace: Trace, now: str) -> dict[str, Any]:
    """推进世界/社会状态的确定性时间演化。

    输入来自 heartbeat registry；输出进入 heartbeat_runs.output_json。函数本身只
    读取候选并逐条提交 LifeOps：条件过期走 WORLD_UPSERT_CONDITION，流言衰退走
    SOCIAL_RUMOR_DECAY，声望静置回归走 SOCIAL_REPUTATION_EVENT。单条失败会写
    warning audit 并继续后续条目，保持 heartbeat 的 best-effort 隔离语义。
    """
    if owner_kind != "agent":
        return {"ok": True, "status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    mode = str(gates.get("world_evolution", "auto") or "auto").lower()
    if mode in {"off", "disabled", "manual", "false"}:
        return {"ok": True, "status": "skipped", "reason": f"gate={mode}"}
    try:
        from .world_evolution import plan_world_evolution

        plan = plan_world_evolution(rt.conn, owner_kind, owner_id, now=now)
        if plan.get("status") == "skipped":
            return {"ok": True, **plan}
        items = plan.get("items") or []
        applied: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            op = item.get("op")
            if not isinstance(op, dict):
                continue
            category = str(item.get("category") or op.get("type") or "world_evolution")
            target_id = item.get("target_id")
            try:
                with trace.span("world_evolution", {"category": category, "target_id": target_id, "op_type": op.get("type")}):
                    commit = rt._commit_ops_locked(
                        [op], owner_kind, owner_id, "heartbeat_world_evolution",
                        session_id=None, turn_id=tick_id, trace=trace, control=control,
                    )
                applied.append({
                    "category": category,
                    "target_id": target_id,
                    "op_type": op.get("type"),
                    "transaction_id": commit.get("transaction_id"),
                    "receipt_id": (commit.get("receipt") or {}).get("receipt_id"),
                })
            except Exception as item_exc:
                failure = {
                    "category": category,
                    "target_id": target_id,
                    "op_type": op.get("type"),
                    "error": f"{type(item_exc).__name__}: {item_exc}",
                }
                failures.append(failure)
                append_audit(
                    rt.conn, owner_kind, owner_id,
                    "world_evolution_item_failed", "warning", failure["error"],
                    {"tick_id": tick_id, **failure}, trace.id,
                )
        status = "partial" if failures else "ok"
        return {
            "ok": not failures,
            "status": status,
            "planned_count": len(items),
            "applied_count": len(applied),
            "counts": plan.get("counts") or {},
            "formulas": plan.get("formulas") or {},
            "applied": applied,
            "failures": failures,
        }
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "world_evolution_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"ok": False, "status": "error", "error": f"{type(exc).__name__}: {exc}"}

def run_inter_agent_sharing(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                      tick_id: str, trace: Trace, now: str) -> dict[str, Any] | None:
    """运行轴五-B 的跨 Agent 公开讲述分享。

    输入来自 heartbeat registry；输出在 gate 开启时进入 heartbeat output，gate
    关闭时返回 None 以保持默认单 agent tick 输出不新增字段。副作用全部委托给
    `inter_agent.run_inter_agent_sharing_for_tick`：只读公开 shareable surface，
    先写 inter_agent outbox，再由轴五-A delivery 通过 Social LifeOps 写入对方
    世界的 `rumor_unverified`。失败会写 warning audit 并让 heartbeat 标记 partial。
    """
    try:
        from .inter_agent import inter_agent_gate_enabled, run_inter_agent_sharing_for_tick
        if not inter_agent_gate_enabled(control):
            return None
        with trace.span("inter_agent_sharing", {"tick_id": tick_id, "owner_id": owner_id}):
            return run_inter_agent_sharing_for_tick(
                rt.conn,
                (owner_kind, owner_id),
                control=control,
            )
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "inter_agent_sharing_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"ok": False, "status": "error", "error": f"{type(exc).__name__}: {exc}"}

def ensure_daily_rhythm(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                  tick_id: str, trace: Trace, now: str) -> dict[str, Any]:
    """在 heartbeat 中补齐当天尚未结束的具体生活节奏。

    输入来自本轮心跳的 owner、控制门、tick/trace 和逻辑时间；输出会进入
    heartbeat_runs.output_json，说明本轮是已生成、已跳过还是失败。函数只为
    Agent 自我生活工作，复用既有 life_rhythm_runs/items 作为幂等真相源，
    并通过 LifeOps 创建事件和日程块。若单个模板冲突或失败，它会记录跳过
    原因并继续处理其它模板；若子流程整体异常，则降级为 heartbeat partial。
    """
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    mode = str(gates.get("daily_rhythm", gates.get("living_rhythm", "auto")) or "auto").lower()
    if mode in {"off", "disabled", "false", "manual"}:
        return {"status": "skipped", "reason": f"gate={mode}"}
    if str(gates.get("schedule", "auto") or "auto").lower() in {"off", "disabled", "false"}:
        return {"status": "skipped", "reason": "schedule gate off"}
    try:
        from zoneinfo import ZoneInfo
        from .impromptu import _next_free_slot
        from .living import living_preset_name, rhythm_templates
        from .time_utils import parse_datetime

        canon = rt._living_canon(owner_kind, owner_id, control)
        tz_name = _tz_from_canon(canon) or "UTC"
        preset = living_preset_name(canon)
        now_dt = parse_datetime(now)
        if now_dt is None:
            return {"status": "skipped", "reason": "unparseable now"}
        local_now = now_dt.astimezone(ZoneInfo(tz_name))
        date_key = local_now.date().isoformat()
        now_ts = int(local_now.timestamp())
        existing = rt.conn.execute(
            """SELECT id, action, status, event_ids_json, schedule_block_ids_json FROM life_rhythm_runs
                 WHERE owner_kind=? AND owner_id=? AND date_key=? AND preset=?
                   AND status IN ('committed','skipped')
                 ORDER BY created_at DESC LIMIT 1""",
            (owner_kind, owner_id, date_key, preset),
        ).fetchone()
        if existing:
            return {
                "status": "skipped",
                "reason": "already generated today",
                "date_key": date_key,
                "run_id": existing["id"],
                "event_ids": loads(existing["event_ids_json"], []),
                "schedule_block_ids": loads(existing["schedule_block_ids_json"], []),
            }
        defined_resources = {
            r["key"] for r in rt.conn.execute(
                "SELECT key FROM resource_definitions WHERE owner_kind=? AND owner_id=?",
                (owner_kind, owner_id),
            ).fetchall()
        }

        materialized: list[dict[str, Any]] = []
        skipped_items: list[dict[str, Any]] = []
        tx_ids: list[str] = []
        receipts: list[str] = []
        templates = rhythm_templates(date_key=date_key, tz=tz_name, canon=canon)
        with trace.span("daily_rhythm", {"date_key": date_key, "template_count": len(templates)}):
            for item in templates:
                start_ts = _to_epoch(item.get("start"))
                end_ts = _to_epoch(item.get("end"))
                if start_ts is None or end_ts is None or end_ts <= now_ts:
                    skipped_items.append({"title": item.get("title"), "reason": "already_elapsed"})
                    continue
                duration_s = max(60, int(end_ts) - int(start_ts))
                planned_start_ts = int(start_ts)
                planned_end_ts = int(end_ts)
                overlap = rt.conn.execute(
                    """SELECT id FROM schedule_blocks
                         WHERE owner_kind=? AND owner_id=? AND status IN ('planned','locked','ready','in_progress')
                           AND start_ts IS NOT NULL AND end_ts IS NOT NULL
                           AND NOT(end_ts <= ? OR start_ts >= ?)
                         LIMIT 1""",
                    (owner_kind, owner_id, planned_start_ts, planned_end_ts),
                ).fetchone()
                if overlap:
                    planned_start_ts, planned_end_ts = _next_free_slot(
                        rt.conn, owner_kind, owner_id,
                        after_ts=max(planned_start_ts, now_ts), duration_s=duration_s, exclude_ids=set(),
                    )
                start_local = datetime.fromtimestamp(planned_start_ts, tz=ZoneInfo(tz_name))
                end_local = datetime.fromtimestamp(planned_end_ts, tz=ZoneInfo(tz_name))
                if start_local.date().isoformat() != date_key:
                    skipped_items.append({"title": item.get("title"), "reason": "no_free_slot_today"})
                    continue
                costs = {
                    key: value for key, value in (item.get("resource_costs") or {}).items()
                    if key in defined_resources
                }
                event_payload = {
                    "title": item["title"],
                    "description": f"heartbeat 为 {preset} 自动补齐的当日生活节奏事项。",
                    "event_type": item.get("event_type") or "routine",
                    "event_category": item.get("event_category") or "maintenance",
                    "activity_domain": item.get("activity_domain"),
                    "source": "heartbeat_daily_rhythm",
                    "status": "planned",
                    "priority": int(item.get("priority", 55)),
                    "importance": int(item.get("importance", 55)),
                    "tags": (item.get("tags") or []) + ["daily_rhythm", date_key],
                    "attributes": {
                        "generated_by": "heartbeat_daily_rhythm",
                        "preset": preset,
                        "date_key": date_key,
                        "worth_diary": item.get("worth_diary", False),
                        "worth_proactive": item.get("worth_proactive", False),
                    },
                    "resource_costs": costs,
                }
                ev_id = None
                try:
                    c1 = rt._commit_ops_locked(
                        [{"type": "CREATE_EVENT", "payload": event_payload}],
                        owner_kind, owner_id, "heartbeat_daily_rhythm",
                        session_id=None, turn_id=tick_id, trace=trace, control=control,
                    )
                    ev_id = (((c1.get("results") or [{}])[0].get("result") or {}).get("id"))
                    if not ev_id:
                        skipped_items.append({"title": item.get("title"), "reason": "event_not_created"})
                        continue
                    try:
                        c2 = rt._commit_ops_locked(
                            [{"type": "CREATE_SCHEDULE_BLOCK", "payload": {
                                "event_id": ev_id,
                                "start": start_local.isoformat(),
                                "end": end_local.isoformat(),
                                "block_type": "daily_rhythm",
                                "timezone_name": tz_name,
                                "interruptibility": {"level": "soft_interruptible", "max_delay_minutes": 20},
                            }}],
                            owner_kind, owner_id, "heartbeat_daily_rhythm",
                            session_id=None, turn_id=tick_id, trace=trace, control=control,
                        )
                    except Exception:
                        try:
                            rt._commit_ops_locked(
                                [{"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": ev_id, "status": "cancelled", "reason": "daily rhythm schedule block creation failed"}}],
                                owner_kind, owner_id, "heartbeat_daily_rhythm_cleanup",
                                session_id=None, turn_id=tick_id, trace=trace, control=control,
                            )
                        except Exception:
                            pass
                        raise
                    bid = (((c2.get("results") or [{}])[0].get("result") or {}).get("id"))
                    tx_ids.extend([c1.get("transaction_id"), c2.get("transaction_id")])
                    receipts.extend([(c1.get("receipt") or {}).get("receipt_id"), (c2.get("receipt") or {}).get("receipt_id")])
                    materialized.append({"template": item, "event_id": ev_id, "schedule_block_id": bid, "start": start_local.isoformat(), "end": end_local.isoformat()})
                except Exception as item_exc:
                    skipped_items.append({"title": item.get("title"), "reason": f"{type(item_exc).__name__}: {item_exc}"})
                    append_audit(rt.conn, owner_kind, owner_id, "daily_rhythm_item_failed", "warning", str(item_exc), {"tick_id": tick_id, "title": item.get("title")}, trace.id)

        run_id = new_id("rhythm")
        event_ids = [m["event_id"] for m in materialized if m.get("event_id")]
        block_ids = [m["schedule_block_id"] for m in materialized if m.get("schedule_block_id")]
        rendered = "heartbeat 每日生活节奏\n====================\n" + (
            "\n".join([f"- {m['start'][11:16]}-{m['end'][11:16]} {m['template']['title']}" for m in materialized])
            if materialized else "今天剩余时间没有可补齐的节奏事项。"
        )
        status = "committed" if materialized else "skipped"
        rt.conn.execute(
            """INSERT INTO life_rhythm_runs(id, owner_kind, owner_id, date_key, preset, action, status,
                 event_ids_json, schedule_block_ids_json, transaction_ids_json, receipt_ids_json, rendered_text)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, owner_kind, owner_id, date_key, preset, "heartbeat_daily_rhythm", status,
             dumps(event_ids), dumps(block_ids), dumps([t for t in tx_ids if t]), dumps([r for r in receipts if r]), rendered),
        )
        for item in materialized:
            template = item["template"]
            rt.conn.execute(
                """INSERT INTO life_rhythm_items(id, run_id, owner_kind, owner_id, title, category,
                     activity_domain, start, end, event_id, schedule_block_id, status, payload_json)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    new_id("rhythmitem"), run_id, owner_kind, owner_id, template["title"],
                    template.get("event_category"), template.get("activity_domain"), item.get("start"), item.get("end"),
                    item.get("event_id"), item.get("schedule_block_id"), "planned", dumps(template),
                ),
            )
        append_journal(
            rt.conn, owner_kind, owner_id, "heartbeat_daily_rhythm_generated",
            {"run_id": run_id, "date_key": date_key, "event_ids": event_ids, "schedule_block_ids": block_ids, "skipped_items": skipped_items},
            "heartbeat_daily_rhythm", canon_version=control.get("active_canon_version"),
        )
        return {
            "status": "ok" if materialized else "skipped",
            "date_key": date_key,
            "run_id": run_id,
            "count": len(materialized),
            "event_ids": event_ids,
            "schedule_block_ids": block_ids,
            "skipped_items": skipped_items,
            "rendered": rendered,
        }
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "daily_rhythm_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def settle_supply_chain(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                  tick_id: str, trace: Trace, now: str,
                                  authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """进销存: settle sales for completed venture occurrences (sold =
    min(demand, stock) → stock down, money up), mark arrived restock orders,
    and auto-create a 进货 (procurement) event when stock runs low — so goods
    never appear from nowhere. Gated by `venture`.

    社交投影是结算后的派生事实：失败时只让本段 heartbeat partial，并通过
    补偿扫描重试已 sale_settled 但缺少 applied projection 的 occurrence，
    不能重复扣库存/入账，也不能留下半截 projection run。
    """
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    if str(gates.get("venture", gates.get("recurring_activities", "auto")) or "auto").lower() in {"off", "disabled", "false"}:
        return {"status": "skipped", "reason": "gate off"}
    try:
        from .time_utils import parse_datetime, to_epoch as _to_epoch
        from datetime import timedelta as _td
        canon = get_active_canon(rt.conn, owner_kind, owner_id)
        tz_name = _tz_from_canon(canon) or "UTC"
        now_ts = int(_to_epoch(now))
        terminal = {"completed", "partial", "done"}
        sold_total = 0.0
        income_total = 0.0
        restocks = 0
        social_projections = 0
        social_projection_errors: list[dict[str, Any]] = []
        attempted_social_occurrences: set[str] = set()
        venture_sale_rumors = (authoring or {}).get("venture_sale_projection_rumors_by_occurrence_id") or {}
        if not isinstance(venture_sale_rumors, dict):
            venture_sale_rumors = {}
        for act in venture.list_ventures(rt.conn, owner_kind, owner_id, status="active"):
            op = act.get("operation_model") or "active"
            passive = op in {"self_service", "staffed"}
            sc = act.get("supply_chain") if isinstance(act.get("supply_chain"), dict) else None
            has_supply = bool(sc and sc.get("goods_resource"))
            # active non-supply ventures settle via the normal event-completion
            # path (their block runs through the execution simulator) — nothing
            # to do here.
            if not (has_supply or passive):
                continue
            goods = sc["goods_resource"] if has_supply else None
            money = (sc or {}).get("money_resource") or "money.lingzhu"
            unit_price = float((sc or {}).get("unit_price") or 0)
            demand = float((sc or {}).get("demand_per_occurrence") or 0)
            wage = float(act.get("wage_per_occurrence") or 0)
            # 1) settle each unsettled occurrence
            occs = rt.conn.execute(
                "SELECT id, event_id, date_key FROM venture_occurrences WHERE owner_kind=? AND owner_id=? AND activity_id=? AND sale_settled=0",
                (owner_kind, owner_id, act["id"]),
            ).fetchall()
            for occ in occs:
                ev = get_event(rt.conn, occ["event_id"]) if occ["event_id"] else None
                if not passive:
                    # active: she ran it — settle once the event completed
                    if not ev or ev.get("status") not in terminal:
                        continue
                else:
                    # passive (self_service/staffed): no occupying block; settle
                    # once the day's window has passed, agent not required.
                    wend = _window_end_ts(occ["date_key"], act.get("end_time"), act.get("timezone") or tz_name)
                    if wend is not None and now_ts < wend:
                        continue
                    if ev and ev.get("status") not in terminal:
                        with trace.span("venture_passive_complete", {"activity_id": act["id"]}):
                            rt._commit_ops_locked([{"type": "COMPLETE_EVENT", "payload": {"event_id": occ["event_id"], "summary": f"{op} 经营结算：{act['title']}", "source": "venture_passive"}}], owner_kind, owner_id, "venture_passive", session_id=None, turn_id=tick_id, trace=trace, control=control)
                sold = 0.0
                income = 0.0
                settle_ops: list[dict[str, Any]] = []
                if has_supply:
                    stock = _account_value(rt, owner_kind, owner_id, goods)
                    sold = max(0.0, min(demand, stock))
                    income = round(sold * unit_price, 2)
                    if sold > 0:
                        settle_ops.append({"type": "RESOURCE_DELTA", "payload": {"resource_key": goods, "delta": -sold, "operation": "consume", "reason": f"售出 @{act['title']}", "source": "venture_sale", "event_id": occ["event_id"]}})
                        if income:
                            settle_ops.append({"type": "RESOURCE_DELTA", "payload": {"resource_key": money, "delta": income, "operation": "produce", "reason": f"营业收入 @{act['title']}", "source": "venture_sale", "event_id": occ["event_id"]}})
                # staffed ventures pay a per-occurrence wage (hired labour)
                if op == "staffed" and wage > 0:
                    settle_ops.append({"type": "RESOURCE_DELTA", "payload": {"resource_key": money, "delta": -wage, "operation": "consume", "reason": f"雇员工资 @{act['title']}", "source": "venture_wage", "event_id": occ["event_id"]}})
                if settle_ops:
                    with trace.span("venture_settle", {"activity_id": act["id"], "sold": sold}):
                        rt._commit_ops_locked(settle_ops, owner_kind, owner_id, "venture_sale", session_id=None, turn_id=tick_id, trace=trace, control=control)
                rt.conn.execute(
                    "UPDATE venture_occurrences SET sale_settled=1, sold_quantity=?, income=? WHERE id=?",
                    (sold, income, occ["id"]),
                )
                attempted_social_occurrences.add(occ["id"])
                projection = _project_venture_sale_settlement_safe(rt, 
                    owner_kind, owner_id, occ["id"],
                    source="social_projector:venture_sale",
                    trace_id=trace.id,
                    rumor_authoring=venture_sale_rumors.get(str(occ["id"])),
                )
                if projection.get("projected"):
                    social_projections += 1
                elif projection.get("reason") == "projection_failed":
                    social_projection_errors.append(projection)
                sold_total += sold
                income_total += income
            for settled in rt.conn.execute(
                """SELECT occ.id
                   FROM venture_occurrences occ
                   WHERE occ.owner_kind=? AND occ.owner_id=? AND occ.activity_id=?
                     AND occ.sale_settled=1
                     AND NOT EXISTS (
                       SELECT 1 FROM social_projection_runs pr
                       WHERE pr.owner_kind=occ.owner_kind
                         AND pr.owner_id=occ.owner_id
                         AND pr.projection_kind='venture_sale_settled'
                         AND pr.projection_key=occ.id
                         AND pr.status='applied'
                     )
                   ORDER BY occ.date_key DESC
                   LIMIT 20""",
                (owner_kind, owner_id, act["id"]),
            ).fetchall():
                if settled["id"] in attempted_social_occurrences:
                    continue
                attempted_social_occurrences.add(settled["id"])
                projection = _project_venture_sale_settlement_safe(rt, 
                    owner_kind, owner_id, settled["id"],
                    source="social_projector:venture_sale_retry",
                    trace_id=trace.id,
                    rumor_authoring=venture_sale_rumors.get(str(settled["id"])),
                )
                if projection.get("projected"):
                    social_projections += 1
                elif projection.get("reason") == "projection_failed":
                    social_projection_errors.append(projection)
            # 2) mark restock orders received once their procurement event completes
            for order in rt.conn.execute(
                "SELECT id, event_id FROM venture_restock_orders WHERE owner_kind=? AND owner_id=? AND activity_id=? AND status='pending'",
                (owner_kind, owner_id, act["id"]),
            ).fetchall():
                ev = get_event(rt.conn, order["event_id"]) if order["event_id"] else None
                if ev and ev.get("status") in terminal:
                    rt.conn.execute("UPDATE venture_restock_orders SET status='received', received_at=datetime('now') WHERE id=?", (order["id"],))
            # 3) low goods → replenish the whole upstream chain, deduped per
            #    resource. recipe(制作) means make from materials (+tools);
            #    restock(进货) means buy finished goods. For 制作, the chain is
            #    买笔(tool) → 买原料(material) → 制作(materials→goods) → 摆摊(sell).
            recipe = sc.get("recipe") if has_supply and isinstance(sc.get("recipe"), dict) else None
            restock = sc.get("restock") if has_supply and isinstance(sc.get("restock"), dict) else None
            if recipe or restock:
                threshold = float((recipe or restock).get("threshold") or 0)
                stock = _account_value(rt, owner_kind, owner_id, goods)
                if stock < threshold:
                    if recipe:
                        goods_name = sc.get("goods_name") or goods
                        # a) tools (durables): bought once if absent, not consumed
                        for tkey, tspec in (recipe.get("tools") or {}).items():
                            tspec = tspec if isinstance(tspec, dict) else {}
                            if _account_value(rt, owner_kind, owner_id, tkey) < 1:
                                tc = float(tspec.get("unit_cost") or 0)
                                if _venture_replenish_event(rt, owner_kind=owner_kind, owner_id=owner_id, control=control, tick_id=tick_id, trace=trace, now=now, act=act,
                                        target_key=tkey, order_qty=1, costs={tkey: 1, money: -round(tc, 2)},
                                        title=f"买{tspec.get('name') or '工具'}：{tspec.get('name') or tkey}", desc=f"「{act['title']}」缺工具 {tkey}，去置办。",
                                        dur=int(tspec.get("duration_minutes") or 60), unit_cost=tc, tag="买工具"):
                                    restocks += 1
                        # b) materials: bought when low (their own threshold)
                        for mkey, mspec in (recipe.get("material_restock") or {}).items():
                            mspec = mspec if isinstance(mspec, dict) else {}
                            if _account_value(rt, owner_kind, owner_id, mkey) < float(mspec.get("threshold") or 0):
                                mq = float(mspec.get("quantity") or 0); mc = float(mspec.get("unit_cost") or 0)
                                if _venture_replenish_event(rt, owner_kind=owner_kind, owner_id=owner_id, control=control, tick_id=tick_id, trace=trace, now=now, act=act,
                                        target_key=mkey, order_qty=mq, costs={mkey: mq, money: -round(mq * mc, 2)},
                                        title=f"买原料：{mspec.get('name') or mkey}", desc=f"为「{act['title']}」备料 {mkey}。",
                                        dur=int(mspec.get("duration_minutes") or 60), unit_cost=mc, tag="买原料"):
                                    restocks += 1
                        # c) make: only when tools present and materials sufficient
                        mats = recipe.get("materials") or {}
                        tools_ok = all(_account_value(rt, owner_kind, owner_id, t) >= 1 for t in (recipe.get("tools") or {}))
                        mats_ok = all(_account_value(rt, owner_kind, owner_id, m) >= float(q) for m, q in mats.items())
                        batch = float(recipe.get("batch_output") or 0)
                        if tools_ok and mats_ok and batch > 0:
                            costs: dict[str, Any] = {goods: batch}
                            for mk, mq in mats.items():
                                costs[mk] = -float(mq)
                            for ek, ev_ in (recipe.get("effort") or {}).items():
                                costs[ek] = float(ev_)
                            if _venture_replenish_event(rt, owner_kind=owner_kind, owner_id=owner_id, control=control, tick_id=tick_id, trace=trace, now=now, act=act,
                                    target_key=goods, order_qty=batch, costs=costs,
                                    title=f"制作：{goods_name}", desc=f"为「{act['title']}」制作补货（库存 {stock:g} 低于 {threshold:g}）。",
                                    dur=int(recipe.get("duration_minutes") or 120), unit_cost=0.0, tag="制作"):
                                restocks += 1
                    else:
                        q = float(restock.get("quantity") or 0); uc = float(restock.get("unit_cost") or 0)
                        if _venture_replenish_event(rt, owner_kind=owner_kind, owner_id=owner_id, control=control, tick_id=tick_id, trace=trace, now=now, act=act,
                                target_key=goods, order_qty=q, costs={goods: q, money: -round(q * uc, 2)},
                                title=f"进货：{sc.get('goods_name') or goods}", desc=f"为「{act['title']}」补货（库存 {stock:g} 低于 {threshold:g}）。",
                                dur=60, unit_cost=uc, tag="进货"):
                            restocks += 1
        status = "partial" if social_projection_errors else "ok"
        out = {"status": status, "sold": sold_total, "income": income_total,
               "restocks_ordered": restocks, "social_projections": social_projections}
        if social_projection_errors:
            out["ok"] = False
            out["social_projection_errors"] = social_projection_errors[:5]
        return out
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "venture_supply_settle_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def roll_opportunities(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                 tick_id: str, trace: Trace, now: str) -> dict[str, Any]:
    """接委托/客人找上门: for opportunity-triggered ventures, roll the day's
    arrivals (deterministic per venture+day) and land any not-yet-arrived
    ones as conflict-arbitrated events from now — so work shows up on its own
    instead of being improvised when asked. Gated by `venture`."""
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    gates = control.get("module_gates") or {}
    if str(gates.get("venture", gates.get("recurring_activities", "auto")) or "auto").lower() in {"off", "disabled", "false"}:
        return {"status": "skipped", "reason": "gate off"}
    try:
        from .time_utils import parse_datetime, to_epoch as _to_epoch
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        from .impromptu import _next_free_slot
        canon = get_active_canon(rt.conn, owner_kind, owner_id)
        tz_name = _tz_from_canon(canon) or "UTC"
        dt = parse_datetime(now)
        local = dt
        try:
            local = dt.astimezone(ZoneInfo(tz_name)) if dt else dt
        except Exception:
            local = dt
        if local is None:
            return {"status": "skipped", "reason": "unparseable now"}
        date_key = local.date().isoformat()
        landed = []
        for act in venture.list_ventures(rt.conn, owner_kind, owner_id, status="active"):
            if (act.get("trigger_kind") or "scheduled") != "opportunity":
                continue
            target = venture.opportunity_target(act, date_key)
            have = venture.count_arrivals(rt.conn, owner_kind, owner_id, act["id"], date_key)
            if have >= target:
                continue
            arrival = act.get("arrival") if isinstance(act.get("arrival"), dict) else {}
            dur_s = max(60, int(arrival.get("duration_minutes") or 90) * 60)
            atz = act.get("timezone") or tz_name
            for _ in range(target - have):
                base_ts = int(_to_epoch(now))
                fs, fe = _next_free_slot(rt.conn, owner_kind, owner_id, after_ts=base_ts, duration_s=dur_s, exclude_ids=set())
                tzinfo = ZoneInfo(atz)
                s_iso = _dt.fromtimestamp(fs, tz=tzinfo).isoformat()
                e_iso = _dt.fromtimestamp(fe, tz=tzinfo).isoformat()
                ev_payload = {
                    "title": act["title"],
                    "description": "有委托/客人找上门，需要出外勤处理。",
                    "event_type": act.get("activity_type") or "work",
                    "event_category": act.get("event_category") or "work",
                    "activity_domain": act.get("activity_domain"),
                    "status": "planned",
                    "importance": int(act.get("importance") or 55),
                    "priority": int(act.get("priority") or 55),
                    "resource_costs": act.get("resource_costs") or {},
                    "source": "venture_opportunity",
                    "tags": (act.get("tags") or []) + ["营生", "委托", "opportunity", act["id"]],
                    "attributes": {"recurring_activity_id": act["id"], "generated_by": "venture_opportunity", "opportunity": True},
                }
                if act.get("location"):
                    ev_payload["location"] = {"name": act.get("location"), "kind": act.get("location_kind") or "flexible"}
                from .db import savepoint
                with savepoint(rt.conn, f"venture_opp_{act['id']}_{date_key}_{have}"):
                    with trace.span("venture_opportunity", {"activity_id": act["id"]}):
                        c1 = rt._commit_ops_locked([{"type": "CREATE_EVENT", "payload": ev_payload}], owner_kind, owner_id, "venture_opportunity", session_id=None, turn_id=tick_id, trace=trace, control=control)
                    ev_id = (((c1.get("results") or [{}])[0].get("result") or {}).get("id"))
                    if not ev_id:
                        raise RuntimeError("venture opportunity event create returned no id")
                    rt._commit_ops_locked([{"type": "CREATE_SCHEDULE_BLOCK", "payload": {"event_id": ev_id, "start": s_iso, "end": e_iso, "block_type": "venture_opportunity", "timezone_name": atz, "interruptibility": {"level": "soft_interruptible", "max_delay_minutes": 30}}}], owner_kind, owner_id, "venture_opportunity", session_id=None, turn_id=tick_id, trace=trace, control=control)
                    venture.record_arrival(rt.conn, owner_kind, owner_id, act["id"], date_key, ev_id)
                landed.append({"activity_id": act["id"], "title": act["title"], "event_id": ev_id, "start": s_iso})
        return {"status": "ok", "date_key": date_key, "count": len(landed), "landed": landed}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "venture_opportunity_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def sync_realtime_to_schedule(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                        tick_id: str, trace: Trace, now: str) -> dict[str, Any]:
    """Reflect what the agent is *currently* doing: if a schedule block's
    window covers now, mark it in_progress and put realtime state into a busy
    (or uninterruptible) mode pointing at that event — so the WebUI sprite and
    the agent's own context show "I'm doing X" instead of idle. When no block
    is active, fall back to idle. Sleep/reply/conversation states are left to
    their own owners."""
    if owner_kind != "agent":
        return {"status": "skipped", "reason": "non-agent owner"}
    try:
        from .time_utils import to_epoch as _to_epoch
        state = get_realtime_state(rt.conn, owner_kind, owner_id) or {}
        mode = state.get("mode")
        # don't fight states owned elsewhere (sleep, pending reply, live chat)
        if mode in {"asleep", "napping", "dreaming", "waiting_to_reply", "in_conversation"} or state.get("active_sleep_session_id"):
            return {"status": "skipped", "reason": f"mode={mode}"}
        now_ts = int(_to_epoch(now))
        row = rt.conn.execute(
            """SELECT sb.id AS block_id, sb.event_id, sb.interruptibility_json,
                      e.title, e.status AS event_status
                 FROM schedule_blocks sb JOIN events e ON e.id=sb.event_id
                WHERE sb.owner_kind=? AND sb.owner_id=? AND sb.status IN ('planned','locked','ready','scheduled','in_progress')
                  AND sb.start_ts IS NOT NULL AND sb.end_ts IS NOT NULL
                  AND sb.start_ts <= ? AND sb.end_ts > ?
                  AND e.status NOT IN ('completed','partial','done','cancelled','rescheduled','skipped')
                ORDER BY sb.start_ts DESC LIMIT 1""",
            (owner_kind, owner_id, now_ts, now_ts),
        ).fetchone()
        if row:
            interro = loads(row["interruptibility_json"] or "{}", {}) if isinstance(row["interruptibility_json"], str) else (row["interruptibility_json"] or {})
            level = (interro or {}).get("level") or "soft_interruptible"
            new_mode = "uninterruptible_event" if level == "uninterruptible" else "busy"
            if state.get("active_event_id") == row["event_id"] and mode == new_mode:
                return {"status": "ok", "active_event_id": row["event_id"], "changed": False}
            ops = [{"type": "UPDATE_REALTIME_STATE", "payload": {
                "mode": new_mode, "active_event_id": row["event_id"], "active_schedule_block_id": row["block_id"],
                "interruptibility_level": level,
                "reply_mode": "defer_until_event_end" if new_mode == "uninterruptible_event" else "immediate",
                "source": "schedule_sync", "reason": f"进行中：{row['title']}"}}]
            if row["event_status"] in {"planned", "scheduled"}:
                ops.append({"type": "UPDATE_EVENT_STATUS", "payload": {"event_id": row["event_id"], "status": "in_progress", "reason": "事件窗口开始，进入进行中"}})
            with trace.span("schedule_realtime_sync", {"event_id": row["event_id"]}):
                rt._commit_ops_locked(ops, owner_kind, owner_id, "schedule_sync", session_id=None, turn_id=tick_id, trace=trace, control=control)
            return {"status": "ok", "active_event_id": row["event_id"], "mode": new_mode}
        # no active block — clear a stale schedule-driven busy state back to idle
        if mode in {"busy", "uninterruptible_event", "event_work"} or state.get("active_event_id"):
            rt._commit_ops_locked([{"type": "UPDATE_REALTIME_STATE", "payload": {
                "mode": "idle", "reply_mode": "immediate", "source": "schedule_sync",
                "reason": "无进行中日程，回到待机"}}], owner_kind, owner_id, "schedule_sync", session_id=None, turn_id=tick_id, trace=trace, control=control)
            return {"status": "ok", "mode": "idle", "cleared": True}
        return {"status": "ok", "mode": mode, "changed": False}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "schedule_realtime_sync_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

def run_companion(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                            tick_id: str, trace: Trace, now: str,
                            authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """运行 heartbeat 的陪伴主动意图生成。

    输入来自 `tick()` 的控制状态、逻辑时间和事务外 authoring 包；输出是创建的
    proactive intent id/type 或空结果。副作用是写 proactive_intents 和可选
    relationship note followed_up_at。生成式文案只能来自 `authoring["companion"]`，
    本函数在事务内传 `allow_authoring=False`，失败时保持沉默。
    """
    if owner_kind != "agent":
        return {"generated": None, "reason": "not agent"}
    gates = control.get("module_gates") or {}
    if str(gates.get("proactive", "pending_only") or "pending_only").strip().lower() == "off":
        return {"generated": None, "reason": "proactive off"}
    if str(gates.get("companion", "auto") or "auto").strip().lower() in {"off", "disabled", "manual", "false"}:
        return {"generated": None, "reason": "companion off"}
    try:
        from . import companion
        temporal_facts = (authoring or {}).get("time") if isinstance(authoring, dict) else None
        if not isinstance(temporal_facts, dict):
            temporal_facts = None
        with trace.span("companion_generate", {"tick_id": tick_id}):
            intent = companion.maybe_generate_companion_intent(
                rt.conn, owner_id, control=control, now=now, trace_id=trace.id,
                authored=(authoring or {}).get("companion"), allow_authoring=False,
                temporal_grounding_facts=temporal_facts,
            )
        return {"generated": intent.get("id") if intent else None,
                "intent_type": intent.get("intent_type") if intent else None}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "companion_failed", "warning", str(exc), {}, trace.id)
        return {"error": f"{type(exc).__name__}: {exc}"}

def run_proactive(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                            tick_id: str, trace: Trace, now: str,
                            authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """运行 heartbeat 的 proactive 自动评估。

    输入来自 `tick()` 的控制状态、trace、逻辑时间和事务外 authoring 包；输出是
    proactive LifeOps commit 或降级错误。副作用只发生在后续 LifeOps 事务内：
    过期 intent、重评等待 intent、按策略创建 outbox。生成式文案只能来自
    `authoring["proactive_outbox_drafts"]`，本函数在事务内始终传
    `allow_authoring=False`，无预生成稿时保留原有 fallback。
    """
    if owner_kind != "agent":
        return {"evaluated": [], "reason": "not agent"}
    gates = control.get("module_gates") or {}
    mode = str(gates.get("proactive", "pending_only") or "pending_only")
    if mode == "off":
        return {"evaluated": [], "reason": "proactive off"}
    drafts = (authoring or {}).get("proactive_outbox_drafts") or {}
    if not isinstance(drafts, dict):
        drafts = {}
    temporal_facts = (authoring or {}).get("time") if isinstance(authoring, dict) else None
    if not isinstance(temporal_facts, dict):
        temporal_facts = None
    try:
        with trace.span("proactive_evaluate", {"mode": mode}):
            commit = rt._commit_ops_locked([
                {"type": "EXPIRE_PROACTIVE_INTENTS", "payload": {}},
                {"type": "RECONSIDER_WAITING_PROACTIVE_INTENTS", "payload": {"trace_id": trace.id, "allow_authoring": False, "draft_texts_by_intent_id": drafts, "temporal_grounding_facts": temporal_facts}},
                {"type": "EVALUATE_PROACTIVE_INTENT", "payload": {"manual": False, "trace_id": trace.id, "allow_authoring": False, "draft_texts_by_intent_id": drafts, "temporal_grounding_facts": temporal_facts}},
            ], owner_kind, owner_id, "proactive_heartbeat", session_id=None, turn_id=tick_id, trace=trace, control=control)
        return {"commit": commit}
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "proactive_failed", "warning", str(exc), {"mode": mode}, trace.id)
        return {"error": f"{type(exc).__name__}: {exc}"}

def run_managed_review(rt, owner_kind: str, owner_id: str, control: dict[str, Any],
                                 tick_id: str, trace: Trace, now: str, manual: bool) -> dict[str, Any]:
    try:
        with trace.span("agent_managed_review_loop", {"tick_id": tick_id, "manual": manual}):
            return rt._run_agent_managed_review_locked(
                owner_kind, owner_id, trigger_source="heartbeat", tick_id=tick_id,
                dry_run=False, force=False, session_id=None, turn_id=tick_id,
            )
    except Exception as exc:
        append_audit(rt.conn, owner_kind, owner_id, "agent_managed_review_loop_failed", "warning", str(exc), {"tick_id": tick_id}, trace.id)
        return {"ok": False, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
