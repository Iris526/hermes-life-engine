"""v0.14.0 living-loop tests: real-time settlement, gap reconciliation,
passive metabolism, and the living-persona drift layer."""
from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("sqlite_vec")

from lifeengine.runtime import LifeEngineRuntime
from lifeengine.resources import reconcile_resources
from lifeengine import persona


def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v014"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime, desc: str = "测试 Agent，好奇而勤奋。"):
    rt.setup(desc)
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def _energy(rt):
    r = rt.conn.execute("SELECT current_value FROM resource_accounts WHERE resource_key='energy'").fetchone()
    return float(r[0]) if r else None


# --- Feature 1: real-time settlement + gap reconciliation -----------------

def test_settlement_scales_with_elapsed_time(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        # First tick: no prior tick -> elapsed 0 -> no settlement.
        t0 = rt.tick(now="2026-06-07T10:00:00+00:00", manual=False)
        assert t0["resource_recovery"]["settled_minutes"] == 0.0
        assert _energy(rt) == 60.0  # untouched
        # +5 minutes settles ~5 min worth.
        t1 = rt.tick(now="2026-06-07T10:05:00+00:00", manual=False)
        assert t1["resource_recovery"]["settled_minutes"] == 5.0
        e_after_5 = _energy(rt)
        assert 60.0 < e_after_5 < 64.0  # recovery +3 minus small metabolism
        # +50 minutes settles proportionally more.
        t2 = rt.tick(now="2026-06-07T10:55:00+00:00", manual=False)
        assert t2["resource_recovery"]["settled_minutes"] == 50.0
        assert "gap" in t2  # 50 > GAP_THRESHOLD_MIN
        # Ledger and accounts always reconcile.
        rec = reconcile_resources(rt.conn, "agent", "default-agent", record=False)
        assert rec["ok"] and not rec["mismatches"]
    finally:
        rt.close()


def test_long_offline_gap_is_capped_and_marked(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.tick(now="2026-06-07T10:00:00+00:00", manual=False)
        # 6 hours later: capped to GAP_CAP_MIN (120) and recorded as life_gap.
        t = rt.tick(now="2026-06-07T16:00:00+00:00", manual=False)
        assert t["gap"]["elapsed_min"] == 360.0
        assert t["gap"]["settled_min"] == 120.0
        assert t["gap"]["capped"] is True
        gaps = rt.conn.execute("SELECT COUNT(*) FROM life_journal WHERE entry_type='life_gap'").fetchone()[0]
        assert gaps >= 1
        rec = reconcile_resources(rt.conn, "agent", "default-agent", record=False)
        assert rec["ok"]
    finally:
        rt.close()


# --- Feature 2: passive metabolism ----------------------------------------

def test_passive_metabolism_gate_off_skips_drain(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.control("module", key="passive_metabolism", value="off")
        rt.tick(now="2026-06-07T10:00:00+00:00", manual=False)
        rt.tick(now="2026-06-07T10:05:00+00:00", manual=False)
        # With metabolism off, energy gets pure recovery (+3 over 5 min).
        assert abs(_energy(rt) - 63.0) < 1e-6
        # No metabolism ledger entries were written.
        n = rt.conn.execute("SELECT COUNT(*) FROM resource_ledger WHERE operation='metabolism'").fetchone()[0]
        assert n == 0
    finally:
        rt.close()


def test_passive_metabolism_drains_fatigue_upward(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        f0 = rt.conn.execute("SELECT current_value FROM resource_accounts WHERE resource_key='fatigue'").fetchone()[0]
        rt.tick(now="2026-06-07T10:00:00+00:00", manual=False)
        rt.tick(now="2026-06-07T11:00:00+00:00", manual=False)  # 60 min
        f1 = rt.conn.execute("SELECT current_value FROM resource_accounts WHERE resource_key='fatigue'").fetchone()[0]
        # fatigue recovery is -2/5min (=-24 over 60) but metabolism +0.05/min (=+3);
        # net should be lower than start but metabolism must have fired.
        n = rt.conn.execute("SELECT COUNT(*) FROM resource_ledger WHERE operation='metabolism' AND resource_key='fatigue'").fetchone()[0]
        assert n >= 1
        assert reconcile_resources(rt.conn, "agent", "default-agent", record=False)["ok"]
    finally:
        rt.close()


# --- Feature 3: living-persona drift --------------------------------------

def test_persona_seeds_and_drift_is_journaled_and_bounded(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ok, oid = "agent", "default-agent"
        # Drive a strong, sustained diligence signal directly via the LifeOp.
        for _ in range(30):
            rt.commit_ops([{"type": "PERSONA_DRIFT", "payload": {"signals": {"diligence": 0.9}, "source": "test"}}], ok, oid, "test")
        traits = persona.get_persona(rt.conn, ok, oid)
        assert traits["diligence"]["value"] > 0.3
        assert all(-1.0 <= t["value"] <= 1.0 for t in traits.values())  # bounded
        # drift is recorded in its own log and the journal.
        assert rt.conn.execute("SELECT COUNT(*) FROM persona_drift_log").fetchone()[0] > 0
        assert rt.conn.execute("SELECT COUNT(*) FROM life_journal WHERE entry_type='persona_drift'").fetchone()[0] > 0
    finally:
        rt.close()


def test_persona_drift_is_reversible_when_experience_stops(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ok, oid = "agent", "default-agent"
        for _ in range(30):
            persona.apply_persona_drift(rt.conn, ok, oid, signals={"diligence": 0.9}, source="test")
        peak = persona.get_persona(rt.conn, ok, oid)["diligence"]["value"]
        # Stop the signal: trait must relax back toward baseline (0), not overshoot.
        for _ in range(60):
            persona.apply_persona_drift(rt.conn, ok, oid, signals={}, source="test")
        relaxed = persona.get_persona(rt.conn, ok, oid)["diligence"]["value"]
        assert relaxed < peak
        assert abs(relaxed) < abs(peak)  # closer to baseline
    finally:
        rt.close()


def test_persona_appears_in_turn_context(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ok, oid = "agent", "default-agent"
        # Push a trait clearly off-neutral so the capsule lists it.
        for _ in range(30):
            persona.apply_persona_drift(rt.conn, ok, oid, signals={"curiosity": 0.9}, source="test")
        # Persona is private + droppable under tight budgets; give a generous
        # budget so it is retained, then confirm it reaches the context.
        rt.context("set", mode="debug", budget_chars=20000)
        ctx = rt.build_context_for_turn("s1", "t1", "hello")
        assert "persona" in ctx and "tone_hint" in ctx
    finally:
        rt.close()


def test_persona_consolidation_proposal_surfaces_in_review(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        ok, oid = "agent", "default-agent"
        # Drift far past the consolidation threshold with ample evidence.
        for _ in range(120):
            persona.apply_persona_drift(rt.conn, ok, oid, signals={"diligence": 1.0}, source="test")
        props = persona.propose_consolidation(rt.conn, ok, oid)
        assert any(p["trait"] == "diligence" for p in props)
        review = rt.review()
        rendered = review.get("rendered", "") if isinstance(review, dict) else str(review)
        assert "人格" in rendered or "persona_consolidation" in str(review)
    finally:
        rt.close()


def test_doctor_reports_persona_in_bounds(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        for _ in range(40):
            persona.apply_persona_drift(rt.conn, "agent", "default-agent", signals={"optimism": 1.0}, source="test")
        doc = rt.doctor()
        by_name = {c["name"]: c for c in doc.get("checks", [])}
        assert by_name["persona"]["ok"] is True
        assert by_name["resource_ledger"]["ok"] is True
    finally:
        rt.close()


# --- Impromptu activity capture + conflict resolution ---------------------

def _eid(res):
    return res["receipt"]["facts"][0]["evidence"]["event_id"]


def test_do_now_records_completed_event_on_free_slot(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.living("init_resources")
        out = rt.event_tool("do_now", title="陪用户逛街", duration_minutes=90,
                            now="2026-06-07T14:00:00+00:00", event_type="social",
                            event_category="social", importance=40, resource_costs={"energy": -10, "mood": 5})
        r = out["results"][0]["result"]
        assert r["completed"] is True
        assert r["conflict_count"] == 0
        ev = rt.conn.execute("SELECT status FROM events WHERE title='陪用户逛街'").fetchone()
        assert ev["status"] == "completed"
        blk = rt.conn.execute(
            "SELECT sb.block_type, sb.status FROM schedule_blocks sb JOIN events e ON e.id=sb.event_id WHERE e.title='陪用户逛街'"
        ).fetchone()
        assert blk["block_type"] == "impromptu"
        assert reconcile_resources(rt.conn, "agent", "default-agent", record=False)["ok"]
    finally:
        rt.close()


def test_do_now_auto_postpones_lower_priority_conflict_silently(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.living("init_resources")
        ce = rt.event_tool("create", title="整理符纸", event_type="maintenance", event_category="maintenance",
                           importance=30, planned_start="2026-06-07T15:00:00+00:00", planned_end="2026-06-07T16:00:00+00:00")
        low = _eid(ce)
        rt.event_tool("schedule", event_id=low, start="2026-06-07T15:00:00+00:00", end="2026-06-07T16:00:00+00:00")
        out = rt.event_tool("do_now", title="临时喝茶", duration_minutes=60, now="2026-06-07T15:10:00+00:00", importance=50)
        r = out["results"][0]["result"]
        assert r["conflict_count"] == 1
        assert r["postponed"][0]["surfaced"] is False  # low priority -> silent
        assert not r["notices"]
        # original task: old block rescheduled, fresh future block, event back to scheduled
        status = rt.conn.execute("SELECT status FROM events WHERE id=?", (low,)).fetchone()["status"]
        assert status == "scheduled"
        blocks = rt.conn.execute("SELECT status FROM schedule_blocks WHERE event_id=? ORDER BY created_at", (low,)).fetchall()
        states = [b["status"] for b in blocks]
        assert "rescheduled" in states and "planned" in states
        assert reconcile_resources(rt.conn, "agent", "default-agent", record=False)["ok"]
    finally:
        rt.close()


def test_do_now_flags_higher_priority_conflict_for_user(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.living("init_resources")
        ce = rt.event_tool("create", title="重要委托", event_type="work", event_category="work",
                           importance=90, planned_start="2026-06-07T15:00:00+00:00", planned_end="2026-06-07T16:00:00+00:00")
        high = _eid(ce)
        rt.event_tool("schedule", event_id=high, start="2026-06-07T15:00:00+00:00", end="2026-06-07T16:00:00+00:00")
        out = rt.event_tool("do_now", title="临时陪用户", duration_minutes=60, now="2026-06-07T15:10:00+00:00", importance=50)
        r = out["results"][0]["result"]
        assert r["conflict_count"] == 1
        assert r["postponed"][0]["surfaced"] is True  # high priority -> surfaced
        assert r["notices"]
        assert "重要委托" in r["agent_notice"]
        # high-priority task still got rescheduled (reality of "now" wins the slot)
        status = rt.conn.execute("SELECT status FROM events WHERE id=?", (high,)).fetchone()["status"]
        assert status == "scheduled"
    finally:
        rt.close()


def test_do_now_preserves_no_active_overlap_invariant(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.living("init_resources")
        # Two adjacent tasks both overlapped by one impromptu window.
        for title, s, e, imp in [("任务A", "15:00", "16:00", 30), ("任务B", "16:00", "17:00", 80)]:
            ce = rt.event_tool("create", title=title, event_type="work", event_category="work", importance=imp,
                               planned_start=f"2026-06-07T{s}:00+00:00", planned_end=f"2026-06-07T{e}:00+00:00")
            rt.event_tool("schedule", event_id=_eid(ce), start=f"2026-06-07T{s}:00+00:00", end=f"2026-06-07T{e}:00+00:00")
        rt.event_tool("do_now", title="临时活动", duration_minutes=90, now="2026-06-07T15:10:00+00:00", importance=50)
        # No two ACTIVE blocks may overlap after resolution.
        active = rt.conn.execute(
            "SELECT start_ts, end_ts FROM schedule_blocks WHERE owner_kind='agent' AND owner_id='default-agent' AND status IN ('planned','locked','ready','in_progress') AND start_ts IS NOT NULL ORDER BY start_ts"
        ).fetchall()
        for i in range(1, len(active)):
            assert active[i]["start_ts"] >= active[i - 1]["end_ts"], "active schedule blocks overlap"
        assert reconcile_resources(rt.conn, "agent", "default-agent", record=False)["ok"]
        assert rt.doctor()["status"] != "failed"
    finally:
        rt.close()
