"""Commit receipts and final-answer evidence matching."""

from __future__ import annotations

import re
from typing import Any

from .jsonutil import dumps, loads
from .trace import new_id


def _fact_text_for(op_type: str, payload: dict[str, Any], result: Any) -> tuple[str, str, dict[str, Any]]:
    if op_type == "CREATE_EVENT":
        event = result if isinstance(result, dict) else {}
        title = payload.get("title") or event.get("title") or "event"
        status = payload.get("status") or event.get("status") or "planned"
        return "event", f"{title} status={status} planned_start={payload.get('planned_start','')}", {"event_id": event.get("id")}
    if op_type == "UPDATE_EVENT_STATUS":
        return "event_status", f"event {payload.get('event_id')} status {payload.get('status')} reason {payload.get('reason','')}", {"event_id": payload.get("event_id")}
    if op_type == "CREATE_SCHEDULE_BLOCK":
        block = result if isinstance(result, dict) else {}
        return "schedule", f"schedule {payload.get('event_id','')} from {payload.get('start')} to {payload.get('end')}", {"schedule_block_id": block.get("id"), "event_id": payload.get("event_id")}
    if op_type == "UPDATE_SCHEDULE_BLOCK_STATUS":
        block = result if isinstance(result, dict) else {}
        return "schedule", f"schedule block {payload.get('schedule_block_id')} status={payload.get('status')} reason={payload.get('reason','')}", {"schedule_block_id": payload.get("schedule_block_id") or block.get("id")}

    if op_type == "PLAN_CORE_SLEEP":
        res = result if isinstance(result, dict) else {}
        plan = res.get("sleep_plan", {}) if isinstance(res, dict) else {}
        sess = res.get("sleep_session", {}) if isinstance(res, dict) else {}
        return "sleep", f"sleep planned for {payload.get('date_key')} from {payload.get('target_bedtime')} to {payload.get('target_wake_time')}", {"sleep_plan_id": plan.get("id"), "sleep_session_id": sess.get("id"), "event_id": (res.get("event") or {}).get("id") if isinstance(res.get("event"), dict) else None}
    if op_type == "START_SLEEP_SESSION":
        sess = (result.get("sleep_session") if isinstance(result, dict) else {}) or {}
        return "sleep", f"sleep session {payload.get('sleep_session_id') or sess.get('id')} started", {"sleep_session_id": payload.get("sleep_session_id") or sess.get("id"), "sleep_plan_id": payload.get("sleep_plan_id") or sess.get("sleep_plan_id")}
    if op_type == "END_SLEEP_SESSION":
        sess = (result.get("sleep_session") if isinstance(result, dict) else {}) or {}
        return "sleep", f"sleep session {payload.get('sleep_session_id')} ended wake_cause={payload.get('wake_cause','natural_wake')} duration={sess.get('actual_duration_minutes','')}", {"sleep_session_id": payload.get("sleep_session_id"), "event_id": sess.get("event_id"), "resource_effects": result.get("resource_effects") if isinstance(result, dict) else {}}
    if op_type == "COMPLETE_EVENT":
        ev = result.get("event", {}) if isinstance(result, dict) else {}
        return "event_result", f"completed {ev.get('title', payload.get('event_id'))}: {payload.get('summary','completed')}", {"event_id": payload.get("event_id"), "result_id": result.get("result_id") if isinstance(result, dict) else None}
    if op_type == "RECORD_REPLY_GATE_DECISION":
        dec = result if isinstance(result, dict) else {}
        return "reply_gate", f"reply gate decision {payload.get('decision') or dec.get('decision')} reason={payload.get('reason') or dec.get('reason','')}", {"reply_gate_decision_id": dec.get("id")}
    if op_type == "CREATE_DELAYED_REPLY":
        delayed = result if isinstance(result, dict) else {}
        return "reply_gate", f"delayed reply queued reason={payload.get('reason','')}", {"delayed_reply_id": delayed.get("id"), "gate_decision_id": payload.get("gate_decision_id")}
    if op_type == "RELEASE_DELAYED_REPLIES":
        return "reply_gate", f"released delayed replies count={result.get('released_count', 0) if isinstance(result, dict) else 0}", {"released_reply_ids": [r.get("id") for r in (result.get("replies", []) if isinstance(result, dict) else [])]}
    if op_type == "CALL_OVERRIDE":
        call = (result.get("call_override") if isinstance(result, dict) else {}) or {}
        return "reply_gate", f"call override reason={payload.get('reason','call override')}", {"call_override_id": call.get("id"), "interrupted_sleep_session_id": call.get("interrupted_sleep_session_id"), "interrupted_event_id": call.get("interrupted_event_id")}
    if op_type == "RESOURCE_DEFINE":
        return "resource", f"defined resource {payload.get('key')} {payload.get('display_name','')}", {"resource_key": payload.get("key")}
    if op_type == "RESOURCE_DELTA":
        return "resource", f"resource {payload.get('resource_key')} changed by {payload.get('delta')} because {payload.get('reason','')}", {"resource_key": payload.get("resource_key"), "ledger_id": result.get("ledger_id") if isinstance(result, dict) else None}
    if op_type == "RESOURCE_RESERVE":
        return "resource", f"reserved {payload.get('amount')} {payload.get('resource_key')} because {payload.get('reason','')}", {"reservation_id": result.get("reservation_id") if isinstance(result, dict) else None}
    if op_type == "RESOURCE_RELEASE":
        return "resource", f"released reservation {payload.get('reservation_id')}", {"reservation_id": payload.get("reservation_id")}
    if op_type == "CREATE_MEMORY":
        mem = result if isinstance(result, dict) else {}
        return "memory", f"memory {payload.get('content','')}", {"memory_id": mem.get("id")}
    if op_type == "CREATE_DIARY":
        diary = result if isinstance(result, dict) else {}
        return "diary", f"diary {payload.get('date','')} {payload.get('content','') or diary.get('content','')}", {"diary_id": diary.get("id")}

    if op_type == "CREATE_INVENTORY_ITEM":
        item = result if isinstance(result, dict) else {}
        return "inventory", f"inventory item {payload.get('name') or item.get('name')} category={payload.get('category','other')} quantity={payload.get('quantity', 1)}", {"item_id": item.get("id")}
    if op_type == "UPDATE_INVENTORY_ITEM":
        item = result if isinstance(result, dict) else {}
        return "inventory", f"updated inventory item {payload.get('item_id')} {payload}", {"item_id": payload.get("item_id") or item.get("id")}
    if op_type == "INVENTORY_DELTA":
        item = result.get("item", {}) if isinstance(result, dict) else {}
        movement = result.get("movement", {}) if isinstance(result, dict) else {}
        return "inventory", f"inventory {item.get('name', payload.get('item_id'))} changed by {payload.get('quantity_delta')} operation={payload.get('operation','adjust')} reason={payload.get('reason','')}", {"item_id": payload.get("item_id"), "movement_id": movement.get("id")}
    if op_type == "INVENTORY_MOVE":
        movement = result.get("movement", {}) if isinstance(result, dict) else {}
        return "inventory", f"inventory item {payload.get('item_id')} movement {payload.get('movement_type') or payload.get('operation','move')} delta={payload.get('quantity_delta',0)} reason={payload.get('reason','')}", {"item_id": payload.get("item_id"), "movement_id": movement.get("id")}
    if op_type == "CREATE_MEAL_RECORD":
        meal = result if isinstance(result, dict) else {}
        foods = payload.get("food_items") or meal.get("food_items") or []
        return "meal", f"meal {payload.get('meal_type')} foods={foods} notes={payload.get('notes','')}", {"meal_id": meal.get("id"), "event_id": payload.get("event_id")}
    if op_type == "CREATE_LIFE_ARC":
        arc = result if isinstance(result, dict) else {}
        return "life_arc", f"life arc {payload.get('title') or arc.get('title')} status={payload.get('status','active')} progress={payload.get('progress', 0)}", {"arc_id": arc.get("id")}
    if op_type == "CREATE_GOAL":
        goal = result if isinstance(result, dict) else {}
        return "goal", f"goal {payload.get('title') or goal.get('title')} status={payload.get('status','active')} progress={payload.get('progress', 0)}", {"goal_id": goal.get("id"), "arc_id": payload.get("arc_id")}
    if op_type == "UPDATE_GOAL_PROGRESS":
        goal = result.get("goal", {}) if isinstance(result, dict) else {}
        return "goal_progress", f"goal {payload.get('goal_id')} progress={goal.get('progress', payload.get('progress'))} reason={payload.get('reason','')}", {"goal_id": payload.get("goal_id"), "progress_entry_id": result.get("progress_entry_id") if isinstance(result, dict) else None}
    if op_type == "LINK_EVENT_TO_GOAL":
        link = result if isinstance(result, dict) else {}
        return "goal_event_link", f"event {payload.get('event_id')} linked to goal {payload.get('goal_id')} role={payload.get('role','supports')}", {"goal_id": payload.get("goal_id"), "event_id": payload.get("event_id"), "link_id": link.get("id")}
    if op_type == "CREATE_EVENT_DEPENDENCY":
        dep = result if isinstance(result, dict) else {}
        return "event_dependency", f"event {payload.get('event_id')} depends on {payload.get('depends_on_event_id')} type={payload.get('dependency_type','finish_to_start')}", {"dependency_id": dep.get("id"), "event_id": payload.get("event_id"), "depends_on_event_id": payload.get("depends_on_event_id")}
    if op_type == "DECOMPOSE_EVENT":
        res = result if isinstance(result, dict) else {}
        return "event_decomposition", f"event {payload.get('parent_event_id')} decomposed into {len(res.get('child_event_ids', []) or payload.get('children', []))} child events", {"decomposition_id": res.get("decomposition_id"), "parent_event_id": payload.get("parent_event_id"), "child_event_ids": res.get("child_event_ids", [])}
    if op_type == "CREATE_REFLECTION":
        ref = result if isinstance(result, dict) else {}
        return "reflection", f"reflection {payload.get('reflection_type','event_review')} {payload.get('content','')}", {"reflection_id": ref.get("id"), "target_kind": payload.get("target_kind"), "target_id": payload.get("target_id")}
    if op_type == "RECOMPUTE_EVENT_PROGRESS":
        res = result if isinstance(result, dict) else {}
        return "event_progress", f"event {payload.get('event_id')} recomputed progress={res.get('computed_progress','')}", {"event_id": payload.get("event_id")}
    if op_type == "CREATE_GOAL_MILESTONE":
        ms = result if isinstance(result, dict) else {}
        return "goal_milestone", f"goal milestone {payload.get('title')} target={payload.get('target_progress','')}", {"milestone_id": ms.get("id"), "goal_id": payload.get("goal_id")}
    if op_type == "AUTONOMY_CREATE_GOAL_STEP":
        res = result if isinstance(result, dict) else {}
        ev = res.get("event", {}) if isinstance(res, dict) else {}
        return "autonomy", f"autonomy step {payload.get('title') or ev.get('title')} for goal {payload.get('goal_id')}", {"goal_id": payload.get("goal_id"), "event_id": ev.get("id"), "schedule_block_id": (res.get("schedule_block") or {}).get("id") if isinstance(res.get("schedule_block"), dict) else None}
    if op_type == "AUTONOMY_SCHEDULE_EVENT":
        res = result if isinstance(result, dict) else {}
        block = res.get("schedule_block", {}) if isinstance(res, dict) else {}
        return "autonomy", f"autonomy scheduled event {payload.get('event_id')} from {payload.get('start')} to {payload.get('end')}", {"event_id": payload.get("event_id"), "schedule_block_id": block.get("id")}
    if op_type == "AUTONOMY_CREATE_GOAL_STEP":
        res = result if isinstance(result, dict) else {}
        event = res.get("event", {}) if isinstance(res, dict) else {}
        return "autonomy_event", f"autonomy created goal step {payload.get('title') or event.get('title')} for goal {payload.get('goal_id')}", {"event_id": event.get("id"), "goal_id": payload.get("goal_id")}
    if op_type == "AUTONOMY_SCHEDULE_EVENT":
        res = result if isinstance(result, dict) else {}
        block = res.get("schedule_block", {}) if isinstance(res, dict) else {}
        return "autonomy_schedule", f"autonomy scheduled event {payload.get('event_id')} from {payload.get('start')} to {payload.get('end')}", {"event_id": payload.get("event_id"), "schedule_block_id": block.get("id")}
    if op_type == "CREATE_SERENDIPITY_EVENT":
        ser = result if isinstance(result, dict) else {}
        ev = ser.get("event", {}) if isinstance(ser, dict) else {}
        return "serendipity", f"serendipity {payload.get('title') or ser.get('title')} type={payload.get('serendipity_type','minor_discovery')}", {"serendipity_id": ser.get("id"), "event_id": ev.get("id"), "trigger_event_id": payload.get("trigger_event_id")}
    if op_type == "CREATE_SLEEP_PLAN":
        res = result if isinstance(result, dict) else {}
        plan = res.get("sleep_plan", {}) if isinstance(res, dict) else {}
        event = res.get("event", {}) if isinstance(res, dict) else {}
        return "sleep_plan", f"sleep plan {plan.get('plan_type', payload.get('plan_type', payload.get('sleep_type','core_sleep')))} from {plan.get('planned_sleep_at', payload.get('planned_sleep_at', payload.get('planned_start','')))} to {plan.get('planned_wake_at', payload.get('planned_wake_at', payload.get('planned_end','')))}", {"sleep_plan_id": plan.get("id"), "event_id": event.get("id"), "schedule_block_id": (res.get("schedule_block") or {}).get("id") if isinstance(res.get("schedule_block"), dict) else None}
    if op_type == "START_SLEEP_SESSION":
        res = result if isinstance(result, dict) else {}
        session = res.get("sleep_session", {}) if isinstance(res, dict) else {}
        return "sleep_session", f"sleep session started {session.get('session_type','sleep')} at {session.get('actual_sleep_at','')}", {"sleep_session_id": session.get("id"), "sleep_plan_id": session.get("sleep_plan_id"), "event_id": session.get("event_id"), "schedule_block_id": session.get("schedule_block_id")}
    if op_type == "END_SLEEP_SESSION":
        res = result if isinstance(result, dict) else {}
        session = res.get("sleep_session", {}) if isinstance(res, dict) else {}
        return "sleep_session", f"sleep session ended wake_cause={session.get('wake_cause','')} duration={session.get('actual_duration_minutes','')} minutes", {"sleep_session_id": session.get("id"), "sleep_plan_id": session.get("sleep_plan_id"), "event_id": session.get("event_id"), "schedule_block_id": session.get("schedule_block_id")}
    if op_type == "WAKE_SLEEP_SESSION":
        res = result if isinstance(result, dict) else {}
        session = res.get("sleep_session") if isinstance(res, dict) else None
        session = session or {}
        sleep_plan = res.get("sleep_plan") if isinstance(res, dict) else None
        sleep_plan = sleep_plan or {}
        if res.get("missed"):
            return "sleep_session", f"sleep plan {payload.get('sleep_plan_id') or sleep_plan.get('id')} missed / all-nighter", {"sleep_session_id": None, "sleep_plan_id": payload.get("sleep_plan_id") or sleep_plan.get("id"), "sleep_day_state": res.get("sleep_day_state")}
        return "sleep_session", f"sleep session woke wake_cause={session.get('wake_cause','')} duration={session.get('actual_duration_minutes','')} minutes", {"sleep_session_id": session.get("id"), "sleep_plan_id": session.get("sleep_plan_id") or payload.get("sleep_plan_id"), "event_id": session.get("event_id"), "schedule_block_id": session.get("schedule_block_id"), "resource_effects": res.get("resource_effects", {}), "sleep_day_state": res.get("sleep_day_state")}
    if op_type == "INTERRUPT_SLEEP_SESSION":
        res = result if isinstance(result, dict) else {}
        sess = (res.get("wake") or {}).get("sleep_session") if isinstance(res.get("wake"), dict) else res.get("sleep_session")
        sess = sess or {}
        return "sleep_session", f"sleep session interrupted reason={payload.get('reason','')}", {"sleep_session_id": sess.get("id") or payload.get("sleep_session_id"), "interruption_id": res.get("interruption_id")}
    if op_type == "SKIP_SLEEP_PLAN":
        res = result if isinstance(result, dict) else {}
        plan = res.get("sleep_plan", {}) if isinstance(res, dict) else {}
        return "sleep_plan", f"sleep plan {payload.get('sleep_plan_id')} skipped reason={payload.get('reason','')}", {"sleep_plan_id": payload.get("sleep_plan_id") or plan.get("id")}
    if op_type == "RUN_DREAM":
        res = result if isinstance(result, dict) else {}
        run = (res.get("dream_run") or {}) if isinstance(res, dict) else {}
        entry = (res.get("entry") or {}) if isinstance(res, dict) else {}
        intent = (res.get("proactive_intent") or {}) if isinstance(res, dict) else {}
        return "dream", f"dream run {run.get('id', payload.get('sleep_session_id',''))} status={run.get('status','')} findings={run.get('findings_count', 0)}", {"dream_run_id": run.get("id"), "dream_entry_id": entry.get("id") or run.get("created_entry_id"), "sleep_session_id": run.get("sleep_session_id") or payload.get("sleep_session_id"), "proactive_intent_id": intent.get("id") or run.get("proactive_intent_id"), "truth_layer": "dream_symbolic"}
    if op_type == "CREATE_DREAM_ENTRY":
        entry = result if isinstance(result, dict) else {}
        return "dream", f"dream entry {payload.get('summary') or entry.get('summary') or 'dream_symbolic'}", {"dream_entry_id": entry.get("id"), "dream_run_id": payload.get("dream_run_id") or entry.get("dream_run_id"), "sleep_session_id": payload.get("sleep_session_id") or entry.get("sleep_session_id"), "truth_layer": entry.get("truth_layer", "dream_symbolic")}

    if op_type == "CREATE_PROACTIVE_INTENT":
        intent = result if isinstance(result, dict) else {}
        return "proactive", f"proactive intent {payload.get('summary','')}", {"intent_id": intent.get("id")}
    if op_type == "EVALUATE_PROACTIVE_INTENT":
        evaluated = result.get("evaluated", []) if isinstance(result, dict) else []
        claims = []
        evidence = {"evaluations": []}
        for item in evaluated:
            claims.append(f"intent {item.get('intent_id')} decision {item.get('decision')} reason {item.get('reason','')}")
            evidence["evaluations"].append({"evaluation_id": item.get("evaluation_id"), "intent_id": item.get("intent_id"), "decision": item.get("decision"), "outbox_id": (item.get("outbox") or {}).get("id")})
        return "proactive", "proactive evaluation " + "; ".join(claims), evidence
    if op_type == "MARK_PROACTIVE_SENT":
        outbox = result.get("outbox", {}) if isinstance(result, dict) else {}
        return "proactive", f"proactive message sent outbox={payload.get('outbox_id')}", {"outbox_id": payload.get("outbox_id"), "intent_id": outbox.get("intent_id")}
    if op_type == "SUPPRESS_PROACTIVE_INTENT":
        intent = result if isinstance(result, dict) else {}
        return "proactive", f"proactive intent {payload.get('intent_id')} suppressed reason={payload.get('reason','')}", {"intent_id": payload.get("intent_id") or intent.get("id")}
    if op_type == "EXPIRE_PROACTIVE_INTENTS":
        return "proactive", f"expired proactive intents count={result.get('count', 0) if isinstance(result, dict) else 0}", {"expired": result.get("expired", []) if isinstance(result, dict) else []}
    return "op", f"{op_type} {payload}", {}


