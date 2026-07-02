"""Regression: resource-settlement watermark must count partial ticks.

A partial tick still runs ``_settle_resources`` inside its committed
transaction, so the next tick must treat it as the settlement watermark.
Counting only ``done`` ticks made the next tick re-settle the window a partial
tick already accounted for, double-charging energy/mood/fatigue.
"""
from __future__ import annotations

import os
import shutil

from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.runtime import LifeEngineRuntime

OWNER_KIND = "agent"
OWNER_ID = DEFAULT_AGENT_ID


def _fresh_home(tmp_path):
    home = tmp_path / "hermes_home_watermark"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _insert_heartbeat_run(rt, *, status, logical_now, started_at):
    rt.conn.execute(
        "INSERT INTO heartbeat_runs(id, owner_kind, owner_id, tick_id, mode, status, output_json, started_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            f"hb-{status}-{started_at}",
            OWNER_KIND,
            OWNER_ID,
            f"tick-{status}-{started_at}",
            "manual",
            status,
            '{"now": "%s"}' % logical_now,
            started_at,
        ),
    )


def test_partial_tick_is_a_settlement_watermark(tmp_path):
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        # A done tick at 08:00, then a partial tick at 09:00 (which already
        # settled its own window inside a committed transaction).
        _insert_heartbeat_run(rt, status="done", logical_now="2026-07-01 08:00:00", started_at="2026-07-01 08:00:00")
        _insert_heartbeat_run(rt, status="partial", logical_now="2026-07-01 09:00:00", started_at="2026-07-01 09:00:00")
        rt.conn.commit()

        minutes = rt._minutes_since_last_tick(OWNER_KIND, OWNER_ID, "2026-07-01 10:00:00")
        # Must count from the 09:00 partial tick (60 min), not the 08:00 done
        # tick (120 min) — otherwise the 08:00-09:00 window is settled twice.
        assert minutes == 60.0, f"expected 60 minutes from partial watermark, got {minutes}"
    finally:
        rt.close()


def test_noop_tick_is_not_a_settlement_watermark(tmp_path):
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        # noop ticks return before settling, so a later noop must NOT become the
        # watermark and swallow the un-settled window before it.
        _insert_heartbeat_run(rt, status="partial", logical_now="2026-07-01 09:00:00", started_at="2026-07-01 09:00:00")
        _insert_heartbeat_run(rt, status="noop", logical_now="2026-07-01 09:30:00", started_at="2026-07-01 09:30:00")
        rt.conn.commit()

        minutes = rt._minutes_since_last_tick(OWNER_KIND, OWNER_ID, "2026-07-01 10:00:00")
        assert minutes == 60.0, f"expected 60 minutes from partial (noop ignored), got {minutes}"
    finally:
        rt.close()


def test_failed_tick_is_not_a_settlement_watermark(tmp_path):
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        # failed ticks roll their settlement back, so they must not anchor the
        # watermark either.
        _insert_heartbeat_run(rt, status="done", logical_now="2026-07-01 08:00:00", started_at="2026-07-01 08:00:00")
        _insert_heartbeat_run(rt, status="failed", logical_now="2026-07-01 09:00:00", started_at="2026-07-01 09:00:00")
        rt.conn.commit()

        minutes = rt._minutes_since_last_tick(OWNER_KIND, OWNER_ID, "2026-07-01 10:00:00")
        assert minutes == 120.0, f"expected 120 minutes from done (failed ignored), got {minutes}"
    finally:
        rt.close()
