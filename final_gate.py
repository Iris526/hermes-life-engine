"""FinalGate claim/evidence checking and advisory repair UX.

v0.9.5 changes the default gate semantics:

* The gate is advisory by default and never exposes raw gate diagnostics to the
  user unless the operator explicitly selects strict/repair/warn modes.
* Soft plans, intentions, and draft schedules are treated as advisory claims,
  not hard unsupported facts.  This prevents normal "what I'm doing today"
  answers from being incorrectly blocked.
* Unsupported hard claims are written to trace/audit and queued as internal
  feedback for the next model turn.  The model should use that feedback to
  commit LifeOps or rephrase, without showing the diagnostic to the user.
"""

from __future__ import annotations

import re
from typing import Any

from .jsonutil import dumps, loads, pretty
from .receipts import canonical_fact_texts, claim_matches_evidence, receipt_facts_for_turn
from .trace import new_id


# Soft words usually indicate intent, preference, rough planning, or uncertain
# narrative rather than a durable completed fact.  They should guide the model,
# not block the user-visible response.
_SOFT_PLAN_WORDS = {
    "打算", "计划", "准备", "想", "要", "应该", "大概", "可能", "也许", "或许",
    "安排", "打算", "会", "希望", "争取", "预计", "先", "再", "看要不要",
}

# Strong markers indicate something is asserted as already done, already owned,
# a real resource state, or a concrete finished state.
_HARD_DONE_WORDS = {
    "已经", "刚刚", "刚才", "吃了", "买了", "去了", "做完", "完成了", "花了", "用了",
    "写了", "睡了", "练了", "复习了", "整理了", "见了", "收到了", "拿到", "赚了",
    "收入", "买到", "办完", "处理完", "推迟了", "取消了", "改到了", "记进", "记入",
}

_RESOURCE_STATE_WORDS = {
    "我的钱包", "我的衣柜", "我的库存", "我的资源", "我的余额", "我有", "我买过", "我拥有",
}

_CJK_SELF_OR_LIFE_RE = re.compile(r"(我|我的|今天|明天|昨天|中午|上午|下午|晚上|日程|安排|计划|钱包|衣柜|库存|资源|目标|委托|单子)")
_EN_SELF_RE = re.compile(r"\b(I|my|today|tomorrow|yesterday|schedule|plan|wallet|inventory|goal)\b", re.I)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?\n])|(?:^\s*[-•]\s*)", re.M)


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().split())


def _split_candidate_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for raw in re.split(r"(?<=[。！？!?])|\n+", text or ""):
        raw = raw.strip()
        if not raw:
            continue
        # Long paragraphs often contain a heading followed by bullets.  Keep the
        # first chunk bounded so one soft planning word does not swallow a whole
        # schedule list as a hard claim.
        for piece in re.split(r"(?<=；)|(?<=;)|(?<=：)|(?<=:)", raw):
            piece = piece.strip(" \t-•")
            if piece:
                parts.append(piece[:300])
    return parts[:30]


def _is_life_related(sentence: str) -> bool:
    return bool(_CJK_SELF_OR_LIFE_RE.search(sentence) or _EN_SELF_RE.search(sentence))


