"""Event-driven Social World projection for Guimingguan-style activity."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from lifeengine import recurring, social_projector
from lifeengine.canon import ensure_control
from lifeengine.db import _SCHEMA_VERSION, transaction
from lifeengine.jsonutil import loads
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.social_projector import project_completed_event, project_venture_sale_settlement
from lifeengine.trace import Trace, new_id


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home_social_projection"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("名字是 明灯。她经营归明观，会接外勤委托。")
    rt.commit_canon()
    rt.rename("明灯")
    rt.control("resume")
    rt.living("init_resources")


def _result(commit: dict, index: int = 0) -> dict:
    return ((commit.get("results") or [])[index].get("result") or {})


def _count(rt: LifeEngineRuntime, table: str) -> int:
    return int(rt.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"])


def _names(rt: LifeEngineRuntime, kind: str | None = None) -> set[str]:
    if kind:
        rows = rt.conn.execute("SELECT display_name FROM world_entities WHERE entity_kind=? AND status='active'", (kind,)).fetchall()
    else:
        rows = rt.conn.execute("SELECT display_name FROM world_entities WHERE status='active'").fetchall()
    return {r["display_name"] for r in rows}


def test_schema_v62_projection_and_request_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert _SCHEMA_VERSION >= 62
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] >= 62
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"social_projection_runs", "social_requests"}.issubset(tables)
        rep_cols = {r[1] for r in rt.conn.execute("PRAGMA table_info(reputation_events)").fetchall()}
        rumor_cols = {r[1] for r in rt.conn.execute("PRAGMA table_info(rumors)").fetchall()}
        assert "evidence_json" in rep_cols
        assert "evidence_json" in rumor_cols
    finally:
        rt.close()


def test_stall_event_completion_projects_social_world_idempotently(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        ev = _result(rt.event_tool(
            "create",
            title="归明观午后摆摊卖净符",
            event_type="work",
            activity_domain="venture",
            tags=["摆摊", "归明观", "净符"],
            attributes={
                "wish_topic": "general_blessing",
                "venue_name": "归明观",
                "customer_group_name": "东市香客",
            },
            location={"name": "东市摊位", "kind": "freeform"},
            resource_costs={},
        ))
        out = rt.event_tool("complete", event_id=ev["id"], summary="卖符顺利，香客愿意再来。")
        projection = _result(out).get("social_projection") or {}
        assert projection["projected"] is True

        assert {"明灯", "归明观", "东市香客"}.issubset(_names(rt))
        summary = rt.social("summary")["social_world"]
        assert summary["requests"]
        assert any(r["request_type"] == "wish" and r["topic"] == "general_blessing" for r in summary["requests"])
        assert any(a["axis"] in {"approachable", "efficacious", "price_fairness"} for a in summary["reputation"])
        assert any(e["axis"] in {"satisfaction", "kindness", "perceived_effectiveness"} for e in summary["evaluations"])
        assert any(r["truth_layer"] == "rumor_unverified" for r in summary["rumors"])

        counts = {t: _count(rt, t) for t in ["reputation_events", "social_evaluations", "rumors", "social_requests"]}
        second = project_completed_event(rt.conn, "agent", "default-agent", ev["id"], summary="卖符顺利，香客愿意再来。")
        assert second["projected"] is False
        assert {t: _count(rt, t) for t in counts} == counts
        assert _count(rt, "social_projection_runs") == 1

        rep_evidence = loads(rt.conn.execute("SELECT evidence_json FROM reputation_events LIMIT 1").fetchone()["evidence_json"], {})
        assert rep_evidence["event_id"] == ev["id"]
        assert rep_evidence["source"].startswith("social_projector")
        venue_meta = loads(rt.conn.execute("SELECT metadata_json FROM world_entities WHERE display_name='归明观'").fetchone()["metadata_json"], {})
        audience_meta = loads(rt.conn.execute("SELECT metadata_json FROM world_entities WHERE display_name='东市香客'").fetchone()["metadata_json"], {})
        assert venue_meta["name_source"] == "event_or_activity"
        assert audience_meta["name_source"] == "event_attributes"
    finally:
        rt.close()


def test_stall_projection_uses_unknown_placeholders_without_fixed_lore(tmp_path):
    """没有结构化场所/客群证据时，社会投影不能写入固定归明观世界观名称。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        rt.setup("一个未命名世界里的经营者，会做小型经营活动。")
        rt.commit_canon()
        rt.rename("阿澜")
        rt.control("resume")
        rt.living("init_resources")

        ev = _result(rt.event_tool(
            "create",
            title="午后摆摊卖护符",
            event_type="work",
            activity_domain="venture",
            tags=["摆摊", "护符"],
            attributes={"wish_topic": "general_blessing", "goods_name": "护符"},
            resource_costs={},
        ))
        rt.event_tool("complete", event_id=ev["id"], summary="卖得还算顺利。")

        names = _names(rt)
        assert "归明观" not in names
        assert "东市香客" not in names
        assert "明灯" not in names
        assert "护符经营点" in names
        assert "未具名来访者" in names
        rumor = rt.conn.execute("SELECT content FROM rumors ORDER BY created_at DESC LIMIT 1").fetchone()
        assert rumor is not None
        assert "阿澜" in rumor["content"]
        assert "归明观" not in rumor["content"]
        venue_meta = loads(rt.conn.execute("SELECT metadata_json FROM world_entities WHERE display_name='护符经营点'").fetchone()["metadata_json"], {})
        audience_meta = loads(rt.conn.execute("SELECT metadata_json FROM world_entities WHERE display_name='未具名来访者'").fetchone()["metadata_json"], {})
        assert venue_meta["name_source"] == "generic_from_goods"
        assert audience_meta["name_source"] == "generic_unknown"
        assert venue_meta["worldview_slots"]["origin"] == "pending_slot"
        assert audience_meta["worldview_slots"]["map_location"] == "unknown"
    finally:
        rt.close()