def create_commit_receipt(conn, owner_kind: str, owner_id: str, transaction_id: str,
                          trace_id: str | None, session_id: str | None, turn_id: str | None,
                          op_results: list[dict[str, Any]]) -> dict[str, Any]:
    receipt_id = new_id("receipt")
    summary = {"op_count": len(op_results)}
    conn.execute(
        """INSERT INTO commit_receipts(id, transaction_id, owner_kind, owner_id, session_id, turn_id, trace_id, summary_json)
              VALUES(?,?,?,?,?,?,?,?)""",
        (receipt_id, transaction_id, owner_kind, owner_id, session_id, turn_id, trace_id, dumps(summary)),
    )
    facts = []
    for item in op_results:
        kind, claim, evidence = _fact_text_for(item["type"], item.get("payload") or {}, item.get("result"))
        fact_id = new_id("fact")
        conn.execute(
            """INSERT INTO commit_receipt_facts(id, receipt_id, transaction_id, owner_kind, owner_id, fact_kind, claim_text, evidence_json)
                  VALUES(?,?,?,?,?,?,?,?)""",
            (fact_id, receipt_id, transaction_id, owner_kind, owner_id, kind, claim, dumps(evidence)),
        )
        facts.append({"id": fact_id, "kind": kind, "claim": claim, "evidence": evidence})
    conn.execute("UPDATE life_transactions SET receipt_id=? WHERE id=?", (receipt_id, transaction_id))
    return {"receipt_id": receipt_id, "facts": facts, "summary": summary}


