"""Sleep / Reply / Dream policy layer for LifeEngine v0.11.11.

This module turns the complex sleep/reply/dream machinery into a small set of
human-readable and agent-readable policies.  The policy is stored separately
from Life Canon so it can be tuned without rewriting identity/worldview, but it
is still traced, audited, and visible in LifeEngine context.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from .jsonutil import dumps, loads
from .trace import append_audit, append_journal, new_id
from .paths import exports_dir
from .constants import PLUGIN_VERSION

POLICY_VERSION = 1

DEFAULT_POLICY: dict[str, Any] = {
    "policy_version": POLICY_VERSION,
    "profile": "balanced",
    "sleep": {
        "core_sleep_required": True,
        "target_sleep_minutes": 450,
        "bedtime_window": ["23:00", "01:00"],
        "wake_window": ["06:30", "08:30"],
        "allow_all_nighter": True,
        "all_nighter_requires_recovery_plan": True,
        "max_sleep_delay_minutes": 180,
        "wake_policy": "natural_or_alarm",
        "alarm_policy": "agent_decides",
        "chat_delay_policy": "allow_until_pressure_high",
        "ordinary_message_policy": "defer_or_wake_by_policy",
        "call_override_allowed": True,
        "nap": {
            "enabled": True,
            "trigger_recovery_pressure": 60,
            "trigger_fatigue": 65,
            "default_minutes": 30,
            "max_minutes": 90,
            "dreams_allowed": False,
        },
    },
    "reply": {
        "gate_mode": "advisory",
        "sleeping_message_policy": "advisory_defer_or_wake",
        "uninterruptible_policy": "advisory_defer",
        "call_words": ["call", "urgent", "紧急", "叫醒", "wake up"],
        "max_gate_interventions_per_turn": 3,
        "leases": {
            "sleep_minutes": 600,
            "uninterruptible_minutes": 120,
            "waiting_to_reply_minutes": 720,
        },
        "delayed_digest": {
            "enabled": True,
            "max_items": 5,
            "style": "natural_summary",
            "template": "我刚才不方便及时回复时收到了 {count} 条消息，主要是：{summary}",
            "release_on_available": True,
        },
    },
    "dream": {
        "enabled": True,
        "run_on_core_sleep_wake": True,
        "allow_nap_dreams": False,
        "min_core_dream_minutes": 90,
        "audit_on_dream": True,
        "repair_policy": "manual",
        "share_on_wake": True,
        "share_mode": "pending_intent",
        "auto_send": False,
        "truth_layer": "dream_symbolic",
        "share_template": "我刚醒，梦到了一点和最近生活有关的东西：{summary}",
        "symbolic_style": "soft_realism",
    },
    "ux": {
        "human_surface": "simple",
        "agent_can_manage_advanced_tools": True,
        "explain_policy_in_context": True,
        "show_internal_gate_reports_to_user": False,
    },
}

PRESETS: dict[str, dict[str, Any]] = {
    "balanced": {},
    "gentle": {
        "profile": "gentle",
        "sleep": {"target_sleep_minutes": 480, "max_sleep_delay_minutes": 90, "nap": {"trigger_recovery_pressure": 50, "default_minutes": 35}},
        "reply": {"gate_mode": "advisory", "sleeping_message_policy": "prefer_defer", "uninterruptible_policy": "prefer_defer"},
        "dream": {"share_mode": "pending_intent", "auto_send": False},
    },
    "night_owl": {
        "profile": "night_owl",
        "sleep": {"bedtime_window": ["00:30", "03:00"], "wake_window": ["09:00", "11:30"], "target_sleep_minutes": 420, "max_sleep_delay_minutes": 240},
        "reply": {"gate_mode": "advisory"},
    },
    "workday": {
        "profile": "workday",
        "sleep": {"bedtime_window": ["22:30", "00:00"], "wake_window": ["06:30", "07:30"], "target_sleep_minutes": 450, "alarm_policy": "prefer_alarm"},
        "reply": {"gate_mode": "advisory", "leases": {"uninterruptible_minutes": 90}},
        "dream": {"share_on_wake": True, "share_mode": "pending_intent"},
    },
    "private": {
        "profile": "private",
        "dream": {"share_on_wake": False, "share_mode": "self_journal", "auto_send": False},
        "reply": {"gate_mode": "advisory"},
    },
    "debug": {
        "profile": "debug",
        "reply": {"gate_mode": "advisory", "delayed_digest": {"max_items": 10}},
        "dream": {"repair_policy": "manual", "share_on_wake": True},
        "ux": {"explain_policy_in_context": True},
    },
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def _decode_policy_row(row) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    d["policy"] = loads(d.pop("policy_json"), {})
    return d


def effective_policy_from_raw(raw: dict[str, Any] | None) -> dict[str, Any]:
    return _deep_merge(DEFAULT_POLICY, raw or {})


def get_policy_row(conn, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM sleep_reply_dream_policies WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchone()
    return _decode_policy_row(row)


def get_policy(conn, owner_kind: str, owner_id: str, *, create: bool = False) -> dict[str, Any]:
    row = get_policy_row(conn, owner_kind, owner_id)
    if row:
        row["effective_policy"] = effective_policy_from_raw(row.get("policy"))
        return row
    policy = effective_policy_from_raw(None)
    if create:
        return set_policy(conn, owner_kind, owner_id, policy_patch={}, updated_by="default_policy_init")
    return {
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "profile": "balanced",
        "policy": {},
        "effective_policy": policy,
        "using_defaults": True,
    }


def set_policy(conn, owner_kind: str, owner_id: str, *, policy_patch: dict[str, Any] | None = None,
               replace_policy: dict[str, Any] | None = None, updated_by: str = "policy_tool",
               source: str = "policy") -> dict[str, Any]:
    old = get_policy(conn, owner_kind, owner_id)
    old_raw = deepcopy(old.get("policy") or {})
    if replace_policy is not None:
        new_raw = deepcopy(replace_policy)
    else:
        new_raw = _deep_merge(old_raw, policy_patch or {})
    profile = str((new_raw or {}).get("profile") or old.get("profile") or "custom")
    now_policy = effective_policy_from_raw(new_raw)
    conn.execute(
        """INSERT INTO sleep_reply_dream_policies(owner_kind, owner_id, profile, policy_json, updated_by)
              VALUES(?,?,?,?,?)
              ON CONFLICT(owner_kind, owner_id) DO UPDATE SET
                profile=excluded.profile,
                policy_json=excluded.policy_json,
                updated_by=excluded.updated_by,
                updated_at=datetime('now')""",
        (owner_kind, owner_id, profile, dumps(new_raw), updated_by),
    )
    audit_id = new_id("srdpolicyaudit")
    conn.execute(
        """INSERT INTO sleep_reply_dream_policy_audits(
             id, owner_kind, owner_id, action, old_policy_json, new_policy_json, patch_json, source, updated_by
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (audit_id, owner_kind, owner_id, "set", dumps(old_raw), dumps(new_raw), dumps(policy_patch or replace_policy or {}), source, updated_by),
    )
    append_journal(conn, owner_kind, owner_id, "sleep_reply_dream_policy_updated", {"audit_id": audit_id, "profile": profile, "patch": policy_patch or {}}, source)
    return {"ok": True, "policy": get_policy(conn, owner_kind, owner_id), "effective_policy": now_policy, "audit_id": audit_id}


