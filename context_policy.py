"""Progressive-disclosure context policy for LifeEngine.

LifeEngine's correctness is enforced by runtime code, LifeOps, validators,
receipts, and trace.  This module keeps the prompt/context surface small: it
injects only the turn-relevant capsule and tells the Agent which tools to call
for more details.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .jsonutil import dumps
from .trace import new_id

DEFAULT_BUDGET_CHARS = 5200
MIN_BUDGET_CHARS = 1800
MAX_BUDGET_CHARS = 12000
WORK_COMPACT_PLATFORMS = {"feishu", "lark", "dingtalk", "wecom", "slack", "teams"}


def is_work_compact_platform(platform: str | None) -> bool:
    return (platform or "").strip().lower() in WORK_COMPACT_PLATFORMS


def _platform_from_data(data: dict[str, Any]) -> str | None:
    scope = data.get("owner_scope") or {}
    return scope.get("platform")


def _safe_work_canon_brief(canon_brief: dict[str, Any] | None) -> dict[str, Any]:
    """Return a work-platform-safe Canon capsule.

    Work chats should know that private agent-life/persona/world details exist,
    but should not receive the QQ/Iris raw world/persona text by default.
    Tools can still fetch details explicitly when the user asks in an appropriate context.
    """
    brief = canon_brief or {}
    truth_bindings = ((brief.get("truth_sources") or {}).get("bindings") or {})
    return {
        "private_agent_life_omitted": True,
        "identity": {"private_persona_omitted_on_work_platform": True},
        "worldview": {"private_world_description_omitted_on_work_platform": True},
        "truth_sources": {"bindings": truth_bindings},
    }

CANONICAL_TOOL_MAP = {
    "config": "life_config / life_interface(domain=config)",
    "schedule": "life_schedule / life_interface(domain=schedule)",
    "event": "life_event / life_interface(domain=event)",
    "resource": "life_resource / life_interface(domain=resource)",
    "collection": "life_collection / life_interface(domain=collection)",
    "behavior": "life_behavior / life_interface(domain=behavior)",
    "sleep": "life_sleep / life_reply / life_call",
    "dream": "life_dream",
    "review": "life_review",
    "truth": "life_truth",
    "goal": "life_goal / life_autonomy",
    "proactive": "life_proactive",
    "trace": "life_trace / life_doctor",
}

INTENT_KEYWORDS = {
    "schedule": ["日程", "安排", "今天", "明天", "本周", "几点", "schedule", "calendar"],
    "collection": ["穿", "衣", "鞋", "袜", "配饰", "梳妆", "衣柜", "衣橱", "鞋柜", "closet", "outfit", "wardrobe"],
    "behavior": ["逛街", "买衣服", "购物", "真相源", "映射", "behavior", "source"],
    "sleep": ["睡", "困", "醒", "叫醒", "通宵", "小憩", "午睡", "sleep", "nap"],
    "dream": ["梦", "梦境", "dream"],
    "review": ["review", "待办", "提醒", "处理", "审核", "inbox"],
    "config": ["设定", "世界观", "人设", "canon", "config", "timezone", "天气", "货币"],
    "resource": ["资源", "钱", "灵铢", "精力", "疲劳", "库存", "账本", "resource"],
    "goal": ["目标", "计划", "推进", "goal", "arc"],
    "trace": ["trace", "doctor", "审计", "为什么", "解释"],
}

ALWAYS_SECTIONS = ["control", "realtime", "next_schedule", "required_settings", "tool_map"]


@dataclass
class ContextPolicy:
    mode: str = "slim"
    budget_chars: int = DEFAULT_BUDGET_CHARS
    progressive: bool = True
    include_raw: bool = False

    @classmethod
    def from_control(cls, control: dict[str, Any] | None) -> "ContextPolicy":
        gates = (control or {}).get("module_gates") or {}
        mode = str(gates.get("context_mode") or "slim").lower()
        if mode not in {"micro", "slim", "balanced", "debug"}:
            mode = "slim"
        try:
            budget = int(gates.get("context_budget_chars") or DEFAULT_BUDGET_CHARS)
        except Exception:
            budget = DEFAULT_BUDGET_CHARS
        budget = max(MIN_BUDGET_CHARS, min(MAX_BUDGET_CHARS, budget))
        if mode == "micro":
            budget = min(budget, 2600)
        elif mode == "balanced":
            budget = max(budget, 7600)
        elif mode == "debug":
            budget = max(budget, 11000)
        return cls(mode=mode, budget_chars=budget, progressive=(mode != "debug"), include_raw=(mode == "debug"))


def infer_turn_domains(user_message: str | None) -> list[str]:
    text = (user_message or "").lower()
    domains: list[str] = []
    for domain, words in INTENT_KEYWORDS.items():
        if any(w.lower() in text for w in words):
            domains.append(domain)
    if not domains:
        # Most turns need only a small state capsule.  Details are retrieved by tools.
        domains = []
    return domains[:5]


def _first(items: Iterable[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return list(items or [])[:n]


def _mini_event(e: dict[str, Any]) -> dict[str, Any]:
    return {k: e.get(k) for k in ("id", "title", "status", "event_category", "event_type", "planned_start", "planned_end", "progress") if e.get(k) is not None}


def _mini_resource(a: dict[str, Any]) -> dict[str, Any]:
    return {"key": a.get("resource_key"), "value": a.get("current_value"), "unit": a.get("unit"), "state": a.get("state")}


def _compact_schedule(today_schedule: dict[str, Any] | None, limit: int = 4) -> dict[str, Any]:
    sched = today_schedule or {}
    items = []
    for it in (sched.get("items") or [])[:limit]:
        block = it.get("schedule_block") or it.get("block") or it
        event = it.get("event") or {}
        items.append({
            "time": f"{block.get('start') or block.get('planned_start') or ''} -> {block.get('end') or block.get('planned_end') or ''}",
            "block_status": block.get("status"),
            "event_status": event.get("status"),
            "title": event.get("title") or block.get("title") or block.get("block_type"),
            "event_id": event.get("id") or block.get("event_id"),
            "block_id": block.get("id"),
        })
    return {"summary": sched.get("summary") or {}, "next": items}


def _section_for_domain(domain: str, data: dict[str, Any]) -> dict[str, Any]:
    work_compact = is_work_compact_platform(_platform_from_data(data))
    if domain == "schedule":
        return {"schedule": _compact_schedule(data.get("today_schedule"), limit=8)}
    if domain == "collection":
        return {"collection": {"inventory_sample": data.get("inventory", [])[:8], "hint": "Use life_collection for wardrobe/shoes/socks/accessories/vanity; use resolver before dressing."}}
    if domain == "behavior":
        return {"behavior": {"mappings": data.get("behavior_mappings", [])[:5], "privacy": "private sources are execution-only; user-facing phrase remains public behavior label."}}
    if domain == "sleep":
        return {"sleep": data.get("sleep") or {}, "reply_gate": data.get("reply_gate") or {}}
    if domain == "dream":
        return {"dreams": data.get("dreams") or {}}
    if domain == "review":
        return {"review": {"hint": "Use life_review for human-readable review inbox; Agent can apply safe items only via policy."}}
    if domain == "config":
        canon_brief = _safe_work_canon_brief(data.get("canon_brief")) if work_compact else (data.get("canon_brief") or {})
        return {"required_settings": data.get("required_settings") or {}, "canon_brief": canon_brief}
    if domain == "resource":
        return {"resources": data.get("resources", [])[:12]}
    if domain == "goal":
        return {"goals": data.get("goals", [])[:5], "life_arcs": data.get("arcs", [])[:3], "autonomy": data.get("autonomy", [])[:3]}
    if domain == "trace":
        return {"trace": {"hint": "Use life_trace / life_doctor for explainability; raw trace is not injected by default."}}
    return {}


def render_progressive_context(data: dict[str, Any], user_message: str | None, control: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    policy = ContextPolicy.from_control(control)
    domains = infer_turn_domains(user_message)
    gates = (control or {}).get("module_gates") or {}
    work_compact = is_work_compact_platform(_platform_from_data(data))
    context_profile = "work_compact" if work_compact else "agent_life"

    capsule: dict[str, Any] = {
        "mode": "progressive_slim_context",
        "context_profile": context_profile,
        "private_agent_life_omitted": work_compact,
        "context_policy": {"mode": policy.mode, "budget_chars": policy.budget_chars, "progressive": policy.progressive},
        "owner_scope": data.get("owner_scope") or {},
        "engine": {"state": data.get("engine_state"), "canon_version": data.get("canon_version")},
        "rules": [
            "LifeEngine state is code-enforced; prompts are only turn-local hints.",
            "Do not narrate durable new life facts unless they already exist or you commit LifeOps first.",
            "Use tools for details instead of relying on injected context.",
            "Never expose private behavior sources or internal gate diagnostics to the user.",
        ],
        "realtime": data.get("realtime") or {},
        "required_settings": data.get("required_settings") or {},
        "next_schedule": _compact_schedule(data.get("today_schedule"), limit=3),
        "resources": data.get("resources", [])[:6],
        "active_or_recent_events": data.get("events", [])[:4],
        "tool_map": {d: CANONICAL_TOOL_MAP[d] for d in sorted(set(domains + ["schedule", "config", "event", "trace"])) if d in CANONICAL_TOOL_MAP},
        "domains_injected": domains,
    }
    feedback = data.get("final_gate_feedback") or []
    if feedback and not work_compact:
        capsule["internal_feedback"] = feedback[:2]
        capsule["internal_final_gate_feedback"] = feedback[:2]
    for domain in domains:
        capsule.update(_section_for_domain(domain, data))
    if policy.mode in {"balanced", "debug"} and not work_compact:
        capsule["memory_sample"] = data.get("memories", [])[:3]
        capsule["goals"] = data.get("goals", [])[:3]
    elif policy.mode in {"balanced", "debug"}:
        capsule["work_platform_note"] = "Private agent-life memories and QQ continuity are omitted on this platform."
    if policy.include_raw:
        capsule["debug_available_sections"] = sorted([k for k, v in data.items() if v])

    text = "\n<LIFEENGINE_CONTEXT mode=\"progressive_slim\">\n" + json.dumps(capsule, ensure_ascii=False, indent=2, sort_keys=True) + "\n</LIFEENGINE_CONTEXT>"
    if len(text) > policy.budget_chars:
        # Hard cap by removing progressively less critical sections.
        for key in ["memory_sample", "goals", "behavior", "collection", "dreams", "sleep", "reply_gate", "resources", "active_or_recent_events"]:
            if key in capsule and len(text) > policy.budget_chars:
                capsule.pop(key, None)
                text = "\n<LIFEENGINE_CONTEXT mode=\"progressive_slim\">\n" + json.dumps(capsule, ensure_ascii=False, indent=2, sort_keys=True) + "\n</LIFEENGINE_CONTEXT>"
        if len(text) > policy.budget_chars:
            minimal_capsule = {
                "mode": "progressive_slim_context",
                "context_profile": context_profile,
                "context_policy": {"mode": policy.mode, "budget_chars": policy.budget_chars, "progressive": policy.progressive},
                "owner_scope": capsule.get("owner_scope") or {},
                "engine": capsule.get("engine") or {},
                "rules": capsule.get("rules") or [],
                "tool_map": {"interface": "Use life_interface catalog/read/write to fetch details."},
                "domains_injected": domains,
                "truncated": True,
                "private_agent_life_omitted": work_compact,
            }
            if feedback and not work_compact:
                minimal_capsule["internal_final_gate_feedback"] = feedback[:2]
                minimal_capsule["internal_feedback"] = feedback[:2]
            text = "\n<LIFEENGINE_CONTEXT mode=\"progressive_slim\">\n" + json.dumps(minimal_capsule, ensure_ascii=False, indent=2, sort_keys=True) + "\n</LIFEENGINE_CONTEXT>"
            capsule = minimal_capsule
    meta = {
        "mode": policy.mode,
        "budget_chars": policy.budget_chars,
        "output_chars": len(text),
        "domains": domains,
        "sections": list(capsule.keys()),
        "module_context_mode": gates.get("context_mode"),
    }
    return text, meta


def record_context_injection(conn, owner_kind: str, owner_id: str, *, session_id: str | None, turn_id: str | None,
                             user_message: str | None, meta: dict[str, Any], trace_id: str | None = None) -> dict[str, Any]:
    run_id = new_id("ctx")
    conn.execute(
        """INSERT INTO prompt_context_runs(
               id, owner_kind, owner_id, session_id, turn_id, mode, budget_chars,
               input_chars, output_chars, domains_json, sections_json, trace_id
             ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            owner_kind,
            owner_id,
            session_id,
            turn_id,
            meta.get("mode") or "slim",
            int(meta.get("budget_chars") or DEFAULT_BUDGET_CHARS),
            len(user_message or ""),
            int(meta.get("output_chars") or 0),
            dumps(meta.get("domains") or []),
            dumps(meta.get("sections") or []),
            trace_id,
        ),
    )
    return {"id": run_id, **meta}


def list_context_runs(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM prompt_context_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def render_context_policy_explanation(control: dict[str, Any] | None = None) -> str:
    pol = ContextPolicy.from_control(control)
    return "\n".join([
        "LifeEngine 上下文策略",
        "======================",
        f"模式：{pol.mode}；预算：{pol.budget_chars} 字符；渐进式披露：{pol.progressive}",
        "原则：流程靠代码闭环，不靠提示词闭环。",
        "注入内容：只给当前 turn 的最小状态胶囊、相关领域提示和工具地图。",
        "详情读取：Agent 必须通过 life_interface / life_schedule / life_collection / life_trace 等工具按需读取。",
        "写入：设定写 CanonDraft；生活事实写 LifeOps；不允许直接 SQL 或靠自然语言落事实。",
    ])