def receipt_facts_for_turn(conn, owner_kind: str, owner_id: str, session_id: str | None, turn_id: str | None) -> list[dict[str, Any]]:
    if not session_id or not turn_id:
        return []
    rows = conn.execute(
        """SELECT f.* FROM commit_receipt_facts f
              JOIN commit_receipts r ON r.id=f.receipt_id
             WHERE f.owner_kind=? AND f.owner_id=? AND r.session_id=? AND r.turn_id=?
             ORDER BY f.created_at""",
        (owner_kind, owner_id, session_id, turn_id),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["evidence"] = loads(d.pop("evidence_json"), {})
        out.append(d)
    return out


def canonical_fact_texts(conn, owner_kind: str, owner_id: str, limit: int = 100) -> list[str]:
    texts: list[str] = []
    for r in conn.execute(
        "SELECT title, description, status, planned_start FROM events WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
        (owner_kind, owner_id, limit),
    ).fetchall():
        texts.append(" ".join(str(x or "") for x in (r["title"], r["description"], r["status"], r["planned_start"])))
    for r in conn.execute(
        "SELECT content FROM memories WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, limit),
    ).fetchall():
        texts.append(str(r["content"] or ""))
    for r in conn.execute(
        "SELECT resource_key, current_value, unit FROM resource_accounts WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
        (owner_kind, owner_id, limit),
    ).fetchall():
        texts.append(f"resource {r['resource_key']} {r['current_value']} {r['unit'] or ''}")
    try:
        for r in conn.execute(
            "SELECT title, status, progress, target_date FROM goals WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"goal {r['title']} {r['status']} progress {r['progress']} target {r['target_date'] or ''}")
        for r in conn.execute(
            "SELECT title, status, stage, progress FROM life_arcs WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"life arc {r['title']} {r['status']} stage {r['stage']} progress {r['progress']}")
    except Exception:
        pass
    try:
        for r in conn.execute(
            "SELECT title, description, status, progress, target_date FROM goals WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"goal {r['title']} {r['description'] or ''} {r['status']} progress {r['progress']} target {r['target_date'] or ''}")
        for r in conn.execute(
            "SELECT title, description, status, progress, current_stage FROM life_arcs WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"life arc {r['title']} {r['description'] or ''} {r['status']} stage {r['current_stage'] or ''} progress {r['progress']}")
        for r in conn.execute(
            "SELECT content FROM reflection_entries WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"reflection {r['content']}")
    except Exception:
        pass
    try:
        for r in conn.execute(
            "SELECT name, category, quantity, unit, condition, location FROM inventory_items WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"inventory {r['name']} {r['category']} quantity {r['quantity']} {r['unit'] or ''} {r['condition'] or ''} {r['location'] or ''}")
        for r in conn.execute(
            "SELECT meal_type, eaten_at, food_items_json, notes FROM meal_records WHERE owner_kind=? AND owner_id=? ORDER BY eaten_at_ts DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"meal {r['meal_type']} {r['eaten_at']} {r['food_items_json']} {r['notes'] or ''}")
    except Exception:
        pass
    for r in conn.execute(
        "SELECT name, category, quantity, unit, condition, location FROM inventory_items WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
        (owner_kind, owner_id, limit),
    ).fetchall():
        texts.append(f"inventory {r['name']} {r['category']} quantity {r['quantity']} {r['unit'] or ''} condition {r['condition'] or ''} location {r['location'] or ''}")
    try:
        for r in conn.execute(
            "SELECT title, description, status, progress, target_date FROM goals WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"goal {r['title']} {r['description'] or ''} status {r['status']} progress {r['progress']} target {r['target_date'] or ''}")
        for r in conn.execute(
            "SELECT title, description, status, progress, current_phase FROM life_arcs WHERE owner_kind=? AND owner_id=? ORDER BY updated_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"life arc {r['title']} {r['description'] or ''} status {r['status']} progress {r['progress']} phase {r['current_phase'] or ''}")
        for r in conn.execute(
            "SELECT content, reflection_type, target_kind, target_id FROM life_reflections WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"reflection {r['reflection_type']} target {r['target_kind'] or ''}:{r['target_id'] or ''} {r['content']}")
    except Exception:
        pass

    try:
        for r in conn.execute(
            "SELECT title, description, serendipity_type, intensity FROM serendipity_events WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"serendipity {r['title']} {r['description'] or ''} type {r['serendipity_type']} intensity {r['intensity']}")
    except Exception:
        pass
    try:
        for r in conn.execute(
            "SELECT plan_type, status, planned_sleep_at, planned_wake_at FROM sleep_plans WHERE owner_kind=? AND owner_id=? ORDER BY planned_sleep_at_ts DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"sleep plan {r['plan_type']} {r['status']} {r['planned_sleep_at']} {r['planned_wake_at']}")
        for r in conn.execute(
            "SELECT session_type, status, actual_sleep_at, actual_wake_at, actual_duration_minutes, wake_cause FROM sleep_sessions WHERE owner_kind=? AND owner_id=? ORDER BY COALESCE(actual_sleep_at_ts, actual_wake_at_ts, unixepoch(created_at)) DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall():
            texts.append(f"sleep session {r['session_type']} {r['status']} {r['actual_sleep_at']} {r['actual_wake_at']} duration {r['actual_duration_minutes']} wake {r['wake_cause']}")
    except Exception:
        pass
    return texts


_STOP_CJK = set("我你他她它的了是在和有就都也又很更要会能这那今天明天昨天上午中午下午晚上现在已经一个一下左右大概")
_ACTION_GROUPS = {
    # Keep these as phrase lists instead of character sets.  v0.99 fixed a
    # bug where set("午餐") made the single character "午" count as an eating
    # action, so unrelated phrases such as "明天下午买裙子" could support
    # "今天中午吃饭".  Single-character entries below are intentionally strong
    # verbs, not generic time/object characters.
    "eat": ["吃", "喝", "用餐", "午餐", "晚餐", "早餐", "外卖", "咖喱饭", "吃饭"],
    "buy": ["买", "购买", "付款", "消费", "支出", "买了", "买到", "花了"],
    "go": ["去", "到了", "到达", "走", "逛", "出门", "通勤", "旅行", "去了"],
    "work": ["做", "处理", "完成", "复查", "修补", "确认", "委托", "单子", "工作"],
    "study": ["学习", "复习", "练习", "考试", "教材", "模拟题", "章节"],
    "sleep": ["睡", "休息", "起床", "午睡"],
    "exercise": ["健身", "跑步", "拉伸", "散步", "运动", "锻炼"],
    "postpone": ["推迟", "延期", "改期", "取消", "跳过", "失败", "部分完成"],
}
_OBJECT_GROUPS = {
    "food": ["饭", "咖喱", "茶", "咖啡", "面包", "早餐", "午餐", "晚餐", "甜水", "外卖"],
    "clothing": ["裙", "裙子", "衣服", "外套", "鞋", "包", "衣柜"],
    "money": ["钱", "钱包", "日元", "收入", "报酬", "余额", "预算", "花了", "支出"],
    "work_item": ["委托", "单子", "节点", "符纸", "朱砂", "铃铛", "结果缝", "雨棚巷", "第七城"],
    "study_item": ["考试", "教材", "章节", "模拟题", "笔记"],
    "place": ["巴黎", "商场", "学校", "公司", "家里", "雨棚巷"],
}


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    latin = set(re.findall(r"[a-z0-9_.:-]{2,}", text))
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    cjk_bi = {"".join(cjk_chars[i:i+2]) for i in range(max(0, len(cjk_chars)-1))}
    # Salient single characters are strong verbs/objects only; time/pronouns
    # are filtered by _STOP_CJK and generic substring overlap is never enough.
    salient_vocab = {"吃", "喝", "买", "去", "做", "学", "睡", "练", "跑", "裙", "饭", "钱"}
    salient = {ch for ch in cjk_chars if ch in salient_vocab and ch not in _STOP_CJK}
    return {t for t in latin | cjk_bi | salient if t.strip() and t not in _STOP_CJK}


def _groups(text: str, group_map: dict[str, list[str]]) -> set[str]:
    lower = (text or "").lower()
    out = set()
    for name, members in group_map.items():
        if any(m and m in lower for m in members):
            out.add(name)
    # English hints.
    if re.search(r"\b(ate|eat|meal|lunch|dinner|breakfast|coffee|tea|curry)\b", lower): out.add("eat" if group_map is _ACTION_GROUPS else "food")
    if re.search(r"\b(bought|buy|spent|purchase|wallet|inventory)\b", lower): out.add("buy" if group_map is _ACTION_GROUPS else "money")
    if re.search(r"\b(went|go|walked|travel)\b", lower): out.add("go" if group_map is _ACTION_GROUPS else "place")
    if re.search(r"\b(study|exam|chapter|textbook)\b", lower): out.add("study" if group_map is _ACTION_GROUPS else "study_item")
    return out


def claim_matches_evidence(claim: str, evidence_texts: list[str]) -> bool:
    """Conservative semantic evidence matcher.

    v0.99+ rule: token overlap alone is not enough, because Chinese claims can
    share only time/pronoun tokens (e.g. 今天/中午/我) while describing a different
    action.  A hard claim with a concrete action must have compatible action
    evidence, and object/domain evidence when present.
    """
    claim_norm = (claim or "").strip().lower()
    if not claim_norm:
        return True
    claim_tokens = _tokens(claim_norm)
    claim_actions = _groups(claim_norm, _ACTION_GROUPS)
    claim_objects = _groups(claim_norm, _OBJECT_GROUPS)
    if not claim_tokens and not claim_actions:
        return False
    for text in evidence_texts:
        text_norm = (text or "").strip().lower()
        if not text_norm:
            continue
        if claim_norm in text_norm or text_norm in claim_norm:
            return True
        ev_actions = _groups(text_norm, _ACTION_GROUPS)
        ev_objects = _groups(text_norm, _OBJECT_GROUPS)
        overlap = claim_tokens & _tokens(text_norm)
        if claim_actions:
            if not (claim_actions & ev_actions):
                continue
            if claim_objects and ev_objects and not (claim_objects & ev_objects):
                continue
            if overlap or claim_objects & ev_objects:
                return True
            continue
        if claim_objects:
            if claim_objects & ev_objects and len(overlap) >= 1:
                return True
            continue
        if len(overlap) >= 3:
            return True
    return False

# Runtime v0.3 canonical aliases -------------------------------------------------

def facts_from_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for item in results:
        kind, claim, evidence = _fact_text_for(item.get("type"), item.get("payload") or {}, item.get("result"))
        facts.append({"fact_kind": kind, "claim_text": claim, "evidence": evidence})
    return facts


def create_receipt(conn, owner_kind: str, owner_id: str, transaction_id: str,
                   session_id: str | None, turn_id: str | None, trace_id: str | None,
                   facts: list[dict[str, Any]]) -> dict[str, Any]:
    # Accept pre-derived facts and persist them in the normalized receipt tables.
    receipt_id = new_id("receipt")
    summary = {"fact_count": len(facts)}
    conn.execute(
        """INSERT INTO commit_receipts(id, transaction_id, owner_kind, owner_id, session_id, turn_id, trace_id, facts_json, summary_json)
              VALUES(?,?,?,?,?,?,?,?,?)""",
        (receipt_id, transaction_id, owner_kind, owner_id, session_id, turn_id, trace_id, dumps(facts), dumps(summary)),
    )
    persisted = []
    for fact in facts:
        fact_id = new_id("fact")
        kind = fact.get("fact_kind") or fact.get("kind") or "fact"
        claim = fact.get("claim_text") or fact.get("claim") or ""
        evidence = fact.get("evidence") or {}
        conn.execute(
            """INSERT INTO commit_receipt_facts(id, receipt_id, transaction_id, owner_kind, owner_id, fact_kind, claim_text, evidence_json)
                  VALUES(?,?,?,?,?,?,?,?)""",
            (fact_id, receipt_id, transaction_id, owner_kind, owner_id, kind, claim, dumps(evidence)),
        )
        persisted.append({"id": fact_id, "kind": kind, "claim": claim, "evidence": evidence})
    return {"receipt_id": receipt_id, "transaction_id": transaction_id, "facts": persisted, "summary": summary}


def get_turn_facts(conn, owner_kind: str, owner_id: str, session_id: str | None, turn_id: str | None) -> list[dict[str, Any]]:
    return receipt_facts_for_turn(conn, owner_kind, owner_id, session_id, turn_id)


def canonical_facts_for_claim(conn, owner_kind: str, owner_id: str, claim: str) -> list[dict[str, Any]]:
    return [{"kind": "canonical", "claim": t, "evidence": {}} for t in canonical_fact_texts(conn, owner_kind, owner_id)]


def claim_supported_by_facts(claim: str, facts: list[dict[str, Any]]) -> bool:
    texts = []
    for f in facts:
        texts.append(str(f.get("claim") or f.get("claim_text") or ""))
        if f.get("evidence"):
            texts.append(dumps(f.get("evidence")))
    return claim_matches_evidence(claim, texts)
