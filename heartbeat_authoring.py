"""Heartbeat 生成式内容预备层。

本模块是 v0.18 LifeAuthor 与确定性 heartbeat 之间的隔离带：它只在
SQLite 写事务外读取当前状态、调用宿主模型并返回短期内存结果；真正的
事件、梦、观点、主动意图仍由 runtime 在 LifeOps / heartbeat 事务里落库。
"""

from __future__ import annotations

from typing import Any

from . import autonomy
from . import campaigns
from . import companion
from . import dream
from . import execution
from . import life_author
from . import opinions
from . import proactive
from . import social_projector
from .canon import get_active_canon
from .context_policy import _compact_time
from .conversation import temporal_grounding
from .events import due_wake_jobs


def _authoring_now_grounding(conn, owner_kind: str, owner_id: str, *,
                             now: str | None = None) -> dict[str, Any]:
    """构建 heartbeat LifeAuthor 共用的“此刻”事实块。

    输入是当前 owner 和 tick 逻辑时间；输出是只含事实的紧凑时间锚点，作用域限于
    本次 `prepare_heartbeat_authoring` 事务外预生成包。调用方把同一份 dict 传给
    dream、companion、execution、serendipity 和 proactive outbox authoring context。
    副作用只读 Canon 与 conversation judgment；任何读取、时区或压缩失败都会返回
    空 dict，让无 host / 异常路径继续走原有确定性 fallback。
    """
    try:
        canon = get_active_canon(conn, owner_kind, owner_id)
        raw = temporal_grounding(conn, owner_kind, owner_id, canon=canon, now=now)
        if not raw:
            return {}
        compact = _compact_time(raw, minimal=True)
        if not compact:
            return {}
        if raw.get("timezone") is not None:
            compact["timezone"] = raw.get("timezone")
        if raw.get("phase_label") is not None:
            compact["phase"] = raw.get("phase_label")
        since = raw.get("since_last_exchange")
        if isinstance(since, dict):
            compact["since_last_exchange"] = {
                "minutes": since.get("minutes"),
                "human": since.get("human"),
            }
        else:
            compact["since_last_exchange"] = None
        return compact
    except Exception:
        return {}


def owner_authoring_pack_key(owner_kind: str, owner_id: str) -> str:
    """生成 heartbeat authoring 包里的 owner 级键。

    输入是 owner_kind/owner_id；输出只在本次内存包中使用的稳定字符串。调用方是
    campaign auto-seed 的事务外写入和事务内读取；副作用为零。用双字段拼接而不是只
    用 owner_id，避免未来多 owner 类型共用同一 authoring 包时发生键碰撞。
    """
    return f"{owner_kind}:{owner_id}"


def _top_active_goals_for_campaign_brief(conn, owner_kind: str, owner_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """读取自动开 campaign brief 所需的高优先级 active goals。

    输入是 owner 和数量上限；输出是精简后的 goal 列表，作用域只限本次
    `campaign_seed` LifeAuthor context。调用方是事务外 authoring 预备层；副作用为零。
    读取失败时返回空列表，让无 goal/旧库路径仍可静默降级。
    """
    try:
        rows = conn.execute(
            """SELECT id, title, description, goal_type, priority, progress, target_date
                 FROM goals
                WHERE owner_kind=? AND owner_id=? AND status='active'
                ORDER BY priority DESC, updated_at DESC
                LIMIT ?""",
            (owner_kind, owner_id, int(limit)),
        ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "title": str(r["title"] or ""),
                "description": r["description"],
                "goal_type": r["goal_type"],
                "priority": int(r["priority"] or 0),
                "progress": float(r["progress"] or 0),
                "target_date": r["target_date"],
            }
            for r in rows
        ]
    except Exception:
        return []


