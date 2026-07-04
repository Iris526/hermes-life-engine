"""Companion / idle outreach (v0.18.0 P2).

The pre-0.18 engine only ever reached out when *something happened* — an event
completed, a goal step, a dream. There was no path for "没事，就是想跟你说句话"
or "你上次说的那个面试怎么样了". This module adds that path: on the heartbeat,
when the agent has been quiet for a while and either it's in a good mood or it
remembers something the user shared that's worth circling back on, it asks the
host model (via LifeAuthor) to author one natural line and files it as a
proactive intent. The existing proactive evaluation then surfaces it on the next
turn (or pushes it, depending on policy).

Degradation: with no host model (dev/CI) LifeAuthor returns None and we simply
generate nothing — a canned "thinking of you" template would feel worse than
silence, so idle outreach is the one place we deliberately don't fall back.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from . import life_author
from . import relationship as rel
from .canon import get_active_canon
from .conversation import temporal_gate
from .emotion import current_mood, mood_band
from .jsonutil import loads
from .living import _skin_data
from .proactive import create_proactive_intent
from .time_utils import parse_datetime
from .trace import append_audit

_IDLE_TYPES = ("idle_share", "ask_about_user")
_COMPANION_MAX_CHARS = 90
_COMPANION_SYSTEM_PHRASES = (
    "LifeEngine",
    "outbox",
    "调度",
    "数据库",
    "tick",
    "心跳",
    "trace",
    "状态报告",
    "系统",
    "资源不足",
    "重新规划",
)
_COMPANION_MECHANICAL_PREFIXES = (
    "我有件事想跟你说",
    "我有一件事想跟你说",
    "有件事想跟你说",
)
_RECENT_IDLE_REPEAT_WINDOW_DAYS = 7
_RECENT_IDLE_REPEAT_JACCARD = 0.32
_GENERIC_IDLE_DEDUP_FILLERS = ("Ringo", "我刚", "刚刚", "忽然", "莫名", "想跟你", "想给你", "一句", "嘿嘿")

_DEFAULT_POLICY: dict[str, Any] = {
    "enabled": True,
    "idle_max_per_day": 3,
    "min_minutes_between": 180,
    "default_user_id": None,
    "timezone": "Asia/Shanghai",
}


def _time_facts_from_authoring(authoring_now: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 heartbeat authoring_now 中读取原始时间事实。

    输入是 `prepare_heartbeat_authoring` 传入的兼容上下文；输出是 reply path
    `data["time"]` 同形 dict 或 None。调用方是 companion 的事务外/事务内
    deterministic gate。函数只读内存，不访问模型或数据库。
    """
    if not isinstance(authoring_now, dict):
        return None
    raw = authoring_now.get("time")
    if isinstance(raw, dict):
        return raw
    if authoring_now.get("today_windows") is not None or authoring_now.get("phase_label") is not None:
        return authoring_now
    return None


def _candidate_ref_window(candidate: dict[str, Any] | None,
                          package: dict[str, Any] | None = None) -> str | dict[str, Any] | None:
    """读取 companion 候选显式声明的 Canon 日内窗口引用。

    输入是 `_candidate()` 的候选和可选事务外 authored 包；输出是窗口 key/fact 或
    None。当前 idle/follow-up 候选默认不声明窗口，因此不会被误杀；未来若某个
    companion 候选要围绕任意 Canon window 发起内容，只需填入通用 ref_window 字段，
    这里不会按餐名或 intent_type 特判。
    """
    for source in (candidate, package):
        if not isinstance(source, dict):
            continue
        for key in ("temporal_ref_window", "ref_window", "time_window", "canon_window", "window_key"):
            value = source.get(key)
            if value is not None:
                return value
    return None


