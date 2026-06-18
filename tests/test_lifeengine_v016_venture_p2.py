"""经营系统 P2: 进销存 — goods don't appear from nowhere. Selling consumes stock
and earns price×sold; when stock runs low the heartbeat auto-creates a 进货
(procurement) event that costs money and restocks on completion."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016p2"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，好奇而勤奋。")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _stock(rt, key):
    r = rt.conn.execute(
        "SELECT current_value FROM resource_accounts WHERE owner_kind='agent' AND owner_id=? AND resource_key=?",
        (DEFAULT_AGENT_ID, key),
    ).fetchone()
    return float(r["current_value"]) if r else None


def _occ(rt, activity_id):
    return rt.conn.execute(
        "SELECT * FROM recurring_activity_occurrences WHERE activity_id=? ORDER BY created_at LIMIT 1",
        (activity_id,),
    ).fetchone()


def _block_end_ts(rt, title_prefix):
    """End ts of the most recent schedule block whose event title starts with prefix."""
    r = rt.conn.execute(
        """SELECT sb.end_ts FROM schedule_blocks sb JOIN events e ON e.id=sb.event_id
             WHERE e.title LIKE ? ORDER BY sb.created_at DESC LIMIT 1""",
        (title_prefix + "%",),
    ).fetchone()
    return int(r["end_ts"]) if r and r["end_ts"] is not None else None


def _iso_after(ts, seconds=7200):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ts) + seconds, timezone.utc).isoformat()


def test_sale_consumes_stock_and_earns(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        reg = rt.activity("register", title="净符摊", cadence_kind="daily",
                          start_time="10:00", end_time="14:00", timezone="UTC",
                          supply_chain={"goods_resource": "stock.jingfu", "goods_name": "净符", "unit": "枚",
                                        "initial_stock": 30, "unit_price": 8, "demand_per_occurrence": 12,
                                        "money_resource": "money.lingzhu"})
        aid = reg["receipt"]["facts"][0]["evidence"]["activity_id"]
        # the goods resource is defined with the initial stock
        assert _stock(rt, "stock.jingfu") == 30
        money0 = _stock(rt, "money.lingzhu")
        # tick 1 materializes the stall event; the completing tick fires after the
        # block's actual end (tz-agnostic), so the execution sweep completes it and
        # sales then settle.
        rt.tick(now="2026-06-15T15:00:00+00:00", manual=False)
        rt.tick(now=_iso_after(_block_end_ts(rt, "净符摊")), manual=False)
        occ = _occ(rt, aid)
        assert occ["sale_settled"] == 1
        assert occ["sold_quantity"] == 12       # min(demand 12, stock 30)
        assert occ["income"] == 96              # 12 × 8
        assert _stock(rt, "stock.jingfu") == 18  # 30 − 12 sold
        assert _stock(rt, "money.lingzhu") == money0 + 96
    finally:
        rt.close()


def test_low_stock_auto_creates_restock(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        reg = rt.activity("register", title="净符摊", cadence_kind="daily",
                          start_time="10:00", end_time="14:00", timezone="UTC",
                          supply_chain={"goods_resource": "stock.jingfu", "goods_name": "净符", "unit": "枚",
                                        "initial_stock": 0, "unit_price": 8, "demand_per_occurrence": 12,
                                        "money_resource": "money.lingzhu",
                                        "restock": {"threshold": 10, "quantity": 20, "unit_cost": 2}})
        aid = reg["receipt"]["facts"][0]["evidence"]["activity_id"]
        rt.tick(now="2026-06-15T15:00:00+00:00", manual=False)
        orders = rt.conn.execute(
            "SELECT * FROM venture_restock_orders WHERE activity_id=? AND status='pending'", (aid,),
        ).fetchall()
        assert len(orders) == 1
        assert orders[0]["quantity"] == 20
        # a 进货 event was created to actually fetch the goods (costs money)
        evs = [e for e in rt.event_tool("list")["events"] if e["title"].startswith("进货")]
        assert evs and evs[0]["resource_costs"].get("money.lingzhu") == -40  # 20 × 2
        assert evs[0]["resource_costs"].get("stock.jingfu") == 20
        # idempotent: another tick the same day does not pile up restock orders
        rt.tick(now="2026-06-15T15:30:00+00:00", manual=False)
        again = rt.conn.execute(
            "SELECT COUNT(*) c FROM venture_restock_orders WHERE activity_id=? AND status='pending'", (aid,),
        ).fetchone()
        assert again["c"] == 1
    finally:
        rt.close()


def test_restock_arrives_and_replenishes(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.activity("register", title="净符摊", cadence_kind="daily",
                    start_time="10:00", end_time="14:00", timezone="UTC",
                    supply_chain={"goods_resource": "stock.jingfu", "goods_name": "净符", "unit": "枚",
                                  "initial_stock": 0, "unit_price": 8, "demand_per_occurrence": 12,
                                  "money_resource": "money.lingzhu",
                                  "restock": {"threshold": 10, "quantity": 20, "unit_cost": 2}})
        rt.tick(now="2026-06-15T15:00:00+00:00", manual=False)   # orders a restock event
        assert _stock(rt, "stock.jingfu") == 0
        rt.tick(now=_iso_after(_block_end_ts(rt, "进货")), manual=False)  # sweep completes restock → stock arrives
        assert _stock(rt, "stock.jingfu") == 20
    finally:
        rt.close()
