"""Concurrency and stress smoke tests for LifeEngine v0.9.5.

These helpers are maintenance diagnostics.  They deliberately operate on
synthetic owner IDs so a real Agent/User workspace is not polluted by smoke
runs.  The goal is to exercise SQLite write serialization, LifeOps receipts,
wake-job idempotency, and larger transaction/index paths before v1.0.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .canon import append_setup_statement, commit_draft, ensure_control
from .jsonutil import dumps
from .trace import append_audit, new_id


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _synthetic_owner(owner_id: str, prefix: str, run_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(owner_id or "owner"))
    return f"{safe}__{prefix}_{run_id[:10]}"


def _prepare_agent_owner(conn, owner_id: str) -> None:
    """Create an active synthetic agent owner using CanonDraft, not LifeOps."""
    control = ensure_control(conn, "agent", owner_id)
    if control.get("engine_state") == "active" and control.get("active_canon_version"):
        return
    append_setup_statement(conn, "agent", owner_id, f"Synthetic v0.9.5 smoke owner {owner_id}.")
    commit_draft(conn, "agent", owner_id, activate=True)


def _record_concurrency_run(conn, owner_kind: str, owner_id: str, *, test_type: str, status: str,
                            worker_count: int, success_count: int, failure_count: int,
                            duration_ms: int, output: dict[str, Any]) -> dict[str, Any]:
    run_id = output.get("run_id") or new_id("conc")
    conn.execute(
        """INSERT INTO concurrency_test_runs(id, owner_kind, owner_id, test_type, status,
              worker_count, success_count, failure_count, duration_ms, output_json)
              VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (run_id, owner_kind, owner_id, test_type, status, int(worker_count), int(success_count), int(failure_count), int(duration_ms), dumps(output)),
    )
    output["concurrency_run_id"] = run_id
    append_audit(conn, owner_kind, owner_id, "life_concurrency_smoke", "info" if status == "ok" else "warning", f"{test_type} status={status}", output)
    return output


def _record_stress_run(conn, owner_kind: str, owner_id: str, *, test_type: str, status: str,
                       item_count: int, duration_ms: int, output: dict[str, Any]) -> dict[str, Any]:
    run_id = output.get("run_id") or new_id("stress")
    conn.execute(
        """INSERT INTO stress_test_runs(id, owner_kind, owner_id, test_type, status, item_count, duration_ms, output_json)
              VALUES(?,?,?,?,?,?,?,?)""",
        (run_id, owner_kind, owner_id, test_type, status, int(item_count), int(duration_ms), dumps(output)),
    )
    output["stress_run_id"] = run_id
    append_audit(conn, owner_kind, owner_id, "life_stress_smoke", "info" if status == "ok" else "warning", f"{test_type} status={status}", output)
    return output