def _companion_temporal_gate(candidate: dict[str, Any] | None,
                             grounding: dict[str, Any] | None,
                             package: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """为 companion 主动候选计算纯代码时间门控。

    输入是候选、`temporal_grounding` facts 和可选 authored 包；输出是
    `conversation.temporal_gate()` 决策或 None。调用方在 LifeAuthor 生成前和
    intent 落库前各检查一次；无窗口引用时不改变现有 idle/follow-up 行为。
    """
    ref_window = _candidate_ref_window(candidate, package)
    if ref_window is None:
        return None
    kind = str((candidate or {}).get("kind") or "companion")
    return temporal_gate(grounding or {}, f"companion:{kind}", ref_window=ref_window)

_LINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "emotional_tone": {"type": "string"},
    },
    "required": ["summary"],
}


def _trim_companion_line(text: str) -> str:
    """Normalize one candidate QQ companion line without changing its meaning."""
    msg = " ".join(str(text or "").strip().strip("\"'“”").split())
    for prefix in _COMPANION_MECHANICAL_PREFIXES:
        if msg.startswith(prefix):
            msg = msg[len(prefix):].lstrip("：:，,。 ")
            break
    return msg.strip()


def _address_text(value: Any) -> str | None:
    """从 Canon/skin 候选值里提取可用于称呼的文本。

    输入是字符串或包含称呼字段的 dict；输出是去空白后的称呼或 None。调用方是
    companion prompt 和去重逻辑。函数无副作用；非文本、空文本或未知形态都按
    None 处理，避免为无 skin 的现代 agent 合成角色称呼。
    """
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("address_term", "user_address_term", "user_display_name", "display_name", "name", "label"):
            text = _address_text(value.get(key))
            if text:
                return text
    return None


def _mapped_address_text(value: Any, user_id: str | None) -> str | None:
    """从按用户划分的 Canon 称呼映射里取当前用户的称呼。

    输入是可能的 user->称呼 映射和当前 user_id；输出是当前用户、default 或 primary
    对应的称呼。调用方是 `_canon_companion_address_term`。函数只读传入对象，缺失
    映射时返回 None，不回退到任何角色默认值。
    """
    if not isinstance(value, dict):
        return None
    candidates: list[Any] = []
    if user_id:
        candidates.append(value.get(user_id))
    candidates.extend(value.get(key) for key in ("default", "primary"))
    for candidate in candidates:
        text = _address_text(candidate)
        if text:
            return text
    return None


def _canon_companion_address_term(canon: dict[str, Any] | None, user_id: str | None) -> str | None:
    """读取 Canon 显式声明的陪伴称呼。

    输入是 active Canon 和目标 user_id；输出是 companion/relationship 块中声明的
    address term 或用户显示名。调用方是 idle prompt 与 idle 去重。函数只解释
    Canon，不读写数据库；Canon 未声明时返回 None，让无 skin agent 省略称呼子句。
    """
    if not isinstance(canon, dict):
        return None
    for block_name in ("companion", "relationship"):
        block = canon.get(block_name)
        if not isinstance(block, dict):
            continue
        for mapping_key in ("address_terms", "user_address_terms", "users"):
            text = _mapped_address_text(block.get(mapping_key), user_id)
            if text:
                return text
        for key in ("address_term", "user_address_term", "user_display_name", "display_name"):
            text = _address_text(block.get(key))
            if text:
                return text
    return None


def _active_agent_canon(conn, agent_id: str) -> dict[str, Any]:
    """读取 companion 当前 agent 的 active Canon。

    输入是 agent_id；输出是 active Canon 或空 dict。调用方是称呼解析。函数只读
    Canon；读取失败按空 Canon 处理，避免 companion 主动消息因称呼缺失而失败。
    """
    try:
        return get_active_canon(conn, "agent", agent_id) or {}
    except Exception:
        return {}