def test_commission_completion_projects_client_relation_and_request(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        ev = _result(rt.event_tool(
            "create",
            title="上门勘察商铺怪响委托",
            event_type="commission",
            activity_domain="fieldwork",
            tags=["委托", "外勤", "上门", "勘察"],
            participants=[{"role": "client", "name": "王掌柜", "circle": "东市商户圈"}],
            attributes={"commission_topic": "shop_noise"},
            resource_costs={},
        ))
        rt.event_tool("complete", event_id=ev["id"], summary="勘察后给出处理办法，委托人表示感谢。")

        assert "王掌柜" in _names(rt, "client")
        assert "东市商户圈" in _names(rt)
        edges = rt.social("edges")["edges"]
        assert {"trust", "familiarity", "gratitude"}.issubset({e["axis"] for e in edges})
        reps = rt.social("reputation_accounts")["reputation"]
        assert any(r["axis"] == "fieldwork_reliability" and r["value"] > 0 for r in reps)
        requests = rt.social("requests")["requests"]
        assert any(r["request_type"] == "fieldwork_request" and r["topic"] == "shop_noise" for r in requests)
        assert rt.social("rumors")["rumors"]
    finally:
        rt.close()


def test_sale_settled_occurrence_projects_once(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        reg = rt.activity(
            "register",
            title="净符摊",
            cadence_kind="daily",
            supply_chain={"goods_resource": "stock.jingfu", "goods_name": "净符", "unit": "枚",
                          "initial_stock": 10, "unit_price": 8, "demand_per_occurrence": 3,
                          "money_resource": "money.lingzhu"},
            tags=["摆摊", "净符"],
        )
        activity_id = reg["receipt"]["facts"][0]["evidence"]["activity_id"]
        ev = _result(rt.event_tool("create", title="净符摊", event_type="work",
                                   activity_domain="venture", tags=["摆摊", "净符"], resource_costs={}))
        recurring.record_occurrence(rt.conn, "agent", "default-agent", activity_id, "2026-06-22", ev["id"], None)
        rt.event_tool("complete", event_id=ev["id"], summary="当日摆摊结束，待结算销售。")
        skipped = rt.conn.execute("SELECT status FROM social_projection_runs WHERE event_id=?", (ev["id"],)).fetchall()
        assert skipped == []

        occ = rt.conn.execute("SELECT * FROM recurring_activity_occurrences WHERE event_id=?", (ev["id"],)).fetchone()
        rt.conn.execute("UPDATE recurring_activity_occurrences SET sale_settled=1, sold_quantity=3, income=24 WHERE id=?", (occ["id"],))
        first = project_venture_sale_settlement(rt.conn, "agent", "default-agent", occ["id"])
        second = project_venture_sale_settlement(rt.conn, "agent", "default-agent", occ["id"])
        assert first["projected"] is True
        assert second["projected"] is False
        assert rt.conn.execute("SELECT COUNT(*) c FROM social_projection_runs WHERE occurrence_id=?", (occ["id"],)).fetchone()["c"] == 1
        assert rt.social("requests", request_type="wish")["requests"]
    finally:
        rt.close()


def test_event_completion_survives_social_projection_failure_and_can_retry(tmp_path):
    """社交投影失败不应阻断事件完成；review 应给出可执行补投影入口。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        ev = _result(rt.event_tool(
            "create",
            title="归明观午后摆摊卖净符",
            event_type="work",
            activity_domain="venture",
            tags=["摆摊", "归明观", "净符"],
            resource_costs={},
        ))

        old_record_rumor = social_projector.record_rumor
        social_projector.record_rumor = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced-rumor-boom"))
        try:
            out = rt.event_tool("complete", event_id=ev["id"], summary="卖符顺利，香客愿意再来。")
        finally:
            social_projector.record_rumor = old_record_rumor

        projection = _result(out).get("social_projection") or {}
        assert out["ok"] is True
        assert projection["reason"] == "projection_failed"
        assert rt.conn.execute("SELECT status FROM events WHERE id=?", (ev["id"],)).fetchone()["status"] == "completed"
        assert _count(rt, "social_projection_runs") == 0
        assert _count(rt, "reputation_events") == 0
        assert _count(rt, "social_evaluations") == 0
        assert _count(rt, "social_requests") == 0
        assert _count(rt, "rumors") == 0

        review = rt.review("summary")
        items = [i for i in review["items"] if i["item_type"] == "social_projection_failed"]
        assert items
        assert review["summary"]["social_projection"]["open_failures"] == 1
        assert "社会投影：待补投影 1 条" in review["rendered"]
        assert f"event_id={ev['id']}" in review["rendered"]
        assert f"audit_id={items[0]['source_id']}" in review["rendered"]
        assert items[0]["action_hint"]["action"] == "retry_projection"
        assert items[0]["action_hint"]["event_id"] == ev["id"]

        plan = rt.review("preview_action", item_id=items[0]["id"])
        assert plan["plan"]["tool"] == "life_social"
        assert plan["plan"]["action"] == "retry_projection"

        retry = rt.review("apply", item_id=items[0]["id"])
        assert retry["ok"] is True
        assert retry["applied"] is True
        assert retry["output"]["projected"] is True
        assert _count(rt, "social_projection_runs") == 1
        assert rt.social("requests", request_type="wish")["requests"]

        repaired_review = rt.review("summary")
        assert not [i for i in repaired_review["items"] if i["item_type"] == "social_projection_failed"]
    finally:
        rt.close()


def test_world_review_batch_safely_retries_social_projection_failure(tmp_path):
    """World-section review batch should repair idempotent social projection failures."""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        policy = rt.review("policy")["review_action_policy"]["policy"]
        assert "social_projection_failed" in policy["safe_item_types"]
        assert "world" in policy["safe_sections"]
        assert "world" in policy["agent_managed_sections"]

        ev = _result(rt.event_tool(
            "create",
            title="归明观午后摆摊卖净符",
            event_type="work",
            activity_domain="venture",
            tags=["摆摊", "归明观", "净符"],
            resource_costs={},
        ))

        old_record_rumor = social_projector.record_rumor
        social_projector.record_rumor = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced-rumor-boom"))
        try:
            rt.event_tool("complete", event_id=ev["id"], summary="卖符顺利，香客愿意再来。")
        finally:
            social_projector.record_rumor = old_record_rumor

        review = rt.review("summary")
        items = [i for i in review["items"] if i["item_type"] == "social_projection_failed"]
        assert len(items) == 1

        preview = rt.review("apply_all", review_run_id=review["review_run_id"], section="world", dry_run=True)
        assert preview["plan"]["selected_count"] == 1
        assert preview["plan"]["items"][0]["item_type"] == "social_projection_failed"

        applied = rt.review("apply_all", review_run_id=review["review_run_id"], section="world")
        assert applied["ok"] is True
        assert applied["applied"] is True
        assert applied["status"] == "applied"
        assert applied["results"][0]["output"]["projected"] is True
        assert _count(rt, "social_projection_runs") == 1
        assert rt.social("requests", request_type="wish")["requests"]

        repaired_review = rt.review("summary")
        assert not [i for i in repaired_review["items"] if i["item_type"] == "social_projection_failed"]
    finally:
        rt.close()


def test_venture_projection_failure_rolls_back_and_heartbeat_retries(tmp_path):
    """经营结算投影失败不应半写社会事实；已结算 occurrence 下次 heartbeat 可补投影。"""
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        reg = rt.activity(
            "register",
            title="净符摊",
            cadence_kind="daily",
            supply_chain={"goods_resource": "stock.jingfu", "goods_name": "净符", "unit": "枚",
                          "initial_stock": 10, "unit_price": 8, "demand_per_occurrence": 3,
                          "money_resource": "money.lingzhu"},
            tags=["摆摊", "净符"],
        )
        activity_id = reg["receipt"]["facts"][0]["evidence"]["activity_id"]
        ev = _result(rt.event_tool("create", title="净符摊", event_type="work",
                                   activity_domain="venture", tags=["摆摊", "净符"], resource_costs={}))
        recurring.record_occurrence(rt.conn, "agent", "default-agent", activity_id, "2026-06-22", ev["id"], None)
        rt.event_tool("complete", event_id=ev["id"], summary="当日摆摊结束，待结算销售。")

        old_record_rumor = social_projector.record_rumor
        social_projector.record_rumor = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced-rumor-boom"))
        try:
            with transaction(rt.conn):
                trace = Trace(rt.conn, "agent", "default-agent", "audit_repro", tick_id=new_id("tick")).start()
                control = ensure_control(rt.conn, "agent", "default-agent")
                first = rt._settle_supply_chain_for_tick("agent", "default-agent", control, trace.tick_id, trace, "2026-06-23T12:00:00+00:00")
                trace.end(status="ok", output_obj=first)
        finally:
            social_projector.record_rumor = old_record_rumor

        occ = rt.conn.execute("SELECT * FROM recurring_activity_occurrences WHERE event_id=?", (ev["id"],)).fetchone()
        assert first["status"] == "partial"
        assert first["ok"] is False
        assert occ["sale_settled"] == 1
        assert occ["sold_quantity"] == 3
        assert occ["income"] == 24
        assert rt.conn.execute("SELECT COUNT(*) c FROM social_projection_runs WHERE occurrence_id=?", (occ["id"],)).fetchone()["c"] == 0
        assert _count(rt, "reputation_events") == 0
        assert _count(rt, "social_evaluations") == 0
        assert _count(rt, "social_requests") == 0
        assert _count(rt, "rumors") == 0

        with transaction(rt.conn):
            trace = Trace(rt.conn, "agent", "default-agent", "audit_repro_retry", tick_id=new_id("tick")).start()
            control = ensure_control(rt.conn, "agent", "default-agent")
            second = rt._settle_supply_chain_for_tick("agent", "default-agent", control, trace.tick_id, trace, "2026-06-23T12:05:00+00:00")
            trace.end(status="ok", output_obj=second)

        assert second["status"] == "ok"
        assert second["social_projections"] == 1
        run = rt.conn.execute("SELECT status FROM social_projection_runs WHERE occurrence_id=?", (occ["id"],)).fetchone()
        assert run["status"] == "applied"
        assert rt.social("requests", request_type="wish")["requests"]
    finally:
        rt.close()


def test_webui_social_requests_have_visible_surface():
    """WebUI 社会世界面板必须把 reader 返回的请求/愿望列表渲染出来。"""
    root = Path(__file__).resolve().parents[1]
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    app = (root / "webui" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="social-requests"' in index
    assert "counts.requests" in app
    assert "social-requests" in app


def test_projected_entities_do_not_invent_origin_faction_or_map_slots(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        ev = _result(rt.event_tool(
            "create",
            title="未具名委托人外勤委托",
            event_type="commission",
            activity_domain="fieldwork",
            tags=["委托", "外勤"],
            resource_costs={},
        ))
        rt.event_tool("complete", event_id=ev["id"], summary="完成基础处理。")
        client = rt.conn.execute("SELECT * FROM world_entities WHERE display_name='未具名委托人'").fetchone()
        metadata = loads(client["metadata_json"], {})
        assert metadata["anonymous"] is True
        assert metadata["origin"]["slot_status"] == "unknown"
        assert metadata["faction"]["slot_status"] == "unknown"
        assert metadata["location"]["slot_status"] == "unknown"
        assert metadata["worldview_slots"]["origin"] == "pending_slot"
        assert metadata["worldview_slots"]["faction"] == "pending_slot"
        assert metadata["worldview_slots"]["map_location"] == "unknown"
    finally:
        rt.close()
