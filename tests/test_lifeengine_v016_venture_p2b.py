"""经营系统 P2b: manufacture recipes — instead of buying goods, make them from
materials. Low stock auto-creates a 制作 event that consumes materials and
produces goods on completion; without materials, making postpones."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.constants import DEFAULT_AGENT_ID


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v016p2b"
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


def _make_event(rt):
    return next((e for e in rt.event_tool("list")["events"] if e["title"].startswith("制作")), None)


def _block_end_ts(rt, title_prefix):
    r = rt.conn.execute(
        """SELECT sb.end_ts FROM schedule_blocks sb JOIN events e ON e.id=sb.event_id
             WHERE e.title LIKE ? ORDER BY sb.created_at DESC LIMIT 1""",
        (title_prefix + "%",),
    ).fetchone()
    return int(r["end_ts"]) if r and r["end_ts"] is not None else None


def _iso_after(ts, seconds=7200):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ts) + seconds, timezone.utc).isoformat()


SUPPLY = {
    "goods_resource": "stock.jingfu", "goods_name": "净符", "initial_stock": 0,
    "unit_price": 8, "demand_per_occurrence": 12,
    "recipe": {"threshold": 10, "batch_output": 20,
               "materials": {"mat.fupaper": 40, "mat.cinnabar": 10},
               "effort": {"energy": -15}, "duration_minutes": 120},
}


def test_materials_are_defined_on_register(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        sc = dict(SUPPLY)
        sc["recipe"] = dict(SUPPLY["recipe"], materials_initial={"mat.fupaper": 100, "mat.cinnabar": 50})
        rt.activity("register", title="自制净符摊", cadence_kind="daily",
                    start_time="10:00", end_time="14:00", timezone="UTC", supply_chain=sc)
        assert _stock(rt, "mat.fupaper") == 100
        assert _stock(rt, "mat.cinnabar") == 50
        assert _stock(rt, "stock.jingfu") == 0
    finally:
        rt.close()


def test_low_stock_makes_from_materials(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        sc = dict(SUPPLY)
        sc["recipe"] = dict(SUPPLY["recipe"], materials_initial={"mat.fupaper": 100, "mat.cinnabar": 50})
        rt.activity("register", title="自制净符摊", cadence_kind="daily",
                    start_time="10:00", end_time="14:00", timezone="UTC", supply_chain=sc)
        rt.tick(now="2026-06-15T15:00:00+00:00", manual=False)   # low stock → 制作 event
        ev = _make_event(rt)
        assert ev is not None
        assert ev["resource_costs"].get("stock.jingfu") == 20      # produces goods
        assert ev["resource_costs"].get("mat.fupaper") == -40      # consumes materials
        assert ev["resource_costs"].get("energy") == -15           # making takes effort
        # complete the 制作 event → materials consumed, goods produced
        rt.tick(now=_iso_after(_block_end_ts(rt, "制作")), manual=False)
        assert _stock(rt, "stock.jingfu") == 20
        assert _stock(rt, "mat.fupaper") == 60      # 100 − 40
        assert _stock(rt, "mat.cinnabar") == 40     # 50 − 10
    finally:
        rt.close()


def test_making_waits_without_materials(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        sc = dict(SUPPLY)  # no materials_initial AND no material_restock → no way to get materials
        rt.activity("register", title="自制净符摊", cadence_kind="daily",
                    start_time="10:00", end_time="14:00", timezone="UTC", supply_chain=sc)
        rt.tick(now="2026-06-15T15:00:00+00:00", manual=False)
        # with no materials in hand (and no restock to fetch them), the engine
        # does NOT create a doomed 制作 event — it waits. Goods never appear.
        assert _make_event(rt) is None
        rt.tick(now="2026-06-16T15:00:00+00:00", manual=False)
        assert _stock(rt, "stock.jingfu") == 0
    finally:
        rt.close()
