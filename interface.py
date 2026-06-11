"""Unified safe LifeEngine interface for agents and human-facing shells.

This is intentionally *not* a raw SQL interface.  It exposes a small, stable
routing layer over LifeEngine's domain APIs so an Agent can read/write settings,
events, schedules, resources, and review items without knowing table names or
risking bypassing LifeOps / CanonDraft / validation.
"""

from __future__ import annotations

from typing import Any


DOMAINS: dict[str, dict[str, Any]] = {
    "config": {
        "label": "设定 / Life Canon",
        "read": ["summary", "check", "draft", "requirements", "suggest_defaults"],
        "write": ["patch", "apply_default_draft"],
        "rule": "写入只进入 CanonDraft；启用必须 /life commit。",
    },
    "schedule": {
        "label": "日程 / Schedule",
        "read": ["today", "tomorrow", "week", "day", "unscheduled", "explain"],
        "write": ["schedule_event", "reschedule", "cancel_block", "complete_block"],
        "rule": "Event 是事情；ScheduleBlock 才是具体排期。写入走 LifeOps。",
    },
    "event": {
        "label": "事件 / Event V2",
        "read": ["list", "get", "transitions", "state"],
        "write": ["create", "transition", "complete", "update_state"],
        "rule": "事件生命周期必须遵守状态机，写入走 LifeOps。",
    },
    "resource": {
        "label": "资源 / Resource Ledger",
        "read": ["list", "reconcile"],
        "write": ["define", "delta", "reserve", "release"],
        "rule": "资源必须先定义；变更写入资源账本。",
    },
    "inventory": {
        "label": "物品 / Inventory",
        "read": ["list", "movements", "meals"],
        "write": ["create", "update", "consume", "discard", "move", "meal"],
        "rule": "实体资源记录物品、衣柜、日用品、饭食。",
    },
    "sleep": {
        "label": "睡眠 / Sleep",
        "read": ["status", "plans", "sessions", "day_state"],
        "write": ["plan", "nap", "start", "wake", "recovery_plan"],
        "rule": "核心睡眠是每日规划；实际睡眠与计划可不同。",
    },
    "dream": {
        "label": "梦 / Dream",
        "read": ["status", "runs", "entries", "findings"],
        "write": ["run", "repair_plan", "repair", "create_entry"],
        "rule": "梦是 dream_symbolic，不证明现实事实。",
    },
    "review": {
        "label": "Review / 待处理 inbox",
        "read": ["summary", "runs", "get_run", "policy"],
        "write": ["preview_action", "apply", "batch_preview", "apply_all", "undo"],
        "rule": "默认人类可读；safe item 可由 Agent 按策略自处理。",
    },
    "truth": {
        "label": "真相源 / TruthSource",
        "read": ["list", "resolve"],
        "write": ["observe", "bind"],
        "rule": "真实来源和虚拟规则都通过 Canon binding 管理。",
    },
    "living": {
        "label": "生活节律 / Living Rhythm",
        "read": ["summary", "consistency", "paper_notes"],
        "write": ["init_inventory", "day_rhythm", "decompose_abstract", "create_note", "diary_draft"],
        "rule": "把抽象目标变成具体日常；写入走 LifeOps 或 Canon/trace，不直接 SQL。",
    },
    "trace": {
        "label": "Trace / 审计",
        "read": ["latest", "explain", "verify", "audit"],
        "write": [],
        "rule": "只读解释层，用于追踪为什么发生。",
    },
}


def catalog() -> dict[str, Any]:
    lines = ["LifeEngine 接口目录", "==================="]
    lines.append("这是给 Agent 和高级人类用的安全接口目录；不是裸 SQL。")
    lines.append("所有写入都走 CanonDraft 或 LifeOps，不绕过校验、receipt、trace。")
    lines.append("")
    for key, meta in DOMAINS.items():
        lines.append(f"- {key}: {meta['label']}")
        lines.append(f"  读：{', '.join(meta['read']) or '-'}")
        lines.append(f"  写：{', '.join(meta['write']) or '-'}")
        lines.append(f"  规则：{meta['rule']}")
    lines.append("")
    lines.append("常用：")
    lines.append("- life_interface(action='read', domain='schedule', view='today')")
    lines.append("- life_interface(action='read', domain='config', view='check')")
    lines.append("- life_interface(action='write', domain='config', intent='patch', text='天气随机，时间和真实时间同步')")
    lines.append("- life_interface(action='write', domain='schedule', intent='schedule_event', event_id='...', start='...', end='...')")
    return {"ok": True, "domains": DOMAINS, "rendered": "\n".join(lines)}


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in {"action", "domain", "view", "intent", "owner_kind", "owner_id", "owner", "agent_id", "user_id"}}


