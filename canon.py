"""Life Canon drafting, extraction, commit, and setup helpers."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .constants import DEFAULT_CANON_TEMPLATE, DEFAULT_MODULE_GATES
from .jsonutil import dumps, loads
from .trace import append_journal, new_id
from .migration import record_canon_migration


def owner_key(owner_kind: str, owner_id: str) -> tuple[str, str]:
    return owner_kind, owner_id


def ensure_control(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM controls WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchone()
    if row:
        d = dict(row)
        d["module_gates"] = loads(d.pop("module_gates_json"), DEFAULT_MODULE_GATES.copy())
        d["paused"] = loads(d.pop("paused_json"), None)
        return d
    state = "setup_required" if owner_kind == "agent" else "paused"
    conn.execute(
        """INSERT INTO controls(owner_kind, owner_id, engine_state, module_gates_json, heartbeat_mode,
               resume_policy, current_workspace) VALUES(?,?,?,?,?,?,?)""",
        (owner_kind, owner_id, state, dumps(DEFAULT_MODULE_GATES), "manual", "mark_gap_only",
         "agent_self" if owner_kind == "agent" else "user_life"),
    )
    append_journal(conn, owner_kind, owner_id, "control_initialized", {"engine_state": state}, "system")
    return ensure_control(conn, owner_kind, owner_id)


def update_control(conn, owner_kind: str, owner_id: str, **fields: Any) -> None:
    allowed = {
        "engine_state", "active_canon_version", "draft_canon_id", "module_gates_json",
        "heartbeat_mode", "resume_policy", "current_workspace", "paused_json",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    sets = ", ".join([f"{k}=?" for k in updates] + ["updated_at=datetime('now')"])
    conn.execute(
        f"UPDATE controls SET {sets} WHERE owner_kind=? AND owner_id=?",
        tuple(updates.values()) + (owner_kind, owner_id),
    )
    append_journal(conn, owner_kind, owner_id, "control_updated", updates, "control")


def begin_setup(conn, owner_kind: str, owner_id: str, reason: str = "setup") -> dict[str, Any]:
    control = ensure_control(conn, owner_kind, owner_id)
    draft_id = control.get("draft_canon_id")
    if not draft_id:
        draft_id = new_id("draft")
        conn.execute(
            """INSERT INTO canon_drafts(id, owner_kind, owner_id, base_version, status,
                   raw_user_statements_json, extracted_json, unresolved_questions_json, conflicts_json)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
            (draft_id, owner_kind, owner_id, control.get("active_canon_version"), "editing",
             dumps([]), dumps({}), dumps(_default_questions(owner_kind)), dumps([])),
        )
    state = "setup" if control["engine_state"] in {"uninitialized", "setup_required"} else "paused_setup"
    update_control(
        conn, owner_kind, owner_id,
        engine_state=state,
        draft_canon_id=draft_id,
        paused_json=dumps({"reason": reason}),
    )
    return get_draft(conn, draft_id)