def _campaign_autoseed_brief(conn, owner_kind: str, owner_id: str, *, now: str,
                             idle_days: int, eligibility: dict[str, Any]) -> str:
    """把显著观点和高优先级目标压成 campaign seed brief。

    输入是当前 owner、逻辑时间、idle 配置和前置判定结果；输出是一段给
    `life_author.author(kind="campaign_seed")` 的自然语言 brief。调用方是事务外
    heartbeat authoring；副作用只读 opinions/goals。没有任何显著看法和 active goal
    时返回空字符串，避免模型在无生活信号时凭空编资料片。brief 只陈述她已有的看法
    和目标，不硬编码人物名、主题或剧情，具体资料片内容交给宿主模型生成。
    """
    salient = []
    try:
        salient = opinions.salient_opinions(conn, owner_id, limit=6)
    except Exception:
        salient = []
    goals = _top_active_goals_for_campaign_brief(conn, owner_kind, owner_id, limit=5)
    if not salient and not goals:
        return ""
    lines = [
        f"当前时间：{now}",
        f"已经没有正在推进的资料片；最近一次资料片活动：{eligibility.get('last_campaign_at') or '从未有过'}。",
        f"如果要自发开始一条新的多日生活弧，请至少避开过去 {max(1, int(idle_days))} 天内刚结束或刚创建过资料片的重复感。",
        "请从下面这些已有看法和目标里找由头，不必逐条覆盖，也不要编造系统语境。",
        "显著看法：",
    ]
    if salient:
        for op in salient:
            target = str(op.get("target") or "").strip()
            if not target:
                continue
            strength = round(float(op.get("strength") or 0), 2)
            confidence = round(float(op.get("confidence") or 0), 2)
            reason = str(op.get("reason") or "").strip()
            suffix = f"；原因：{reason}" if reason else ""
            lines.append(f"- 对「{target}」：{op.get('opinion_type') or '看法'}，强度 {strength}，信心 {confidence}{suffix}")
    else:
        lines.append("- 暂无显著看法。")
    lines.append("高优先级目标：")
    if goals:
        for goal in goals:
            desc = str(goal.get("description") or "").strip()
            desc_part = f"；说明：{desc}" if desc else ""
            lines.append(
                f"- {goal.get('title')}（类型 {goal.get('goal_type')}，优先级 {goal.get('priority')}，"
                f"进度 {goal.get('progress')}%{desc_part}）"
            )
    else:
        lines.append("- 暂无 active goal。")
    return "\n".join(lines)


def _campaign_seed_instructions() -> str:
    """返回 heartbeat auto-seed 复用的 campaign_seed 输出契约说明。

    调用方是事务外 auto-seed authoring；输出与 manual `campaign(action="seed")`
    使用同一类 `campaign_seed` schema。副作用为零。说明只定义资料片结构，不写入
    任何具体主题，确保资料片内容仍来自模型和她的生活数据。
    """
    return (
        "给你自己张罗一件最近想做的大事，铺成一条跨越若干天的弧。"
        "分几个阶段（预兆/铺垫→升温→高潮→收尾），越往后越密、越要紧。"
        "每个阶段给 title、duration_days、daily_spawns（每天铺几件相关小事）、"
        "spawn_template（每天那类事的模板：title/event_type/importance/duration_minutes）、"
        "可选 one_time_events（这个阶段的关键节点事件）。输出 title、description、importance、phases。"
        "全部关于*生活*，自洽，别提任何系统/工程词。"
    )


def _prepare_campaign_autoseed_authoring(conn, owner_kind: str, owner_id: str, control: dict[str, Any], *,
                                         now: str, trace_id: str | None) -> dict[str, Any] | None:
    """在写事务外预生成 heartbeat 自动开 campaign 的 blueprint。

    输入是当前 owner/control/逻辑时间；输出是 `{blueprint, brief, idle_days, eligibility}`
    或 `None`。调用方是 `prepare_heartbeat_authoring`，事务内 runner 只消费这个
    blueprint，不再访问宿主模型。副作用仅限 LifeAuthor 自身审计；无 host、门控关闭、
    已有 active campaign、idle 未到或模型异常都返回 `None`，保证 campaign 表不变。
    """
    try:
        canon = get_active_canon(conn, owner_kind, owner_id)
        idle_days = campaigns.autoseed_idle_days(canon)
        eligibility = campaigns.autoseed_eligibility(
            conn, owner_kind, owner_id, control, now=now, idle_days=idle_days,
        )
        if not eligibility.get("eligible"):
            return None
        brief = _campaign_autoseed_brief(
            conn, owner_kind, owner_id, now=now, idle_days=idle_days, eligibility=eligibility,
        )
        if not brief.strip():
            return None
        parsed = life_author.author(
            conn, owner_kind, owner_id, kind="campaign_seed",
            instructions=_campaign_seed_instructions(),
            context={"由头/想法": brief},
            schema=campaigns.SEED_SCHEMA, max_tokens=1400, temperature=0.85, trace_id=trace_id,
        )
        if not parsed or not (parsed.get("phases") or []):
            return None
        return {
            "blueprint": parsed,
            "brief": brief,
            "idle_days": idle_days,
            "eligibility": eligibility,
        }
    except Exception:
        return None


