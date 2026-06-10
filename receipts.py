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
    if op_type == "COMPLETE_EVENT":
        ev = result.get("event", {}) if isinstance(result, dict) else {}
        return "event_result", f"completed {ev.get('title', payload.get('event_id'))}: {payload.get('summary','completed')}", {"event_id": payload.get("event_id"), "result_id": result.get("result_id") if isinstance(result, dict) else None}
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
    if op_type == "CREATE_SERENDIPITY_EVENT":
        ser = result if isinstance(result, dict) else {}
        ev = ser.get("event", {}) if isinstance(ser, dict) else {}
        return "serendipity", f"serendipity {payload.get('title') or ser.get('title')} type={payload.get('serendipity_type','minor_discovery')}", {"serendipity_id": ser.get("id"), "event_id": ev.get("id"), "trigger_event_id": payload.get("trigger_event_id")}
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
    return texts



_TIME_CJK = set("我今明昨早中晚上午下周月号天刚才过几后前本这")
_ACTION_CJK = set("吃买去做学练睡花用写复习健身散步整理见约开始计划准备推迟取消完成取消改安排")
_OBJECT_CJK = set("饭餐咖喱茶啡裙衣柜钱包钱资源物品库存教材书考试目标进度复盘里程碑小发现意外问题天气雨风雪药日用品早餐午餐晚餐")
_LIFE_CJK = _ACTION_CJK | _OBJECT_CJK
_EN_STOP = {
    "status", "planned", "start", "event", "resource", "goal", "life", "arc",
    "today", "tomorrow", "yesterday", "will", "plan", "planned_start", "none",
    "completed", "reason", "because", "from", "with", "the", "and", "for", "into",
}
_EN_ACTIONS = {
    "ate", "eat", "bought", "buy", "went", "go", "study", "studied", "exercise",
    "exercised", "sleep", "slept", "spend", "spent", "write", "wrote", "postpone",
    "postponed", "cancel", "cancelled", "complete", "completed", "schedule", "scheduled",
}


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    latin = {t for t in re.findall(r"[a-z0-9_.:-]{2,}", text) if t not in _EN_STOP}
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    # Keep adjacent bigrams for object phrases like 咖喱/裙子/考试, but avoid
    # letting pure temporal overlap (今天/中午) prove a life claim.
    cjk_bigrams = {"".join(cjk_chars[i:i+2]) for i in range(max(0, len(cjk_chars)-1))}
    salient = {ch for ch in cjk_chars if ch in _LIFE_CJK}
    return {t for t in latin | cjk_bigrams | salient if t.strip()}


def _cjk_actions(text: str) -> set[str]:
    return {ch for ch in (text or "") if ch in _ACTION_CJK}


def _semantic_tokens(text: str) -> set[str]:
    toks = _tokens(text)
    cjk_sem = {t for t in toks if any(ch in _LIFE_CJK for ch in t)}
    en_sem = {t for t in toks if t in _EN_ACTIONS or t not in _EN_STOP}
    return cjk_sem | en_sem


def claim_matches_evidence(claim: str, evidence_texts: list[str]) -> bool:
    """Conservatively match a final-answer claim against committed evidence.

    Earlier versions allowed two-token overlap, which made unrelated claims like
    “我今天中午买了一条裙子” match evidence for “今天中午吃了咖喱饭” via shared
    temporal tokens.  This matcher requires semantic overlap and, when the claim
    has a concrete CJK action verb, a compatible action verb in the evidence.
    """
    claim_norm = (claim or "").strip().lower()
    if not claim_norm:
        return True
    claim_tokens = _tokens(claim_norm)
    claim_sem = _semantic_tokens(claim_norm)
    claim_actions = _cjk_actions(claim_norm)
    if not claim_tokens:
        return False
    for text in evidence_texts:
        text_norm = (text or "").strip().lower()
        if not text_norm:
            continue
        if claim_norm in text_norm or text_norm in claim_norm:
            return True
        ev_tokens = _tokens(text_norm)
        ev_sem = _semantic_tokens(text_norm)
        ev_actions = _cjk_actions(text_norm)
        sem_overlap = claim_sem & ev_sem
        token_overlap = claim_tokens & ev_tokens
        # A planned canonical event can support a natural-language intention to
        # do/prepare for that same event even when the verbs are not identical
        # (e.g. claim: “我今天更要好好做了：第七城雨棚巷...委托”, evidence:
        # “第七城雨棚巷结界节点复查 ... planned”).  Require strong semantic
        # overlap so unrelated events cannot match merely through time words.
        strong_event_overlap = len(sem_overlap) >= 4 and len(token_overlap) >= 4
        generic_intent_claim = bool(claim_actions & set("做准备计划安排"))
        planned_evidence = any(w in text_norm for w in ["planned", "计划", "准备", "预计", "安排"])
        if strong_event_overlap and (generic_intent_claim or planned_evidence):
            return True
        # If the final claim names a concrete action, evidence with a different
        # concrete action is not enough, even if time words or pronouns overlap.
        # Generic planning/intention verbs above are handled by strong overlap.
        if claim_actions and ev_actions and not (claim_actions & ev_actions):
            continue
        if claim_actions and not ev_actions:
            continue
        if sem_overlap and len(token_overlap) >= 3:
            return True
        if len(sem_overlap) >= 2 and len(token_overlap) >= 2:
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