def _companion_address_term(conn, agent_id: str, user_id: str | None) -> str | None:
    """解析 idle companion 可使用的用户称呼。

    输入是数据库连接、agent_id 和 user_id；输出是 Canon 显式称呼、用户显示名或
    active skin 的 companion.address_term。调用方是 prompt 构造和重复度检查。
    副作用为无；没有任何来源时返回 None，prompt 不生成称呼子句，去重也不剥离
    角色称呼。
    """
    canon = _active_agent_canon(conn, agent_id)
    text = _canon_companion_address_term(canon, user_id)
    if text:
        return text
    try:
        skin = _skin_data(canon)
    except Exception:
        skin = {}
    companion = skin.get("companion") if isinstance(skin, dict) else {}
    return _address_text((companion or {}).get("address_term") if isinstance(companion, dict) else None)


def _companion_rejection_reason(text: str) -> str | None:
    """Return why an authored companion line should not become an intent."""
    raw = str(text or "")
    msg = _trim_companion_line(raw)
    if not msg:
        return "empty"
    if "\n" in raw or "\r" in raw:
        return "multiline"
    if len(msg) > _COMPANION_MAX_CHARS:
        return "too_long"
    for phrase in _COMPANION_SYSTEM_PHRASES:
        if phrase in msg:
            return f"system_phrase:{phrase}"
    if any(marker in msg for marker in ("1.", "2.", "首先", "其次", "建议：", "总结：")):
        return "report_like"
    sentence_marks = sum(msg.count(ch) for ch in "。！？!?")
    if sentence_marks > 2:
        return "too_many_sentences"
    return None


def _line_bigrams(text: str, *, address_term: str | None = None) -> set[str]:
    """生成陪伴文案去重用的轻量 bigram。

    输入是一句候选文案和当前 Canon/skin 解析出的可选称呼；输出是去掉通用 QQ
    口头词后的 bigram 集合。调用方是最近 idle 文案重复检查。函数无副作用；
    只有传入称呼时才剥离称呼，现代 agent 不会因固定角色词被误删。
    """
    s = _trim_companion_line(text)
    s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
    fillers = []
    if isinstance(address_term, str) and address_term.strip():
        fillers.append(address_term.strip())
    fillers.extend(_GENERIC_IDLE_DEDUP_FILLERS)
    for filler in fillers:
        s = s.replace(filler, "")
    return {s[i:i + 2] for i in range(max(0, len(s) - 1)) if s[i:i + 2].strip()}


def _looks_like_recent_idle_repeat(conn, agent_id: str, user_id: str | None, text: str,
                                   now: str | None = None) -> bool:
    """判断候选 idle 文案是否过于接近近期陪伴文案。

    输入是 agent/user、候选文本和可选逻辑时间；输出是是否应拒绝。调用方是
    `_sanitize_parsed_line`。函数只读 proactive_intents；窗口锚定传入的逻辑
    `now`，避免固定日期测试或回放因 wall-clock 漂移而漏掉近期重复。
    """
    address_term = _companion_address_term(conn, agent_id, user_id)
    mine = _line_bigrams(text, address_term=address_term)
    if len(mine) < 6:
        return False
    params: list[Any] = [agent_id]
    target_sql = ""
    if user_id:
        target_sql = " AND target_type='user' AND target_id=?"
        params.append(user_id)
    rows = conn.execute(
        "SELECT summary FROM proactive_intents WHERE agent_id=? "
        "AND intent_type IN ('idle_share','ask_about_user')"
        f"{target_sql} "
        "AND created_at >= datetime(?, ?) "
        "ORDER BY created_at DESC LIMIT 20",
        (*params, now or "now", f"-{_RECENT_IDLE_REPEAT_WINDOW_DAYS} days"),
    ).fetchall()
    for row in rows:
        other = _line_bigrams(str(row["summary"] or ""), address_term=address_term)
        if len(other) < 6:
            continue
        overlap = len(mine & other)
        union = len(mine | other) or 1
        if overlap / union >= _RECENT_IDLE_REPEAT_JACCARD:
            return True
        if overlap >= 8 and overlap / max(1, min(len(mine), len(other))) >= 0.50:
            return True
    return False


