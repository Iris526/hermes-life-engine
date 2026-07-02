"""Regression: a failed wake_job must retry with backoff, not sleep forever.

Before this, ``finish_wake_job(status='failed')`` was terminal. ``due_wake_jobs``
only selects ``pending`` and the reaper only recovers ``running``, so a single
transient error on a ``sleep_plan_wake`` left the agent asleep until a manual
``/life call``. These tests pin the retry/backoff/dead-letter contract and the
end-to-end recovery through a real tick.
"""
from __future__ import annotations

import os
import shutil

from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.events import (
    WAKE_MAX_ATTEMPTS,
    due_wake_jobs,
    retry_or_fail_wake_job,
)
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.time_utils import to_epoch

OWNER_KIND = "agent"
OWNER_ID = DEFAULT_AGENT_ID


def _fresh_home(tmp_path):
    home = tmp_path / "hermes_home_wake_retry"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def _insert_wake_job(rt, *, job_id, wake_at, reason="sleep_plan_wake", target_id="plan-1", status="pending"):
    rt.conn.execute(
        """INSERT INTO wake_jobs(id, owner_kind, owner_id, wake_at, wake_at_ts, reason, target_id, status, idempotency_key)
              VALUES(?,?,?,?,?,?,?,?,?)""",
        (job_id, OWNER_KIND, OWNER_ID, wake_at, to_epoch(wake_at), reason, target_id, status, f"idem-{job_id}"),
    )
    rt.conn.commit()


def _status_of(rt, job_id):
    return rt.conn.execute("SELECT status, attempt_count, next_retry_ts FROM wake_jobs WHERE id=?", (job_id,)).fetchone()


def test_failed_wake_job_is_requeued_with_backoff(tmp_path):
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        _insert_wake_job(rt, job_id="w1", wake_at="2026-07-01 08:00:00", status="running")
        out = retry_or_fail_wake_job(rt.conn, OWNER_KIND, OWNER_ID, "w1", "boom", "2026-07-01 08:00:00")
        rt.conn.commit()

        assert out["outcome"] == "retry_scheduled"
        assert out["attempt_count"] == 1
        row = _status_of(rt, "w1")
        # Back to pending (not terminal failed), with a future backoff gate.
        assert row["status"] == "pending"
        assert row["attempt_count"] == 1
        assert row["next_retry_ts"] == to_epoch("2026-07-01 08:00:00") + out["backoff_seconds"]
    finally:
        rt.close()


def test_backoff_gate_hides_then_reveals_the_job(tmp_path):
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        _insert_wake_job(rt, job_id="w2", wake_at="2026-07-01 08:00:00", status="running")
        out = retry_or_fail_wake_job(rt.conn, OWNER_KIND, OWNER_ID, "w2", "boom", "2026-07-01 08:00:00")
        rt.conn.commit()
        backoff = out["backoff_seconds"]

        # Just before the backoff elapses: not due.
        just_before = "2026-07-01 08:00:00"
        assert to_epoch(just_before) < to_epoch("2026-07-01 08:00:00") + backoff
        due_ids = [j["id"] for j in due_wake_jobs(rt.conn, OWNER_KIND, OWNER_ID, just_before)]
        assert "w2" not in due_ids

        # After the backoff elapses: due again.
        after = to_epoch("2026-07-01 08:00:00") + backoff + 1
        # Feed an ISO 'now' comfortably past the gate.
        due_ids = [j["id"] for j in due_wake_jobs(rt.conn, OWNER_KIND, OWNER_ID, "2026-07-01 09:00:00")]
        assert after <= to_epoch("2026-07-01 09:00:00")
        assert "w2" in due_ids
    finally:
        rt.close()


def _setup_agent(rt):
    rt.setup("wake retry test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")


def test_tick_requeues_failed_sleep_wake_instead_of_sleeping_forever(tmp_path):
    """The critical §4-2 scenario, end to end.

    A ``sleep_plan_wake`` whose plan is missing raises inside the tick. It must
    come back as ``pending`` with a retry gate, never a terminal ``failed`` that
    ``due_wake_jobs`` would ignore forever (= the agent never wakes).
    """
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        _setup_agent(rt)
        _insert_wake_job(rt, job_id="wbad", wake_at="2026-07-01 08:00:00",
                         reason="sleep_plan_wake", target_id="nonexistent-plan")
        out = rt.tick(now="2026-07-01 09:00:00")

        processed = out.get("wake_jobs") or []
        assert any(p.get("wake_job_id") == "wbad" and p.get("outcome") == "retry_scheduled" for p in processed)
        row = _status_of(rt, "wbad")
        assert row["status"] == "pending", "a transient sleep-wake failure must retry, not become terminal"
        assert row["attempt_count"] == 1
        assert row["next_retry_ts"] is not None
    finally:
        rt.close()


def test_dead_letter_after_max_attempts(tmp_path):
    _fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        _insert_wake_job(rt, job_id="w3", wake_at="2026-07-01 08:00:00", status="running")
        last = None
        for _ in range(WAKE_MAX_ATTEMPTS):
            last = retry_or_fail_wake_job(rt.conn, OWNER_KIND, OWNER_ID, "w3", "boom", "2026-07-01 08:00:00")
            rt.conn.commit()
        assert last["outcome"] == "dead_letter"
        row = _status_of(rt, "w3")
        assert row["status"] == "failed"  # terminal, human attention
        assert row["attempt_count"] == WAKE_MAX_ATTEMPTS
        # Dead-lettered job is never due again.
        due_ids = [j["id"] for j in due_wake_jobs(rt.conn, OWNER_KIND, OWNER_ID, "2027-01-01 00:00:00")]
        assert "w3" not in due_ids
    finally:
        rt.close()