def classify_life_claim(sentence: str) -> dict[str, Any] | None:
    """Classify a candidate sentence.

    Returns None for non-life text.  Returned severity is either:
      * hard: completed/resource/canonical-looking fact that should have evidence
      * soft: plan/intent/draft that should at most become an advisory reminder
    """
    s = _normalize(sentence)
    if not s or len(s) < 4:
        return None
    if "LifeEngine" in s or "CommitReceipt" in s or "canonical state" in s:
        return None
    if not _is_life_related(s):
        return None

    lower = s.lower()
    # English simple hard assertions.
    if re.search(r"\bI\s+(ate|bought|went|finished|completed|spent|slept|studied|wrote|exercised|postponed|cancelled)\b", s, re.I):
        return {"claim": s, "category": "completed_or_past_fact", "severity": "hard"}
    if re.search(r"\bI\s+(will|plan|planned|intend|want|might|may|should|am going to)\b", s, re.I):
        return {"claim": s, "category": "plan_or_intent", "severity": "soft"}

    has_soft = any(w in s for w in _SOFT_PLAN_WORDS)
    has_hard = any(w in s for w in _HARD_DONE_WORDS)
    has_resource_state = any(w in s for w in _RESOURCE_STATE_WORDS)

    # A sentence like "我今天就更要好好做了" is motivation/intent, not a
    # durable completed fact.  "今天安排大概是" is likewise a draft schedule.
    if has_soft and not has_hard:
        return {"claim": s, "category": "plan_or_intent", "severity": "soft"}

    if has_hard or has_resource_state:
        return {"claim": s, "category": "completed_or_state_fact", "severity": "hard"}

    # Very concrete date + life-domain statements can be advisory, but should
    # not block by default because they may simply be answer framing.
    if re.search(r"(今天|明天|昨天|中午|上午|下午|晚上).*(单子|委托|日程|安排|计划|目标)", s):
        return {"claim": s, "category": "schedule_context", "severity": "soft"}

    return None


def detect_life_claim_items(text: str, limit: int = 12) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sent in _split_candidate_sentences(text):
        item = classify_life_claim(sent)
        if not item:
            continue
        claim = item["claim"][:280]
        if claim in seen:
            continue
        item["claim"] = claim
        items.append(item)
        seen.add(claim)
        if len(items) >= limit:
            break
    return items


def detect_life_claims(text: str, limit: int = 8) -> list[str]:
    return [i["claim"] for i in detect_life_claim_items(text, limit=limit)]


def _suggest_life_ops_for_claim(claim: str, owner_kind: str, category: str | None = None) -> list[dict[str, Any]]:
    """Return conservative LifeOps skeletons the model/user can commit.

    These are intentionally incomplete suggestions, not automatic mutations.
    """
    c = claim or ""
    source = "agent_prediction" if owner_kind == "agent" else "user_reported"
    ops: list[dict[str, Any]] = []
    title = c[:80]
    if category in {"plan_or_intent", "schedule_context"} or any(w in c for w in ["明天", "计划", "准备", "will", "plan", "scheduled", "下周", "过几天", "安排"]):
        ops.append({
            "type": "CREATE_EVENT",
            "payload": {
                "title": title,
                "event_type": "other",
                "status": "planned",
                "source": source,
                "description": "Suggested by FinalGate from a plan/intent claim. Fill planned_start/planned_end/resource_costs before committing if this should become durable state.",
            },
        })
    elif any(w in c for w in ["吃", "午饭", "晚饭", "早餐", "晚餐", "ate", "meal"]):
        if owner_kind == "agent":
            ops.append({
                "type": "CREATE_EVENT",
                "payload": {
                    "title": title,
                    "event_type": "meal",
                    "status": "completed",
                    "source": "agent_retro_assertion",
                    "description": "Suggested by FinalGate from an unsupported meal/life claim.",
                },
            })
        else:
            ops.append({
                "type": "CREATE_MEAL_RECORD",
                "payload": {
                    "meal_type": "meal",
                    "food_items": [],
                    "notes": claim,
                    "source": "user_confirmed",
                },
            })
    elif any(w in c for w in ["买", "花", "钱包", "衣柜", "库存", "bought", "spent", "inventory"]):
        ops.append({
            "type": "CREATE_EVENT",
            "payload": {
                "title": title,
                "event_type": "purchase",
                "status": "completed",
                "source": "agent_retro_assertion" if owner_kind == "agent" else "user_confirmed",
                "description": "Suggested by FinalGate from an unsupported purchase/resource claim. Add RESOURCE_DELTA/CREATE_INVENTORY_ITEM if this is real.",
            },
        })
    else:
        ops.append({
            "type": "CREATE_MEMORY",
            "payload": {
                "content": claim,
                "memory_type": "episodic",
                "source": "model_output" if owner_kind == "agent" else "user_confirmed",
                "importance": 50,
                "confidence": 0.6,
            },
        })
    return ops[:3]