def get_active_canon(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    control = ensure_control(conn, owner_kind, owner_id)
    version = control.get("active_canon_version")
    if not version:
        return deepcopy(DEFAULT_CANON_TEMPLATE)
    row = conn.execute(
        "SELECT data_json FROM canon_versions WHERE owner_kind=? AND owner_id=? AND version=? AND status='active'",
        (owner_kind, owner_id, version),
    ).fetchone()
    if not row:
        return deepcopy(DEFAULT_CANON_TEMPLATE)
    data = loads(row[0], {})
    merged = deepcopy(DEFAULT_CANON_TEMPLATE)
    _deep_update(merged, data)
    return merged


def get_draft(conn, draft_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM canon_drafts WHERE id=?", (draft_id,)).fetchone()
    if not row:
        raise ValueError(f"CanonDraft not found: {draft_id}")
    d = dict(row)
    d["raw_user_statements"] = loads(d.pop("raw_user_statements_json"), [])
    d["extracted"] = loads(d.pop("extracted_json"), {})
    d["unresolved_questions"] = loads(d.pop("unresolved_questions_json"), [])
    d["conflicts"] = loads(d.pop("conflicts_json"), [])
    return d


def append_setup_statement(conn, owner_kind: str, owner_id: str, text: str, source: str = "user") -> dict[str, Any]:
    draft = begin_setup(conn, owner_kind, owner_id, reason="setup_statement")
    if not text.strip():
        return draft
    raw = list(draft.get("raw_user_statements", []))
    raw.append({"id": new_id("stmt"), "source": source, "text": text.strip()})
    extracted = draft.get("extracted", {}) or {}
    inferred = infer_canon_from_text(text)
    _deep_update(extracted, inferred)
    questions = _unresolved_questions(extracted, owner_kind)
    conn.execute(
        """UPDATE canon_drafts SET raw_user_statements_json=?, extracted_json=?,
                  unresolved_questions_json=?, updated_at=datetime('now') WHERE id=?""",
        (dumps(raw), dumps(extracted), dumps(questions), draft["id"]),
    )
    append_journal(conn, owner_kind, owner_id, "canon_draft_updated", {"draft_id": draft["id"], "inferred": inferred}, "setup")
    return get_draft(conn, draft["id"])


def commit_draft(conn, owner_kind: str, owner_id: str, draft_id: str | None = None, activate: bool = True) -> dict[str, Any]:
    control = ensure_control(conn, owner_kind, owner_id)
    draft_id = draft_id or control.get("draft_canon_id")
    if not draft_id:
        raise ValueError("No CanonDraft to commit")
    draft = get_draft(conn, draft_id)
    base = get_active_canon(conn, owner_kind, owner_id)
    from_version = control.get("active_canon_version")
    data = deepcopy(base)
    extracted = deepcopy(draft.get("extracted", {}) or {})
    delete_paths = extracted.pop("__delete_paths__", []) or []
    if isinstance(delete_paths, str):
        delete_paths = [delete_paths]
    for delete_path in delete_paths:
        _delete_nested_path(data, str(delete_path))
    _deep_update(data, extracted)
    version_row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM canon_versions WHERE owner_kind=? AND owner_id=?",
        (owner_kind, owner_id),
    ).fetchone()
    version = int(version_row[0] or 0) + 1
    if activate:
        conn.execute(
            "UPDATE canon_versions SET status='superseded' WHERE owner_kind=? AND owner_id=? AND status='active'",
            (owner_kind, owner_id),
        )
    canon_id = new_id("canon")
    status = "active" if activate else "archived"
    conn.execute(
        """INSERT INTO canon_versions(id, owner_kind, owner_id, version, status, data_json, activated_at)
               VALUES(?,?,?,?,?,?,datetime('now'))""",
        (canon_id, owner_kind, owner_id, version, status, dumps(data)),
    )
    conn.execute("UPDATE canon_drafts SET status='committed', updated_at=datetime('now') WHERE id=?", (draft_id,))
    migration = record_canon_migration(conn, owner_kind, owner_id, from_version, version, base, data)
    if activate:
        # First setup may activate immediately; later reconfiguration is safer when paused.
        # The caller can /life resume after reviewing the migration plan.
        next_state = "active" if owner_kind == "agent" and from_version is None else ("paused" if owner_kind == "agent" else "paused")
        update_control(
            conn, owner_kind, owner_id,
            engine_state=next_state,
            active_canon_version=version,
            draft_canon_id=None,
            paused_json=None if next_state == "active" else dumps({"reason": "canon committed; review migration before resume"}),
        )
    _ensure_resources_from_canon(conn, owner_kind, owner_id, data, version)
    append_journal(conn, owner_kind, owner_id, "canon_committed", {"version": version, "canon_id": canon_id, "migration": migration}, "canon")
    return {"canon_id": canon_id, "version": version, "status": status, "data": data, "migration": migration}


def set_engine_state(conn, owner_kind: str, owner_id: str, state: str, reason: str | None = None) -> dict[str, Any]:
    from .constants import ENGINE_STATES
    if state not in ENGINE_STATES:
        raise ValueError(f"Invalid engine state: {state}")
    paused_json = dumps({"reason": reason or state}) if state in {"paused", "paused_setup", "disabled"} else None
    update_control(conn, owner_kind, owner_id, engine_state=state, paused_json=paused_json)
    return ensure_control(conn, owner_kind, owner_id)


def set_module_gate(conn, owner_kind: str, owner_id: str, key: str, value: str) -> dict[str, Any]:
    control = ensure_control(conn, owner_kind, owner_id)
    gates = dict(control.get("module_gates") or DEFAULT_MODULE_GATES)
    gates[key] = value
    update_control(conn, owner_kind, owner_id, module_gates_json=dumps(gates))
    return ensure_control(conn, owner_kind, owner_id)


def infer_canon_from_text(text: str) -> dict[str, Any]:
    """Heuristic CanonDraft extraction.

    This is intentionally deterministic.  The LLM can still call life_setup with
    structured sections, but setup-mode pre_llm_call can safely store natural
    language without creating life events.
    """
    t = text.strip()
    out: dict[str, Any] = {}
    lower = t.lower()

    # Explicit structured-ish TruthSource binding produced by life_truth(action=bind).
    tb = re.search(r"真相源绑定[：:]\s*([A-Za-z0-9_.-]+)\s*使用\s*([A-Za-z0-9_.-]+)", t)
    if tb:
        domain = tb.group(1)
        authority = tb.group(2)
        binding: dict[str, Any] = {"domain": domain, "authority": authority}
        ttl = re.search(r"freshness_ttl_minutes=(\d+)", t)
        if ttl:
            binding["freshness_ttl_minutes"] = int(ttl.group(1))
        fb = re.search(r"fallback=([A-Za-z0-9_.-]+)", t)
        if fb:
            binding["fallback"] = fb.group(1)
        fixed = re.search(r"固定值\s*([^；;]+)", t)
        if fixed:
            binding["value"] = fixed.group(1).strip()
        out.setdefault("truth_sources", {}).setdefault("bindings", {})[domain] = binding

    # Identity / personhood
    name_match = re.search(r"(?:叫|名字是|name is)\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,32})", t)
    if name_match:
        out.setdefault("identity", {})["name"] = name_match.group(1)
    if re.search(r"女孩子|女性|woman|girl", lower):
        out.setdefault("identity", {})["gender"] = "female"
    if re.search(r"男孩子|男性|man|boy", lower):
        out.setdefault("identity", {})["gender"] = "male"
    age_match = re.search(r"(\d{1,3})\s*岁", t)
    if age_match:
        out.setdefault("identity", {})["age"] = int(age_match.group(1))

    if "世界观" in t or "世界" in t:
        out.setdefault("worldview", {})["raw_world_description"] = t
    if "和我一样" in t or "跟我一样" in t or "same as me" in lower:
        out.setdefault("worldview", {})["world_binding"] = "same_as_user"

    if "天气" in t:
        binding: dict[str, Any] = {"domain": "weather"}
        if "我这边" in t or "和我" in t or "跟我" in t:
            binding.update({"authority": "user_current_location", "freshness_ttl_minutes": 120})
        elif "真实" in t or "工具" in t or "查" in t:
            binding.update({"authority": "external_tool", "freshness_ttl_minutes": 120})
        else:
            binding.update({"authority": "narrative_simulator"})
        out.setdefault("truth_sources", {}).setdefault("bindings", {})["weather"] = binding

    if "日元" in t or "jpy" in lower:
        out.setdefault("truth_sources", {}).setdefault("bindings", {})["currency"] = {"domain": "currency", "authority": "fixed_setting", "value": "JPY"}
        out.setdefault("resources", {}).setdefault("definitions", {})["money.jpy"] = {
            "display_name": "JPY wallet", "resource_class": "fungible", "unit": "JPY", "min": 0, "initial": 0,
        }
    if "人民币" in t or "cny" in lower:
        out.setdefault("truth_sources", {}).setdefault("bindings", {})["currency"] = {"domain": "currency", "authority": "fixed_setting", "value": "CNY"}
        out.setdefault("resources", {}).setdefault("definitions", {})["money.cny"] = {
            "display_name": "CNY wallet", "resource_class": "fungible", "unit": "CNY", "min": 0, "initial": 0,
        }

    # Known core resources.
    resources = {
        "精力": ("energy", "Energy", "capacity"),
        "体力": ("stamina", "Stamina", "capacity"),
        "心情": ("mood", "Mood", "state"),
        "压力": ("stress", "Stress", "state"),
        "专注": ("focus", "Focus", "capacity"),
        "灵感": ("inspiration", "Inspiration", "capacity"),
        "衣柜": ("wardrobe.items", "Wardrobe Items", "durable_item"),
        "钱包": ("money.jpy", "JPY wallet", "fungible"),
        "学习进度": ("skill.study_progress", "Study Progress", "skill"),
    }
    for zh, (key, display, klass) in resources.items():
        if zh in t:
            out.setdefault("resources", {}).setdefault("definitions", {})[key] = {
                "display_name": display,
                "resource_class": klass,
                "unit": "points" if klass in {"capacity", "state", "skill"} else None,
                "min": 0,
                "max": 100 if klass in {"capacity", "state", "skill"} else None,
                "initial": 50 if klass in {"capacity", "state"} else 0,
            }

    custom = re.search(r"(?:增加|添加|add).*?(?:资源|resource)[：:，,\s]*([A-Za-z0-9_\.\-\u4e00-\u9fff]{1,32})", t)
    if custom:
        label = custom.group(1).strip(" 。.")
        key = _slug_resource(label)
        out.setdefault("resources", {}).setdefault("definitions", {})[key] = {
            "display_name": label,
            "resource_class": "capacity",
            "unit": "points",
            "min": 0,
            "max": 100,
            "initial": 50,
        }

    if "主动" in t and ("聊天" in t or "找我" in t or "消息" in t):
        out.setdefault("proactive", {})["mode"] = "pending_only"
        out["proactive"]["max_per_day"] = 1 if "每天最多一次" in t else out["proactive"].get("max_per_day", 1)
    if "日记" in t:
        out.setdefault("diary", {})["mode"] = "manual"
        if "每天" in t or "每日" in t:
            out["diary"]["mode"] = "daily"
    if "睡" in t:
        out.setdefault("schedule_rules", {})["needs_sleep"] = True
    if "吃饭" in t or "吃东西" in t:
        out.setdefault("schedule_rules", {})["needs_food"] = True
    return out


def _slug_resource(label: str) -> str:
    asciiish = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_").lower()
    if asciiish:
        return f"custom.{asciiish}"
    return "custom." + str(abs(hash(label)) % 1000000)


def _default_questions(owner_kind: str) -> list[str]:
    if owner_kind == "agent":
        return [
            "你希望我是一个什么样的人？",
            "我生活在什么世界？是否和你处在同一个地点/时间？",
            "天气、货币、物价等真相源参考什么？",
            "我有哪些可登记资源？初始值是多少？",
            "是否允许 heartbeat、自发行为、主动聊天、日记？",
        ]
    return [
        "你希望我帮你记录哪些用户侧资源和计划？",
        "哪些事实来源可以写入你的生活状态？",
        "是否需要日程提醒、资源账本或日记整理？",
    ]


def _unresolved_questions(extracted: dict[str, Any], owner_kind: str) -> list[str]:
    q: list[str] = []
    if owner_kind == "agent":
        if not extracted.get("identity"):
            q.append("还缺 Agent 的身份设定。")
        if not extracted.get("worldview"):
            q.append("还缺 Agent 的世界观/环境设定。")
        if not extracted.get("truth_sources"):
            q.append("还缺天气、货币、地点等真相源绑定。")
    if not extracted.get("resources", {}).get("definitions"):
        q.append("还缺可计数资源定义和初始值。")
    return q


def _ensure_resources_from_canon(conn, owner_kind: str, owner_id: str, canon: dict[str, Any], version: int) -> None:
    defs = canon.get("resources", {}).get("definitions", {}) or {}
    for key, spec in defs.items():
        display = spec.get("display_name") or key
        klass = spec.get("resource_class") or "custom"
        unit = spec.get("unit")
        minv = spec.get("min")
        maxv = spec.get("max")
        initial = float(spec.get("initial", 0) or 0)
        conn.execute(
            """INSERT INTO resource_definitions(id, owner_kind, owner_id, key, display_name, resource_class,
                   unit, min_value, max_value, rules_json, canon_version)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(owner_kind, owner_id, key) DO UPDATE SET
                     display_name=excluded.display_name, resource_class=excluded.resource_class,
                     unit=excluded.unit, min_value=excluded.min_value, max_value=excluded.max_value,
                     rules_json=excluded.rules_json, canon_version=excluded.canon_version""",
            (new_id("resdef"), owner_kind, owner_id, key, display, klass, unit, minv, maxv, dumps(spec.get("rules", {})), version),
        )
        existing = conn.execute(
            "SELECT id FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
            (owner_kind, owner_id, key),
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO resource_accounts(id, owner_kind, owner_id, resource_key, current_value, unit, capacity)
                       VALUES(?,?,?,?,?,?,?)""",
                (new_id("resacct"), owner_kind, owner_id, key, initial, unit, maxv),
            )
            conn.execute(
                """INSERT INTO resource_ledger(id, owner_kind, owner_id, resource_key, delta, unit, operation,
                       reason, source) VALUES(?,?,?,?,?,?,?,?,?)""",
                (new_id("reslog"), owner_kind, owner_id, key, initial, unit, "produce", "initial resource from Life Canon", "canon_rule"),
            )


def _deep_update(target: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    for k, v in (update or {}).items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_update(target[k], v)
        else:
            target[k] = v
    return target

# ----- Human/agent-friendly Canon IO helpers -----------------------------

def _set_nested_path(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in str(path).replace("/", ".").split(".") if p]
    if not parts:
        raise ValueError("path is required")
    cur: dict[str, Any] = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _delete_nested_path(obj: dict[str, Any], path: str) -> bool:
    parts = [p for p in str(path).replace("/", ".").split(".") if p]
    if not parts:
        raise ValueError("delete path is required")

    def rec(cur: Any, idx: int) -> bool:
        if not isinstance(cur, dict):
            return False
        remaining = ".".join(parts[idx:])
        # Resource keys and other identifiers may themselves contain dots
        # (e.g. resources.definitions.money.jpy). Prefer an exact remaining-key
        # match before treating the next dot as another nesting level.
        if remaining in cur:
            cur.pop(remaining, None)
            return True
        if idx >= len(parts):
            return False
        nxt = cur.get(parts[idx])
        return rec(nxt, idx + 1)

    return rec(obj, 0)


def patch_canon_draft(conn, owner_kind: str, owner_id: str, *, path: str | None = None, value: Any = None,
                      section: str | None = None, patch: dict[str, Any] | None = None,
                      text: str | None = None, delete_path: str | None = None,
                      delete_paths: list[str] | None = None, source: str = "agent") -> dict[str, Any]:
    """Patch CanonDraft without mutating active Canon.

    This is the safe write interface for agents to complete missing settings.
    Natural language goes through ``append_setup_statement``; structured patch
    updates CanonDraft.extracted and still requires /life commit to activate.
    """
    if text:
        return append_setup_statement(conn, owner_kind, owner_id, text, source=source)
    draft = begin_setup(conn, owner_kind, owner_id, reason="canon_patch")
    extracted = draft.get("extracted", {}) or {}
    removed_paths: list[str] = []
    raw_delete_paths: list[str] = []
    if delete_path:
        raw_delete_paths.append(str(delete_path))
    if delete_paths:
        raw_delete_paths.extend(str(p) for p in delete_paths if p)
    for pth in raw_delete_paths:
        _delete_nested_path(extracted, pth)
        removed_paths.append(pth)
    if removed_paths:
        tracked = list(extracted.get("__delete_paths__") or [])
        for pth in removed_paths:
            if pth not in tracked:
                tracked.append(pth)
        extracted["__delete_paths__"] = tracked
    if patch is not None:
        if section:
            base = extracted.setdefault(section, {})
            if not isinstance(base, dict):
                base = {}
                extracted[section] = base
            _deep_update(base, patch)
        else:
            _deep_update(extracted, patch)
    elif path:
        _set_nested_path(extracted, path, value)
    elif not removed_paths:
        raise ValueError("Provide text, patch, delete_path(s), or path+value")
    questions = _unresolved_questions(extracted, owner_kind)
    conn.execute(
        """UPDATE canon_drafts SET extracted_json=?, unresolved_questions_json=?, updated_at=datetime('now') WHERE id=?""",
        (dumps(extracted), dumps(questions), draft["id"]),
    )
    append_journal(conn, owner_kind, owner_id, "canon_draft_patched", {"draft_id": draft["id"], "path": path, "section": section, "patch": patch, "delete_paths": removed_paths}, "canon_io")
    return get_draft(conn, draft["id"])


def render_canon_summary(canon: dict[str, Any]) -> str:
    identity = canon.get("identity") or {}
    worldview = canon.get("worldview") or {}
    truth = canon.get("truth_sources") or {}
    bindings = truth.get("bindings") or {}
    resources = ((canon.get("resources") or {}).get("definitions") or {})
    sleep = canon.get("sleep") or {}
    autonomy = canon.get("autonomy") or {}
    lines = ["LifeEngine 设定摘要", "=================="]
    lines.append(f"身份：{identity.get('name') or identity.get('selfDescription') or identity.get('self_description') or '未设置'}")
    if identity.get("age") or identity.get("gender"):
        lines.append(f"  年龄/性别：{identity.get('age') or '-'} / {identity.get('gender') or '-'}")
    lines.append(f"世界观：{worldview.get('raw_world_description') or worldview.get('world_binding') or worldview.get('world_type') or '未设置'}")
    lines.append("真相源：")
    if bindings:
        for k, v in bindings.items():
            if isinstance(v, dict):
                lines.append(f"  - {k}: {v.get('authority') or '-'}" + (f" / {v.get('value')}" if v.get('value') else ""))
            else:
                lines.append(f"  - {k}: {v}")
    else:
        lines.append("  - 未设置")
    lines.append("资源：")
    if resources:
        for k, v in list(resources.items())[:12]:
            lines.append(f"  - {k}: {(v or {}).get('display_name') or k}, 初始值={(v or {}).get('initial', '-')}")
        if len(resources) > 12:
            lines.append(f"  - ... 还有 {len(resources)-12} 项")
    else:
        lines.append("  - 未设置")
    lines.append(f"睡眠：目标 {sleep.get('target_minutes') or sleep.get('defaultSleepHours') or '未设置'}；允许通宵={sleep.get('allow_all_nighter', '-')}")
    lines.append(f"自治：{autonomy or '默认开启 Agent 自我生活管理'}")
    lines.append("")
    lines.append("修改设定：/life setup <自然语言> 或 Agent 调用 life_config(action='patch')；提交：/life commit。")
    return "\n".join(lines)


def render_draft_summary(draft: dict[str, Any]) -> str:
    lines = ["LifeEngine 设定草案", "=================="]
    lines.append(f"草案：{draft.get('id')}；状态：{draft.get('status')}")
    extracted = draft.get("extracted") or {}
    if not extracted:
        lines.append("还没有抽取到结构化设定。")
    else:
        lines.append("已记录的设定块：" + "、".join(sorted(extracted.keys())))
    unresolved = draft.get("unresolved_questions") or []
    if unresolved:
        lines.append("还需要补充：")
        for q in unresolved:
            lines.append(f"  - {q}")
    lines.append("")
    lines.append("继续补充：/life setup <设定>；确认启用：/life commit。")
    return "\n".join(lines)
