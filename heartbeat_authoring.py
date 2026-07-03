"""Heartbeat 生成式内容预备层。

本模块是 v0.18 LifeAuthor 与确定性 heartbeat 之间的隔离带：它只在
SQLite 写事务外读取当前状态、调用宿主模型并返回短期内存结果；真正的
事件、梦、观点、主动意图仍由 runtime 在 LifeOps / heartbeat 事务里落库。
"""

from __future__ import annotations

from typing import Any

from . import autonomy
from . import companion
from . import dream
from . import opinions
from . import proactive
from .events import due_wake_jobs


def prepare_heartbeat_authoring(conn, owner_kind: str, owner_id: str, control: dict[str, Any], *,
                                now: str, tick_id: str, trace_id: str | None,
                                manual: bool) -> dict[str, Any]:
    """在 heartbeat 写事务外预生成本轮可能需要的 LifeAuthor 内容。

    输入来自 `LifeEngineRuntime.tick()` 已创建的 tick/trace/control；输出是一个只在
    本次 tick 内有效的内存包，键包括 `autonomy_goal_step`、`reflection`、
    `companion`、`proactive_outbox_drafts` 和 `dreams_by_sleep_plan_id`。调用方会把
    这些结构化结果传入事务内子流程消费。副作用仅限各 LifeAuthor 调用自己的审计
    记录；本函数不写生活事实、不 claim wake job、不创建 event/outbox/opinion/dream。
    失败逐项降级为空包，保证 heartbeat 仍可用确定性模板继续执行。
    """
    package: dict[str, Any] = {
        "autonomy_goal_step": None,
        "reflection": None,
        "companion": None,
        "dreams_by_sleep_plan_id": {},
        "proactive_outbox_drafts": {},
    }
    if owner_kind != "agent" or control.get("engine_state") != "active":
        return package

    gates = control.get("module_gates") or {}
    if str(gates.get("heartbeat", "manual") or "manual").strip().lower() == "off" and not manual:
        return package
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
        )
    except Exception:
        package["companion"] = None

    try:
        package["proactive_outbox_drafts"] = proactive.prepare_auto_send_outbox_authoring(
            conn, owner_id, control, trace_id=trace_id,
        )
    except Exception:
        package["proactive_outbox_drafts"] = {}

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
                )
    except Exception:
        package["dreams_by_sleep_plan_id"] = {}

    return package
