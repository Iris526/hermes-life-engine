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
from .emotion import current_mood, mood_band
from .jsonutil import loads
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

_DEFAULT_POLICY: dict[str, Any] = {
    "enabled": True,
    "idle_max_per_day": 3,
    "min_minutes_between": 180,
    "default_user_id": "anonymous-user",
    "timezone": "Asia/Shanghai",
}

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


def _line_bigrams(text: str) -> set[str]:
    """Return lightweight content bigrams for repeated companion-line checks."""
    s = _trim_companion_line(text)
    s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
    # Remove common QQ filler so repeated props/actions carry the score, not
    # every line beginning with 师兄/我刚/忽然想.
    for filler in ("师兄", "Ringo", "我刚", "刚刚", "忽然", "莫名", "想跟你", "想给你", "一句", "嘿嘿"):
        s = s.replace(filler, "")
    return {s[i:i + 2] for i in range(max(0, len(s) - 1)) if s[i:i + 2].strip()}


def _looks_like_recent_idle_repeat(conn, agent_id: str, user_id: str | None, text: str,
                                   now: str | None = None) -> bool:
    """Return True when a draft is too similar to recent companion prose.

    The recency window is anchored to the tick's LOGICAL ``now`` when supplied,
    not wall-clock ``datetime('now')``. A wall-clock anchor made the window slide
    out from under fixed-date test fixtures (and, in replays, drift off the data),
    so an intent authored inside the tick's own timeframe could fall outside the
    dedup window and reappear as a rephrased repeat.
    """
    mine = _line_bigrams(text)
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
        other = _line_bigrams(str(row["summary"] or ""))
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
    # If the companion layer does not name its own target user, inherit the
    # proactive delivery target. Otherwise idle messages can be authored for
    # anonymous-user, missing real relationship follow-ups and creating a
    # separate cooldown/pending queue from the actual QQ recipient.
    if not companion_policy.get("default_user_id") and isinstance(data, dict):
        proactive_target = (data.get("proactive") or {}).get("default_target_user_id")
        if proactive_target:
            merged["default_user_id"] = proactive_target
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
    user_id = user_id or str(pol.get("default_user_id") or "anonymous-user")
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
                              trace_id: str | None = None) -> dict[str, Any] | None:
    """在写事务外为陪伴主动意图预生成一句话。

    输入来自 heartbeat 的 agent/control/时间上下文；输出是一个短期有效的内存包，
    包含候选 kind、user_id、可选 note_id 以及 LifeAuthor 的 parsed 文案。调用方
    随后把该包传给 `maybe_generate_companion_intent`，由后者在事务内复查并创建
    proactive intent。副作用仅限 LifeAuthor 审计；如果无 host、节奏不满足或模型
    失败，返回 `None`，不写 relationship/proactive 状态。
    """
    try:
        cand = _candidate(conn, agent_id, control=control, user_id=user_id, now=now)
        if not cand:
            return None
        if cand["kind"] == "ask_about_user":
            parsed = _author_followup_line(conn, agent_id, cand["user_id"], cand["note"], now=now, trace_id=trace_id)
            note_id = cand["note"].get("id")
        else:
            parsed = _author_idle_line(conn, agent_id, cand["user_id"], trace_id=trace_id)
            note_id = None
        parsed = _sanitize_parsed_line(conn, agent_id, cand["kind"], parsed, user_id=cand["user_id"], now=now, trace_id=trace_id)
        if not parsed or not str(parsed.get("summary") or "").strip():
            return None
        return {"kind": cand["kind"], "user_id": cand["user_id"], "note_id": note_id, "parsed": parsed}
    except Exception:
        return None


def maybe_generate_companion_intent(conn, agent_id: str, *, control: dict[str, Any] | None = None,
                                    user_id: str | None = None, now: str | None = None,
                                    trace_id: str | None = None,
                                    authored: dict[str, Any] | None = None,
                                    allow_authoring: bool = True) -> dict[str, Any] | None:
    """创建至多一条陪伴主动意图。

    输入来自 heartbeat 或工具层；`authored` 是事务外预生成的短期内存包，
    `allow_authoring=False` 表示事务内不得补调模型。输出是创建好的 proactive intent
    或 `None`。副作用是写 proactive_intents，并在 follow-up 成功创建后标记对应
    relationship note 已回访；所有失败都降级为 `None`，避免陪伴链路 destabilise
    heartbeat。函数会在落库前重新检查节奏和候选 note，保证幂等与不刷屏。
    """
    try:
        cand = _candidate(conn, agent_id, control=control, user_id=user_id, now=now)
        if not cand:
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
                          now: str | None, trace_id: str | None) -> dict[str, Any] | None:
    """只生成回访文案，不创建 proactive intent。

    输入是已通过节奏检查的一条 relationship note；输出是 LifeAuthor parsed dict。
    调用方是事务外预生成流程或兼容旧路径的事务外调用。副作用仅限 LifeAuthor 审计，
    失败返回 `None`，由上层保持沉默而不是发送模板化关心。
    """
    context = {
        "对方上次跟你说过的他生活里的事": note.get("content"),
        "话题": note.get("topic"),
        "你对此的感受倾向": note.get("sentiment"),
    }
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


def _author_idle_line(conn, agent_id: str, user_id: str, *, trace_id: str | None) -> dict[str, Any] | None:
    """只生成闲聊陪伴文案，不创建 proactive intent。

    输入是 agent/user id 和 trace id；输出是 LifeAuthor parsed dict。调用方是事务外
    预生成流程；副作用仅限 LifeAuthor 审计。无 host 或返回无效时返回 `None`，
    保持“没事不硬发模板”的产品约束。
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
    instructions = (
        "你现在心情不错，也没什么大事，就是想跟对方说句话——可以是你今天的一件小事、"
        "一个忽然冒出来的念头，或是想起了对方。一句话，自然、轻，像随手发的消息。"
        "这是 QQ 私聊；用第一人称，可以自然叫他“师兄”。不要像汇报，不要解释系统，"
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