def read(rt: Any, owner_kind: str, owner_id: str, domain: str, view: str | None = None, **payload: Any) -> dict[str, Any]:
    domain = (domain or "catalog").strip().lower()
    view = (view or "summary").strip().lower()
    p = _clean_payload(payload)
    if domain in {"catalog", "interfaces", "help"}:
        return catalog()
    if domain == "config":
        return rt.required_settings(view, owner_kind, owner_id, **p)
    if domain == "schedule":
        return rt.schedule(view, owner_kind, owner_id, **p)
    if domain == "event":
        return rt.event_tool(view if view != "summary" else "list", owner_kind, owner_id, **p)
    if domain == "resource":
        return rt.resources(view if view != "summary" else "list", owner_kind, owner_id, **p)
    if domain == "inventory":
        return rt.inventory(view if view != "summary" else "list", owner_kind, owner_id, None, None, **p)
    if domain == "sleep":
        return rt.sleep(view if view != "summary" else "status", owner_kind, owner_id, None, None, **p)
    if domain == "dream":
        return rt.dream(view if view != "summary" else "status", owner_kind, owner_id, None, None, **p)
    if domain == "review":
        return rt.review(view if view != "summary" else "summary", owner_kind, owner_id, None, None, **p)
    if domain == "truth":
        return rt.truth(view if view != "summary" else "list", owner_kind, owner_id, None, None, **p)
    if domain == "living":
        return rt.living(view if view != "summary" else "summary", owner_kind, owner_id, None, None, **p)
    if domain == "trace":
        return rt.traces(view if view != "summary" else "latest", owner_kind, owner_id, **p)
    raise ValueError(f"unknown LifeEngine interface domain: {domain}")


def write(rt: Any, owner_kind: str, owner_id: str, domain: str, intent: str | None = None,
          session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
    domain = (domain or "").strip().lower()
    intent = (intent or payload.get("view") or payload.get("action") or "").strip().lower()
    p = _clean_payload(payload)
    if domain == "config":
        # Settings always go to CanonDraft, never active Canon.
        if intent in {"patch", "set", "write", "补充", "update"}:
            return rt.required_settings("patch", owner_kind, owner_id, **p)
        if intent in {"apply_default_draft", "defaults", "suggest_defaults", "complete_defaults"}:
            return rt.required_settings("apply_default_draft", owner_kind, owner_id, **p)
        raise ValueError("config write intent must be patch or apply_default_draft")
    if domain == "schedule":
        return rt.schedule(intent, owner_kind, owner_id, session_id=session_id, turn_id=turn_id, **p)
    if domain == "event":
        return rt.event_tool(intent, owner_kind, owner_id, session_id, turn_id, **p)
    if domain == "resource":
        return rt.resources(intent, owner_kind, owner_id, session_id, turn_id, **p)
    if domain == "inventory":
        return rt.inventory(intent, owner_kind, owner_id, session_id, turn_id, **p)
    if domain == "sleep":
        return rt.sleep(intent, owner_kind, owner_id, session_id, turn_id, **p)
    if domain == "dream":
        return rt.dream(intent, owner_kind, owner_id, session_id, turn_id, **p)
    if domain == "review":
        return rt.review(intent, owner_kind, owner_id, session_id, turn_id, **p)
    if domain == "truth":
        return rt.truth(intent, owner_kind, owner_id, session_id, turn_id, **p)
    if domain == "living":
        return rt.living(intent, owner_kind, owner_id, session_id, turn_id, **p)
    raise ValueError(f"unknown or read-only LifeEngine interface domain: {domain}")


def run(rt: Any, action: str, owner_kind: str, owner_id: str, session_id: str | None = None, turn_id: str | None = None, **payload: Any) -> dict[str, Any]:
    action = (action or "catalog").strip().lower()
    if action in {"catalog", "help", "interfaces", "list"}:
        return catalog()
    if action == "read":
        p = dict(payload)
        domain = p.pop("domain", None)
        view = p.pop("view", None) or p.pop("intent", None)
        return read(rt, owner_kind, owner_id, domain, view, **p)
    if action == "write":
        p = dict(payload)
        domain = p.pop("domain", None)
        intent = p.pop("intent", None) or p.pop("view", None)
        return write(rt, owner_kind, owner_id, domain, intent, session_id, turn_id, **p)
    raise ValueError("life_interface action must be catalog, read, or write")
