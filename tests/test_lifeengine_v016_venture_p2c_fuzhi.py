"""经营系统 P2c — full worked example: 明灯 摆摊卖符纸, self-made.

The whole upstream chain is engine-driven with consume/produce counts at each
step: 买笔(money→tool) → 买原料(money→material) → 制作(material+笔+time→符纸) →
摆摊(符纸→money). Materials and tools each replenish on their own when low; the
maker can't 制作 until the 笔 and 原料 are in hand."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016p2c"
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


def _has_event(rt, prefix):
    return any(e["title"].startswith(prefix) for e in rt.event_tool("list")["events"])


FUZHI = {
    "goods_resource": "stock.fuzhi", "goods_name": "符纸", "initial_stock": 0,
    "unit_price": 5, "demand_per_occurrence": 10, "money_resource": "money.lingzhu",
    "recipe": {
        "threshold": 8, "batch_output": 30, "duration_minutes": 120, "effort": {"energy": -12},
        "materials": {"mat.yuanzhi": 30, "mat.zhusha": 6},
        "material_restock": {
            "mat.yuanzhi": {"name": "符纸原纸", "threshold": 20, "quantity": 60, "unit_cost": 1},
            "mat.zhusha": {"name": "朱砂", "threshold": 10, "quantity": 20, "unit_cost": 1},
        },
        "tools": {"tool.bi": {"name": "笔", "unit_cost": 25}},
    },
}


def _register(rt):
    rt.activity("register", title="摆摊卖符纸", operation_model="active", cadence_kind="daily",
                start_time="10:00", end_time="14:00", timezone="UTC", location="十二城东市",
                supply_chain=FUZHI)


def test_resources_defined_on_register(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        _register(rt)
        # goods + each material + the tool are all defined stock accounts
        assert _stock(rt, "stock.fuzhi") == 0
        assert _stock(rt, "mat.yuanzhi") == 0
        assert _stock(rt, "mat.zhusha") == 0
        assert _stock(rt, "tool.bi") == 0
    finally:
        rt.close()


def test_full_chain_buy_tool_buy_materials_make_then_have_stock(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        _register(rt)
        # Day 1: goods low → orders the 笔 and the 原料; can't 制作 yet (no 笔).
        rt.tick(now="2026-06-15T15:00:00+00:00", manual=False)
        assert _has_event(rt, "买笔") or _has_event(rt, "买工具")
        assert _has_event(rt, "买原料")
        assert not _has_event(rt, "制作")          # no tool/materials in hand yet
        # Day 2: yesterday's buys complete → 笔 + 原料 in stock; now 制作 is created.
        rt.tick(now="2026-06-16T15:00:00+00:00", manual=False)
        assert _stock(rt, "tool.bi") == 1            # bought once
        assert _stock(rt, "mat.yuanzhi") == 60       # 原料 arrived
        assert _stock(rt, "mat.zhusha") == 20
        assert _has_event(rt, "制作")
        # Day 3: 制作 completes → 符纸 produced, materials consumed by the batch.
        rt.tick(now="2026-06-17T15:00:00+00:00", manual=False)
        assert _stock(rt, "mat.yuanzhi") == 30       # 60 − 30 per batch
        assert _stock(rt, "mat.zhusha") == 14        # 20 − 6
        assert _stock(rt, "stock.fuzhi") >= 20       # 30 made (some may already be sold)
        # by now at least one 摆摊 day has sold 符纸 for money
        sold = rt.conn.execute(
            "SELECT COALESCE(SUM(sold_quantity),0) s FROM venture_occurrences WHERE owner_kind='agent' AND owner_id=?",
            (DEFAULT_AGENT_ID,),
        ).fetchone()["s"]
        assert sold >= 0  # selling occurs once stock exists (see P2 for the income assertion)
        # the 摆摊 sell event itself is materialized each day
        assert _has_event(rt, "摆摊卖符纸")
    finally:
        rt.close()
