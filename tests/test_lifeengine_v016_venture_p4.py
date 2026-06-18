"""经营系统 P4: operation-model behaviour. active occupies the agent's time;
self_service / staffed run without her (no block, no effort) and settle
passively; staffed pays a per-occurrence wage."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016p4"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("测试 Agent，好奇而勤奋。")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _event(rt, title):
    evs = [e for e in rt.event_tool("list")["events"] if e["title"] == title]
    return evs[0] if evs else None


def _has_block(rt, event_id):
    return rt.conn.execute("SELECT 1 FROM schedule_blocks WHERE event_id=? LIMIT 1", (event_id,)).fetchone() is not None


def _stock(rt, key):
    r = rt.conn.execute(
        "SELECT current_value FROM resource_accounts WHERE owner_kind='agent' AND owner_id=? AND resource_key=?",
        (DEFAULT_AGENT_ID, key),
    ).fetchone()
    return float(r["current_value"]) if r else None


def test_self_service_no_block_settles_passively(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        # no window → passive settlement fires promptly; self_service occupies no time
        rt.activity("register", title="自助净符架", operation_model="self_service", timezone="UTC",
                    supply_chain={"goods_resource": "stock.jingfu", "goods_name": "净符", "initial_stock": 30,
                                  "unit_price": 8, "demand_per_occurrence": 12})
        money0 = _stock(rt, "money.lingzhu")
        rt.tick(now="2026-06-15T09:00:00+00:00", manual=False)
        ev = _event(rt, "自助净符架")
        assert ev is not None
        assert not _has_block(rt, ev["id"])          # passive → occupies no agent time
        assert _stock(rt, "stock.jingfu") == 18       # sold 12 of 30, passively
        assert _stock(rt, "money.lingzhu") == money0 + 96
    finally:
        rt.close()


def test_staffed_pays_wage(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        # staffed, non-supply: a passive pay of +50 each time, minus a wage of 15
        rt.activity("register", title="雇人代摆摊", operation_model="staffed", timezone="UTC",
                    wage_per_occurrence=15, resource_costs={"money.lingzhu": 50, "energy": -20})
        money0 = _stock(rt, "money.lingzhu")
        rt.tick(now="2026-06-15T09:00:00+00:00", manual=False)
        ev = _event(rt, "雇人代摆摊")
        assert ev is not None
        assert not _has_block(rt, ev["id"])           # staffed → her time not occupied
        # net = +50 pay − 15 wage; effort (energy) stripped since she isn't there
        assert _stock(rt, "money.lingzhu") == money0 + 35
        assert ev["resource_costs"].get("energy") is None
    finally:
        rt.close()


def test_active_still_occupies_a_block(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.activity("register", title="亲自坐摊", operation_model="active", timezone="UTC",
                    start_time="10:00", end_time="14:00", resource_costs={"energy": -10})
        rt.tick(now="2026-06-15T15:00:00+00:00", manual=False)
        ev = _event(rt, "亲自坐摊")
        assert ev is not None and _has_block(rt, ev["id"])  # active → occupies her time
    finally:
        rt.close()
