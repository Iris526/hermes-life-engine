"""Event-driven Social World projection.

This module turns completed events and settled venture-sale occurrences into
durable social-world facts. It deliberately keeps worldview-specific origin,
faction, and map-location attributes as freeform/unknown/pending metadata until
the active worldview defines those slots.
"""

from __future__ import annotations

from typing import Any

from . import life_author
from .canon import get_active_canon
from .db import savepoint
from .jsonutil import dumps, loads
from .living import _canon_skin_name, _skin_data
from .skins import DEFAULT_LEGACY_LIVING_SKIN
from .social_world import (
    WORLD_AUDIENCE,
    apply_reputation_event,
    create_entity,
    ensure_default_guimingguan_social_slots,
    link_affiliation,
    record_evaluation,
    record_rumor,
    record_social_request,
    upsert_social_edge,
)
from .trace import append_journal, new_id

_STALL_SIGNALS = {
    "stall", "venture", "sale", "shop", "business", "customer",
    "摆摊", "摊", "归明观", "净符", "卖符", "经营", "营业", "售出", "买卖",
}

_COMMISSION_SIGNALS = {
    "commission", "fieldwork", "onsite", "client", "requester",
    "委托", "外勤", "上门", "勘察", "解决", "客户", "委托人", "找上门",
}

_NEGATIVE_SIGNALS = {
    "failed", "failure", "delay", "delayed", "concern", "problem",
    "失败", "未解决", "延期", "延误", "不顺利", "担心", "疑虑", "问题",
}

_CLIENT_ROLES = {"client", "requester", "customer", "委托人", "客户", "请求人"}

# LifeAuthor 输出合同：只承载社会投影流言的人类可见正文。作用域限于一次
# 事务外 authoring pack；事务内投影只消费 `content`，缺失、空值或异常时逐字
# 使用当前固定模板，不能改变声望、热度、truth_layer 或投影幂等账本。
_SOCIAL_PROJECTION_RUMOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "content": {
            "type": "string",
            "description": "一条可写入 rumors.content 的自然社会传言正文。",
        },
    },
    "required": ["content"],
}


