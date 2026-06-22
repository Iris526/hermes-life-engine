"""Event-driven Social World projection.

This module turns completed events and settled venture-sale occurrences into
durable social-world facts. It deliberately keeps worldview-specific origin,
faction, and map-location attributes as freeform/unknown/pending metadata until
the active worldview defines those slots.
"""

from __future__ import annotations

from typing import Any

from .db import savepoint
from .jsonutil import dumps, loads
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
    "摆摊", "摊", "归明观", "香客", "净符", "卖符", "经营", "营业", "售出", "买卖",
}

_COMMISSION_SIGNALS = {
    "commission", "fieldwork", "onsite", "client", "requester",
    "委托", "外勤", "上门", "勘察", "解决", "客户", "委托人", "找上门",
}

_NEGATIVE_SIGNALS = {
    "failed", "failure", "delay", "delayed", "concern", "problem",
    "失败", "未解决", "延期", "延误", "不顺利", "担心", "疑虑", "问题",
}

_CLIENT_ROLES = {"client", "requester", "customer", "委托人", "客户", "请求人", "香客"}


def project_completed_event(conn, owner_kind: str, owner_id: str, event_id: str, *,
                            summary: str | None = None,
                            source: str = "social_projector") -> dict[str, Any]:
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

        ensure_default_guimingguan_social_slots(conn, owner_kind, owner_id, source=source)
        counts = _project_by_kind(conn, owner_kind, owner_id, kind=kind, event=event,
                                  occurrence=occurrence, activity=activity, evidence=evidence,
                                  summary=summary, source=source)
        _finish_run(conn, run["id"], counts)
        append_journal(conn, owner_kind, owner_id, "social_projection_applied",
                       {"run_id": run["id"], "projection_kind": "event_completed",
                        "event_id": event_id, "counts": counts}, source)
        return {"projected": True, "run_id": run["id"], "kind": kind, "counts": counts}


def project_venture_sale_settlement(conn, owner_kind: str, owner_id: str, occurrence_id: str, *,
                                    source: str = "venture_sale") -> dict[str, Any]:
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

        ensure_default_guimingguan_social_slots(conn, owner_kind, owner_id, source=source)
        counts = _project_stall(conn, owner_kind, owner_id, event=event, occurrence=occurrence,
                                activity=activity, evidence=evidence, summary=None, source=source)
        _finish_run(conn, run["id"], counts)
        append_journal(conn, owner_kind, owner_id, "social_projection_applied",
                       {"run_id": run["id"], "projection_kind": "venture_sale_settled",
                        "occurrence_id": occurrence_id, "counts": counts}, source)
        return {"projected": True, "run_id": run["id"], "kind": kind, "counts": counts}


def _project_by_kind(conn, owner_kind: str, owner_id: str, *, kind: str,
                     event: dict[str, Any], occurrence: dict[str, Any] | None,
                     activity: dict[str, Any] | None, evidence: dict[str, Any],
                     summary: str | None, source: str) -> dict[str, int]:
    if kind == "commission":
        return _project_commission(conn, owner_kind, owner_id, event=event,
                                   occurrence=occurrence, activity=activity,
                                   evidence=evidence, summary=summary, source=source)
    return _project_stall(conn, owner_kind, owner_id, event=event,
                          occurrence=occurrence, activity=activity,
                          evidence=evidence, summary=summary, source=source)


def _project_stall(conn, owner_kind: str, owner_id: str, *, event: dict[str, Any],
                   occurrence: dict[str, Any] | None, activity: dict[str, Any] | None,
                   evidence: dict[str, Any], summary: str | None, source: str) -> dict[str, int]:
    agent = _agent_entity(conn, owner_kind, owner_id, event, source)
    shrine = _get_or_create_entity(
        conn, owner_kind, owner_id,
        entity_kind="shrine",
        display_name="归明观",
        summary="由事件投影沉淀的归明观经营/香火社会实体。",
        traits={"role": "shrine_or_venture"},
        metadata=_entity_metadata(event, activity=activity, source=source),
        source=source,
    )
    visitors = _get_or_create_entity(
        conn, owner_kind, owner_id,
        entity_kind="visitor_group",
        display_name="东市香客",
        summary="由摆摊、经营和香客来访事件沉淀的本地访客群体；具体地图槽位待世界观定义。",
        traits={"group": "local_customers_and_visitors"},
        metadata=_entity_metadata(event, activity=activity, source=source, group=True),
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
                reason=_reason(event, summary, default="归明观经营/摆摊事件完成"),
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
    }
    topic = _request_topic(event, default="general_blessing")
    record_social_request(
        conn, owner_kind, owner_id,
        requester_entity_id=visitors["id"],
        target_entity_id=shrine["id"],
        request_type="wish",
        topic=topic,
        summary="香客/顾客在经营事件中留下的祝愿或购买需求。",
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

    rumor_content = (
        "有香客低声说，归明观这回经营顺利，明灯待人也算温和。"
        if outcome == "positive"
        else "有香客担心，归明观这回经营的效果还需要再看看。"
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
                        evidence: dict[str, Any], summary: str | None, source: str) -> dict[str, int]:
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
        content=(
            "有委托人私下说，明灯这次外勤处理得稳妥。"
            if outcome == "positive"
            else "有人私下担心，明灯这次外勤没有完全解决问题。"
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
    return "明灯", "default_pending_canon_identity"


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
        """SELECT * FROM recurring_activity_occurrences
           WHERE owner_kind=? AND owner_id=? AND event_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (owner_kind, owner_id, event_id),
    ).fetchone()
    return dict(row) if row else None


def _get_occurrence(conn, owner_kind: str, owner_id: str, occurrence_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM recurring_activity_occurrences WHERE owner_kind=? AND owner_id=? AND id=?",
        (owner_kind, owner_id, occurrence_id),
    ).fetchone()
    return dict(row) if row else None


def _activity_for_occurrence(conn, owner_kind: str, owner_id: str,
                             occurrence: dict[str, Any] | None) -> dict[str, Any] | None:
    if not occurrence or not occurrence.get("activity_id"):
        return None
    from . import recurring
    return recurring.get_recurring_activity(conn, owner_kind, owner_id, occurrence["activity_id"])


def _activity_has_supply(activity: dict[str, Any] | None) -> bool:
    return bool(isinstance(activity, dict) and isinstance(activity.get("supply_chain"), dict) and activity["supply_chain"].get("goods_resource"))


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