def run_parallel_commit_smoke(conn, owner_kind: str, owner_id: str, *, workers: int = 8) -> dict[str, Any]:
    """Spawn independent runtime connections that commit synthetic events."""
    run_id = new_id("conc")
    test_owner = _synthetic_owner(owner_id, "parallel_commit", run_id)
    _prepare_agent_owner(conn, test_owner)
    start = time.perf_counter()
    lock = threading.Lock()
    successes: list[dict[str, Any]] = []
    failures: list[str] = []

    def worker(i: int) -> None:
        from .runtime import LifeEngineRuntime
        rt = LifeEngineRuntime()
        try:
            out = rt.commit_ops(
                [{"type": "CREATE_EVENT", "payload": {"title": f"v0.9.5 parallel commit event {i}", "event_type": "smoke", "source": "manual_entry"}}],
                "agent",
                test_owner,
                source="concurrency_smoke",
                session_id=f"conc-session-{run_id}",
                turn_id=f"turn-{i}",
            )
            with lock:
                successes.append({"worker": i, "transaction_id": out.get("transaction_id"), "receipt_id": (out.get("receipt") or {}).get("receipt_id")})
        except Exception as exc:  # pragma: no cover - exercised by failure cases
            with lock:
                failures.append(f"worker {i}: {type(exc).__name__}: {exc}")
        finally:
            rt.close()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(int(workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = _ms(start)
    event_count = conn.execute("SELECT COUNT(*) FROM events WHERE owner_kind='agent' AND owner_id=? AND title LIKE 'v0.9.5 parallel commit event %'", (test_owner,)).fetchone()[0]
    tx_count = conn.execute("SELECT COUNT(*) FROM life_transactions WHERE owner_kind='agent' AND owner_id=? AND source='concurrency_smoke'", (test_owner,)).fetchone()[0]
    status = "ok" if len(successes) == int(workers) and not failures and event_count == int(workers) and tx_count == int(workers) else "warning"
    out = {"ok": status == "ok", "run_id": run_id, "test_owner_id": test_owner, "test_type": "parallel_commit", "workers": int(workers), "successes": successes, "failures": failures[:10], "event_count": event_count, "transaction_count": tx_count, "duration_ms": duration}
    return _record_concurrency_run(conn, owner_kind, owner_id, test_type="parallel_commit", status=status, worker_count=int(workers), success_count=len(successes), failure_count=len(failures), duration_ms=duration, output=out)


def run_parallel_schedule_overlap_smoke(conn, owner_kind: str, owner_id: str, *, workers: int = 6) -> dict[str, Any]:
    """Verify concurrent overlapping schedule writes serialize to exactly one success."""
    run_id = new_id("conc")
    test_owner = _synthetic_owner(owner_id, "schedule_overlap", run_id)
    _prepare_agent_owner(conn, test_owner)
    from .runtime import LifeEngineRuntime
    rt0 = LifeEngineRuntime()
    try:
        event_commit = rt0.commit_ops(
            [{"type": "CREATE_EVENT", "payload": {"title": "v0.9.5 overlap target", "event_type": "smoke", "source": "manual_entry"}}],
            "agent", test_owner, source="concurrency_smoke",
        )
        event_id = event_commit["results"][0]["result"]["id"]
    finally:
        rt0.close()
    now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    start_iso = now.isoformat()
    end_iso = (now + timedelta(hours=1)).isoformat()
    start = time.perf_counter()
    lock = threading.Lock()
    successes: list[dict[str, Any]] = []
    failures: list[str] = []

    def worker(i: int) -> None:
        from .runtime import LifeEngineRuntime
        rt = LifeEngineRuntime()
        try:
            out = rt.commit_ops(
                [{"type": "CREATE_SCHEDULE_BLOCK", "payload": {"event_id": event_id, "start": start_iso, "end": end_iso, "block_type": "planned_event", "timezone_name": "UTC"}}],
                "agent", test_owner, source="concurrency_smoke", session_id=f"overlap-{run_id}", turn_id=f"turn-{i}",
            )
            with lock:
                successes.append({"worker": i, "transaction_id": out.get("transaction_id")})
        except Exception as exc:
            with lock:
                failures.append(f"worker {i}: {type(exc).__name__}: {exc}")
        finally:
            rt.close()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(int(workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = _ms(start)
    active_blocks = conn.execute("SELECT COUNT(*) FROM schedule_blocks WHERE owner_kind='agent' AND owner_id=? AND event_id=? AND status IN ('planned','locked','ready','in_progress')", (test_owner, event_id)).fetchone()[0]
    status = "ok" if len(successes) == 1 and active_blocks == 1 and len(failures) == int(workers) - 1 else "warning"
    out = {"ok": status == "ok", "run_id": run_id, "test_owner_id": test_owner, "event_id": event_id, "test_type": "parallel_schedule_overlap", "workers": int(workers), "successes": successes, "failures": failures[:10], "active_schedule_blocks": active_blocks, "duration_ms": duration}
    return _record_concurrency_run(conn, owner_kind, owner_id, test_type="parallel_schedule_overlap", status=status, worker_count=int(workers), success_count=len(successes), failure_count=len(failures), duration_ms=duration, output=out)


def run_parallel_heartbeat_idempotency_smoke(conn, owner_kind: str, owner_id: str, *, workers: int = 6) -> dict[str, Any]:
    """Verify concurrent heartbeats do not double-execute one due wake job."""
    run_id = new_id("conc")
    test_owner = _synthetic_owner(owner_id, "heartbeat", run_id)
    _prepare_agent_owner(conn, test_owner)
    from .runtime import LifeEngineRuntime
    rt0 = LifeEngineRuntime()
    try:
        base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
        event_commit = rt0.commit_ops(
            [{"type": "CREATE_EVENT", "payload": {"title": "v0.9.5 heartbeat idempotent event", "event_type": "smoke", "source": "manual_entry", "importance": 45}}],
            "agent", test_owner, source="concurrency_smoke",
        )
        event_id = event_commit["results"][0]["result"]["id"]
        rt0.commit_ops(
            [{"type": "CREATE_SCHEDULE_BLOCK", "payload": {"event_id": event_id, "start": base.isoformat(), "end": (base + timedelta(minutes=5)).isoformat(), "timezone_name": "UTC"}}],
            "agent", test_owner, source="concurrency_smoke",
        )
    finally:
        rt0.close()
    start = time.perf_counter()
    lock = threading.Lock()
    successes: list[dict[str, Any]] = []
    failures: list[str] = []
    tick_now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def worker(i: int) -> None:
        from .runtime import LifeEngineRuntime
        rt = LifeEngineRuntime()
        try:
            out = rt.tick("agent", test_owner, now=tick_now, manual=True)
            with lock:
                successes.append({"worker": i, "status": out.get("status"), "completed": len(out.get("completed") or [])})
        except Exception as exc:
            with lock:
                failures.append(f"worker {i}: {type(exc).__name__}: {exc}")
        finally:
            rt.close()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(int(workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = _ms(start)
    event = conn.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()
    action_count = conn.execute("SELECT COUNT(*) FROM actions WHERE owner_kind='agent' AND owner_id=? AND event_id=?", (test_owner, event_id)).fetchone()[0]
    result_count = conn.execute("SELECT COUNT(*) FROM results WHERE owner_kind='agent' AND owner_id=? AND event_id=?", (test_owner, event_id)).fetchone()[0]
    done_jobs = conn.execute("SELECT COUNT(*) FROM wake_jobs WHERE owner_kind='agent' AND owner_id=? AND target_id IN (SELECT id FROM schedule_blocks WHERE event_id=?) AND status='done'", (test_owner, event_id)).fetchone()[0]
    status = "ok" if not failures and event and event["status"] == "completed" and action_count == 1 and result_count == 1 and done_jobs == 1 else "warning"
    out = {"ok": status == "ok", "run_id": run_id, "test_owner_id": test_owner, "event_id": event_id, "test_type": "parallel_heartbeat_idempotency", "workers": int(workers), "successes": successes, "failures": failures[:10], "event_status": event["status"] if event else None, "action_count": action_count, "result_count": result_count, "done_wake_jobs": done_jobs, "duration_ms": duration}
    return _record_concurrency_run(conn, owner_kind, owner_id, test_type="parallel_heartbeat_idempotency", status=status, worker_count=int(workers), success_count=len(successes), failure_count=len(failures), duration_ms=duration, output=out)


def run_lifeops_stress_smoke(conn, owner_kind: str, owner_id: str, *, items: int = 200) -> dict[str, Any]:
    """Commit a larger synthetic LifeOps batch and verify receipts/journal rows."""
    run_id = new_id("stress")
    test_owner = _synthetic_owner(owner_id, "lifeops", run_id)
    _prepare_agent_owner(conn, test_owner)
    from .runtime import LifeEngineRuntime
    start = time.perf_counter()
    rt = LifeEngineRuntime()
    try:
        ops = [
            {"type": "CREATE_EVENT", "payload": {"title": f"v0.9.5 stress event {i}", "event_type": "smoke", "source": "manual_entry", "importance": i % 100}}
            for i in range(int(items))
        ]
        out_commit = rt.commit_ops(ops, "agent", test_owner, source="stress_smoke", session_id=f"stress-{run_id}", turn_id="bulk")
    finally:
        rt.close()
    duration = _ms(start)
    event_count = conn.execute("SELECT COUNT(*) FROM events WHERE owner_kind='agent' AND owner_id=? AND title LIKE 'v0.9.5 stress event %'", (test_owner,)).fetchone()[0]
    fact_count = conn.execute("SELECT COUNT(*) FROM commit_receipt_facts WHERE transaction_id=?", (out_commit.get("transaction_id"),)).fetchone()[0]
    journal_count = conn.execute("SELECT COUNT(*) FROM life_journal WHERE transaction_id=?", (out_commit.get("transaction_id"),)).fetchone()[0]
    status = "ok" if event_count == int(items) and fact_count >= int(items) and journal_count >= int(items) else "warning"
    out = {"ok": status == "ok", "run_id": run_id, "test_owner_id": test_owner, "test_type": "lifeops_stress", "items": int(items), "transaction_id": out_commit.get("transaction_id"), "receipt_id": (out_commit.get("receipt") or {}).get("receipt_id"), "event_count": event_count, "receipt_fact_count": fact_count, "journal_count": journal_count, "duration_ms": duration}
    return _record_stress_run(conn, owner_kind, owner_id, test_type="lifeops_stress", status=status, item_count=int(items), duration_ms=duration, output=out)


def list_concurrency_runs(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM concurrency_test_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            import json
            d["output"] = json.loads(d.pop("output_json") or "{}")
        except Exception:
            d["output"] = {}
        out.append(d)
    return {"ok": True, "concurrency_runs": out}


def list_stress_runs(conn, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM stress_test_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            import json
            d["output"] = json.loads(d.pop("output_json") or "{}")
        except Exception:
            d["output"] = {}
        out.append(d)
    return {"ok": True, "stress_runs": out}
