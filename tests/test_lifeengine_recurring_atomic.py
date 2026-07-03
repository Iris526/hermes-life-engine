"""Regression: recurring materialization must be atomic with its idempotency guard.

due_activities skips an activity once it has a venture_occurrences row
for the day. If the event were created but record_occurrence then failed, the
event would persist un-guarded and the next tick would re-materialize a duplicate
(polluting the schedule and, via completion, the ledger). The event + block +
occurrence are wrapped in one savepoint, so a failing record_occurrence rolls the
event back too — no orphan, no duplicate.
"""
from __future__ import annotations

import os
import shutil

from lifeengine import venture
from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.runtime import LifeEngineRuntime

OWNER_ID = DEFAULT_AGENT_ID


def _fresh_home(tmp_path):
    home = tmp_path / "hermes_home_recurring_atomic"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _register_daily(rt):
    reg = rt.activity("register", title="净符摊", cadence_kind="daily",
                      start_time="10:00", end_time="14:00", timezone="UTC")
    return reg["receipt"]["facts"][0]["evidence"]["activity_id"]


def _event_count(rt, aid):
    return rt.conn.execute(
        "SELECT COUNT(*) FROM events WHERE json_extract(attributes_json,'$.recurring_activity_id')=?",
        (aid,),
    ).fetchone()[0]


def _occ_count(rt, aid):
    return rt.conn.execute(
        "SELECT COUNT(*) FROM venture_occurrences WHERE activity_id=?",
        (aid,),
    ).fetchone()[0]


def test_failed_record_occurrence_rolls_back_the_event(tmp_path, monkeypatch):
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        rt.setup("recurring atomic agent")
        rt.commit_canon()
        rt.control("resume")
        rt.living("init_resources")
        aid = _register_daily(rt)

        # Force the occurrence guard write to fail on this tick.
        def _boom(*a, **k):
            raise RuntimeError("occurrence write failed")

        monkeypatch.setattr(venture, "record_occurrence", _boom)
        rt.tick(now="2026-06-15T09:00:00+00:00", manual=False)

        # The event must NOT have leaked past the failed guard.
        assert _event_count(rt, aid) == 0, "event must roll back when its occurrence guard fails"
        assert _occ_count(rt, aid) == 0

        # Next tick with the guard restored materializes exactly one — no duplicate.
        monkeypatch.undo()
        rt.tick(now="2026-06-15T09:30:00+00:00", manual=False)
        assert _event_count(rt, aid) == 1
        assert _occ_count(rt, aid) == 1

        # And a later same-day tick does not double up (idempotent).
        rt.tick(now="2026-06-15T10:00:00+00:00", manual=False)
        assert _event_count(rt, aid) == 1
        assert _occ_count(rt, aid) == 1
    finally:
        rt.close()