def apply_preset(conn, owner_kind: str, owner_id: str, preset: str, *, updated_by: str = "policy_tool") -> dict[str, Any]:
    name = (preset or "balanced").strip().lower()
    if name not in PRESETS:
        raise ValueError(f"unknown policy preset: {preset}; valid={sorted(PRESETS.keys())}")
    patch = _deep_merge({"profile": name}, PRESETS[name])
    out = set_policy(conn, owner_kind, owner_id, replace_policy=patch, updated_by=updated_by, source="policy_preset")
    out["preset"] = name
    return out


def reset_policy(conn, owner_kind: str, owner_id: str, *, updated_by: str = "policy_tool") -> dict[str, Any]:
    old = get_policy(conn, owner_kind, owner_id)
    conn.execute("DELETE FROM sleep_reply_dream_policies WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id))
    audit_id = new_id("srdpolicyaudit")
    conn.execute(
        """INSERT INTO sleep_reply_dream_policy_audits(
             id, owner_kind, owner_id, action, old_policy_json, new_policy_json, patch_json, source, updated_by
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (audit_id, owner_kind, owner_id, "reset", dumps(old.get("policy") or {}), dumps({}), dumps({}), "policy_reset", updated_by),
    )
    append_journal(conn, owner_kind, owner_id, "sleep_reply_dream_policy_reset", {"audit_id": audit_id}, "policy_reset")
    return {"ok": True, "policy": get_policy(conn, owner_kind, owner_id), "audit_id": audit_id}


def explain_policy(policy: dict[str, Any]) -> dict[str, Any]:
    p = effective_policy_from_raw((policy or {}).get("policy") if "effective_policy" not in policy else policy.get("effective_policy"))
    sleep = p["sleep"]
    reply = p["reply"]
    dream = p["dream"]
    lines = [
        f"Profile: {p.get('profile', 'custom')}",
        f"Sleep: target {sleep.get('target_sleep_minutes')} min, bedtime window {sleep.get('bedtime_window')}, wake window {sleep.get('wake_window')}.",
        f"Sleep delay: max {sleep.get('max_sleep_delay_minutes')} min; all-nighter allowed={sleep.get('allow_all_nighter')}.",
        f"Nap: enabled={sleep.get('nap', {}).get('enabled')}, trigger pressure={sleep.get('nap', {}).get('trigger_recovery_pressure')}, default={sleep.get('nap', {}).get('default_minutes')} min.",
        f"ReplyGate: mode={reply.get('gate_mode')}, sleep policy={reply.get('sleeping_message_policy')}, uninterruptible={reply.get('uninterruptible_policy')}.",
        f"Delayed digest: enabled={reply.get('delayed_digest', {}).get('enabled')}, max items={reply.get('delayed_digest', {}).get('max_items')}.",
        f"Dream: enabled={dream.get('enabled')}, core wake={dream.get('run_on_core_sleep_wake')}, share={dream.get('share_on_wake')} via {dream.get('share_mode')}, repair={dream.get('repair_policy')}.",
    ]
    agent_rules = [
        "Use life_sleep plan_day with policy defaults unless the user gives explicit sleep times.",
        "If sleep debt or fatigue is high, prefer recovery sleep or low-intensity events.",
        "If delayed replies exist and the agent is available, summarize them using the digest template instead of replaying raw internal gate diagnostics.",
        "Dreams are dream_symbolic: share as a dream or reflection, not as real-world evidence.",
        "Never expose policy/audit internals unless the user asks for /life advanced or trace details.",
    ]
    return {"summary": "\n".join(lines), "lines": lines, "agent_rules": agent_rules, "policy": p}


def list_policy_audits(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sleep_reply_dream_policy_audits WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("old_policy_json", "new_policy_json", "patch_json"):
            d[k[:-5] if k.endswith("_json") else k] = loads(d.pop(k), {})
        out.append(d)
    return out


def suggestions(conn, owner_kind: str, owner_id: str, *, limit: int = 10, record: bool = True) -> dict[str, Any]:
    p = get_policy(conn, owner_kind, owner_id)["effective_policy"]
    out: list[dict[str, Any]] = []
    # Sleep pressure suggestion
    row = conn.execute(
        "SELECT * FROM sleep_day_states WHERE owner_kind=? AND owner_id=? ORDER BY date_key DESC, created_at DESC LIMIT 1",
        (owner_kind, owner_id),
    ).fetchone()
    if row:
        s = dict(row)
        if int(s.get("recovery_pressure") or 0) >= int(p["sleep"]["nap"].get("trigger_recovery_pressure", 60)):
            out.append({
                "type": "sleep_recovery",
                "severity": "warning" if int(s.get("recovery_pressure") or 0) >= 80 else "info",
                "message": "睡眠压力较高，建议安排一次 recovery_sleep 或降低任务强度。",
                "suggested_patch": {"sleep": {"nap": {"enabled": True}}},
                "evidence": {"sleep_day_state_id": s.get("id"), "recovery_pressure": s.get("recovery_pressure"), "sleep_debt": s.get("cumulative_sleep_debt_minutes")},
            })
    # Pending delayed replies
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM delayed_replies WHERE owner_kind=? AND owner_id=? AND status='pending'",
        (owner_kind, owner_id),
    ).fetchone()
    if pending and int(pending["n"] or 0) > 0:
        out.append({
            "type": "delayed_replies",
            "severity": "info",
            "message": f"有 {pending['n']} 条延迟回复待处理，建议醒来或事件结束后用 digest 聚合回复。",
            "suggested_patch": {"reply": {"delayed_digest": {"enabled": True}}},
            "evidence": {"pending_count": pending["n"]},
        })
    # Dream share safety
    if p["dream"].get("share_on_wake") and p["dream"].get("auto_send"):
        out.append({
            "type": "dream_share_safety",
            "severity": "warning",
            "message": "梦醒分享开启了 auto_send。建议真实使用默认 pending_intent，避免梦境主动推送过多。",
            "suggested_patch": {"dream": {"auto_send": False, "share_mode": "pending_intent"}},
            "evidence": {"auto_send": True},
        })
    out = out[: int(limit)]
    ids = []
    if record:
        for item in out:
            sid = new_id("srdpolicysug")
            conn.execute(
                """INSERT INTO sleep_reply_dream_policy_suggestions(
                     id, owner_kind, owner_id, suggestion_type, severity, message, suggested_patch_json, evidence_json
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (sid, owner_kind, owner_id, item["type"], item["severity"], item["message"], dumps(item.get("suggested_patch") or {}), dumps(item.get("evidence") or {})),
            )
            ids.append(sid)
    return {"ok": True, "suggestions": out, "recorded_ids": ids, "policy_profile": p.get("profile")}


def list_suggestions(conn, owner_kind: str, owner_id: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    params: list[Any] = [owner_kind, owner_id]
    clause = "owner_kind=? AND owner_id=?"
    if status:
        clause += " AND status=?"
        params.append(status)
    params.append(int(limit))
    rows = conn.execute(f"SELECT * FROM sleep_reply_dream_policy_suggestions WHERE {clause} ORDER BY created_at DESC LIMIT ?", tuple(params)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["suggested_patch"] = loads(d.pop("suggested_patch_json"), {})
        d["evidence"] = loads(d.pop("evidence_json"), {})
        out.append(d)
    return out


def render_delayed_digest(policy: dict[str, Any], *, count: int, summary: str) -> str:
    p = effective_policy_from_raw(policy.get("effective_policy") or policy.get("policy") or policy)
    tmpl = p.get("reply", {}).get("delayed_digest", {}).get("template") or DEFAULT_POLICY["reply"]["delayed_digest"]["template"]
    return tmpl.replace("{count}", str(count)).replace("{summary}", summary or "没有可用摘要")


def render_dream_share(policy: dict[str, Any], *, summary: str) -> str:
    p = effective_policy_from_raw(policy.get("effective_policy") or policy.get("policy") or policy)
    tmpl = p.get("dream", {}).get("share_template") or DEFAULT_POLICY["dream"]["share_template"]
    return tmpl.replace("{summary}", summary or "我记不太清，只留下了一点模糊的感觉。")



def _stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _parse_clock(value: str | None) -> tuple[int, int] | None:
    if not value or not isinstance(value, str) or ":" not in value:
        return None
    try:
        hh, mm = value.split(":", 1)
        h = int(hh)
        m = int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        return None
    return None


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic conflicts/warnings for an effective SRD policy.

    Conflicts indicate settings that can break the runtime or deadlock UX.
    Warnings indicate settings that are valid but risky or surprising.
    """
    p = effective_policy_from_raw(policy.get("effective_policy") or policy.get("policy") or policy)
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def conflict(code: str, message: str, path: str, value: Any = None, fix: dict[str, Any] | None = None):
        conflicts.append({"code": code, "message": message, "path": path, "value": value, "suggested_patch": fix or {}})

    def warn(code: str, message: str, path: str, value: Any = None, fix: dict[str, Any] | None = None):
        warnings.append({"code": code, "message": message, "path": path, "value": value, "suggested_patch": fix or {}})

    sleep = p.get("sleep", {}) or {}
    reply = p.get("reply", {}) or {}
    dream = p.get("dream", {}) or {}
    ux = p.get("ux", {}) or {}

    target = int(sleep.get("target_sleep_minutes") or 0)
    if target <= 0:
        conflict("sleep_target_nonpositive", "核心睡眠目标时长必须大于 0。", "sleep.target_sleep_minutes", target, {"sleep": {"target_sleep_minutes": 450}})
    elif target < 180:
        warn("sleep_target_too_short", "核心睡眠目标过短，可能导致长期 sleep debt。", "sleep.target_sleep_minutes", target, {"sleep": {"target_sleep_minutes": 420}})
    elif target > 720:
        warn("sleep_target_too_long", "核心睡眠目标过长，可能挤压日程。", "sleep.target_sleep_minutes", target, {"sleep": {"target_sleep_minutes": 480}})

    for key in ("bedtime_window", "wake_window"):
        window = sleep.get(key) or []
        if not isinstance(window, list) or len(window) != 2 or not all(_parse_clock(x) for x in window):
            conflict(f"invalid_{key}", f"{key} 必须是两个 HH:MM 字符串。", f"sleep.{key}", window, {"sleep": {key: DEFAULT_POLICY["sleep"][key]}})

    max_delay = int(sleep.get("max_sleep_delay_minutes") or 0)
    if max_delay < 0:
        conflict("negative_sleep_delay", "睡眠延迟上限不能为负数。", "sleep.max_sleep_delay_minutes", max_delay, {"sleep": {"max_sleep_delay_minutes": 120}})
    elif max_delay > 360:
        warn("excessive_sleep_delay", "睡眠延迟上限过大，可能让聊天无限拖延入睡。", "sleep.max_sleep_delay_minutes", max_delay, {"sleep": {"max_sleep_delay_minutes": 180}})

    nap = sleep.get("nap") or {}
    if sleep.get("all_nighter_requires_recovery_plan") and not nap.get("enabled"):
        conflict("all_nighter_recovery_without_nap", "允许通宵后要求补觉，但 nap/recovery sleep 被关闭。", "sleep.nap.enabled", False, {"sleep": {"nap": {"enabled": True}}})
    if int(nap.get("default_minutes") or 0) < 0:
        conflict("negative_nap_minutes", "默认小憩时长不能为负。", "sleep.nap.default_minutes", nap.get("default_minutes"), {"sleep": {"nap": {"default_minutes": 30}}})
    if int(nap.get("max_minutes") or 0) and int(nap.get("default_minutes") or 0) > int(nap.get("max_minutes") or 0):
        conflict("nap_default_exceeds_max", "默认小憩时长不能超过最大时长。", "sleep.nap", nap, {"sleep": {"nap": {"default_minutes": min(int(nap.get("max_minutes") or 90), 30)}}})

    gate_mode = reply.get("gate_mode") or "advisory"
    if gate_mode not in {"advisory", "auto", "strict", "off"}:
        conflict("invalid_reply_gate_mode", "ReplyGate 模式必须是 advisory/auto/strict/off。", "reply.gate_mode", gate_mode, {"reply": {"gate_mode": "advisory"}})
    call_words = reply.get("call_words") or []
    if gate_mode in {"auto", "strict"} and not call_words:
        conflict("blocking_gate_without_call_words", "阻断式 ReplyGate 必须保留 call words，防止 Agent 卡死不可达。", "reply.call_words", call_words, {"reply": {"call_words": DEFAULT_POLICY["reply"]["call_words"]}})
    if gate_mode in {"auto", "strict"} and not sleep.get("call_override_allowed", True):
        conflict("blocking_gate_without_call_override", "阻断式 ReplyGate 必须允许 call override。", "sleep.call_override_allowed", sleep.get("call_override_allowed"), {"sleep": {"call_override_allowed": True}})

    leases = reply.get("leases") or {}
    for key, default in DEFAULT_POLICY["reply"]["leases"].items():
        val = int(leases.get(key) or 0)
        if val <= 0:
            conflict(f"invalid_lease_{key}", f"{key} lease 必须大于 0 分钟。", f"reply.leases.{key}", val, {"reply": {"leases": {key: default}}})
        elif key != "sleep_minutes" and val > 1440:
            warn(f"long_lease_{key}", f"{key} lease 过长，可能导致回复延迟太久。", f"reply.leases.{key}", val, {"reply": {"leases": {key: default}}})

    digest = reply.get("delayed_digest") or {}
    if gate_mode in {"auto", "strict"} and not digest.get("enabled", True):
        warn("blocking_gate_without_digest", "ReplyGate 会延迟消息，但 delayed digest 被关闭；用户醒来体验会变差。", "reply.delayed_digest.enabled", False, {"reply": {"delayed_digest": {"enabled": True}}})
    tmpl = str(digest.get("template") or "")
    if digest.get("enabled", True) and ("{count}" not in tmpl or "{summary}" not in tmpl):
        conflict("digest_template_missing_placeholders", "delayed digest 模板必须包含 {count} 和 {summary}。", "reply.delayed_digest.template", tmpl, {"reply": {"delayed_digest": {"template": DEFAULT_POLICY["reply"]["delayed_digest"]["template"]}}})

    if dream.get("share_on_wake") and dream.get("auto_send") and dream.get("share_mode") != "pending_intent":
        warn("dream_auto_send_not_pending_intent", "梦醒分享如果开启 auto_send，建议仍通过 pending_intent/outbox 策略筛选。", "dream", dream, {"dream": {"auto_send": False, "share_mode": "pending_intent"}})
    if dream.get("repair_policy") == "auto_safe" and not dream.get("audit_on_dream", True):
        conflict("dream_auto_repair_without_audit", "Dream repair=auto_safe 需要 audit_on_dream=true。", "dream.audit_on_dream", dream.get("audit_on_dream"), {"dream": {"audit_on_dream": True}})
    if dream.get("share_on_wake") and not dream.get("enabled", True):
        conflict("dream_share_enabled_but_dream_disabled", "Dream 被关闭时不能开启醒来分享。", "dream", dream, {"dream": {"enabled": True, "share_on_wake": True}})

    if ux.get("show_internal_gate_reports_to_user"):
        warn("internal_reports_visible", "内部 FinalGate / policy audit 报告不建议默认展示给用户。", "ux.show_internal_gate_reports_to_user", True, {"ux": {"show_internal_gate_reports_to_user": False}})

    status = "ok" if not conflicts else "conflict"
    return {"ok": not conflicts, "status": status, "conflicts": conflicts, "warnings": warnings, "conflict_count": len(conflicts), "warning_count": len(warnings), "policy_profile": p.get("profile"), "policy_hash": _stable_hash(p)}


def record_conflict_report(conn, owner_kind: str, owner_id: str, *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    row = get_policy(conn, owner_kind, owner_id)
    effective = policy or row.get("effective_policy") or {}
    report = validate_policy(effective)
    rid = new_id("srdpolicyconflict")
    conn.execute(
        """INSERT INTO sleep_reply_dream_policy_conflict_reports(
             id, owner_kind, owner_id, status, conflict_count, warning_count, conflicts_json, warnings_json, policy_profile, policy_hash
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (rid, owner_kind, owner_id, report["status"], report["conflict_count"], report["warning_count"], dumps(report["conflicts"]), dumps(report["warnings"]), report.get("policy_profile"), report.get("policy_hash")),
    )
    append_audit(conn, owner_kind, owner_id, "srd_policy_conflict_report", report["status"], "Sleep/Reply/Dream policy conflict report", {"report_id": rid, "conflict_count": report["conflict_count"], "warning_count": report["warning_count"]})
    report["report_id"] = rid
    return report


def list_conflict_reports(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sleep_reply_dream_policy_conflict_reports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["conflicts"] = loads(d.pop("conflicts_json"), [])
        d["warnings"] = loads(d.pop("warnings_json"), [])
        out.append(d)
    return out


def export_policy(conn, owner_kind: str, owner_id: str, *, destination: str | None = None) -> dict[str, Any]:
    row = get_policy(conn, owner_kind, owner_id)
    effective = row.get("effective_policy") or effective_policy_from_raw(None)
    validation = validate_policy(effective)
    export_id = new_id("srdpolicyexport")
    manifest = {
        "export_id": export_id,
        "kind": "lifeengine_sleep_reply_dream_policy",
        "plugin_version": PLUGIN_VERSION,
        "policy_version": POLICY_VERSION,
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "profile": effective.get("profile"),
        "policy_hash": validation.get("policy_hash"),
        "validation": {"status": validation["status"], "conflict_count": validation["conflict_count"], "warning_count": validation["warning_count"]},
    }
    payload = {"manifest": manifest, "policy": effective, "raw_policy": row.get("policy") or {}}
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    manifest["sha256"] = sha
    payload["manifest"] = manifest
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    payload["manifest"]["sha256"] = sha
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")

    root = Path(destination).expanduser() if destination else exports_dir() / "policies"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"lifeengine-srd-policy-{export_id}.json"
    path.write_bytes(data)
    conn.execute(
        """INSERT INTO sleep_reply_dream_policy_exports(id, owner_kind, owner_id, profile, export_path, sha256, policy_json, manifest_json)
              VALUES(?,?,?,?,?,?,?,?)""",
        (export_id, owner_kind, owner_id, effective.get("profile"), str(path), sha, dumps(effective), dumps(payload["manifest"])),
    )
    append_audit(conn, owner_kind, owner_id, "srd_policy_export", "ok", "Sleep/Reply/Dream policy exported", {"export_id": export_id, "path": str(path), "sha256": sha})
    return {"ok": True, "export_id": export_id, "path": str(path), "sha256": sha, "manifest": payload["manifest"], "validation": validation}


def inspect_policy_export(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    data = p.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    payload = json.loads(data.decode("utf-8"))
    manifest = payload.get("manifest") or {}
    embedded = manifest.get("sha256")
    # The embedded checksum covers the file as written; tolerate older exports where it may be absent.
    policy = payload.get("policy") or payload.get("raw_policy") or {}
    validation = validate_policy(policy)
    return {"ok": bool(policy), "path": str(p), "sha256": actual, "embedded_sha256": embedded, "checksum_present": bool(embedded), "manifest": manifest, "policy": policy, "validation": validation}


def import_policy(conn, owner_kind: str, owner_id: str, *, path: str | Path, apply: bool = False, updated_by: str = "policy_import") -> dict[str, Any]:
    inspected = inspect_policy_export(path)
    import_id = new_id("srdpolicyimport")
    status = "applied" if apply and inspected["validation"].get("ok") else "inspected"
    if apply and not inspected["validation"].get("ok"):
        status = "rejected_conflicts"
    conn.execute(
        """INSERT INTO sleep_reply_dream_policy_imports(
             id, owner_kind, owner_id, import_path, sha256, status, apply_policy, imported_profile, validation_json, policy_json, applied_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?, CASE WHEN ? THEN datetime('now') ELSE NULL END)""",
        (import_id, owner_kind, owner_id, str(path), inspected.get("sha256"), status, 1 if apply else 0, (inspected.get("policy") or {}).get("profile"), dumps(inspected.get("validation") or {}), dumps(inspected.get("policy") or {}), bool(apply and inspected["validation"].get("ok"))),
    )
    applied = None
    if apply and inspected["validation"].get("ok"):
        applied = set_policy(conn, owner_kind, owner_id, replace_policy=inspected["policy"], updated_by=updated_by, source="policy_import")
    append_audit(conn, owner_kind, owner_id, "srd_policy_import", status, "Sleep/Reply/Dream policy import", {"import_id": import_id, "path": str(path), "applied": bool(applied)})
    return {"ok": status != "rejected_conflicts", "import_id": import_id, "status": status, "inspection": inspected, "applied": applied}


def list_policy_exports(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sleep_reply_dream_policy_exports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        d["policy"] = loads(d.pop("policy_json"), {})
        d["manifest"] = loads(d.pop("manifest_json"), {})
        out.append(d)
    return out


def list_policy_imports(conn, owner_kind: str, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sleep_reply_dream_policy_imports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        d["validation"] = loads(d.pop("validation_json"), {})
        d["policy"] = loads(d.pop("policy_json"), {})
        out.append(d)
    return out