def _sanitize_parsed_line(conn, agent_id: str, kind: str, parsed: dict[str, Any] | None, *,
                          user_id: str | None = None, now: str | None = None,
                          trace_id: str | None = None) -> dict[str, Any] | None:
    """Validate and normalize LifeAuthor companion output before persistence."""
    if not isinstance(parsed, dict):
        return None
    raw = str(parsed.get("summary") or "")
    msg = _trim_companion_line(raw)
    reason = _companion_rejection_reason(raw)
    if not reason and _looks_like_recent_idle_repeat(conn, agent_id, user_id, msg, now=now):
        reason = "recent_repeat"
    if reason:
        append_audit(
            conn, "agent", agent_id, "companion_author_rejected", "warning",
            "LifeAuthor companion line rejected",
            {
                "kind": kind,
                "target_user_id": user_id,
                "reason": reason,
                "draft_preview": msg[:160],
            },
            trace_id=trace_id,
        )
        return None
    out = dict(parsed)
    out["summary"] = msg
    return out


def _gate(control: dict[str, Any] | None) -> tuple[str, str]:
    gates = (control or {}).get("module_gates") or {}
    proactive = str(gates.get("proactive", "pending_only") or "pending_only").strip().lower()
    companion = str(gates.get("companion", "auto") or "auto").strip().lower()
    return proactive, companion


def _policy(conn, agent_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT data_json FROM canon_versions WHERE owner_kind='agent' AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    data = loads(row["data_json"], {}) if row else {}
    companion_policy = (data.get("companion") or {}) if isinstance(data, dict) else {}
    merged = {**_DEFAULT_POLICY, **companion_policy}
    # The target user is resolved centrally by relationship.resolve_primary_user
    # (canon proactive.default_target_user_id) at the point of use, so the whole
    # loop — record / companion / dream / reflection — agrees on one id. A canon
    # companion.default_user_id still overrides if explicitly set.
    if not companion_policy.get("timezone") and isinstance(data, dict):
        proactive_policy = data.get("proactive") or {}
        quiet = proactive_policy.get("quiet_hours") or {}
        merged["timezone"] = proactive_policy.get("timezone") or quiet.get("timezone") or merged.get("timezone") or "Asia/Shanghai"
    return merged


def _zoneinfo(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or "Asia/Shanghai"))
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _local_day_utc_bounds(timezone_name: str | None, now: str | None = None) -> tuple[str, str]:
    tz = _zoneinfo(timezone_name)
    try:
        ref = parse_datetime(now, default_tz="UTC") if now else datetime.now(timezone.utc)
    except Exception:
        ref = datetime.now(timezone.utc)
    assert ref is not None
    local = ref.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        end_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _has_pending_idle(conn, agent_id: str, user_id: str | None = None) -> bool:
    if user_id:
        n = conn.execute(
            "SELECT COUNT(*) FROM proactive_intents WHERE agent_id=? AND target_type='user' AND target_id=? "
            "AND intent_type IN ('idle_share','ask_about_user') AND status IN ('generated','queued')",
            (agent_id, user_id),
        ).fetchone()[0]
        return int(n or 0) > 0
    n = conn.execute(
        "SELECT COUNT(*) FROM proactive_intents WHERE agent_id=? AND intent_type IN ('idle_share','ask_about_user') "
        "AND status IN ('generated','queued')",
        (agent_id,),
    ).fetchone()[0]
    return int(n or 0) > 0


def _today_idle_count(conn, agent_id: str, user_id: str | None = None,
                      *, timezone_name: str | None = None, now: str | None = None) -> int:
    start_utc, end_utc = _local_day_utc_bounds(timezone_name, now)
    if user_id:
        n = conn.execute(
            "SELECT COUNT(*) FROM proactive_intents WHERE agent_id=? AND target_type='user' AND target_id=? "
            "AND intent_type IN ('idle_share','ask_about_user') AND created_at >= ? AND created_at < ?",
            (agent_id, user_id, start_utc, end_utc),
        ).fetchone()[0]
        return int(n or 0)
    n = conn.execute(
        "SELECT COUNT(*) FROM proactive_intents WHERE agent_id=? AND intent_type IN ('idle_share','ask_about_user') "
        "AND created_at >= ? AND created_at < ?",
        (agent_id, start_utc, end_utc),
    ).fetchone()[0]
    return int(n or 0)


