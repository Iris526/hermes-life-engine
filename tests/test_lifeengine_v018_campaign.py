"""v0.18.0 P3 — campaigns / 资料片: cross-week themed arcs.

A campaign gives the life a big thing in motion (预兆→升温→高潮→收尾) instead of
scattered daily tasks. The heartbeat materializes the current phase's themed
events day by day (idempotent), advances phases by elapsed time, and resolves at
the end. ``seed`` lets the agent author a whole arc from a one-line idea (offline
fake host model); the engine itself is fully deterministic.
"""

from __future__ import annotations

import os
from pathlib import Path

from lifeengine import campaigns, life_author
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.runtime import LifeEngineRuntime


def fresh_home(tmp_path: Path):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def setup_agent(rt: LifeEngineRuntime):
    rt.setup("v0.18.0 campaign test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


_PHASES = [
    {"title": "预兆", "kind": "预兆", "duration_days": 2, "daily_spawns": 1,
     "spawn_template": {"title": "留意征兆", "event_type": "personal", "importance": 55, "duration_minutes": 60},
     "one_time_events": [{"title": "发现奇怪的征兆", "event_type": "personal", "importance": 60}]},
    {"title": "高潮", "kind": "高潮", "duration_days": 1, "daily_spawns": 2,
     "spawn_template": {"title": "正面应对", "event_type": "personal", "importance": 80, "duration_minutes": 90},
     "one_time_events": [{"title": "决战", "event_type": "personal", "importance": 90}]},
]


def _count_event(rt: LifeEngineRuntime, title: str) -> int:
    return rt.conn.execute(
        "SELECT COUNT(*) FROM events WHERE owner_kind='agent' AND owner_id=? AND title=?",
        (DEFAULT_AGENT_ID, title),
    ).fetchone()[0]


def _campaign_status(rt: LifeEngineRuntime, camp_id: str):
    row = rt.conn.execute("SELECT status, current_phase FROM campaigns WHERE id=?", (camp_id,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _register(rt: LifeEngineRuntime):
    out = rt.campaign("register", title="怪谈事变", phases=_PHASES, timezone="UTC", start_date="2026-06-12")
    return out["campaign"]["id"]


# ---------------------------------------------------------------------------

def test_schema_and_campaign_tables(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        tables = {r[0] for r in rt.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"campaigns", "campaign_phase_occurrences"}.issubset(tables)
    finally:
        rt.close()


def test_locate_phase_is_a_pure_function():
    # phases: [dur2, dur1] → total 3
    assert campaigns.locate_phase(_PHASES, 0)[0] == 0
    assert campaigns.locate_phase(_PHASES, 1)[0] == 0
    assert campaigns.locate_phase(_PHASES, 2)[0] == 1   # crossed into phase 1
    assert campaigns.locate_phase(_PHASES, 3)[0] is None  # past the arc → resolve


def test_campaign_materializes_phase_by_phase_and_resolves(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        camp_id = _register(rt)

        # Day 0: enter 预兆 → its one-time beat + 1 daily spawn
        rt.tick(now="2026-06-12T03:00:00+00:00")
        assert _count_event(rt, "发现奇怪的征兆") == 1
        assert _count_event(rt, "留意征兆") == 1
        assert _campaign_status(rt, camp_id) == ("active", 0)

        # Day 0 again: idempotent — nothing new materializes the same day
        rt.tick(now="2026-06-12T06:00:00+00:00")
        assert _count_event(rt, "发现奇怪的征兆") == 1
        assert _count_event(rt, "留意征兆") == 1

        # Day 1: still 预兆, no repeated beat, +1 daily
        rt.tick(now="2026-06-13T03:00:00+00:00")
        assert _count_event(rt, "发现奇怪的征兆") == 1
        assert _count_event(rt, "留意征兆") == 2

        # Day 2: cross into 高潮 → its beat + 2 daily (denser, higher-stakes)
        rt.tick(now="2026-06-14T03:00:00+00:00")
        assert _count_event(rt, "决战") == 1
        assert _count_event(rt, "正面应对") == 2
        assert _campaign_status(rt, camp_id)[1] == 1

        # Day 3: past the arc → resolved, no further spawns
        rt.tick(now="2026-06-15T03:00:00+00:00")
        assert _campaign_status(rt, camp_id)[0] == "resolved"
        assert _count_event(rt, "决战") == 1
    finally:
        rt.close()


def test_cancelled_campaign_stops_materializing(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        camp_id = _register(rt)
        rt.campaign("cancel", campaign_id=camp_id)
        rt.tick(now="2026-06-12T03:00:00+00:00")
        assert _count_event(rt, "发现奇怪的征兆") == 0
        assert _campaign_status(rt, camp_id)[0] == "cancelled"
    finally:
        rt.close()


def test_no_campaigns_is_a_clean_noop(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.tick(now="2026-06-12T03:00:00+00:00")
        assert out["campaigns"]["status"] == "ok"
        assert out["campaigns"]["campaigns"] == []
    finally:
        rt.close()


def test_agent_can_seed_its_own_arc_from_a_brief(tmp_path):
    fresh_home(tmp_path)
    blueprint = {
        "title": "归明观夏夜庙会",
        "description": "张罗一场夏夜庙会。",
        "importance": 70,
        "phases": [
            {"title": "筹备", "duration_days": 3, "daily_spawns": 1,
             "spawn_template": {"title": "采买灯笼与香烛", "event_type": "personal", "importance": 55, "duration_minutes": 60}},
            {"title": "庙会当夜", "duration_days": 1, "daily_spawns": 0,
             "one_time_events": [{"title": "点灯开场", "event_type": "personal", "importance": 85}]},
        ],
    }

    class _U:
        input_tokens = 300; output_tokens = 200; total_tokens = 500; cost_usd = 0.004

    class _R:
        def __init__(self, p): self.parsed = p; self.usage = _U(); self.provider = "f"; self.model = "m"

    class _L:
        def __init__(self, p): self._p = p; self.calls = []
        def complete_structured(self, **k): self.calls.append(k); return _R(self._p)

    life_author.set_test_llm(_L(blueprint))
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.campaign("seed", brief="想给归明观办一场夏夜庙会", timezone="UTC", start_date="2026-06-12")
        assert out["ok"] and out["seeded"]
        camp = out["campaign"]
        assert camp["title"] == "归明观夏夜庙会"
        assert len(camp["phases"]) == 2
        assert camp["source"] == "campaign_seed"

        # and it actually materializes on the heartbeat
        rt.tick(now="2026-06-12T03:00:00+00:00")
        assert _count_event(rt, "采买灯笼与香烛") == 1
    finally:
        life_author.set_test_llm(None)
        rt.close()


def test_seed_degrades_without_host_model(tmp_path):
    fresh_home(tmp_path)
    life_author.set_test_llm(None)
    rt = LifeEngineRuntime()
    try:
        setup_agent(rt)
        out = rt.campaign("seed", brief="想办点大事")
        assert out["ok"] is False and out["seeded"] is False
        assert rt.campaign("list")["campaigns"] == []
    finally:
        rt.close()