def _guimingguan_social_slots_for_active_canon(conn, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """读取当前 active Canon 允许 seed 的归明观社会槽。

    输入是投影 owner；输出是 guimingguan skin 中的 `social_slots` 或空 dict。
    调用方是事件/经营投影 savepoint 内的兼容 seeding。函数只读 Canon 和 skin，
    不写数据库；未声明 guimingguan skin、未知 skin 或读取失败时返回空 dict，
    让现代 owner 保持 character-clean。
    """
    try:
        canon = get_active_canon(conn, owner_kind, owner_id)
        if _canon_skin_name(canon) != DEFAULT_LEGACY_LIVING_SKIN:
            return {}
        skin = _skin_data(canon)
    except Exception:
        return {}
    social_slots = skin.get("social_slots") if isinstance(skin, dict) else None
    return social_slots if isinstance(social_slots, dict) else {}


def _ensure_guimingguan_social_slots_for_active_skin(conn, owner_kind: str, owner_id: str,
                                                     *, source: str) -> list[dict[str, Any]]:
    """按 active skin 条件写入归明观默认社会槽。

    输入是投影 owner 和来源；输出是 `ensure_default_guimingguan_social_slots`
    返回的新增槽列表。调用方是完成事件和经营结算投影。副作用只在 active Canon
    指向 guimingguan skin 时发生；否则不写任何 slot。
    """
    social_slots = _guimingguan_social_slots_for_active_canon(conn, owner_kind, owner_id)
    if not social_slots:
        return []
    return ensure_default_guimingguan_social_slots(
        conn, owner_kind, owner_id, social_slots=social_slots, source=source,
    )


def project_completed_event(conn, owner_kind: str, owner_id: str, event_id: str, *,
                            summary: str | None = None,
                            source: str = "social_projector",
                            rumor_authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """把一个已完成事件投影为社会事实。

    输入来自 LifeOps 的 COMPLETE_EVENT 或人工补投影调用；输出是本次是否产生
    社会事实及计数。函数会写入 projection ledger、实体、关系、声望、评价、
    请求、流言与 journal。关键不变量是：投影事实必须在局部 savepoint 内全成
    或全不成，失败向上抛出供调用方降级/审计，不能留下半截事实或卡死幂等键。
    """
    from .events import get_event

    event = get_event(conn, event_id)
    if event.get("owner_kind") != owner_kind or event.get("owner_id") != owner_id:
        return {"projected": False, "reason": "owner_mismatch", "event_id": event_id}
    if event.get("status") != "completed":
        return {"projected": False, "reason": "event_not_completed", "event_id": event_id}

    occurrence = _occurrence_for_event(conn, owner_kind, owner_id, event_id)
    activity = _activity_for_occurrence(conn, owner_kind, owner_id, occurrence)
    kind = _classify_event(event, summary=summary, activity=activity, force_stall=False)
    if kind == "stall" and _activity_has_supply(activity) and occurrence and not int(occurrence.get("sale_settled") or 0):
        return {"projected": False, "reason": "awaiting_sale_settlement", "event_id": event_id}
    if kind is None:
        return {"projected": False, "reason": "no_social_projection_signal", "event_id": event_id}

    evidence = _evidence(event=event, occurrence=occurrence, activity=activity,
                         source=source, projection_kind="event_completed", summary=summary)
    with savepoint(conn, f"social_projection_event_completed_{event_id}"):
        run = _begin_run(conn, owner_kind, owner_id, "event_completed", event_id, evidence, source)
        if not run.get("created"):
            return {"projected": False, "reason": "already_projected", "run": run}

        _ensure_guimingguan_social_slots_for_active_skin(conn, owner_kind, owner_id, source=source)
        counts = _project_by_kind(conn, owner_kind, owner_id, kind=kind, event=event,
                                  occurrence=occurrence, activity=activity, evidence=evidence,
                                  summary=summary, source=source,
                                  rumor_authoring=rumor_authoring)
        _finish_run(conn, run["id"], counts)
        append_journal(conn, owner_kind, owner_id, "social_projection_applied",
                       {"run_id": run["id"], "projection_kind": "event_completed",
                        "event_id": event_id, "counts": counts}, source)
        return {"projected": True, "run_id": run["id"], "kind": kind, "counts": counts}


def project_venture_sale_settlement(conn, owner_kind: str, owner_id: str, occurrence_id: str, *,
                                    source: str = "venture_sale",
                                    rumor_authoring: dict[str, Any] | None = None) -> dict[str, Any]:
    """把一次已结算经营 occurrence 投影为社会事实。

    输入来自 heartbeat 进销存结算或人工补投影调用；输出是本次投影状态与计数。
    函数只处理已 sale_settled 的 occurrence，不负责扣库存/入账。所有社会事实
    写入都位于局部 savepoint 内；失败时回滚投影事实并向调用方抛出，保证后续
    heartbeat 或人工调用仍可按 occurrence 幂等键重试。
    """
    from .events import get_event

    occurrence = _get_occurrence(conn, owner_kind, owner_id, occurrence_id)
    if not occurrence:
        return {"projected": False, "reason": "occurrence_not_found", "occurrence_id": occurrence_id}
    if not int(occurrence.get("sale_settled") or 0):
        return {"projected": False, "reason": "sale_not_settled", "occurrence_id": occurrence_id}
    activity = _activity_for_occurrence(conn, owner_kind, owner_id, occurrence)
    if not activity:
        return {"projected": False, "reason": "activity_not_found", "occurrence_id": occurrence_id}

    event = {}
    if occurrence.get("event_id"):
        try:
            event = get_event(conn, occurrence["event_id"])
        except Exception:
            event = {}
    sold = _float(occurrence.get("sold_quantity"))
    income = _float(occurrence.get("income"))
    if not _activity_has_supply(activity) and sold <= 0 and income <= 0:
        return {"projected": False, "reason": "no_sale_social_signal", "occurrence_id": occurrence_id}

    kind = _classify_event(event, activity=activity, force_stall=True)
    if kind is None:
        return {"projected": False, "reason": "no_social_projection_signal", "occurrence_id": occurrence_id}
    evidence = _evidence(event=event, occurrence=occurrence, activity=activity,
                         source=source, projection_kind="venture_sale_settled",
                         summary=f"sold={sold:g}; income={income:g}")
    with savepoint(conn, f"social_projection_venture_sale_settled_{occurrence_id}"):
        run = _begin_run(conn, owner_kind, owner_id, "venture_sale_settled", occurrence_id, evidence, source)
        if not run.get("created"):
            return {"projected": False, "reason": "already_projected", "run": run}

        _ensure_guimingguan_social_slots_for_active_skin(conn, owner_kind, owner_id, source=source)
        counts = _project_stall(conn, owner_kind, owner_id, event=event, occurrence=occurrence,
                                activity=activity, evidence=evidence, summary=None, source=source,
                                rumor_authoring=rumor_authoring)
        _finish_run(conn, run["id"], counts)
        append_journal(conn, owner_kind, owner_id, "social_projection_applied",
                       {"run_id": run["id"], "projection_kind": "venture_sale_settled",
                        "occurrence_id": occurrence_id, "counts": counts}, source)
        return {"projected": True, "run_id": run["id"], "kind": kind, "counts": counts}


def _project_by_kind(conn, owner_kind: str, owner_id: str, *, kind: str,
                     event: dict[str, Any], occurrence: dict[str, Any] | None,
                     activity: dict[str, Any] | None, evidence: dict[str, Any],
                     summary: str | None, source: str,
                     rumor_authoring: dict[str, Any] | None) -> dict[str, int]:
    if kind == "commission":
        return _project_commission(conn, owner_kind, owner_id, event=event,
                                   occurrence=occurrence, activity=activity,
                                   evidence=evidence, summary=summary, source=source,
                                   rumor_authoring=rumor_authoring)
    return _project_stall(conn, owner_kind, owner_id, event=event,
                          occurrence=occurrence, activity=activity,
                          evidence=evidence, summary=summary, source=source,
                          rumor_authoring=rumor_authoring)


def _project_stall(conn, owner_kind: str, owner_id: str, *, event: dict[str, Any],
                   occurrence: dict[str, Any] | None, activity: dict[str, Any] | None,
                   evidence: dict[str, Any], summary: str | None, source: str,
                   rumor_authoring: dict[str, Any] | None) -> dict[str, int]:
    agent = _agent_entity(conn, owner_kind, owner_id, event, source)
    ctx = _stall_context(event, activity)
    shrine = _get_or_create_entity(
        conn, owner_kind, owner_id,
        entity_kind=ctx["venue_kind"],
        display_name=ctx["venue_name"],
        summary=ctx["venue_summary"],
        traits={"role": "shrine_or_venture"},
        metadata={**_entity_metadata(event, activity=activity, source=source), "name_source": ctx["venue_name_source"]},
        source=source,
    )
    visitors = _get_or_create_entity(
        conn, owner_kind, owner_id,
        entity_kind="visitor_group",
        display_name=ctx["audience_name"],
        summary=ctx["audience_summary"],
        traits={"group": "local_customers_and_visitors"},
        metadata={**_entity_metadata(event, activity=activity, source=source, group=True), "name_source": ctx["audience_name_source"]},
        source=source,
    )

    counts = {"entities": 3, "edges": 0, "reputation_events": 0, "evaluations": 0, "rumors": 0, "requests": 0}
    outcome = _outcome(summary, event)
    rep_delta = 2.5 if outcome == "positive" else -2.0
    shrine_delta = 3.0 if outcome == "positive" else -2.5
    for subject, axes in (
        (agent, ["approachable", "trustworthy", "price_fairness"]),
        (shrine, ["efficacious", "approachable"]),
    ):
        for axis in axes:
            apply_reputation_event(
                conn, owner_kind, owner_id,
                subject_entity_id=subject["id"],
                audience_entity_id=visitors["id"],
                axis=axis,
                delta=shrine_delta if subject["id"] == shrine["id"] and axis == "efficacious" else rep_delta,
                reason=_reason(event, summary, default=f"{ctx['venue_name']}经营/摆摊事件完成"),
                evidence_kind=evidence.get("projection_kind"),
                evidence_id=evidence.get("occurrence_id") or evidence.get("event_id"),
                evidence=evidence,
                source=source,
            )
            counts["reputation_events"] += 1

    score = 36 if outcome == "positive" else -24
    for subject, axis in (
        (agent, "satisfaction"),
        (agent, "kindness"),
        (agent, "price_acceptance"),
        (shrine, "perceived_effectiveness"),
    ):
        record_evaluation(
            conn, owner_kind, owner_id,
            evaluator_entity_id=visitors["id"],
            subject_entity_id=subject["id"],
            axis=axis,
            score=score if axis != "price_acceptance" else (22 if outcome == "positive" else -15),
            target_kind="event",
            target_id=evidence.get("event_id") or evidence.get("occurrence_id"),
            reason=_reason(event, summary, default="访客对经营/摆摊结果的社会评价"),
            truth_layer="social_perception",
            evidence=evidence,
            source=source,
        )
        counts["evaluations"] += 1

    sc = activity.get("supply_chain") if isinstance(activity, dict) and isinstance(activity.get("supply_chain"), dict) else {}
    details = {
        "sold_quantity": _float((occurrence or {}).get("sold_quantity")),
        "income": _float((occurrence or {}).get("income")),
        "goods_name": sc.get("goods_name") or sc.get("goods_resource"),
        "venue_name": ctx["venue_name"],
        "audience_name": ctx["audience_name"],
    }
    topic = _request_topic(event, default="general_blessing")
    record_social_request(
        conn, owner_kind, owner_id,
        requester_entity_id=visitors["id"],
        target_entity_id=shrine["id"],
        request_type="wish",
        topic=topic,
        summary="来访者/顾客在经营事件中留下的祝愿或购买需求。",
        details={**details, "event_title": event.get("title")},
        privacy_level="local",
        linked_event_id=evidence.get("event_id"),
        linked_schedule_block_id=evidence.get("schedule_block_id"),
        linked_activity_id=evidence.get("activity_id"),
        linked_occurrence_id=evidence.get("occurrence_id"),
        evidence=evidence,
        idempotency_key=f"{evidence.get('projection_kind')}:{evidence.get('event_id') or evidence.get('occurrence_id')}:stall_wish",
        source=source,
    )
    counts["requests"] += 1

    rumor_content = _authored_rumor_content(
        rumor_authoring,
        _stall_rumor_content_fallback(agent, ctx, outcome),
    )
    record_rumor(
        conn, owner_kind, owner_id,
        subject_entity_id=shrine["id"],
        target_kind="event",
        target_id=evidence.get("event_id") or evidence.get("occurrence_id"),
        content=rumor_content,
        channel="visitor_word_of_mouth",
        heat=0.24 if outcome == "positive" else 0.28,
        credibility=0.34,
        sentiment="positive" if outcome == "positive" else "concern",
        truth_layer="rumor_unverified",
        evidence=evidence,
        source=source,
    )
    counts["rumors"] += 1
    return counts


def _project_commission(conn, owner_kind: str, owner_id: str, *, event: dict[str, Any],
                        occurrence: dict[str, Any] | None, activity: dict[str, Any] | None,
                        evidence: dict[str, Any], summary: str | None, source: str,
                        rumor_authoring: dict[str, Any] | None) -> dict[str, int]:
    agent = _agent_entity(conn, owner_kind, owner_id, event, source)
    client, circle = _client_entity(conn, owner_kind, owner_id, event, activity, source)
    counts = {"entities": 2 + (1 if circle else 0), "edges": 0, "reputation_events": 0, "evaluations": 0, "rumors": 0, "requests": 0}
    if circle:
        link_affiliation(
            conn, owner_kind, owner_id,
            subject_entity_id=client["id"],
            faction_entity_id=circle["id"],
            role="member",
            strength=0.6,
            evidence=evidence,
            source=source,
        )

    outcome = _outcome(summary, event)
    if outcome == "positive":
        edge_values = {"trust": 24, "familiarity": 18, "gratitude": 34}
    else:
        edge_values = {"trust": -10, "familiarity": 12, "suspicion": 28}
    for axis, value in edge_values.items():
        upsert_social_edge(
            conn, owner_kind, owner_id,
            source_entity_id=client["id"],
            target_entity_id=agent["id"],
            axis=axis,
            value=value,
            confidence=0.66,
            visibility="known",
            evidence=evidence,
            source=source,
        )
        counts["edges"] += 1

    audience = circle["id"] if circle else client["id"]
    for axis, delta in (
        ("fieldwork_reliability", 5 if outcome == "positive" else -5),
        ("trustworthy", 3 if outcome == "positive" else -3),
    ):
        apply_reputation_event(
            conn, owner_kind, owner_id,
            subject_entity_id=agent["id"],
            audience_entity_id=audience or WORLD_AUDIENCE,
            axis=axis,
            delta=delta,
            reason=_reason(event, summary, default="委托/外勤事件完成"),
            evidence_kind=evidence.get("projection_kind"),
            evidence_id=evidence.get("event_id"),
            evidence=evidence,
            source=source,
        )
        counts["reputation_events"] += 1

    for axis, score in (
        ("professionalism", 42 if outcome == "positive" else -18),
        ("satisfaction", 38 if outcome == "positive" else -28),
        ("price_acceptance", 18 if outcome == "positive" else -12),
    ):
        record_evaluation(
            conn, owner_kind, owner_id,
            evaluator_entity_id=client["id"],
            subject_entity_id=agent["id"],
            axis=axis,
            score=score,
            target_kind="event",
            target_id=evidence.get("event_id"),
            reason=_reason(event, summary, default="委托人对外勤/委托服务的评价"),
            truth_layer="social_perception",
            evidence=evidence,
            source=source,
        )
        counts["evaluations"] += 1

    topic = _request_topic(event, default="general_commission")
    record_social_request(
        conn, owner_kind, owner_id,
        requester_entity_id=client["id"],
        target_entity_id=agent["id"],
        request_type="fieldwork_request",
        topic=topic,
        summary="委托/外勤事件沉淀的客户请求。",
        details={"event_title": event.get("title"), "summary": summary, "anonymous": bool(client.get("metadata", {}).get("anonymous"))},
        privacy_level="private",
        linked_event_id=evidence.get("event_id"),
        linked_schedule_block_id=evidence.get("schedule_block_id"),
        linked_activity_id=evidence.get("activity_id"),
        linked_occurrence_id=evidence.get("occurrence_id"),
        evidence=evidence,
        idempotency_key=f"{evidence.get('projection_kind')}:{evidence.get('event_id')}:commission_request",
        source=source,
    )
    counts["requests"] += 1

    record_rumor(
        conn, owner_kind, owner_id,
        subject_entity_id=agent["id"],
        target_kind="event",
        target_id=evidence.get("event_id"),
        content=_authored_rumor_content(
            rumor_authoring,
            _commission_rumor_content_fallback(agent, outcome),
        ),
        channel="commission_backchannel",
        heat=0.22 if outcome == "positive" else 0.32,
        credibility=0.36,
        sentiment="positive" if outcome == "positive" else "concern",
        truth_layer="rumor_unverified",
        evidence=evidence,
        source=source,
    )
    counts["rumors"] += 1
    return counts


def _stall_rumor_content_fallback(agent: dict[str, Any], ctx: dict[str, str], outcome: str) -> str:
    """返回经营/摆摊投影流言的历史固定正文。

    输入是已经创建或读取到的主体实体、经营上下文和正/负向结果；输出逐字兼容旧版
    `rumors.content`。调用方包括事务内投影和事务外 authoring context。无副作用；
    这是 no-host、模型空返回、字段非法或已应用投影二次调用时的唯一 fallback。
    """
    return (
        f"有来访者低声说，{ctx['venue_name']}这回经营顺利，{agent.get('display_name') or '当前主体'}待人也算温和。"
        if outcome == "positive"
        else f"有来访者担心，{ctx['venue_name']}这回经营的效果还需要再看看。"
    )


def _commission_rumor_content_fallback(agent: dict[str, Any], outcome: str) -> str:
    """返回委托/外勤投影流言的历史固定正文。

    输入是主体实体和正/负向结果；输出逐字兼容旧版 `rumors.content`。调用方包括
    事务内投影和事务外 authoring context。无副作用；维护时不能改动字符串内容，
    否则 no-host fallback 将不再 byte-identical。
    """
    return (
        f"有委托人私下说，{agent.get('display_name') or '当前主体'}这次外勤处理得稳妥。"
        if outcome == "positive"
        else f"有人私下担心，{agent.get('display_name') or '当前主体'}这次外勤没有完全解决问题。"
    )


def _clean_authored_rumor_content(value: Any) -> str | None:
    """规整 LifeAuthor 返回的流言正文。

    输入是模型 parsed JSON 的 `content` 字段；输出是去除首尾空白后的非空字符串，
    或 `None`。调用方是事务内投影消费层和事务外准备层。无副作用；空值由上层
    回落固定模板，避免半成品写入 social world。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _authored_rumor_content(authored: dict[str, Any] | None, fallback: str) -> str:
    """把事务外 rumor authoring 包转换为最终流言正文。

    输入是短期内存包和固定 fallback；输出总是可写入 `record_rumor` 的正文。
    调用方是 `_project_stall` / `_project_commission` 的事务内路径。无模型调用、
    无数据库读写；只要 `content` 不是非空字符串，就逐字返回 fallback。
    """
    authored = authored if isinstance(authored, dict) else {}
    return _clean_authored_rumor_content(authored.get("content")) or fallback


def _projection_applied(conn, owner_kind: str, owner_id: str, projection_kind: str,
                        projection_key: str) -> bool:
    """判断某个社会投影是否已经 applied。

    输入是 owner、projection_kind 和稳定 projection_key；输出布尔值。调用方是
    事务外 authoring 准备层，用来保证同一 event/occurrence 已投影后不再调用
    LifeAuthor。副作用为只读 SELECT；失败由上层吞掉并降级为不 author。
    """
    row = conn.execute(
        """SELECT 1 FROM social_projection_runs
           WHERE owner_kind=? AND owner_id=? AND projection_kind=? AND projection_key=?
             AND status='applied'
           LIMIT 1""",
        (owner_kind, owner_id, projection_kind, projection_key),
    ).fetchone()
    return bool(row)


def _completion_summary_for_authoring(event: dict[str, Any],
                                      completion_authoring: dict[str, Any] | None) -> str:
    """推导完成事件投影预写作时使用的 completion summary。

    输入是当前 event 和 execution authoring 包；输出应与 `COMPLETE_EVENT.summary`
    保持一致的字符串。调用方是事务外 social projection authoring。无副作用；
    这里镜像 execution 的 no-host fallback，以便 outcome 判定和投影实际消费一致。
    """
    authored = completion_authoring if isinstance(completion_authoring, dict) else {}
    narrative = _clean_authored_rumor_content(authored.get("narrative"))
    return narrative or f"执行完成：{event.get('title')}"


def _rumor_authoring_context(*, kind: str, event: dict[str, Any], occurrence: dict[str, Any] | None,
                             activity: dict[str, Any] | None, evidence: dict[str, Any],
                             outcome: str, fallback_content: str,
                             channel: str, heat: float, sentiment: str,
                             subject_hint: dict[str, Any],
                             authoring_now: dict[str, Any] | None = None) -> dict[str, Any]:
    """构建 LifeAuthor 写 social projection rumor 的只读上下文。

    输入是投影分类、证据、旧正文和不可改变的 rumor 元数据；输出只含事实与约束的
    JSON context。调用方是事务外准备函数；无数据库读写。上下文显式携带 channel、
    heat、truth_layer 和 fallback，提醒模型只改 wording，不能改社会事实合同。
    """
    return {
        "projection_kind": evidence.get("projection_kind"),
        "social_projection_kind": kind,
        "outcome": outcome,
        "event": {
            "id": event.get("id"),
            "title": event.get("title"),
            "description": event.get("description"),
            "event_type": event.get("event_type"),
            "activity_domain": event.get("activity_domain"),
            "tags": event.get("tags"),
            "attributes": event.get("attributes"),
            "participants": event.get("participants"),
            "location": event.get("location"),
        },
        "occurrence": {
            "id": (occurrence or {}).get("id"),
            "date_key": (occurrence or {}).get("date_key"),
            "sale_settled": (occurrence or {}).get("sale_settled"),
            "sold_quantity": (occurrence or {}).get("sold_quantity"),
            "income": (occurrence or {}).get("income"),
        },
        "activity": {
            "id": (activity or {}).get("id"),
            "title": (activity or {}).get("title"),
            "operation_model": (activity or {}).get("operation_model"),
            "tags": (activity or {}).get("tags"),
            "location": (activity or {}).get("location"),
            "supply_chain": (activity or {}).get("supply_chain"),
        },
        "subject_hint": subject_hint,
        "rumor_contract": {
            "channel": channel,
            "heat": heat,
            "truth_layer": "rumor_unverified",
            "sentiment": sentiment,
            "fallback_content": fallback_content,
        },
        "evidence": evidence,
        "now": authoring_now or {},
    }


def _author_social_projection_rumor(conn, owner_kind: str, owner_id: str,
                                    context: dict[str, Any], *,
                                    trace_id: str | None = None) -> dict[str, str] | None:
    """用 LifeAuthor 生成社会投影流言正文。

    输入是 `_rumor_authoring_context` 产出的只读上下文；输出 `{content}` 或 `None`。
    调用方式是 heartbeat/manual execution 进入写事务前的 best-effort 准备。副作用
    仅限 LifeAuthor 自己的审计行；若调用方已在 SQLite 事务内、无 host、模型失败、
    返回空或字段非法，本函数都返回 `None`，让投影使用 byte-identical fallback。
    """
    if getattr(conn, "in_transaction", False):
        return None
    try:
        parsed = life_author.author(
            conn,
            owner_kind,
            owner_id,
            kind="social_projection_rumor",
            instructions=(
                "为一条社会世界投影生成中文流言正文，只改写 wording。"
                "不要改变事实关系、热度、可信度、truth_layer、sentiment、请求类型或声望含义；"
                "不要发明没有在上下文出现的专名、势力、地点或世界观设定。"
            ),
            context=context,
            schema=_SOCIAL_PROJECTION_RUMOR_SCHEMA,
            max_tokens=220,
            temperature=0.65,
            trace_id=trace_id,
        )
        content = _clean_authored_rumor_content((parsed or {}).get("content") if isinstance(parsed, dict) else None)
        if not content:
            return None
        return {"content": content}
    except Exception:
        return None


def prepare_completed_event_projection_authoring_for_block(
    conn,
    owner_kind: str,
    owner_id: str,
    block: dict[str, Any] | None,
    *,
    completion_authoring: dict[str, Any] | None = None,
    trace_id: str | None = None,
    source: str = "social_projector:execution_simulator",
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """在写事务外为一个即将完成的 schedule block 预生成投影流言。

    输入是 due block 和 execution completion authoring 包；输出 `{content}` 或
    `None`。调用方是 heartbeat/manual execution 预备层。副作用只读当前 event、
    occurrence、activity 和 projection ledger，再 best-effort 调 LifeAuthor；
    已 applied 的 event_completed projection 会直接跳过，保证同一事实不二次 author。
    """
    if not isinstance(block, dict) or getattr(conn, "in_transaction", False):
        return None
    event_id = block.get("event_id")
    if not event_id:
        return None
    try:
        from .events import get_event

        event = get_event(conn, str(event_id))
        if event.get("owner_kind") != owner_kind or event.get("owner_id") != owner_id:
            return None
        if _projection_applied(conn, owner_kind, owner_id, "event_completed", str(event_id)):
            return None
        occurrence = _occurrence_for_event(conn, owner_kind, owner_id, str(event_id))
        activity = _activity_for_occurrence(conn, owner_kind, owner_id, occurrence)
        summary = _completion_summary_for_authoring(event, completion_authoring)
        kind = _classify_event(event, summary=summary, activity=activity, force_stall=False)
        if kind == "stall" and _activity_has_supply(activity) and occurrence and not int(occurrence.get("sale_settled") or 0):
            return None
        if kind is None:
            return None
        evidence = _evidence(event=event, occurrence=occurrence, activity=activity,
                             source=source, projection_kind="event_completed", summary=summary)
        context = _prepare_rumor_authoring_context(
            conn, owner_kind, owner_id, kind=kind, event=event, occurrence=occurrence,
            activity=activity, evidence=evidence, summary=summary, authoring_now=authoring_now,
        )
        if not context:
            return None
        return _author_social_projection_rumor(conn, owner_kind, owner_id, context, trace_id=trace_id)
    except Exception:
        return None


def _social_projection_authoring_blocks_for_tick(conn, owner_kind: str, owner_id: str,
                                                 now: str, limit: int = 20) -> list[dict[str, Any]]:
    """读取本轮 heartbeat 可能完成的非睡眠 schedule block。

    输入是 owner、逻辑时间和数量上限；输出去重后的 block 列表。调用方是事务外
    social projection authoring 准备层。副作用只读 wake_jobs/schedule_blocks；
    排序和筛选镜像 execution authoring，避免为本轮不会执行的 block 调模型。
    """
    from .events import due_schedule_blocks, due_wake_jobs

    by_id: dict[str, dict[str, Any]] = {}
    for job in due_wake_jobs(conn, owner_kind, owner_id, now):
        if job.get("reason") != "schedule_block_end" or not job.get("target_id"):
            continue
        row = conn.execute(
            "SELECT * FROM schedule_blocks WHERE id=? AND owner_kind=? AND owner_id=?",
            (job["target_id"], owner_kind, owner_id),
        ).fetchone()
        if not row:
            continue
        block = dict(row)
        if block.get("block_type") == "sleep":
            continue
        if block.get("status") in {"planned", "locked", "ready", "in_progress"}:
            by_id[str(block["id"])] = block
    for block in due_schedule_blocks(conn, owner_kind, owner_id, now):
        if block.get("block_type") == "sleep":
            continue
        by_id.setdefault(str(block["id"]), block)
    return list(by_id.values())[: max(1, int(limit))]


def prepare_completed_event_projection_authoring_for_tick(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    now: str,
    completion_authoring_by_block_id: dict[str, Any] | None = None,
    trace_id: str | None = None,
    limit: int = 20,
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """在 heartbeat 写事务外预生成 completion social projection rumor 包。

    输入来自 `prepare_heartbeat_authoring` 的逻辑时间和 execution authoring 包；
    输出 `{block_id: {content}}`，只在本 tick 内经 `COMPLETE_EVENT` payload 消费。
    失败处理是逐项跳过：无 host、模型空返回、非投影事件或已 applied 投影都不会
    抛错，事务内 projection 会使用旧固定 phrasing。
    """
    authored: dict[str, dict[str, str]] = {}
    if getattr(conn, "in_transaction", False):
        return authored
    completion_authoring_by_block_id = completion_authoring_by_block_id if isinstance(completion_authoring_by_block_id, dict) else {}
    try:
        blocks = _social_projection_authoring_blocks_for_tick(conn, owner_kind, owner_id, now, limit=limit)
    except Exception:
        return authored
    for block in blocks:
        block_id = str(block.get("id") or "")
        if not block_id:
            continue
        item = prepare_completed_event_projection_authoring_for_block(
            conn, owner_kind, owner_id, block,
            completion_authoring=completion_authoring_by_block_id.get(block_id),
            trace_id=trace_id,
            authoring_now=authoring_now,
        )
        if item:
            authored[block_id] = item
    return authored


def _prepare_rumor_authoring_context(conn, owner_kind: str, owner_id: str, *, kind: str,
                                     event: dict[str, Any], occurrence: dict[str, Any] | None,
                                     activity: dict[str, Any] | None, evidence: dict[str, Any],
                                     summary: str | None,
                                     authoring_now: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """按投影类型构建流言 authoring context。

    输入是已分类的投影候选；输出 LifeAuthor context 或 `None`。调用方是事务外
    completed-event 和 venture-sale authoring 准备函数。副作用只读 canon identity；
    不创建实体，不触碰默认 slot，不写 reputation/evaluation/rumor/request。
    """
    outcome = _outcome(summary, event)
    agent_name, agent_name_source = _agent_name(conn, owner_kind, owner_id)
    agent_hint = {"display_name": agent_name, "name_source": agent_name_source}
    if kind == "commission":
        fallback = _commission_rumor_content_fallback(agent_hint, outcome)
        return _rumor_authoring_context(
            kind=kind, event=event, occurrence=occurrence, activity=activity, evidence=evidence,
            outcome=outcome, fallback_content=fallback, channel="commission_backchannel",
            heat=0.22 if outcome == "positive" else 0.32,
            sentiment="positive" if outcome == "positive" else "concern",
            subject_hint=agent_hint, authoring_now=authoring_now,
        )
    if kind == "stall":
        ctx = _stall_context(event, activity)
        fallback = _stall_rumor_content_fallback(agent_hint, ctx, outcome)
        return _rumor_authoring_context(
            kind=kind, event=event, occurrence=occurrence, activity=activity, evidence=evidence,
            outcome=outcome, fallback_content=fallback, channel="visitor_word_of_mouth",
            heat=0.24 if outcome == "positive" else 0.28,
            sentiment="positive" if outcome == "positive" else "concern",
            subject_hint={**agent_hint, **ctx}, authoring_now=authoring_now,
        )
    return None


def prepare_venture_sale_settlement_authoring(
    conn,
    owner_kind: str,
    owner_id: str,
    occurrence_id: str,
    *,
    source: str = "social_projector:venture_sale",
    projected_summary: str | None = None,
    trace_id: str | None = None,
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """在写事务外为一个经营结算 occurrence 预生成投影流言。

    输入是 occurrence_id 和可选的预估结算 summary；输出 `{content}` 或 `None`。
    调用方是 heartbeat venture_supply 预备层和测试直接准备层。副作用只读
    occurrence/activity/event/projection ledger；已 applied 的 venture_sale_settled
    projection 会直接跳过，保证同一 occurrence 不二次 author。
    """
    if getattr(conn, "in_transaction", False):
        return None
    try:
        if _projection_applied(conn, owner_kind, owner_id, "venture_sale_settled", str(occurrence_id)):
            return None
        from .events import get_event

        occurrence = _get_occurrence(conn, owner_kind, owner_id, str(occurrence_id))
        if not occurrence:
            return None
        activity = _activity_for_occurrence(conn, owner_kind, owner_id, occurrence)
        if not activity:
            return None
        event: dict[str, Any] = {}
        if occurrence.get("event_id"):
            try:
                event = get_event(conn, occurrence["event_id"])
            except Exception:
                event = {}
        sold = _float(occurrence.get("sold_quantity"))
        income = _float(occurrence.get("income"))
        if not _activity_has_supply(activity) and sold <= 0 and income <= 0:
            return None
        kind = _classify_event(event, activity=activity, force_stall=True)
        if kind is None:
            return None
        summary = projected_summary if projected_summary is not None else f"sold={sold:g}; income={income:g}"
        evidence = _evidence(event=event, occurrence=occurrence, activity=activity,
                             source=source, projection_kind="venture_sale_settled",
                             summary=summary)
        context = _prepare_rumor_authoring_context(
            conn, owner_kind, owner_id, kind=kind, event=event, occurrence=occurrence,
            activity=activity, evidence=evidence, summary=summary, authoring_now=authoring_now,
        )
        if not context:
            return None
        return _author_social_projection_rumor(conn, owner_kind, owner_id, context, trace_id=trace_id)
    except Exception:
        return None


def _account_value(conn, owner_kind: str, owner_id: str, key: str | None) -> float:
    """只读获取资源账户当前值，供事务外经营结算 authoring 预估 sold/income。

    输入是资源 key；输出当前值或 0。调用方是 `prepare_venture_sale_settlement_authoring_for_tick`。
    副作用为 SELECT；缺账户、空 key 或读取异常都按 0 降级，不影响真实结算逻辑。
    """
    if not key:
        return 0.0
    try:
        row = conn.execute(
            "SELECT current_value FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
            (owner_kind, owner_id, key),
        ).fetchone()
        return float(row["current_value"]) if row and row["current_value"] is not None else 0.0
    except Exception:
        return 0.0


def _window_end_ts(date_key: str, end_time: str | None, tz_name: str) -> int | None:
    """计算 passive venture 当日窗口结束时间戳。

    输入是 occurrence date_key、活动 end_time 和时区名；输出 epoch 秒或 `None`。
    调用方是事务外 sale-settlement authoring 预判层。无数据库副作用；解析失败按
    `None` 处理，与 runtime 结算逻辑保持同样的“无窗口则可立即结算”语义。
    """
    if not end_time:
        return None
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt

        eh, em = (int(x) for x in str(end_time).split(":")[:2])
        y, mo, d = (int(x) for x in str(date_key).split("-"))
        return int(_dt(y, mo, d, eh, em, tzinfo=ZoneInfo(tz_name or "UTC")).timestamp())
    except Exception:
        return None


def prepare_venture_sale_settlement_authoring_for_tick(
    conn,
    owner_kind: str,
    owner_id: str,
    *,
    now: str,
    control: dict[str, Any] | None = None,
    trace_id: str | None = None,
    limit: int = 20,
    authoring_now: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """在 heartbeat 写事务外预生成经营结算投影流言包。

    输入是 owner、控制状态和逻辑时间；输出 `{occurrence_id: {content}}`，供
    `_settle_supply_chain_for_tick` 在事务内消费。函数只读 venture/activity/event/
    resource/projection ledger；它只预估本轮会被 runtime 结算或补投影的 occurrence，
    不写 sale_settled、库存、收入或任何社会事实。
    """
    authored: dict[str, dict[str, str]] = {}
    if owner_kind != "agent" or getattr(conn, "in_transaction", False):
        return authored
    gates = (control or {}).get("module_gates") or {}
    if str(gates.get("venture", gates.get("recurring_activities", "auto")) or "auto").lower() in {"off", "disabled", "false"}:
        return authored
    try:
        from . import venture
        from .canon import get_active_canon
        from .events import get_event
        from .schedule_view import _tz_from_canon
        from .time_utils import to_epoch as _to_epoch

        canon = get_active_canon(conn, owner_kind, owner_id)
        tz_name = _tz_from_canon(canon) or "UTC"
        now_ts = int(_to_epoch(now))
        terminal = {"completed", "partial", "done"}
        attempted: set[str] = set()
        for act in venture.list_ventures(conn, owner_kind, owner_id, status="active"):
            if len(authored) >= max(1, int(limit)):
                break
            op = act.get("operation_model") or "active"
            passive = op in {"self_service", "staffed"}
            sc = act.get("supply_chain") if isinstance(act.get("supply_chain"), dict) else None
            has_supply = bool(sc and sc.get("goods_resource"))
            if not (has_supply or passive):
                continue
            goods = sc["goods_resource"] if has_supply else None
            unit_price = float((sc or {}).get("unit_price") or 0)
            demand = float((sc or {}).get("demand_per_occurrence") or 0)
            rows = conn.execute(
                "SELECT id, event_id, date_key FROM venture_occurrences WHERE owner_kind=? AND owner_id=? AND activity_id=? AND sale_settled=0",
                (owner_kind, owner_id, act["id"]),
            ).fetchall()
            for row in rows:
                if len(authored) >= max(1, int(limit)):
                    break
                occ = dict(row)
                ev = get_event(conn, occ["event_id"]) if occ.get("event_id") else None
                if not passive:
                    if not ev or ev.get("status") not in terminal:
                        continue
                else:
                    wend = _window_end_ts(occ.get("date_key"), act.get("end_time"), act.get("timezone") or tz_name)
                    if wend is not None and now_ts < wend:
                        continue
                    # passive occurrence 会在 runtime 事务内先补 COMPLETE_EVENT 再结算；
                    # authoring 这里只预备 occurrence 流言，不改变 linked event 状态。
                sold = 0.0
                income = 0.0
                if has_supply:
                    stock = _account_value(conn, owner_kind, owner_id, goods)
                    sold = max(0.0, min(demand, stock))
                    income = round(sold * unit_price, 2)
                summary = f"sold={sold:g}; income={income:g}"
                item = prepare_venture_sale_settlement_authoring(
                    conn, owner_kind, owner_id, str(occ["id"]),
                    source="social_projector:venture_sale",
                    projected_summary=summary,
                    trace_id=trace_id,
                    authoring_now=authoring_now,
                )
                attempted.add(str(occ["id"]))
                if item:
                    authored[str(occ["id"])] = item
            for settled in conn.execute(
                """SELECT occ.id
                   FROM venture_occurrences occ
                   WHERE occ.owner_kind=? AND occ.owner_id=? AND occ.activity_id=?
                     AND occ.sale_settled=1
                     AND NOT EXISTS (
                       SELECT 1 FROM social_projection_runs pr
                       WHERE pr.owner_kind=occ.owner_kind
                         AND pr.owner_id=occ.owner_id
                         AND pr.projection_kind='venture_sale_settled'
                         AND pr.projection_key=occ.id
                         AND pr.status='applied'
                     )
                   ORDER BY occ.date_key DESC
                   LIMIT 20""",
                (owner_kind, owner_id, act["id"]),
            ).fetchall():
                if len(authored) >= max(1, int(limit)):
                    break
                occurrence_id = str(settled["id"])
                if occurrence_id in attempted:
                    continue
                item = prepare_venture_sale_settlement_authoring(
                    conn, owner_kind, owner_id, occurrence_id,
                    source="social_projector:venture_sale_retry",
                    trace_id=trace_id,
                    authoring_now=authoring_now,
                )
                if item:
                    authored[occurrence_id] = item
    except Exception:
        return authored
    return authored


def _classify_event(event: dict[str, Any], *, summary: str | None = None,
                    activity: dict[str, Any] | None = None, force_stall: bool = False) -> str | None:
    text = _text_blob(event.get("event_type"), event.get("activity_domain"), event.get("title"),
                      event.get("description"), event.get("tags"), event.get("attributes"),
                      event.get("location"), summary, activity)
    if _has_signal(text, _COMMISSION_SIGNALS):
        return "commission"
    if force_stall or _has_signal(text, _STALL_SIGNALS):
        return "stall"
    return None


def _outcome(summary: str | None, event: dict[str, Any]) -> str:
    text = _text_blob(summary, event.get("title"), event.get("description"), event.get("tags"), event.get("attributes"))
    return "negative" if _has_signal(text, _NEGATIVE_SIGNALS) else "positive"


def _has_signal(text: str, signals: set[str]) -> bool:
    low = text.lower()
    return any(sig.lower() in low for sig in signals)


def _text_blob(*parts: Any) -> str:
    out: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (dict, list, tuple)):
            out.append(dumps(part))
        else:
            out.append(str(part))
    return " ".join(out)


def _begin_run(conn, owner_kind: str, owner_id: str, projection_kind: str,
               projection_key: str, evidence: dict[str, Any], source: str) -> dict[str, Any]:
    """开始或恢复一个社会投影幂等 run。

    输入是 owner、投影类型和稳定 projection_key；输出给上层判断是否应继续写
    事实。调用方必须已经处在投影 savepoint 中。已 applied 的 run 视为完成并
    阻止重复写入；started/failed 等未完成状态允许复用同一 ledger 行重试，避免
    旧的半提交 run 永久卡住后续投影。
    """
    run_id = new_id("socproj")
    cur = conn.execute(
        """INSERT OR IGNORE INTO social_projection_runs(
             id, owner_kind, owner_id, projection_kind, projection_key, source,
             status, event_id, schedule_block_id, activity_id, occurrence_id,
             evidence_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, owner_kind, owner_id, projection_kind, projection_key, source,
         "started", evidence.get("event_id"), evidence.get("schedule_block_id"),
         evidence.get("activity_id"), evidence.get("occurrence_id"), dumps(evidence)),
    )
    if cur.rowcount:
        return {"created": True, "id": run_id}
    row = conn.execute(
        """SELECT * FROM social_projection_runs
           WHERE owner_kind=? AND owner_id=? AND projection_kind=? AND projection_key=?""",
        (owner_kind, owner_id, projection_kind, projection_key),
    ).fetchone()
    if row and str(row["status"] or "") != "applied":
        conn.execute(
            """UPDATE social_projection_runs
               SET source=?, status='started', event_id=?, schedule_block_id=?,
                   activity_id=?, occurrence_id=?, fact_counts_json='{}',
                   evidence_json=?, updated_at=datetime('now')
               WHERE id=?""",
            (source, evidence.get("event_id"), evidence.get("schedule_block_id"),
             evidence.get("activity_id"), evidence.get("occurrence_id"), dumps(evidence), row["id"]),
        )
        return {"created": True, "id": row["id"], "retried": True}
    return dict(row) | {"created": False} if row else {"created": False}


def _finish_run(conn, run_id: str, counts: dict[str, int]) -> None:
    conn.execute(
        "UPDATE social_projection_runs SET status='applied', fact_counts_json=?, updated_at=datetime('now') WHERE id=?",
        (dumps(counts), run_id),
    )


def _evidence(*, event: dict[str, Any] | None, occurrence: dict[str, Any] | None,
              activity: dict[str, Any] | None, source: str, projection_kind: str,
              summary: str | None = None) -> dict[str, Any]:
    event = event or {}
    occurrence = occurrence or {}
    activity = activity or {}
    block_ids = event.get("schedule_block_ids") if isinstance(event.get("schedule_block_ids"), list) else []
    return {
        "event_id": event.get("id") or occurrence.get("event_id"),
        "schedule_block_id": occurrence.get("schedule_block_id") or event.get("current_schedule_block_id") or (block_ids[0] if block_ids else None),
        "activity_id": activity.get("id") or occurrence.get("activity_id") or (event.get("attributes") or {}).get("recurring_activity_id"),
        "occurrence_id": occurrence.get("id"),
        "source": source,
        "projection_kind": projection_kind,
        "summary": summary,
    }


def _agent_entity(conn, owner_kind: str, owner_id: str, event: dict[str, Any], source: str) -> dict[str, Any]:
    name, name_source = _agent_name(conn, owner_kind, owner_id)
    return _get_or_create_entity(
        conn, owner_kind, owner_id,
        entity_kind="agent",
        display_name=name,
        summary="当前 LifeEngine 主体在社会世界中的实体。",
        traits={"role": "lifeengine_owner"},
        metadata={**_entity_metadata(event, source=source), "identity_source": name_source},
        source=source,
    )


def _agent_name(conn, owner_kind: str, owner_id: str) -> tuple[str, str]:
    row = conn.execute(
        """SELECT data_json FROM canon_versions
           WHERE owner_kind=? AND owner_id=? AND status='active'
           ORDER BY version DESC LIMIT 1""",
        (owner_kind, owner_id),
    ).fetchone()
    if row:
        data = loads(row["data_json"], {})
        ident = data.get("identity") if isinstance(data, dict) else {}
        name = (ident or {}).get("name") or (ident or {}).get("display_name")
        if name:
            return str(name), "canon_identity"
    return "当前主体", "default_pending_canon_identity"


def _stall_context(event: dict[str, Any], activity: dict[str, Any] | None) -> dict[str, str]:
    """Derive stall/venture social labels only from provided evidence.

    Older projector code used Guimingguan-specific names as defaults. That made
    review and social facts feel rich, but it also wrote fixed lore into worlds
    that had not defined those slots. These labels now come from event/activity
    attributes when available, otherwise they remain deliberately generic.
    """
    activity = activity or {}
    attrs = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
    loc = event.get("location") if isinstance(event.get("location"), dict) else {}
    supply = activity.get("supply_chain") if isinstance(activity.get("supply_chain"), dict) else {}

    venue_name = _first_text(
        attrs.get("venue_name"),
        attrs.get("shrine_name"),
        attrs.get("shop_name"),
        attrs.get("stall_name"),
        attrs.get("place_name"),
        loc.get("venue_name"),
        loc.get("site_name"),
        activity.get("venue_name"),
        activity.get("shop_name"),
        activity.get("title") if activity.get("title") else None,
    )
    venue_source = "event_or_activity"
    if not venue_name:
        goods_name = _first_text(attrs.get("goods_name"), supply.get("goods_name"), supply.get("goods_resource"))
        venue_name = f"{goods_name}经营点" if goods_name else "未命名经营点"
        venue_source = "generic_from_goods" if goods_name else "generic_unknown"

    audience_name = _first_text(
        attrs.get("visitor_group_name"),
        attrs.get("customer_group_name"),
        attrs.get("audience_name"),
        attrs.get("requester_group"),
        attrs.get("client_circle"),
        attrs.get("circle"),
        attrs.get("group"),
    )
    audience_source = "event_attributes"
    if not audience_name:
        loc_name = _first_text(loc.get("name"), loc.get("display_name"), loc.get("label"), activity.get("location"))
        audience_name = f"{loc_name}来访者" if loc_name else "未具名来访者"
        audience_source = "location_generic" if loc_name else "generic_unknown"

    return {
        "venue_kind": str(attrs.get("venue_kind") or "shrine"),
        "venue_name": venue_name,
        "venue_name_source": venue_source,
        "venue_summary": "由经营/摆摊事件投影沉淀的场所实体；名称来自事件证据或保持未知占位。",
        "audience_name": audience_name,
        "audience_name_source": audience_source,
        "audience_summary": "由摆摊、经营和来访事件沉淀的本地来访者群体；具体地图/势力槽位待世界观定义。",
    }


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _client_entity(conn, owner_kind: str, owner_id: str, event: dict[str, Any],
                   activity: dict[str, Any] | None, source: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    participants = event.get("participants") if isinstance(event.get("participants"), list) else []
    chosen: dict[str, Any] | str | None = None
    for item in participants:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("participant_role") or item.get("type") or "").lower()
            if role in _CLIENT_ROLES or any(sig in role for sig in _CLIENT_ROLES):
                chosen = item
                break
        elif isinstance(item, str) and item.strip():
            chosen = item
            break
    attrs = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
    client_name = attrs.get("client_name") or attrs.get("requester_name") or attrs.get("customer_name")
    client_metadata = _entity_metadata(event, activity=activity, source=source)
    anonymous = False
    circle_name = attrs.get("client_circle") or attrs.get("circle") or attrs.get("group")
    if isinstance(chosen, dict):
        client_name = client_name or chosen.get("display_name") or chosen.get("name") or chosen.get("id")
        circle_name = circle_name or chosen.get("circle") or chosen.get("group") or chosen.get("affiliation") or chosen.get("organization")
        client_metadata["participant"] = {k: v for k, v in chosen.items() if k not in {"display_name", "name"}}
    elif isinstance(chosen, str):
        client_name = client_name or chosen
    if not client_name:
        client_name = "未具名委托人"
        anonymous = True
    client_metadata["anonymous"] = anonymous
    client = _get_or_create_entity(
        conn, owner_kind, owner_id,
        entity_kind="client",
        display_name=str(client_name),
        summary="由委托/外勤事件自动沉淀的委托人实体。",
        traits={"role": "client"},
        metadata=client_metadata,
        source=source,
    )
    circle = None
    if circle_name:
        circle_kind = "merchant" if _has_signal(str(circle_name), {"merchant", "商户", "商家"}) else "neighborhood"
        circle = _get_or_create_entity(
            conn, owner_kind, owner_id,
            entity_kind=circle_kind,
            display_name=str(circle_name),
            summary="由事件参与者或属性提供的委托人所属圈层；不是硬编码势力。",
            traits={"role": "client_circle"},
            metadata={**_entity_metadata(event, activity=activity, source=source), "provided_by_event": True},
            source=source,
        )
    return client, circle


def _get_or_create_entity(conn, owner_kind: str, owner_id: str, *, entity_kind: str,
                          display_name: str, summary: str | None,
                          traits: dict[str, Any] | None,
                          metadata: dict[str, Any] | None,
                          source: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT * FROM world_entities
           WHERE owner_kind=? AND owner_id=? AND entity_kind=? AND display_name=? AND status='active'
           ORDER BY created_at LIMIT 1""",
        (owner_kind, owner_id, entity_kind, display_name),
    ).fetchone()
    if row:
        item = dict(row)
        item["traits"] = loads(item.pop("traits_json"), {})
        item["metadata"] = loads(item.pop("metadata_json"), {})
        return item
    return create_entity(conn, owner_kind, owner_id, entity_kind=entity_kind,
                         display_name=display_name, summary=summary,
                         traits=traits or {}, metadata=metadata or {}, source=source)


def _entity_metadata(event: dict[str, Any] | None, *, activity: dict[str, Any] | None = None,
                     source: str, group: bool = False) -> dict[str, Any]:
    event = event or {}
    activity = activity or {}
    loc = event.get("location") if isinstance(event.get("location"), dict) else {}
    loc_value = loc.get("name") or loc.get("display_name") or loc.get("label") or loc.get("value") or activity.get("location")
    location_meta = (
        {"slot_status": "freeform", "value": loc_value, "kind": loc.get("kind") or activity.get("location_kind") or "freeform"}
        if loc_value else
        {"slot_status": "unknown", "pending_slot": "map_location"}
    )
    return {
        "source": source,
        "auto_projected": True,
        "group": bool(group),
        "worldview_slots": {
            "origin": "pending_slot",
            "faction": "pending_slot",
            "map_location": location_meta.get("slot_status"),
        },
        "origin": {"slot_status": "unknown", "pending_slot": "origin"},
        "faction": {"slot_status": "unknown", "pending_slot": "faction"},
        "location": location_meta,
        "evidence_summary": {
            "event_id": event.get("id"),
            "event_title": event.get("title"),
            "activity_id": activity.get("id"),
        },
    }


def _request_topic(event: dict[str, Any], default: str) -> str:
    attrs = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
    for key in ("request_topic", "wish_topic", "topic", "commission_topic", "need_topic"):
        value = attrs.get(key)
        if value:
            return str(value)
    return default


def _reason(event: dict[str, Any], summary: str | None, *, default: str) -> str:
    title = event.get("title")
    if summary:
        return f"{title}: {summary}" if title else summary
    return f"{title}: {default}" if title else default


def _occurrence_for_event(conn, owner_kind: str, owner_id: str, event_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT * FROM venture_occurrences
           WHERE owner_kind=? AND owner_id=? AND event_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (owner_kind, owner_id, event_id),
    ).fetchone()
    return dict(row) if row else None


def _get_occurrence(conn, owner_kind: str, owner_id: str, occurrence_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM venture_occurrences WHERE owner_kind=? AND owner_id=? AND id=?",
        (owner_kind, owner_id, occurrence_id),
    ).fetchone()
    return dict(row) if row else None


def _activity_for_occurrence(conn, owner_kind: str, owner_id: str,
                             occurrence: dict[str, Any] | None) -> dict[str, Any] | None:
    if not occurrence or not occurrence.get("activity_id"):
        return None
    from . import venture
    return venture.get_venture(conn, owner_kind, owner_id, occurrence["activity_id"])


def _activity_has_supply(activity: dict[str, Any] | None) -> bool:
    return bool(isinstance(activity, dict) and isinstance(activity.get("supply_chain"), dict) and activity["supply_chain"].get("goods_resource"))


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