def _minutes_since_last_idle(conn, agent_id: str, user_id: str | None = None) -> float | None:
    if user_id:
        row = conn.execute(
            "SELECT (julianday('now') - julianday(created_at)) * 1440.0 AS mins FROM proactive_intents "
            "WHERE agent_id=? AND target_type='user' AND target_id=? AND intent_type IN ('idle_share','ask_about_user') "
            "ORDER BY created_at DESC LIMIT 1",
            (agent_id, user_id),
        ).fetchone()
        return float(row["mins"]) if row and row["mins"] is not None else None
    row = conn.execute(
        "SELECT (julianday('now') - julianday(created_at)) * 1440.0 AS mins FROM proactive_intents "
        "WHERE agent_id=? AND intent_type IN ('idle_share','ask_about_user') ORDER BY created_at DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    return float(row["mins"]) if row and row["mins"] is not None else None


def _recent_user_bedtime_signal(conn, agent_id: str, user_id: str, *, minutes: int = 480) -> bool:
    """Return True when the latest user turn looks like a bedtime sign-off.

    QQ companion idle messages should not fire right after Ringo says 困困/晚安.
    ReplyGate decisions are the closest always-on record of incoming messages;
    use only the latest message so a later real conversation naturally clears
    the sleep cue.
    """
    row = conn.execute(
        """SELECT incoming_message_preview,
                  (julianday('now') - julianday(created_at)) * 1440.0 AS mins
             FROM reply_gate_decisions
            WHERE owner_kind='agent' AND owner_id=? AND source='incoming_message'
            ORDER BY created_at DESC LIMIT 1""",
        (agent_id,),
    ).fetchone()
    if not row:
        return False
    try:
        if float(row["mins"] or 999999) > float(minutes):
            return False
    except Exception:
        return False
    text = str(row["incoming_message_preview"] or "").strip().lower()
    bedtime_words = ("晚安", "困困", "睡觉", "睡了", "睡啦", "睡咯", "困了", "good night", "gn")
    return any(w in text for w in bedtime_words)


def _recent_life(conn, agent_id: str, *, limit: int = 4) -> dict[str, list[str]]:
    mem = conn.execute(
        "SELECT content FROM memories WHERE owner_kind='agent' AND owner_id=? "
        "AND COALESCE(memory_type,'') NOT IN ('system_log','debug_trace','engineering_note','audit','system') "
        "ORDER BY created_at DESC LIMIT ?",
        (agent_id, int(limit)),
    ).fetchall()
    ev = conn.execute(
        "SELECT title FROM events WHERE owner_kind='agent' AND owner_id=? "
        "AND COALESCE(event_category,'') NOT IN ('system','system_maintenance','maintenance','debug','internal_audit') "
        "ORDER BY updated_at DESC LIMIT ?",
        (agent_id, int(limit)),
    ).fetchall()
    return {
        "memories": [str(r[0])[:120] for r in mem if str(r[0] or "").strip()],
        "events": [str(r[0])[:60] for r in ev if str(r[0] or "").strip()],
    }


def _candidate(conn, agent_id: str, *, control: dict[str, Any] | None = None,
               user_id: str | None = None, now: str | None = None) -> dict[str, Any] | None:
    """只读判断本轮是否值得生成一条陪伴主动意图。

    输入来自 heartbeat 或显式调用时的控制状态、agent/user id 和逻辑时间；输出是
    `ask_about_user` 或 `idle_share` 的候选描述，或在门控、节奏、心情条件不满足时
    返回 `None`。调用方是事务外 authoring 预备流程和事务内最终落库流程。该函数只读
    proactive/relationship/mood 状态，不访问网络、不创建 intent；事务内会再次调用它
    做节奏复查，避免预生成期间状态变化导致重复主动消息。
    """
    proactive_gate, companion_gate = _gate(control)
    if proactive_gate == "off" or companion_gate in {"off", "disabled", "manual", "false"}:
        return None
    pol = _policy(conn, agent_id)
    if not pol.get("enabled", True):
        return None
    user_id = user_id or pol.get("default_user_id") or rel.resolve_primary_user(conn, agent_id)
    if _recent_user_bedtime_signal(conn, agent_id, user_id):
        return None
    if _has_pending_idle(conn, agent_id, user_id):
        return None
    if _today_idle_count(conn, agent_id, user_id, timezone_name=pol.get("timezone"), now=now) >= int(pol.get("idle_max_per_day") or 3):
        return None
    gap = _minutes_since_last_idle(conn, agent_id, user_id)
    if gap is not None and gap < float(pol.get("min_minutes_between") or 180):
        return None
    due = rel.notes_due_for_followup(conn, agent_id, user_id, now=now, limit=1)
    if due:
        return {"kind": "ask_about_user", "user_id": user_id, "note": due[0]}
    if mood_band(current_mood(conn, "agent", agent_id)) == "high":
        return {"kind": "idle_share", "user_id": user_id}
    return None


def author_companion_for_tick(conn, agent_id: str, *, control: dict[str, Any] | None = None,
                              user_id: str | None = None, now: str | None = None,
                              trace_id: str | None = None,
                              authoring_now: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """在写事务外为陪伴主动意图预生成一句话。

    输入来自 heartbeat 的 agent/control/时间上下文；输出是一个短期有效的内存包，
    包含候选 kind、user_id、可选 note_id 以及 LifeAuthor 的 parsed 文案。调用方
    随后把该包传给 `maybe_generate_companion_intent`，由后者在事务内复查并创建
    proactive intent。副作用仅限 LifeAuthor 审计；如果无 host、节奏不满足或模型
    失败，返回 `None`，不写 relationship/proactive 状态。`authoring_now` 是 heartbeat
    事务外预先算好的结构化时间事实，只进入 LifeAuthor context，不改变节奏门。
    """
    try:
        cand = _candidate(conn, agent_id, control=control, user_id=user_id, now=now)
        if not cand:
            return None
        gate = _companion_temporal_gate(cand, _time_facts_from_authoring(authoring_now))
        if gate and gate.get("suppress"):
            return None
        if cand["kind"] == "ask_about_user":
            parsed = _author_followup_line(
                conn, agent_id, cand["user_id"], cand["note"], now=now,
                trace_id=trace_id, authoring_now=authoring_now,
            )
            note_id = cand["note"].get("id")
        else:
            parsed = _author_idle_line(
                conn, agent_id, cand["user_id"], trace_id=trace_id,
                authoring_now=authoring_now,
            )
            note_id = None
        parsed = _sanitize_parsed_line(conn, agent_id, cand["kind"], parsed, user_id=cand["user_id"], now=now, trace_id=trace_id)
        if not parsed or not str(parsed.get("summary") or "").strip():
            return None
        package = {"kind": cand["kind"], "user_id": cand["user_id"], "note_id": note_id, "parsed": parsed}
        if gate:
            package["temporal_gate"] = gate
        for ref_key in ("temporal_ref_window", "ref_window", "time_window", "canon_window", "window_key"):
            if cand.get(ref_key) is not None:
                package[ref_key] = cand.get(ref_key)
        return package
    except Exception:
        return None


def maybe_generate_companion_intent(conn, agent_id: str, *, control: dict[str, Any] | None = None,
                                    user_id: str | None = None, now: str | None = None,
                                    trace_id: str | None = None,
                                    authored: dict[str, Any] | None = None,
                                    allow_authoring: bool = True,
                                    temporal_grounding_facts: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """创建至多一条陪伴主动意图。

    输入来自 heartbeat 或工具层；`authored` 是事务外预生成的短期内存包，
    `allow_authoring=False` 表示事务内不得补调模型。输出是创建好的 proactive intent
    或 `None`。副作用是写 proactive_intents，并在 follow-up 成功创建后标记对应
    relationship note 已回访；所有失败都降级为 `None`，避免陪伴链路 destabilise
    heartbeat。函数会在落库前重新检查节奏和候选 note，保证幂等与不刷屏。
    `temporal_grounding_facts` 只用于显式窗口引用的纯代码 gate，不通过 prompt 指令
    改写模型行为。
    """
    try:
        cand = _candidate(conn, agent_id, control=control, user_id=user_id, now=now)
        if not cand:
            return None
        gate = _companion_temporal_gate(cand, temporal_grounding_facts, authored)
        if gate and gate.get("suppress"):
            return None
        package = authored
        if package is not None:
            if package.get("kind") != cand.get("kind") or package.get("user_id") != cand.get("user_id"):
                package = None
            if cand.get("note") and package and package.get("note_id") != cand["note"].get("id"):
                package = None
        if package is None and allow_authoring:
            package = author_companion_for_tick(conn, agent_id, control=control, user_id=cand.get("user_id"), now=now, trace_id=trace_id)
        if not package or not isinstance(package.get("parsed"), dict):
            return None
        parsed = _sanitize_parsed_line(conn, agent_id, cand["kind"], package.get("parsed"), user_id=cand["user_id"], now=now, trace_id=trace_id)
        if not parsed:
            return None
        package = {**package, "parsed": parsed}
        if cand["kind"] == "ask_about_user":
            return _create_followup_intent(conn, agent_id, cand["user_id"], cand["note"], package["parsed"], now=now, trace_id=trace_id)
        return _create_idle_intent(conn, agent_id, cand["user_id"], package["parsed"], trace_id=trace_id)
    except Exception:
        return None


def _author_followup_line(conn, agent_id: str, user_id: str, note: dict[str, Any], *,
                          now: str | None, trace_id: str | None,
                          authoring_now: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """只生成回访文案，不创建 proactive intent。

    输入是已通过节奏检查的一条 relationship note；输出是 LifeAuthor parsed dict。
    调用方是事务外预生成流程或兼容旧路径的事务外调用。副作用仅限 LifeAuthor 审计，
    失败返回 `None`，由上层保持沉默而不是发送模板化关心。`authoring_now` 只承载
    heartbeat 当前时间事实，空值时不进入 context。
    """
    context = {
        "对方上次跟你说过的他生活里的事": note.get("content"),
        "话题": note.get("topic"),
        "你对此的感受倾向": note.get("sentiment"),
    }
    if authoring_now:
        context["authoring_now"] = authoring_now
    instructions = (
        "对方上次跟你说过上面这件他生活里的事，你一直惦记着。"
        "现在想自然地问一句后续——关心，但别啰嗦、别像查岗。"
        "这是 QQ 私聊，只写一行第一人称聊天句；不要写报告、列表或系统解释。"
        "输出 summary=你想问的那一句话；emotional_tone=语气。"
    )
    parsed = life_author.author(conn, "agent", agent_id, kind="ask_about_user",
                               instructions=instructions, context=context, schema=_LINE_SCHEMA,
                               max_tokens=200, temperature=0.7, trace_id=trace_id)
    return parsed if isinstance(parsed, dict) else None


def _create_followup_intent(conn, agent_id: str, user_id: str, note: dict[str, Any],
                            parsed: dict[str, Any], *, now: str | None,
                            trace_id: str | None) -> dict[str, Any] | None:
    """把已生成的回访文案落为 proactive intent。

    输入是事务外生成的 parsed 文案和当前仍然 due 的 relationship note；输出是创建好的
    intent。副作用是写 proactive_intents 并标记 note followed_up_at。调用方是
    `maybe_generate_companion_intent`；失败返回 `None`，不会重复追问。
    """
    if not parsed or not str(parsed.get("summary") or "").strip():
        return None
    intent = create_proactive_intent(
        conn, agent_id,
        target_type="user", target_id=user_id,
        intent_type="ask_about_user", summary=str(parsed["summary"]).strip(),
        emotional_tone=str(parsed.get("emotional_tone") or "caring"),
        importance=75, urgency=45, novelty=70, relationship_relevance=80,
        privacy_level="safe_to_share", status="generated",
        generated_by="companion", source="companion",
        delivery_policy={"canPush": True, "canMentionNextTurn": True, "quietHoursRespect": True},
        trace_id=trace_id,
    )
    rel.mark_followed_up(conn, note["id"], now=now)
    return intent


def _author_idle_line(conn, agent_id: str, user_id: str, *, trace_id: str | None,
                      authoring_now: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """只生成闲聊陪伴文案，不创建 proactive intent。

    输入是 agent/user id 和 trace id；输出是 LifeAuthor parsed dict。调用方是事务外
    预生成流程；副作用仅限 LifeAuthor 审计。无 host 或返回无效时返回 `None`，
    保持“没事不硬发模板”的产品约束。`authoring_now` 只承载 heartbeat 当前时间事实，
    空值时不进入 context。
    """
    life = _recent_life(conn, agent_id)
    notes = [n.get("content") for n in rel.recent_salient_notes(conn, agent_id, user_id, limit=2) if n.get("content")]
    try:
        from . import opinions as _op
        stances = _op.opinion_phrases(conn, agent_id, limit=3)
    except Exception:
        stances = []
    def _usable(xs: list[str]) -> list[str]:
        out: list[str] = []
        for x in xs:
            s = str(x or "").strip()
            if not s or s.startswith("完成了『") or "LifeEngine" in s:
                continue
            out.append(s)
        return out

    context = {
        "你最近的生活片段": _usable(life.get("memories") or []),
        "你近来做的事": _usable(life.get("events") or []),
        "你记得的对方的生活": notes,
        "你最近的一些看法/在意的": stances,
        "此刻心情": "不错",
    }
    if authoring_now:
        context["authoring_now"] = authoring_now
    address_term = _companion_address_term(conn, agent_id, user_id)
    address_clause = f"，可以自然叫他“{address_term}”" if address_term else ""
    instructions = (
        "你现在心情不错，也没什么大事，就是想跟对方说句话——可以是你今天的一件小事、"
        "一个忽然冒出来的念头，或是想起了对方。一句话，自然、轻，像随手发的消息。"
        f"这是 QQ 私聊；用第一人称{address_clause}。不要像汇报，不要解释系统，"
        "不要总写添灯油/火苗/常明净愿灯；如果素材重复，就换成更贴近日常的小动作或一句惦记。"
        "输出 summary=你想说的那句话；emotional_tone=语气。"
    )
    parsed = life_author.author(conn, "agent", agent_id, kind="idle_share",
                               instructions=instructions, context=context, schema=_LINE_SCHEMA,
                               max_tokens=200, temperature=0.9, trace_id=trace_id)
    return parsed if isinstance(parsed, dict) else None


def _create_idle_intent(conn, agent_id: str, user_id: str, parsed: dict[str, Any], *,
                        trace_id: str | None) -> dict[str, Any] | None:
    """把已生成的闲聊文案落为 proactive intent。

    输入是事务外生成的 parsed 文案；输出是创建好的 idle_share intent。副作用只写
    proactive_intents，不访问模型、不修改 relationship note。调用方会先做节奏复查，
    因此这里保持窄职责。
    """
    if not parsed or not str(parsed.get("summary") or "").strip():
        return None
    return create_proactive_intent(
        conn, agent_id,
        target_type="user", target_id=user_id,
        intent_type="idle_share", summary=str(parsed["summary"]).strip(),
        emotional_tone=str(parsed.get("emotional_tone") or "warm"),
        importance=75, urgency=40, novelty=75, relationship_relevance=80,
        privacy_level="safe_to_share", status="generated",
        generated_by="companion", source="companion",
        delivery_policy={"canPush": True, "canMentionNextTurn": True, "quietHoursRespect": True},
        trace_id=trace_id,
    )