def _evidence_sample(texts: list[str], limit: int = 5) -> list[str]:
    out = []
    for text in texts:
        text = _normalize(str(text or ""))[:240]
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def evaluate_final_response(conn, owner_kind: str, owner_id: str, response_text: str,
                            session_id: str | None, turn_id: str | None) -> dict[str, Any]:
    claim_items = detect_life_claim_items(response_text)
    turn_facts = receipt_facts_for_turn(conn, owner_kind, owner_id, session_id, turn_id)
    turn_texts = [str(f.get("claim_text") or f.get("claim") or "") for f in turn_facts]
    canonical_texts = canonical_fact_texts(conn, owner_kind, owner_id)
    evidence_texts = turn_texts + canonical_texts
    supported: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    advisory: list[dict[str, Any]] = []
    for item in claim_items:
        claim = item["claim"]
        category = item.get("category")
        severity = item.get("severity", "soft")
        matched = claim_matches_evidence(claim, evidence_texts)
        row = {"claim": claim, "category": category, "severity": severity}
        if matched:
            row["match"] = "receipt_or_canonical"
            supported.append(row)
        elif severity == "hard":
            row["suggested_ops"] = _suggest_life_ops_for_claim(claim, owner_kind, category)
            unsupported.append(row)
        else:
            row["suggested_ops"] = _suggest_life_ops_for_claim(claim, owner_kind, category)
            advisory.append(row)
    return {
        "ok": not unsupported,
        "claims": [i["claim"] for i in claim_items],
        "claim_items": claim_items,
        "supported": supported,
        "unsupported": unsupported,
        "advisory": advisory,
        "turn_fact_count": len(turn_facts),
        "canonical_fact_count": len(canonical_texts),
        "evidence_sample": _evidence_sample(evidence_texts),
    }


