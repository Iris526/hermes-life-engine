"""对话时间仲裁与用户当前活动时间窗。

本模块只维护“这轮聊天如何影响时间”的可审计运行态：它判断用户消息是否
会占用 Agent 的日程时间，并把用户报告的当前活动记录成带过期判断的
短期时间窗。它不直接改 Agent 日程；需要占用 Agent 时间时，只给出
`life_event.do_now` 等后续动作建议，真正排程仍由 LifeOps/事件模块负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .constants import DEFAULT_USER_ID
from .jsonutil import dumps, loads
from .time_utils import parse_datetime
from .trace import append_journal, new_id

# Day-phase bands by local hour. Deliberately distinguishes 深夜/凌晨 from 夜晚 so
# a reply at 00:30 reads "凌晨", not the same "夜晚" as 20:00 — that distinction is
# exactly what was missing when the agent kept suggesting dinner in the small hours.
_DAY_PHASES = [
    (0, "small_hours", "凌晨"),
    (5, "early_morning", "清晨"),
    (8, "morning", "上午"),
    (11, "noon", "中午"),
    (13, "afternoon", "下午"),
    (17, "dusk", "傍晚"),
    (19, "evening", "夜晚"),
    (23, "late_night", "深夜"),
]
_WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass(frozen=True)
class UserActivitySpec:
    """用户当前活动识别结果。

    含义：从一条用户消息中识别出的“用户正在做什么”。作用域只在
    conversation preflight 内有效，用来创建 `user_activity_spans`。
    生命周期从单条消息解析开始，到写入数据库或丢弃结束。调用方是
    `record_turn_interaction()`；字段来自用户消息与本地规则，不来自模型猜测。
    """

    activity_type: str
    title: str
    expected_duration_minutes: int
    grace_minutes: int
    confidence: float


@dataclass(frozen=True)
class AgentTimeJudgment:
    """聊天对 Agent 时间的占用判定。

    含义：一轮用户输入对 Agent 自身日程的影响分类。作用域是一次
    session/turn，可持久化到 `conversation_activity_judgments` 供上下文、
    trace 和测试读取。生命周期从消息进入网关或 pre-LLM context 开始，
    后续 turn 只读取最近判定，不把它当成已完成生活事实。调用方是网关
    preflight、context preflight 和 prompt context 渲染。
    """

    judgment_type: str
    agent_time_policy: str
    occupies_agent_time: bool
    expected_duration_minutes: int | None
    recommended_action: str
    confidence: float
    reason: str


BACKGROUND_PATTERNS = [
    "自拍",
    "生图",
    "画张",
    "画一张",
    "照片",
    "拍一张",
    "给我看",
    "穿搭",
    "怎么穿",
    "换身衣服",
]

CALL_PATTERNS = ["醒醒", "叫醒", "紧急", "urgent", "emergency", "立刻", "马上", "call"]

OCCUPY_PARTNER_PATTERNS = ["一起", "我们去", "陪我", "陪你", "跟我", "和我"]
OCCUPY_ACTIVITY_PATTERNS = ["逛街", "买", "购物", "吃饭", "散步", "看电影", "喝茶", "出去", "聊半小时", "陪我聊"]
FUTURE_PATTERNS = ["明天", "后天", "今晚", "晚上", "周末", "下周", "改天", "等会", "一会儿"]

USER_ACTIVITY_START_PATTERNS: list[tuple[re.Pattern[str], UserActivitySpec]] = [
    (
        re.compile(r"(?:我|俺|我这边)?(?:现在|正在|在|刚开始|准备|要去)?\s*(吃饭|吃早饭|吃午饭|吃晚饭|吃夜宵|用餐)"),
        UserActivitySpec("meal", "吃饭", 45, 45, 0.88),
    ),
    (
        re.compile(r"(?:我|俺|我这边)?(?:现在|正在|在)?\s*(开会|会议|上课|听课)"),
        UserActivitySpec("meeting_or_class", "开会/上课", 90, 30, 0.78),
    ),
    (
        re.compile(r"(?:我|俺|我这边)?(?:现在|正在|在)?\s*(路上|通勤|坐车|打车|开车|回家路上)"),
        UserActivitySpec("travel", "在路上", 45, 30, 0.76),
    ),
    (
        re.compile(r"(?:我|俺|我这边)?(?:现在|正在|在|准备|要去)?\s*(洗澡|冲澡)"),
        UserActivitySpec("shower", "洗澡", 30, 30, 0.82),
    ),
    (
        re.compile(r"(?:我|俺|我这边)?(?:现在|正在|在|准备|要)?\s*(睡觉|睡了|去睡|补觉)"),
        UserActivitySpec("sleep", "睡觉", 480, 120, 0.72),
    ),
]

USER_ACTIVITY_END_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(吃完|吃好了|吃过了|吃完饭|饭吃完)"), "meal"),
    (re.compile(r"(开完会|会开完|下课了|课上完)"), "meeting_or_class"),
    (re.compile(r"(到家了|到了|下车了)"), "travel"),
    (re.compile(r"(洗完澡|冲完澡)"), "shower"),
    (re.compile(r"(醒了|睡醒)"), "sleep"),
]


def _preview(text: str | None, max_len: int = 300) -> str:
    return (text or "").replace("\n", " ").strip()[:max_len]


def _looks_about_someone_else(text: str | None) -> bool:
    """过滤“你/他/她正在做什么”的询问。

    输入是原始用户消息；输出表示它是否更像在谈论别人而不是用户自己。
    调用方是用户活动识别；函数无副作用。这个保护避免把“你在吃饭吗”
    误写成用户自己的 `user_activity_span`。
    """

    s = text or ""
    return bool(re.search(
        r"[你妳他她它].{0,6}(吃饭|用餐|吃完|吃好了|吃过|开会|会议|开完会|上课|下课|路上|通勤|坐车|到家|到了|洗澡|洗完|睡觉|睡了|醒了)",
        s,
    ))


def _now_dt(now: str | None = None) -> datetime:
    parsed = parse_datetime(now) if now else None
    return parsed or datetime.now(timezone.utc)


def _iso_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def _detect_user_activity_start(text: str | None) -> UserActivitySpec | None:
    s = text or ""
    if _looks_about_someone_else(s):
        return None
    for pattern, spec in USER_ACTIVITY_START_PATTERNS:
        if pattern.search(s):
            return spec
    return None


def _detect_user_activity_end(text: str | None) -> str | None:
    s = text or ""
    if _looks_about_someone_else(s):
        return None
    for pattern, activity_type in USER_ACTIVITY_END_PATTERNS:
        if pattern.search(s):
            return activity_type
    return None


def judge_agent_time(text: str | None, *, reply_gate_decision: str | None = None) -> AgentTimeJudgment:
    """判断一条聊天是否占用 Agent 的生活时间。

    输入是当前用户消息和可选 ReplyGate 决策；输出是可持久化的占时分类。
    调用方式为同步纯函数，无数据库副作用。失败策略是保守降级为普通聊天：
    只有显式“现在一起做”的请求才建议占用日程时间。
    """

    s = (text or "").strip()
    low = s.lower()
    if reply_gate_decision == "defer":
        return AgentTimeJudgment("defer", "defer", False, None, "reply_gate.defer", 0.95, "ReplyGate 已判定需要延迟回复")
    if reply_gate_decision == "call_override" or any(w.lower() in low for w in CALL_PATTERNS):
        return AgentTimeJudgment("interrupt_or_call", "interrupt", True, None, "life_call", 0.9, "用户消息显式要求叫醒或紧急打断")
    if any(p in s for p in BACKGROUND_PATTERNS):
        return AgentTimeJudgment("background_response", "free", False, 0, "answer_without_reschedule", 0.82, "请求属于图片/穿搭/展示类回应，不占用正常日程")
    has_partner = any(p in s for p in OCCUPY_PARTNER_PATTERNS)
    has_activity = any(p in s for p in OCCUPY_ACTIVITY_PATTERNS)
    if has_partner and has_activity:
        duration = 90 if any(p in s for p in ["逛街", "购物", "看电影"]) else 60
        if "聊半小时" in s:
            duration = 30
        return AgentTimeJudgment("occupy_now", "occupy_now", True, duration, "life_event.do_now", 0.86, "用户邀请 Agent 现在共同参与一项活动")
    if any(p in s for p in FUTURE_PATTERNS) and has_activity:
        return AgentTimeJudgment("plan_future", "schedule_future", False, 60, "life_event.schedule", 0.68, "用户提到未来活动，适合创建或调整计划而不是占用当前时间")
    return AgentTimeJudgment("ambient_chat", "free", False, 0, "answer_without_reschedule", 0.55, "普通聊天默认不占用 Agent 日程")


def _decode_json_field(row: dict[str, Any], field: str, default: Any) -> None:
    if field in row:
        row[field.replace("_json", "")] = loads(row.pop(field), default)


def _decode_judgment(row: Any) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        d["occupies_agent_time"] = bool(d.get("occupies_agent_time"))
        _decode_json_field(d, "metadata_json", {})
    return d


def _decode_span(row: Any) -> dict[str, Any]:
    d = dict(row) if row else {}
    if d:
        _decode_json_field(d, "evidence_json", {})
    return d


def close_user_activity_from_message(conn, owner_kind: str, owner_id: str, *, user_id: str | None,
                                     text: str | None, now: str | None = None,
                                     source: str = "conversation_preflight") -> dict[str, Any] | None:
    """根据用户“已经结束了”的消息关闭最近的用户活动时间窗。

    输入来自当前用户消息；只关闭同一用户、同一活动类型下最近的 active /
    likely_ended 记录。输出为被关闭的 span，找不到匹配时返回 None。副作用是
    更新 `user_activity_spans` 并写 journal；失败由调用方事务回滚。
    """

    activity_type = _detect_user_activity_end(text)
    if not activity_type:
        return None
    user_id = user_id or DEFAULT_USER_ID
    now_dt = _now_dt(now)
    now_ts = int(now_dt.timestamp())
    row = conn.execute(
        """SELECT * FROM user_activity_spans
             WHERE owner_kind=? AND owner_id=? AND user_id=? AND activity_type=?
               AND status IN ('active','likely_ended')
             ORDER BY started_at_ts DESC, created_at DESC LIMIT 1""",
        (owner_kind, owner_id, user_id, activity_type),
    ).fetchone()
    if not row:
        return None
    conn.execute(
        """UPDATE user_activity_spans
              SET status='confirmed_ended', ended_at=?, ended_at_ts=?, updated_at=datetime('now')
            WHERE id=?""",
        (now_dt.isoformat(), now_ts, row["id"]),
    )
    append_journal(conn, owner_kind, owner_id, "user_activity_span_closed",
                   {"span_id": row["id"], "activity_type": activity_type, "user_id": user_id}, source)
    updated = conn.execute("SELECT * FROM user_activity_spans WHERE id=?", (row["id"],)).fetchone()
    return _decode_span(updated)


def create_user_activity_span(conn, owner_kind: str, owner_id: str, *, user_id: str | None,
                              spec: UserActivitySpec, message_text: str | None,
                              session_id: str | None = None, turn_id: str | None = None,
                              now: str | None = None, source: str = "conversation_preflight") -> dict[str, Any]:
    """创建用户当前活动时间窗。

    输入来自用户明确自述的当前活动；输出为持久化 span。副作用是写
    `user_activity_spans` 和 journal。时间字段均以 epoch 秒参与比较，
    ISO 文本只用于展示。若同一用户同类活动已 active，本函数不会合并旧
    记录，调用方通过 turn 幂等避免同消息重复写入。
    """

    user_id = user_id or DEFAULT_USER_ID
    start_dt = _now_dt(now)
    start_ts = int(start_dt.timestamp())
    expected_end_ts = start_ts + int(spec.expected_duration_minutes * 60)
    expires_ts = expected_end_ts + int(spec.grace_minutes * 60)
    span_id = new_id("usract")
    evidence = {
        "source": "user_message",
        "session_id": session_id,
        "turn_id": turn_id,
        "message_preview": _preview(message_text),
    }
    conn.execute(
        """INSERT INTO user_activity_spans(
             id, owner_kind, owner_id, user_id, activity_type, title, status,
             started_at, started_at_ts, expected_end_at, expected_end_at_ts,
             expires_at, expires_at_ts, expected_duration_minutes, grace_minutes,
             source_session_id, source_turn_id, source_message_preview,
             confidence, evidence_json, source
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            span_id, owner_kind, owner_id, user_id, spec.activity_type, spec.title, "active",
            start_dt.isoformat(), start_ts, _iso_from_ts(expected_end_ts), expected_end_ts,
            _iso_from_ts(expires_ts), expires_ts, int(spec.expected_duration_minutes), int(spec.grace_minutes),
            session_id, turn_id, _preview(message_text), float(spec.confidence), dumps(evidence), source,
        ),
    )
    append_journal(conn, owner_kind, owner_id, "user_activity_span_created",
                   {"span_id": span_id, "activity_type": spec.activity_type, "user_id": user_id}, source)
    row = conn.execute("SELECT * FROM user_activity_spans WHERE id=?", (span_id,)).fetchone()
    return _decode_span(row)