def prepare_heartbeat_authoring(conn, owner_kind: str, owner_id: str, control: dict[str, Any], *,
                                now: str, tick_id: str, trace_id: str | None,
                                manual: bool) -> dict[str, Any]:
    """在 heartbeat 写事务外预生成本轮可能需要的 LifeAuthor 内容。

    输入来自 `LifeEngineRuntime.tick()` 已创建的 tick/trace/control；输出是一个只在
    本次 tick 内有效的内存包，键包括 `autonomy_goal_step`、`reflection`、
    `companion`、`campaign_autoseeds_by_owner`、`execution_narratives_by_block_id`、`serendipity_texts_by_block_id`、
    `social_projection_rumors_by_block_id`、`venture_sale_projection_rumors_by_occurrence_id`、
    `proactive_outbox_drafts` 和 `dreams_by_sleep_plan_id`。调用方会把这些结构化结果
    传入事务内子流程消费。副作用仅限各 LifeAuthor 调用自己的审计记录；本函数不写
    生活事实、不 claim wake job、不创建 event/outbox/opinion/dream/serendipity。
    失败逐项降级为空包，保证 heartbeat 仍可用确定性模板继续执行。
    """
    package: dict[str, Any] = {
        "autonomy_goal_step": None,
        "reflection": None,
        "campaign_autoseeds_by_owner": {},
        "companion": None,
        "execution_narratives_by_block_id": {},
        "serendipity_texts_by_block_id": {},
        "social_projection_rumors_by_block_id": {},
        "venture_sale_projection_rumors_by_occurrence_id": {},
        "dreams_by_sleep_plan_id": {},
        "proactive_outbox_drafts": {},
        "authoring_now": {},
    }
    if owner_kind != "agent" or control.get("engine_state") != "active":
        return package

    gates = control.get("module_gates") or {}
    if str(gates.get("heartbeat", "manual") or "manual").strip().lower() == "off" and not manual:
        return package
    authoring_now = _authoring_now_grounding(conn, owner_kind, owner_id, now=now)
    package["authoring_now"] = authoring_now
    try:
        package["autonomy_goal_step"] = autonomy.author_goal_step_for_tick(
            conn, owner_kind, owner_id, control, tick_id=tick_id,
            trace_id=trace_id, manual=False, now=now,
        )
    except Exception:
        package["autonomy_goal_step"] = None

    try:
        mode = str(gates.get("reflection", "auto") or "auto").strip().lower()
        if mode not in {"off", "disabled", "manual", "false"}:
            package["reflection"] = opinions.author_reflection_for_tick(
                conn, owner_id, owner_kind=owner_kind, now=now, trace_id=trace_id,
            )
    except Exception:
        package["reflection"] = None

    try:
        package["companion"] = companion.author_companion_for_tick(
            conn, owner_id, control=control, now=now, trace_id=trace_id,
            authoring_now=authoring_now,
        )
    except Exception:
        package["companion"] = None

    try:
        package["execution_narratives_by_block_id"] = execution.prepare_execution_completion_authoring_for_tick(
            conn, owner_kind, owner_id, now=now, trace_id=trace_id,
            authoring_now=authoring_now,
        )
    except Exception:
        package["execution_narratives_by_block_id"] = {}

    try:
        package["serendipity_texts_by_block_id"] = execution.prepare_serendipity_authoring_for_tick(
            conn, owner_kind, owner_id, now=now, trace_id=trace_id,
            authoring_now=authoring_now,
        )
    except Exception:
        package["serendipity_texts_by_block_id"] = {}

    try:
        package["social_projection_rumors_by_block_id"] = social_projector.prepare_completed_event_projection_authoring_for_tick(
            conn, owner_kind, owner_id, now=now,
            completion_authoring_by_block_id=package.get("execution_narratives_by_block_id"),
            trace_id=trace_id,
            authoring_now=authoring_now,
        )
    except Exception:
        package["social_projection_rumors_by_block_id"] = {}

    try:
        package["venture_sale_projection_rumors_by_occurrence_id"] = social_projector.prepare_venture_sale_settlement_authoring_for_tick(
            conn, owner_kind, owner_id, now=now, control=control,
            trace_id=trace_id,
            authoring_now=authoring_now,
        )
    except Exception:
        package["venture_sale_projection_rumors_by_occurrence_id"] = {}

    try:
        package["proactive_outbox_drafts"] = proactive.prepare_auto_send_outbox_authoring(
            conn, owner_id, control, trace_id=trace_id, authoring_now=authoring_now,
        )
    except Exception:
        package["proactive_outbox_drafts"] = {}

    try:
        campaign_seed = _prepare_campaign_autoseed_authoring(
            conn, owner_kind, owner_id, control, now=now, trace_id=trace_id,
        )
        if campaign_seed:
            package["campaign_autoseeds_by_owner"][owner_authoring_pack_key(owner_kind, owner_id)] = campaign_seed
    except Exception:
        package["campaign_autoseeds_by_owner"] = {}

    try:
        dream_mode = str(gates.get("dream", "auto") or "auto").strip().lower()
        if dream_mode in {"auto", "daily", "on", "manual_ok"}:
            for job in due_wake_jobs(conn, owner_kind, owner_id, now):
                if job.get("reason") != "sleep_plan_wake" or not job.get("target_id"):
                    continue
                sleep_plan_id = str(job["target_id"])
                if sleep_plan_id in package["dreams_by_sleep_plan_id"]:
                    continue
                package["dreams_by_sleep_plan_id"][sleep_plan_id] = dream.author_dream_preview(
                    conn, owner_kind, owner_id, trace_id=trace_id,
                    authoring_now=authoring_now,
                )
    except Exception:
        package["dreams_by_sleep_plan_id"] = {}

    return package