def write_final_gate_report(conn, owner_kind: str, owner_id: str, session_id: str | None,
                            turn_id: str | None, mode: str, status: str,
                            response_text: str, report: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
    report_id = new_id("fg")
    conn.execute(
        """INSERT INTO final_gate_reports(
               id, owner_kind, owner_id, session_id, turn_id, trace_id, mode, status,
               response_preview, claims_json, unsupported_json, supported_json,
               suggested_ops_json, repair_json
             ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            report_id, owner_kind, owner_id, session_id, turn_id, trace_id, mode, status,
            (response_text or "")[:1000],
            dumps(report.get("claims") or []),
            dumps(report.get("unsupported") or []),
            dumps(report.get("supported") or []),
            dumps([op for item in (report.get("unsupported", []) + report.get("advisory", [])) for op in (item.get("suggested_ops") or [])]),
            dumps({
                "advisory": report.get("advisory", []),
                "evidence_sample": report.get("evidence_sample", []),
                "turn_fact_count": report.get("turn_fact_count", 0),
                "canonical_fact_count": report.get("canonical_fact_count", 0),
            }),
        ),
    )
    report["report_id"] = report_id
    return report


def enqueue_final_gate_feedback(conn, owner_kind: str, owner_id: str, report: dict[str, Any],
                                session_id: str | None, turn_id: str | None) -> dict[str, Any] | None:
    unsupported = report.get("unsupported") or []
    advisory = report.get("advisory") or []
    if not unsupported and not advisory:
        return None
    fid = new_id("fgfb")
    hard = [i.get("claim") for i in unsupported[:3]]
    soft = [i.get("claim") for i in advisory[:3]]
    msg = (
        "FinalGate internal feedback from previous turn. Do NOT show this diagnostic to the user. "
        "Use it only to decide whether to call life_commit or to phrase future content as tentative. "
        f"Unsupported hard claims: {hard}. Advisory soft claims: {soft}. "
        f"Report id: {report.get('report_id')}."
    )
    conn.execute(
        """INSERT INTO final_gate_feedback_queue(id, owner_kind, owner_id, session_id, turn_id, report_id, message)
              VALUES(?,?,?,?,?,?,?)""",
        (fid, owner_kind, owner_id, session_id, turn_id, report.get("report_id"), msg),
    )
    return {"id": fid, "message": msg, "report_id": report.get("report_id")}


def consume_final_gate_feedback(conn, owner_kind: str, owner_id: str, limit: int = 3) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM final_gate_feedback_queue
             WHERE owner_kind=? AND owner_id=? AND status='pending'
             ORDER BY created_at ASC LIMIT ?""",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = [dict(r) for r in rows]
    for item in out:
        conn.execute(
            "UPDATE final_gate_feedback_queue SET status='delivered', delivered_at=datetime('now') WHERE id=?",
            (item["id"],),
        )
    return out


def list_final_gate_reports(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM final_gate_reports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in ["claims_json", "unsupported_json", "supported_json", "suggested_ops_json", "repair_json"]:
            d[key[:-5] if key.endswith("_json") else key] = loads(d.pop(key), [] if key != "repair_json" else {})
        out.append(d)
    return out


def get_final_gate_report(conn, report_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM final_gate_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for key in ["claims_json", "unsupported_json", "supported_json", "suggested_ops_json", "repair_json"]:
        d[key[:-5] if key.endswith("_json") else key] = loads(d.pop(key), [] if key != "repair_json" else {})
    return d


def final_gate_intervention_count(conn, owner_kind: str, owner_id: str, session_id: str | None, turn_id: str | None) -> int:
    if not session_id or not turn_id:
        return 0
    row = conn.execute(
        """SELECT COUNT(*) FROM final_gate_reports
             WHERE owner_kind=? AND owner_id=? AND session_id=? AND turn_id=?
               AND status IN ('blocked','repaired','warned')""",
        (owner_kind, owner_id, session_id, turn_id),
    ).fetchone()
    return int(row[0] if row else 0)


def build_repair_message(report: dict[str, Any], mode: str = "strict") -> str:
    unsupported = report.get("unsupported") or []
    advisory = report.get("advisory") or []
    supported = report.get("supported") or []
    if not unsupported and not advisory:
        return ""
    lines: list[str] = []
    if mode == "warn":
        lines.append("\n\n（LifeEngine 提醒：这次回复里有少量生活状态还没有完整证据，已写入 trace，不影响本次对话。）")
        return "".join(lines)
    if mode == "repair":
        lines.append("我先保守修正一下：有些生活细节还没有完全写入 LifeEngine 状态，所以我会把它们当作计划/想法，而不是已经确认的事实。")
    else:
        lines.append("LifeEngine final gate 发现生活事实缺少证据。")
    if supported:
        lines.append("\n可以确认的内容：")
        for item in supported[:3]:
            lines.append(f"- {item.get('claim')}")
    if unsupported:
        lines.append("\n需要先提交或改写的硬事实：")
        for idx, item in enumerate(unsupported[:5], 1):
            lines.append(f"{idx}. {item.get('claim')}")
    if advisory:
        lines.append("\n仅作为提醒的计划/意图：")
        for idx, item in enumerate(advisory[:5], 1):
            lines.append(f"{idx}. {item.get('claim')}")
    suggested = [op for item in unsupported for op in (item.get("suggested_ops") or [])]
    if suggested:
        lines.append("\n建议 LifeOps 草案：")
        lines.append("```json")
        lines.append(pretty({"commit_type": "ops", "ops": suggested[:5]}))
        lines.append("```")
    rid = report.get("report_id")
    if rid:
        lines.append(f"\nTrace：`/life final_gate get {rid}` 可以查看这次记录。")
    return "\n".join(lines)