def observe_user_activity_from_message(conn, owner_kind: str, owner_id: str, *, user_id: str | None,
                                       text: str | None, session_id: str | None = None,
                                       turn_id: str | None = None, now: str | None = None,
                                       source: str = "conversation_preflight") -> dict[str, Any]:
    """把用户消息里的当前活动转成可过期的用户生活事件线索。

    输入是单条用户消息；输出包含创建或关闭的 span。调用方式为同步数据库写入，
    只处理用户明确自述，不从上下文猜测。副作用受外层事务保护；失败会交给
    调用方回滚。
    """

    closed = close_user_activity_from_message(
        conn, owner_kind, owner_id, user_id=user_id, text=text, now=now, source=source,
    )
    spec = _detect_user_activity_start(text)
    created = None
    if spec:
        created = create_user_activity_span(
            conn, owner_kind, owner_id, user_id=user_id, spec=spec, message_text=text,
            session_id=session_id, turn_id=turn_id, now=now, source=source,
        )
    return {"created": created, "closed": closed}


def record_turn_interaction(conn, owner_kind: str, owner_id: str, *, session_id: str | None,
                            turn_id: str | None, user_id: str | None, platform: str | None,
                            text: str | None, reply_gate_decision: str | None = None,
                            now: str | None = None, source: str = "conversation_preflight") -> dict[str, Any] | None:
    """记录一轮聊天的时间影响判定。

    输入来自网关 preflight 或 pre-LLM context；输出为 judgment 行。若同一
    owner/session/turn 已记录，则直接返回旧记录以保持幂等。副作用是可选创建
    用户活动 span、写 `conversation_activity_judgments` 和 journal。失败由
    外层事务回滚，不会自动改 Agent 日程。
    """

    if not (text or "").strip():
        return None
    if session_id and turn_id:
        existing = conn.execute(
            """SELECT * FROM conversation_activity_judgments
                 WHERE owner_kind=? AND owner_id=? AND session_id=? AND turn_id=?
                 ORDER BY created_at DESC LIMIT 1""",
            (owner_kind, owner_id, session_id, turn_id),
        ).fetchone()
        if existing:
            return _decode_judgment(existing)

    activity = observe_user_activity_from_message(
        conn, owner_kind, owner_id, user_id=user_id, text=text, session_id=session_id,
        turn_id=turn_id, now=now, source=source,
    )
    judgment = judge_agent_time(text, reply_gate_decision=reply_gate_decision)
    created_activity = activity.get("created") if activity else None
    closed_activity = activity.get("closed") if activity else None
    now_dt = _now_dt(now)
    jid = new_id("convjudge")
    metadata = {
        "created_user_activity_id": (created_activity or {}).get("id"),
        "closed_user_activity_id": (closed_activity or {}).get("id"),
        "platform": platform,
    }
    conn.execute(
        """INSERT INTO conversation_activity_judgments(
             id, owner_kind, owner_id, session_id, turn_id, user_id, platform,
             message_preview, judgment_type, agent_time_policy, occupies_agent_time,
             expected_duration_minutes, recommended_action, confidence, reason,
             related_user_activity_id, created_at_ts, metadata_json, source
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            jid, owner_kind, owner_id, session_id, turn_id, user_id or DEFAULT_USER_ID, platform,
            _preview(text), judgment.judgment_type, judgment.agent_time_policy,
            1 if judgment.occupies_agent_time else 0, judgment.expected_duration_minutes,
            judgment.recommended_action, float(judgment.confidence), judgment.reason,
            (created_activity or closed_activity or {}).get("id"), int(now_dt.timestamp()),
            dumps(metadata), source,
        ),
    )
    append_journal(conn, owner_kind, owner_id, "conversation_activity_judged",
                   {"judgment_id": jid, "judgment_type": judgment.judgment_type,
                    "occupies_agent_time": judgment.occupies_agent_time}, source)
    row = conn.execute("SELECT * FROM conversation_activity_judgments WHERE id=?", (jid,)).fetchone()
    return _decode_judgment(row)


def advance_user_activity_spans(conn, owner_kind: str, owner_id: str, *, user_id: str | None = None,
                                now: str | None = None, limit: int = 100,
                                source: str = "conversation_preflight") -> dict[str, Any]:
    """推进用户活动时间窗状态。

    输入是当前时间；输出列出从 active 变为 likely_ended、从 active/likely_ended
    变为 expired 的 span。调用方通常是每次构建上下文前。副作用是更新时间窗
    状态并写 journal；该过程幂等，重复运行不会重复推进已变更记录。
    """

    now_ts = int(_now_dt(now).timestamp())
    params: list[Any] = [owner_kind, owner_id, now_ts]
    user_clause = ""
    if user_id:
        user_clause = " AND user_id=?"
        params.append(user_id)
    likely_rows = conn.execute(
        f"""SELECT * FROM user_activity_spans
              WHERE owner_kind=? AND owner_id=? AND status='active'
                AND expected_end_at_ts IS NOT NULL AND expected_end_at_ts <= ?{user_clause}
              ORDER BY expected_end_at_ts LIMIT ?""",
        (*params, int(limit)),
    ).fetchall()
    likely_ids = [r["id"] for r in likely_rows]
    for sid in likely_ids:
        conn.execute("UPDATE user_activity_spans SET status='likely_ended', updated_at=datetime('now') WHERE id=?", (sid,))

    params = [owner_kind, owner_id, now_ts]
    if user_id:
        params.append(user_id)
    expired_rows = conn.execute(
        f"""SELECT * FROM user_activity_spans
              WHERE owner_kind=? AND owner_id=? AND status IN ('active','likely_ended')
                AND expires_at_ts IS NOT NULL AND expires_at_ts <= ?{user_clause}
              ORDER BY expires_at_ts LIMIT ?""",
        (*params, int(limit)),
    ).fetchall()
    expired_ids = [r["id"] for r in expired_rows]
    for sid in expired_ids:
        conn.execute("UPDATE user_activity_spans SET status='expired', updated_at=datetime('now') WHERE id=?", (sid,))

    if likely_ids or expired_ids:
        append_journal(conn, owner_kind, owner_id, "user_activity_spans_advanced",
                       {"likely_ended": likely_ids, "expired": expired_ids}, source)
    return {
        "ok": True,
        "likely_ended": [_decode_span(r) for r in likely_rows],
        "expired": [_decode_span(r) for r in expired_rows],
    }


def list_user_activity_spans(conn, owner_kind: str, owner_id: str, *, user_id: str | None = None,
                             statuses: tuple[str, ...] = ("active", "likely_ended"),
                             limit: int = 10) -> list[dict[str, Any]]:
    """读取仍会影响对话理解的用户活动时间窗。

    输入是 owner/user/status 过滤；输出按开始时间倒序排列的 span。函数只读
    数据库，无副作用。调用方是 context capsule、测试和未来可视化入口。
    """

    params: list[Any] = [owner_kind, owner_id]
    status_clause = ""
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        status_clause = f" AND status IN ({placeholders})"
        params.extend(statuses)
    user_clause = ""
    if user_id:
        user_clause = " AND user_id=?"
        params.append(user_id)
    params.append(int(limit))
    rows = conn.execute(
        f"""SELECT * FROM user_activity_spans
              WHERE owner_kind=? AND owner_id=?{status_clause}{user_clause}
              ORDER BY started_at_ts DESC, created_at DESC LIMIT ?""",
        tuple(params),
    ).fetchall()
    return [_decode_span(r) for r in rows]


def latest_activity_judgment(conn, owner_kind: str, owner_id: str, *, session_id: str | None = None,
                             turn_id: str | None = None) -> dict[str, Any] | None:
    """读取当前 turn 或最近一条聊天时间判定。

    输入是可选 session/turn；输出为 judgment 或 None。函数只读数据库，无副作用。
    调用方是上下文注入和调试测试。
    """

    if session_id and turn_id:
        row = conn.execute(
            """SELECT * FROM conversation_activity_judgments
                 WHERE owner_kind=? AND owner_id=? AND session_id=? AND turn_id=?
                 ORDER BY created_at DESC LIMIT 1""",
            (owner_kind, owner_id, session_id, turn_id),
        ).fetchone()
        if row:
            return _decode_judgment(row)
    row = conn.execute(
        """SELECT * FROM conversation_activity_judgments
             WHERE owner_kind=? AND owner_id=?
             ORDER BY created_at_ts DESC, created_at DESC LIMIT 1""",
        (owner_kind, owner_id),
    ).fetchone()
    return _decode_judgment(row) if row else None


def interaction_time_context(conn, owner_kind: str, owner_id: str, *, session_id: str | None = None,
                             turn_id: str | None = None, user_id: str | None = None,
                             now: str | None = None) -> dict[str, Any]:
    """构建 prompt 使用的互动时间胶囊。

    输入是当前 owner/session/turn/user；输出包含最近判定、仍需考虑的用户活动
    和模型行为规则。副作用是先推进过期状态，确保注入内容不会把旧状态当成现在。
    """

    advance_user_activity_spans(conn, owner_kind, owner_id, user_id=user_id, now=now)
    spans = list_user_activity_spans(conn, owner_kind, owner_id, user_id=user_id, limit=6)
    judgment = latest_activity_judgment(conn, owner_kind, owner_id, session_id=session_id, turn_id=turn_id)
    return {
        "latest_judgment": judgment or {},
        "user_activity_spans": [
            {
                "id": s.get("id"),
                "user_id": s.get("user_id"),
                "activity_type": s.get("activity_type"),
                "title": s.get("title"),
                "status": s.get("status"),
                "started_at": s.get("started_at"),
                "expected_end_at": s.get("expected_end_at"),
                "expires_at": s.get("expires_at"),
                "confidence": s.get("confidence"),
            }
            for s in spans
        ],
        "rules": [
            "ambient_chat/background_response 不占用 Agent 日程；不要因为普通聊天自动挪走她原本的安排。",
            "occupy_now 只表示需要调用 life_event.do_now 或等价 LifeOps 后才真的占用当前时间。",
            "用户活动 status=likely_ended 时，不要继续断言用户仍在做这件事；应询问或按已大概率结束处理。",
        ],
    }


def _tz_from_canon(canon: dict[str, Any] | None) -> tuple[ZoneInfo, str]:
    data = canon or {}
    name = (
        ((data.get("schedule_rules") or {}).get("timezone"))
        or (((data.get("truth_sources") or {}).get("bindings") or {}).get("time") or {}).get("timezone")
    )
    try:
        return ZoneInfo(str(name or "UTC")), str(name or "UTC")
    except Exception:
        return ZoneInfo("UTC"), "UTC"


def _phase_for_hour(hour: int) -> tuple[str, str]:
    chosen = _DAY_PHASES[0]
    for band in _DAY_PHASES:
        if hour >= band[0]:
            chosen = band
    return chosen[1], chosen[2]


def _humanize_gap(minutes: int) -> str:
    """Precise (not bucketed) human phrasing for an elapsed gap."""
    if minutes < 1:
        return "刚刚"
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}小时{mins}分钟前"
    if hours:
        return f"{hours}小时前"
    return f"{mins}分钟前"


def _today_windows(conn, owner_kind: str, owner_id: str, canon: dict[str, Any] | None,
                   local: datetime) -> list[dict[str, Any]]:
    """Each of today's canon-defined daily windows, related to the current local
    time. Meals are the source of windows today, but the relation logic is generic
    — nothing here is meal-specific; any windowed daily routine flows through it.
    A dinner window reads relation='passed', minutes=421 late at night instead of
    'pending', which is the fact the model needs to stop offering dinner at 3am.
    """
    from .meals import _meal_config, meal_status_for_date

    cfg = _meal_config(canon)
    if not cfg.get("enabled"):
        return []
    date_key = local.date().isoformat()
    try:
        status = (meal_status_for_date(conn, owner_kind, owner_id, date_key, canon) or {}).get("meals") or {}
    except Exception:
        status = {}
    window_min = int(cfg.get("window_minutes") or 150)
    now_min = local.hour * 60 + local.minute
    out: list[dict[str, Any]] = []
    for key, hhmm in (cfg.get("times") or {}).items():
        try:
            h, m = (int(x) for x in str(hhmm).split(":")[:2])
        except Exception:
            continue
        start = h * 60 + m
        end = start + window_min
        if now_min < start:
            relation, delta = "upcoming", start - now_min      # minutes until it opens
        elif now_min <= end:
            relation, delta = "in_window", end - now_min        # minutes left in window
        else:
            relation, delta = "passed", now_min - end           # minutes since it closed
        st = (status.get(key) or {}).get("status") or "pending"
        out.append({
            "key": key,
            "window": f"{h:02d}:{m:02d}-{end // 60:02d}:{end % 60:02d}",
            "relation": relation,
            "minutes": delta,
            "status": st,
            "recorded_today": st != "pending",
        })
    return out


def temporal_grounding(conn, owner_kind: str, owner_id: str, *, canon: dict[str, Any] | None = None,
                       now: str | None = None, session_id: str | None = None,
                       turn_id: str | None = None) -> dict[str, Any]:
    """Precise temporal facts for the reply capsule — DATA, not prose instructions.

    The reply context had no sharp sense of *when now is* or *how long since we
    last spoke*, so after a 4h gap the model kept the previous thread going (e.g.
    still suggesting dinner at 3am). This returns exact facts the model reasons
    over on its own: local time + day phase, the precise gap since the previous
    exchange, and each daily window's status relative to now. No behavioural
    instruction is added — the facts carry it.
    """
    now_dt = _now_dt(now)
    tz, tz_name = _tz_from_canon(canon)
    local = now_dt.astimezone(tz)
    now_ts = int(now_dt.timestamp())
    phase, phase_label = _phase_for_hour(local.hour)
    grounding: dict[str, Any] = {
        "now_local": local.strftime("%Y-%m-%d %H:%M"),
        "timezone": tz_name,
        "weekday": _WEEKDAY_ZH[local.weekday()],
        "phase": phase,
        "phase_label": phase_label,
    }

    # Precise gap since the previous exchange: the newest prior conversation
    # judgment's timestamp (this turn's row, if already written, is excluded by
    # turn_id). None on the first exchange — never fabricate a gap.
    params: list[Any] = [owner_kind, owner_id]
    exclude = ""
    if turn_id is not None:
        exclude = " AND (turn_id IS NULL OR turn_id != ?)"
        params.append(turn_id)
    row = conn.execute(
        "SELECT created_at_ts FROM conversation_activity_judgments "
        "WHERE owner_kind=? AND owner_id=? AND created_at_ts IS NOT NULL" + exclude +
        " ORDER BY created_at_ts DESC, rowid DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    if row and row["created_at_ts"] is not None:
        gap_min = max(0, int((now_ts - int(row["created_at_ts"])) // 60))
        grounding["since_last_exchange"] = {
            "minutes": gap_min,
            "human": _humanize_gap(gap_min),
        }
    else:
        grounding["since_last_exchange"] = None

    grounding["today_windows"] = _today_windows(conn, owner_kind, owner_id, canon, local)
    return grounding
