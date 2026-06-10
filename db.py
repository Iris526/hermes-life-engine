"""SQLite + sqlite-vec storage for LifeEngine.

LifeEngine is intentionally local-first and embedded. sqlite-vec is a required
runtime dependency: every connection loads it and the schema uses vec0 virtual
tables for memory recall.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator

from .constants import PLUGIN_VERSION, VECTOR_DIM
from .paths import db_path

_SCHEMA_VERSION = 19


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec into a connection or raise a clear error."""
    try:
        import sqlite_vec  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "LifeEngine requires sqlite-vec. Install it in the Hermes Python environment: pip install sqlite-vec"
        ) from exc
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    _load_sqlite_vec(conn)
    migrate(conn)
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a serializing SQLite transaction.

    BEGIN IMMEDIATE gives us a per-profile write lock. This protects against
    concurrent gateway turns or heartbeat jobs double-executing the same event.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _ensure_schema_migration_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          id TEXT PRIMARY KEY,
          schema_version INTEGER UNIQUE,
          from_version INTEGER,
          to_version INTEGER NOT NULL,
          plugin_version TEXT NOT NULL,
          status TEXT NOT NULL,
          notes TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_schema_migrations_version ON schema_migrations(schema_version);
        """
    )


def _record_schema_migration(conn: sqlite3.Connection, version: int, name: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO schema_migrations(
               id, schema_version, from_version, to_version, plugin_version, status, notes
             ) VALUES(?,?,?,?,?,?,?)""",
        (f"schema_v{version}", version, None, version, PLUGIN_VERSION, "applied", name),
    )


def migrate(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations.

    v0 -> v1 creates the original LifeEngine tables; later versions add
    receipts, truth sources, inventory, goals, autonomy, proactive, execution,
    doctor checks, v0.9.2 install/upgrade diagnostics, v0.9.3 FinalGate repair reports, v0.9.4 export/import/package manifests, and v0.9.5 concurrency/stress hardening telemetry, and v0.9.6 integration acceptance/API freeze metadata, and v0.9.7 acceptance scenario reports, v0.99 trace coverage reports, and v0.10 advisory FinalGate/human-surface metadata.
    """
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    _ensure_schema_migration_table(conn)
    if current >= _SCHEMA_VERSION:
        return
    if current < 1:
        _create_schema_v1(conn)
        _record_schema_migration(conn, 1, "initial_lifeengine_schema")
    if current < 2:
        _create_schema_v2(conn)
        _record_schema_migration(conn, 2, "migration_branch_confirmation_outbox")
    if current < 3:
        _create_schema_v3(conn)
        _record_schema_migration(conn, 3, "commit_receipts_time_wake_resources")
    if current < 4:
        _create_schema_v4(conn)
        _record_schema_migration(conn, 4, "truth_source_cache")
    if current < 5:
        _create_schema_v5(conn)
        _record_schema_migration(conn, 5, "inventory_and_meals")
    if current < 6:
        _create_schema_v6(conn)
        _record_schema_migration(conn, 6, "goals_life_arcs_decomposition")
    if current < 7:
        _create_schema_v7(conn)
        _record_schema_migration(conn, 7, "autonomy_planner")
    if current < 8:
        _create_schema_v8(conn)
        _record_schema_migration(conn, 8, "proactive_outbox")
    if current < 9:
        _create_schema_v9(conn)
        _record_schema_migration(conn, 9, "autonomy_proactive_linking")
    if current < 10:
        _create_schema_v10(conn)
        _record_schema_migration(conn, 10, "narrative_execution_serendipity")
    if current < 11:
        _create_schema_v11(conn)
        _record_schema_migration(conn, 11, "doctor_hardening")
    if current < 12:
        _create_schema_v12(conn)
        _record_schema_migration(conn, 12, "install_upgrade_hardening")
    if current < 13:
        _create_schema_v13(conn)
        _record_schema_migration(conn, 13, "final_gate_repair_reports")
    if current < 14:
        _create_schema_v14(conn)
        _record_schema_migration(conn, 14, "export_import_package_manifests")
    if current < 15:
        _create_schema_v15(conn)
        _record_schema_migration(conn, 15, "concurrency_stress_hardening")
    if current < 16:
        _create_schema_v16(conn)
        _record_schema_migration(conn, 16, "integration_acceptance_api_freeze")
    if current < 17:
        _create_schema_v17(conn)
        _record_schema_migration(conn, 17, "acceptance_scenarios_reports")
    if current < 18:
        _create_schema_v18(conn)
        _record_schema_migration(conn, 18, "trace_coverage_hardening")
    if current < 19:
        _create_schema_v19(conn)
        _record_schema_migration(conn, 19, "v010_advisory_final_gate_human_surface")
    conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")


def _create_schema_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS owners (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL CHECK(kind IN ('agent','user','relationship')),
          display_name TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS controls (
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          engine_state TEXT NOT NULL,
          active_canon_version INTEGER,
          draft_canon_id TEXT,
          module_gates_json TEXT NOT NULL,
          heartbeat_mode TEXT NOT NULL DEFAULT 'manual',
          resume_policy TEXT NOT NULL DEFAULT 'mark_gap_only',
          current_workspace TEXT NOT NULL DEFAULT 'agent_self',
          paused_json TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY(owner_kind, owner_id)
        );

        CREATE TABLE IF NOT EXISTS canon_versions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          version INTEGER NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('active','superseded','archived')),
          data_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          activated_at TEXT,
          UNIQUE(owner_kind, owner_id, version)
        );

        CREATE TABLE IF NOT EXISTS canon_drafts (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          base_version INTEGER,
          status TEXT NOT NULL,
          raw_user_statements_json TEXT NOT NULL,
          extracted_json TEXT NOT NULL,
          unresolved_questions_json TEXT NOT NULL,
          conflicts_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS life_transactions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          source TEXT NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          trace_id TEXT,
          canon_version INTEGER,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          committed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS life_ops (
          id TEXT PRIMARY KEY,
          transaction_id TEXT NOT NULL REFERENCES life_transactions(id) ON DELETE CASCADE,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          op_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS life_journal (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          transaction_id TEXT,
          op_id TEXT,
          entry_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          source TEXT NOT NULL,
          canon_version INTEGER,
          prev_hash TEXT,
          entry_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_life_journal_owner_time ON life_journal(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_life_journal_tx ON life_journal(transaction_id);

        CREATE TABLE IF NOT EXISTS trace_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          trace_type TEXT NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          tick_id TEXT,
          engine_state TEXT,
          canon_version INTEGER,
          started_at TEXT NOT NULL DEFAULT (datetime('now')),
          ended_at TEXT,
          status TEXT NOT NULL,
          input_json TEXT,
          output_json TEXT,
          error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_trace_runs_owner_time ON trace_runs(owner_kind, owner_id, started_at);

        CREATE TABLE IF NOT EXISTS trace_spans (
          id TEXT PRIMARY KEY,
          trace_id TEXT NOT NULL REFERENCES trace_runs(id) ON DELETE CASCADE,
          parent_span_id TEXT,
          name TEXT NOT NULL,
          status TEXT NOT NULL,
          started_at TEXT NOT NULL DEFAULT (datetime('now')),
          ended_at TEXT,
          input_json TEXT,
          output_json TEXT,
          error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_trace_spans_trace ON trace_spans(trace_id, started_at);

        CREATE TABLE IF NOT EXISTS audit_log (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          audit_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          message TEXT NOT NULL,
          payload_json TEXT,
          trace_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT,
          event_type TEXT NOT NULL DEFAULT 'other',
          source TEXT NOT NULL DEFAULT 'life_commit',
          status TEXT NOT NULL,
          parent_event_id TEXT,
          planned_start TEXT,
          planned_end TEXT,
          actual_start TEXT,
          actual_end TEXT,
          priority INTEGER NOT NULL DEFAULT 50,
          importance INTEGER NOT NULL DEFAULT 50,
          progress INTEGER NOT NULL DEFAULT 0,
          resource_costs_json TEXT NOT NULL DEFAULT '{{}}',
          schedule_block_ids_json TEXT NOT NULL DEFAULT '[]',
          postponement_count INTEGER NOT NULL DEFAULT 0,
          revision_count INTEGER NOT NULL DEFAULT 0,
          visibility TEXT NOT NULL DEFAULT 'agent_private',
          confidence REAL NOT NULL DEFAULT 1.0,
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          closed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_owner_status ON events(owner_kind, owner_id, status);
        CREATE INDEX IF NOT EXISTS idx_events_owner_time ON events(owner_kind, owner_id, planned_start, actual_start);

        CREATE TABLE IF NOT EXISTS actions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
          action_type TEXT NOT NULL DEFAULT 'other',
          verb TEXT NOT NULL,
          target TEXT,
          status TEXT NOT NULL,
          scheduled_start TEXT,
          scheduled_end TEXT,
          actual_start TEXT,
          actual_end TEXT,
          duration_minutes INTEGER,
          resource_deltas_json TEXT NOT NULL DEFAULT '{{}}',
          result_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS results (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
          action_id TEXT REFERENCES actions(id) ON DELETE SET NULL,
          result_type TEXT NOT NULL,
          summary TEXT NOT NULL,
          progress_after INTEGER,
          state_changes_json TEXT NOT NULL DEFAULT '[]',
          memory_ids_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS schedule_blocks (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
          action_id TEXT REFERENCES actions(id) ON DELETE SET NULL,
          block_type TEXT NOT NULL DEFAULT 'planned_event',
          start TEXT NOT NULL,
          end TEXT NOT NULL,
          timezone TEXT NOT NULL DEFAULT 'UTC',
          status TEXT NOT NULL,
          lock_strength TEXT NOT NULL DEFAULT 'soft',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_owner_time ON schedule_blocks(owner_kind, owner_id, start, end);

        CREATE TABLE IF NOT EXISTS resource_definitions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          key TEXT NOT NULL,
          display_name TEXT NOT NULL,
          resource_class TEXT NOT NULL,
          unit TEXT,
          min_value REAL,
          max_value REAL,
          is_ledger_backed INTEGER NOT NULL DEFAULT 1,
          is_reservable INTEGER NOT NULL DEFAULT 1,
          rules_json TEXT NOT NULL DEFAULT '{{}}',
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(owner_kind, owner_id, key)
        );

        CREATE TABLE IF NOT EXISTS resource_accounts (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          resource_key TEXT NOT NULL,
          current_value REAL NOT NULL DEFAULT 0,
          unit TEXT,
          capacity REAL,
          state TEXT NOT NULL DEFAULT 'available',
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(owner_kind, owner_id, resource_key)
        );

        CREATE TABLE IF NOT EXISTS resource_ledger (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          resource_key TEXT NOT NULL,
          delta REAL NOT NULL,
          unit TEXT,
          operation TEXT NOT NULL,
          event_id TEXT,
          action_id TEXT,
          result_id TEXT,
          schedule_block_id TEXT,
          reason TEXT NOT NULL,
          source TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_resource_ledger_owner_key ON resource_ledger(owner_kind, owner_id, resource_key, created_at);

        CREATE TABLE IF NOT EXISTS resource_reservations (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          resource_key TEXT NOT NULL,
          amount REAL NOT NULL,
          unit TEXT,
          status TEXT NOT NULL,
          event_id TEXT,
          schedule_block_id TEXT,
          reason TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          released_at TEXT
        );

        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          memory_type TEXT NOT NULL,
          content TEXT NOT NULL,
          event_id TEXT,
          action_id TEXT,
          result_id TEXT,
          importance INTEGER NOT NULL DEFAULT 50,
          emotional_weight INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL,
          confidence REAL NOT NULL DEFAULT 1.0,
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          last_accessed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_memories_owner_time ON memories(owner_kind, owner_id, created_at);

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
          memory_rowid UNINDEXED,
          owner_kind UNINDEXED,
          owner_id UNINDEXED,
          content,
          tokenize = 'unicode61'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(embedding float[{VECTOR_DIM}]);

        CREATE TABLE IF NOT EXISTS thoughts (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          thought_type TEXT NOT NULL,
          content TEXT NOT NULL,
          related_event_id TEXT,
          related_goal_id TEXT,
          may_become_plan INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'private',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS diary_entries (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          diary_type TEXT NOT NULL,
          date TEXT NOT NULL,
          source_event_ids_json TEXT NOT NULL DEFAULT '[]',
          source_result_ids_json TEXT NOT NULL DEFAULT '[]',
          source_resource_ledger_ids_json TEXT NOT NULL DEFAULT '[]',
          canon_version INTEGER,
          content TEXT NOT NULL,
          privacy TEXT NOT NULL DEFAULT 'agent_private',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS proactive_intents (
          id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          target_type TEXT NOT NULL,
          target_id TEXT,
          trigger_event_id TEXT,
          trigger_result_id TEXT,
          intent_type TEXT NOT NULL,
          summary TEXT NOT NULL,
          emotional_tone TEXT,
          importance INTEGER NOT NULL DEFAULT 50,
          urgency INTEGER NOT NULL DEFAULT 50,
          novelty INTEGER NOT NULL DEFAULT 50,
          relationship_relevance INTEGER NOT NULL DEFAULT 50,
          privacy_level TEXT NOT NULL DEFAULT 'safe_to_share',
          status TEXT NOT NULL DEFAULT 'generated',
          delivery_policy_json TEXT NOT NULL DEFAULT '{{}}',
          expires_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS wake_jobs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          wake_at TEXT NOT NULL,
          reason TEXT NOT NULL,
          target_id TEXT,
          status TEXT NOT NULL,
          idempotency_key TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_wake_jobs_due ON wake_jobs(owner_kind, owner_id, status, wake_at);

        CREATE TABLE IF NOT EXISTS turn_commits (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          transaction_id TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(owner_kind, owner_id, session_id, turn_id, transaction_id)
        );
        """
    )


def _create_schema_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS life_branches (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          name TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('active','archived')),
          base_branch_id TEXT,
          created_from_canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          archived_at TEXT
        );

        CREATE TABLE IF NOT EXISTS canon_migrations (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          from_version INTEGER,
          to_version INTEGER NOT NULL,
          migration_type TEXT NOT NULL,
          affected_domains_json TEXT NOT NULL DEFAULT '[]',
          plan_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'planned',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          applied_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_canon_migrations_owner ON canon_migrations(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS user_confirmations (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          proposed_ops_json TEXT NOT NULL,
          reason TEXT,
          session_id TEXT,
          turn_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS proactive_outbox (
          id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          target_user_id TEXT,
          intent_id TEXT,
          draft_text TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'drafted',
          send_after TEXT,
          sent_at TEXT,
          suppression_reason TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS truth_source_reads (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          domain TEXT NOT NULL,
          authority TEXT NOT NULL,
          parameters_json TEXT NOT NULL DEFAULT '{}',
          result_json TEXT NOT NULL DEFAULT '{}',
          trace_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS heartbeat_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          tick_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          status TEXT NOT NULL,
          started_at TEXT NOT NULL DEFAULT (datetime('now')),
          ended_at TEXT,
          output_json TEXT,
          error TEXT
        );

        CREATE TABLE IF NOT EXISTS trace_integrity_checks (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          checked_entries INTEGER NOT NULL DEFAULT 0,
          first_bad_journal_id TEXT,
          message TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )





def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _create_schema_v3(conn: sqlite3.Connection) -> None:
    # Time normalization columns. Existing ISO text remains canonical for display;
    # *_ts columns are used for scheduler comparisons.
    for table, columns in {
        "events": [
            ("planned_start_ts", "planned_start_ts INTEGER"),
            ("planned_end_ts", "planned_end_ts INTEGER"),
            ("actual_start_ts", "actual_start_ts INTEGER"),
            ("actual_end_ts", "actual_end_ts INTEGER"),
            ("branch_id", "branch_id TEXT"),
        ],
        "schedule_blocks": [
            ("start_ts", "start_ts INTEGER"),
            ("end_ts", "end_ts INTEGER"),
            ("idempotency_key", "idempotency_key TEXT"),
            ("completed_at", "completed_at TEXT"),
        ],
        "wake_jobs": [
            ("wake_at_ts", "wake_at_ts INTEGER"),
            ("running_at", "running_at TEXT"),
            ("completed_at", "completed_at TEXT"),
            ("error", "error TEXT"),
            ("claimed_by", "claimed_by TEXT"),
        ],
        "life_transactions": [
            ("receipt_id", "receipt_id TEXT"),
            ("receipt_json", "receipt_json TEXT NOT NULL DEFAULT '{}'"),
            ("validator_report_json", "validator_report_json TEXT NOT NULL DEFAULT '{}'"),
        ],
        "life_ops": [
            ("result_json", "result_json TEXT NOT NULL DEFAULT '{}'"),
            ("evidence_json", "evidence_json TEXT NOT NULL DEFAULT '{}'"),
            ("validator_report_json", "validator_report_json TEXT NOT NULL DEFAULT '{}'"),
        ],
        "turn_commits": [
            ("receipt_id", "receipt_id TEXT"),
        ],
        "controls": [
            ("active_branch_id", "active_branch_id TEXT"),
        ],
    }.items():
        for col, ddl in columns:
            _add_column_if_missing(conn, table, col, ddl)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS commit_receipts (
          id TEXT PRIMARY KEY,
          transaction_id TEXT NOT NULL,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          trace_id TEXT,
          facts_json TEXT NOT NULL DEFAULT '[]',
          summary_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_commit_receipts_turn ON commit_receipts(owner_kind, owner_id, session_id, turn_id);

        CREATE TABLE IF NOT EXISTS commit_receipt_facts (
          id TEXT PRIMARY KEY,
          receipt_id TEXT NOT NULL,
          transaction_id TEXT NOT NULL,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          fact_kind TEXT NOT NULL,
          claim_text TEXT NOT NULL,
          evidence_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_commit_receipt_facts_owner ON commit_receipt_facts(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_commit_receipt_facts_receipt ON commit_receipt_facts(receipt_id);

        CREATE TABLE IF NOT EXISTS schedule_block_executions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          schedule_block_id TEXT NOT NULL,
          wake_job_id TEXT,
          idempotency_key TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL,
          started_at TEXT NOT NULL DEFAULT (datetime('now')),
          committed_at TEXT,
          error TEXT
        );

        CREATE TABLE IF NOT EXISTS resource_reconcile_checks (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          report_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS resource_reconciliations (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          checked_resources INTEGER NOT NULL DEFAULT 0,
          mismatches_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_schedule_owner_ts ON schedule_blocks(owner_kind, owner_id, start_ts, end_ts);
        CREATE INDEX IF NOT EXISTS idx_wake_jobs_due_ts ON wake_jobs(owner_kind, owner_id, status, wake_at_ts);
        """
    )

    # Backfill best-effort timestamps for existing rows using SQLite strftime.
    conn.execute("UPDATE events SET planned_start_ts=CAST(strftime('%s', planned_start) AS INTEGER) WHERE planned_start IS NOT NULL AND planned_start_ts IS NULL")
    conn.execute("UPDATE events SET planned_end_ts=CAST(strftime('%s', planned_end) AS INTEGER) WHERE planned_end IS NOT NULL AND planned_end_ts IS NULL")
    conn.execute("UPDATE events SET actual_start_ts=CAST(strftime('%s', actual_start) AS INTEGER) WHERE actual_start IS NOT NULL AND actual_start_ts IS NULL")
    conn.execute("UPDATE events SET actual_end_ts=CAST(strftime('%s', actual_end) AS INTEGER) WHERE actual_end IS NOT NULL AND actual_end_ts IS NULL")
    conn.execute("UPDATE schedule_blocks SET start_ts=CAST(strftime('%s', start) AS INTEGER) WHERE start IS NOT NULL AND start_ts IS NULL")
    conn.execute("UPDATE schedule_blocks SET end_ts=CAST(strftime('%s', end) AS INTEGER) WHERE end IS NOT NULL AND end_ts IS NULL")
    conn.execute("UPDATE wake_jobs SET wake_at_ts=CAST(strftime('%s', wake_at) AS INTEGER) WHERE wake_at IS NOT NULL AND wake_at_ts IS NULL")


def _create_schema_v4(conn: sqlite3.Connection) -> None:
    # TruthSource execution layer. Canon keeps bindings; these tables keep
    # executable reads, cache entries, and observation freshness metadata.
    for table, columns in {
        "truth_source_reads": [
            ("status", "status TEXT NOT NULL DEFAULT 'observed'"),
            ("source", "source TEXT NOT NULL DEFAULT 'truth_source'"),
            ("expires_at", "expires_at TEXT"),
            ("expires_at_ts", "expires_at_ts INTEGER"),
            ("error", "error TEXT"),
            ("cached_from_read_id", "cached_from_read_id TEXT"),
        ],
    }.items():
        for col, ddl in columns:
            _add_column_if_missing(conn, table, col, ddl)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS truth_source_cache (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          domain TEXT NOT NULL,
          cache_key TEXT NOT NULL,
          authority TEXT NOT NULL,
          parameters_json TEXT NOT NULL DEFAULT '{}',
          result_json TEXT NOT NULL DEFAULT '{}',
          observed_at TEXT NOT NULL DEFAULT (datetime('now')),
          expires_at TEXT,
          expires_at_ts INTEGER,
          source TEXT NOT NULL DEFAULT 'truth_source',
          read_id TEXT,
          UNIQUE(owner_kind, owner_id, domain, cache_key)
        );
        CREATE INDEX IF NOT EXISTS idx_truth_cache_owner_domain ON truth_source_cache(owner_kind, owner_id, domain, observed_at);
        CREATE INDEX IF NOT EXISTS idx_truth_cache_expiry ON truth_source_cache(owner_kind, owner_id, domain, expires_at_ts);
        CREATE INDEX IF NOT EXISTS idx_truth_reads_owner_domain ON truth_source_reads(owner_kind, owner_id, domain, created_at);
        """
    )


def _create_schema_v5(conn: sqlite3.Connection) -> None:
    """User confirmation + entity resource schema.

    v0.5 adds first-class entity resources: inventory items, inventory
    movements, and meal records. It also completes user-life confirmation by
    adding resolution metadata to the existing user_confirmations table.
    """
    for table, columns in {
        "user_confirmations": [
            ("source", "source TEXT NOT NULL DEFAULT 'life_confirmation_tool'"),
            ("resolution_json", "resolution_json TEXT NOT NULL DEFAULT '{}'"),
            ("resolved_by", "resolved_by TEXT"),
            ("result_transaction_id", "result_transaction_id TEXT"),
        ],
        "resource_ledger": [
            ("inventory_item_id", "inventory_item_id TEXT"),
            ("meal_id", "meal_id TEXT"),
        ],
    }.items():
        for col, ddl in columns:
            _add_column_if_missing(conn, table, col, ddl)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inventory_items (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          name TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'other',
          subcategory TEXT,
          quantity REAL NOT NULL DEFAULT 1,
          unit TEXT,
          attributes_json TEXT NOT NULL DEFAULT '{}',
          acquired_at TEXT,
          acquired_by_event_id TEXT,
          acquired_by_transaction_id TEXT,
          condition TEXT NOT NULL DEFAULT 'good',
          location TEXT,
          emotional_value INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'active',
          notes TEXT,
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_owner_category ON inventory_items(owner_kind, owner_id, category, status);
        CREATE INDEX IF NOT EXISTS idx_inventory_owner_time ON inventory_items(owner_kind, owner_id, updated_at);

        CREATE TABLE IF NOT EXISTS inventory_movements (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          operation TEXT NOT NULL,
          quantity_delta REAL NOT NULL DEFAULT 0,
          unit TEXT,
          from_location TEXT,
          to_location TEXT,
          event_id TEXT,
          action_id TEXT,
          result_id TEXT,
          transaction_id TEXT,
          reason TEXT,
          source TEXT NOT NULL DEFAULT 'life_commit',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_movements_item ON inventory_movements(item_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_inventory_movements_owner ON inventory_movements(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS meal_records (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          meal_type TEXT NOT NULL,
          eaten_at TEXT NOT NULL,
          food_items_json TEXT NOT NULL DEFAULT '[]',
          location TEXT,
          cost_json TEXT NOT NULL DEFAULT '{}',
          event_id TEXT,
          satisfaction INTEGER,
          notes TEXT,
          source TEXT NOT NULL DEFAULT 'life_commit',
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_meal_records_owner_time ON meal_records(owner_kind, owner_id, eaten_at);
        CREATE INDEX IF NOT EXISTS idx_meal_records_owner_type ON meal_records(owner_kind, owner_id, meal_type, eaten_at);
        """
    )





def _create_schema_v6(conn: sqlite3.Connection) -> None:
    """Goals / Life Arcs / Event Decomposition schema.

    v0.6 adds long-running autobiographical structure: life arcs, goals,
    goal progress entries, event-goal links, explicit event dependencies,
    decomposition records, and reflection records.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS life_arcs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT,
          arc_type TEXT NOT NULL DEFAULT 'lifestyle',
          status TEXT NOT NULL DEFAULT 'active',
          theme_json TEXT NOT NULL DEFAULT '{}',
          start_date TEXT,
          end_date TEXT,
          current_phase TEXT,
          progress REAL NOT NULL DEFAULT 0,
          priority INTEGER NOT NULL DEFAULT 50,
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_life_arcs_owner_status ON life_arcs(owner_kind, owner_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS goals (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          arc_id TEXT,
          title TEXT NOT NULL,
          description TEXT,
          goal_type TEXT NOT NULL DEFAULT 'lifestyle',
          status TEXT NOT NULL DEFAULT 'active',
          priority INTEGER NOT NULL DEFAULT 50,
          progress REAL NOT NULL DEFAULT 0,
          started_at TEXT NOT NULL DEFAULT (datetime('now')),
          target_date TEXT,
          target_date_ts INTEGER,
          related_event_ids_json TEXT NOT NULL DEFAULT '[]',
          metrics_json TEXT NOT NULL DEFAULT '{}',
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_goals_owner_status ON goals(owner_kind, owner_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_goals_arc ON goals(arc_id, status);

        CREATE TABLE IF NOT EXISTS goal_progress_entries (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          goal_id TEXT NOT NULL,
          delta REAL,
          progress_after REAL NOT NULL,
          reason TEXT,
          event_id TEXT,
          result_id TEXT,
          source TEXT NOT NULL DEFAULT 'life_commit',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_goal_progress_goal ON goal_progress_entries(goal_id, created_at);

        CREATE TABLE IF NOT EXISTS event_goal_links (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          goal_id TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'supports',
          weight REAL NOT NULL DEFAULT 1.0,
          source TEXT NOT NULL DEFAULT 'life_commit',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(owner_kind, owner_id, event_id, goal_id, role)
        );
        CREATE INDEX IF NOT EXISTS idx_event_goal_links_event ON event_goal_links(event_id);
        CREATE INDEX IF NOT EXISTS idx_event_goal_links_goal ON event_goal_links(goal_id);

        CREATE TABLE IF NOT EXISTS event_dependencies (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          depends_on_event_id TEXT NOT NULL,
          dependency_type TEXT NOT NULL DEFAULT 'finish_to_start',
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(owner_kind, owner_id, event_id, depends_on_event_id, dependency_type)
        );
        CREATE INDEX IF NOT EXISTS idx_event_dependencies_event ON event_dependencies(event_id, status);
        CREATE INDEX IF NOT EXISTS idx_event_dependencies_depends ON event_dependencies(depends_on_event_id, status);

        CREATE TABLE IF NOT EXISTS event_decompositions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          parent_event_id TEXT NOT NULL,
          goal_id TEXT,
          decomposition_type TEXT NOT NULL DEFAULT 'manual',
          strategy TEXT NOT NULL DEFAULT 'children',
          child_event_ids_json TEXT NOT NULL DEFAULT '[]',
          dependency_ids_json TEXT NOT NULL DEFAULT '[]',
          weights_json TEXT NOT NULL DEFAULT '{}',
          source TEXT NOT NULL DEFAULT 'life_commit',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_event_decompositions_parent ON event_decompositions(parent_event_id, created_at);

        CREATE TABLE IF NOT EXISTS life_reflections (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          reflection_type TEXT NOT NULL DEFAULT 'event_review',
          target_kind TEXT,
          target_id TEXT,
          content TEXT NOT NULL,
          insights_json TEXT NOT NULL DEFAULT '{}',
          proposed_ops_json TEXT NOT NULL DEFAULT '[]',
          source TEXT NOT NULL DEFAULT 'reflection',
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_life_reflections_target ON life_reflections(owner_kind, owner_id, target_kind, target_id, created_at);
        """
    )



def _create_schema_v7(conn: sqlite3.Connection) -> None:
    """v0.6 polish: milestones, goal contribution idempotency, reflection memory links."""
    for table, columns in {
        "event_goal_links": [
            ("progress_contribution", "progress_contribution REAL NOT NULL DEFAULT 0"),
            ("applied_at", "applied_at TEXT"),
        ],
        "life_reflections": [
            ("memory_id", "memory_id TEXT"),
        ],
        "events": [
            ("goal_id", "goal_id TEXT"),
            ("decomposition_status", "decomposition_status TEXT"),
            ("dependency_ids_json", "dependency_ids_json TEXT NOT NULL DEFAULT '[]'"),
        ],
    }.items():
        for col, ddl in columns:
            _add_column_if_missing(conn, table, col, ddl)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS goal_milestones (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          goal_id TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT,
          target_progress REAL,
          due_at TEXT,
          due_at_ts INTEGER,
          status TEXT NOT NULL DEFAULT 'planned',
          completed_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_goal_milestones_goal ON goal_milestones(owner_kind, owner_id, goal_id, status, due_at_ts);
        """
    )



def _create_schema_v8(conn: sqlite3.Connection) -> None:
    """Autonomy planner schema.

    v0.7 records every autonomous planning choice, including skipped choices,
    proposed LifeOps, committed transaction ids, and planner scores. The
    autonomy decision is trace state; durable life changes still go through
    LifeOps and CommitReceipt.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS autonomy_decisions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          tick_id TEXT,
          trace_id TEXT,
          mode TEXT NOT NULL,
          status TEXT NOT NULL,
          reason TEXT,
          selected_goal_id TEXT,
          selected_event_id TEXT,
          score_json TEXT NOT NULL DEFAULT '{}',
          proposed_ops_json TEXT NOT NULL DEFAULT '[]',
          result_transaction_id TEXT,
          result_receipt_id TEXT,
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          committed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_autonomy_decisions_owner_time ON autonomy_decisions(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_autonomy_decisions_tick ON autonomy_decisions(tick_id, status);
        CREATE INDEX IF NOT EXISTS idx_autonomy_decisions_goal ON autonomy_decisions(owner_kind, owner_id, selected_goal_id, created_at);
        """
    )


def _create_schema_v9(conn: sqlite3.Connection) -> None:
    """Proactive outbox / active chat state machine schema.

    v0.8 upgrades proactive intent from a passive table into a traceable
    state machine: intent -> evaluation -> pending/queued outbox -> sent or
    suppressed/expired, with relationship-level cooldown and daily budgets.
    """
    for table, columns in {
        "proactive_intents": [
            ("expires_at_ts", "expires_at_ts INTEGER"),
            ("generated_by", "generated_by TEXT"),
            ("queued_at", "queued_at TEXT"),
            ("suppressed_at", "suppressed_at TEXT"),
            ("sent_at", "sent_at TEXT"),
            ("expired_at", "expired_at TEXT"),
            ("suppression_reason", "suppression_reason TEXT"),
            ("result_outbox_id", "result_outbox_id TEXT"),
            ("score_json", "score_json TEXT NOT NULL DEFAULT '{}'"),
            ("decision_json", "decision_json TEXT NOT NULL DEFAULT '{}'"),
            ("trace_id", "trace_id TEXT"),
        ],
        "proactive_outbox": [
            ("delivery_channel", "delivery_channel TEXT"),
            ("delivery_result_json", "delivery_result_json TEXT NOT NULL DEFAULT '{}'"),
            ("error", "error TEXT"),
        ],
    }.items():
        for col, ddl in columns:
            _add_column_if_missing(conn, table, col, ddl)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_user_proactive_state (
          id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'silent',
          pending_intent_ids_json TEXT NOT NULL DEFAULT '[]',
          last_proactive_sent_at TEXT,
          next_allowed_proactive_at TEXT,
          user_responsiveness_score INTEGER NOT NULL DEFAULT 50,
          interruption_sensitivity INTEGER NOT NULL DEFAULT 50,
          daily_sent_count INTEGER NOT NULL DEFAULT 0,
          last_daily_reset_date TEXT,
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(agent_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_state_agent ON agent_user_proactive_state(agent_id, updated_at);

        CREATE TABLE IF NOT EXISTS proactive_evaluations (
          id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          target_user_id TEXT,
          intent_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          score REAL NOT NULL DEFAULT 0,
          decision TEXT NOT NULL,
          reason TEXT,
          policy_json TEXT NOT NULL DEFAULT '{}',
          trace_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_eval_intent ON proactive_evaluations(intent_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_proactive_eval_agent ON proactive_evaluations(agent_id, created_at);

        CREATE TABLE IF NOT EXISTS proactive_deliveries (
          id TEXT PRIMARY KEY,
          outbox_id TEXT NOT NULL,
          intent_id TEXT,
          agent_id TEXT NOT NULL,
          target_user_id TEXT,
          status TEXT NOT NULL DEFAULT 'queued',
          delivery_channel TEXT,
          payload_json TEXT NOT NULL DEFAULT '{}',
          result_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_deliveries_outbox ON proactive_deliveries(outbox_id, status);

        CREATE INDEX IF NOT EXISTS idx_proactive_intents_agent_status ON proactive_intents(agent_id, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_proactive_outbox_agent_status ON proactive_outbox(agent_id, status, created_at);
        """
    )
    conn.execute("UPDATE proactive_intents SET expires_at_ts=CAST(strftime('%s', expires_at) AS INTEGER) WHERE expires_at IS NOT NULL AND expires_at_ts IS NULL")


def _create_schema_v10(conn: sqlite3.Connection) -> None:
    """Narrative execution simulator / serendipity schema.

    v0.9 records every heartbeat execution decision separately from the
    resulting LifeOps transaction. Serendipity is stored as a first-class
    traceable entity and also materialized as a completed Life Event.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS execution_decisions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          tick_id TEXT,
          trace_id TEXT,
          wake_job_id TEXT,
          schedule_block_id TEXT,
          event_id TEXT,
          decision_type TEXT NOT NULL,
          status TEXT NOT NULL,
          reason TEXT,
          score_json TEXT NOT NULL DEFAULT '{}',
          proposed_ops_json TEXT NOT NULL DEFAULT '[]',
          result_transaction_id TEXT,
          result_receipt_id TEXT,
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          committed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_execution_decisions_owner_time ON execution_decisions(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_execution_decisions_event ON execution_decisions(owner_kind, owner_id, event_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_execution_decisions_wake ON execution_decisions(wake_job_id, status);

        CREATE TABLE IF NOT EXISTS serendipity_events (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          trigger_event_id TEXT,
          trigger_result_id TEXT,
          serendipity_type TEXT NOT NULL DEFAULT 'minor_discovery',
          title TEXT NOT NULL,
          description TEXT,
          intensity INTEGER NOT NULL DEFAULT 25,
          emotional_impact_json TEXT NOT NULL DEFAULT '{}',
          proposed_ops_json TEXT NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'committed',
          trace_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_serendipity_owner_time ON serendipity_events(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_serendipity_trigger ON serendipity_events(trigger_event_id, created_at);
        """
    )



def _create_schema_v11(conn: sqlite3.Connection) -> None:
    """v0.9.1 hardening schema: invariant/doctor checks."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS life_invariant_checks (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          checks_json TEXT NOT NULL DEFAULT '{}',
          issues_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_life_invariant_checks_owner_time
          ON life_invariant_checks(owner_kind, owner_id, created_at);
        """
    )


def _create_schema_v12(conn: sqlite3.Connection) -> None:
    """v0.9.2 install/upgrade hardening schema.

    This migration deliberately does not change durable life semantics.  It adds
    release/upgrade observability so installs, package upgrades, backup/rebuild
    actions, and heartbeat script checks can be diagnosed from SQLite.
    """
    _ensure_schema_migration_table(conn)
    for column, ddl in [
        ("version", "version INTEGER"),
        ("from_version", "from_version INTEGER"),
        ("to_version", "to_version INTEGER"),
        ("plugin_version", "plugin_version TEXT"),
        ("status", "status TEXT NOT NULL DEFAULT 'applied'"),
        ("notes", "notes TEXT"),
        ("created_at", "created_at TEXT NOT NULL DEFAULT (datetime('now'))"),
    ]:
        _add_column_if_missing(conn, "schema_migrations", column, ddl)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS install_checks (
          id TEXT PRIMARY KEY,
          owner_kind TEXT,
          owner_id TEXT,
          check_type TEXT NOT NULL,
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_install_checks_owner_time
          ON install_checks(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS upgrade_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          db_user_version INTEGER NOT NULL,
          expected_schema_version INTEGER NOT NULL,
          checks_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_upgrade_runs_owner_time
          ON upgrade_runs(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS db_backups (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          backup_path TEXT NOT NULL,
          size_bytes INTEGER NOT NULL DEFAULT 0,
          reason TEXT,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_db_backups_owner_time
          ON db_backups(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS maintenance_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          action TEXT NOT NULL,
          status TEXT NOT NULL,
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_maintenance_runs_owner_time
          ON maintenance_runs(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS cron_heartbeat_tests (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          status TEXT NOT NULL,
          script_path TEXT,
          returncode INTEGER,
          stdout TEXT,
          stderr TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_cron_heartbeat_tests_owner_time
          ON cron_heartbeat_tests(owner_kind, owner_id, created_at);
        """
    )
    _record_schema_migration(conn, 12, "install/upgrade hardening metadata")



def _create_schema_v13(conn: sqlite3.Connection) -> None:
    """v0.9.3 FinalGate UX schema.

    Stores final-answer claim/evidence reports, unsupported claims, suggested
    LifeOps, and repair metadata.  This is diagnostic/repair metadata only; it
    does not create life events or alter durable life facts.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS final_gate_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          trace_id TEXT,
          mode TEXT NOT NULL,
          status TEXT NOT NULL,
          response_preview TEXT,
          claims_json TEXT NOT NULL DEFAULT '[]',
          unsupported_json TEXT NOT NULL DEFAULT '[]',
          supported_json TEXT NOT NULL DEFAULT '[]',
          suggested_ops_json TEXT NOT NULL DEFAULT '[]',
          repair_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_final_gate_reports_owner_time
          ON final_gate_reports(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_final_gate_reports_session_turn
          ON final_gate_reports(session_id, turn_id);
        CREATE INDEX IF NOT EXISTS idx_final_gate_reports_trace
          ON final_gate_reports(trace_id);
        """
    )



def _create_schema_v14(conn: sqlite3.Connection) -> None:
    """v0.9.4 release maintenance schema.

    Adds export/import/staged-restore and package-manifest observability.
    These tables do not create life facts; they make long-lived profile
    maintenance auditable before v1.0.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS profile_exports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          export_path TEXT NOT NULL,
          db_sha256 TEXT NOT NULL,
          manifest_sha256 TEXT NOT NULL,
          size_bytes INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL,
          manifest_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_profile_exports_owner_time
          ON profile_exports(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS profile_imports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          archive_path TEXT NOT NULL,
          staging_dir TEXT,
          db_sha256 TEXT,
          manifest_sha256 TEXT,
          status TEXT NOT NULL,
          manifest_json TEXT NOT NULL DEFAULT '{}',
          notes TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_profile_imports_owner_time
          ON profile_imports(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS restore_staging (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          import_id TEXT,
          staged_db_path TEXT NOT NULL,
          current_db_path TEXT NOT NULL,
          pre_restore_backup_path TEXT,
          status TEXT NOT NULL,
          instructions TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_restore_staging_owner_time
          ON restore_staging(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS package_manifests (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          root_path TEXT NOT NULL,
          file_count INTEGER NOT NULL DEFAULT 0,
          total_bytes INTEGER NOT NULL DEFAULT 0,
          manifest_sha256 TEXT NOT NULL,
          manifest_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_package_manifests_owner_time
          ON package_manifests(owner_kind, owner_id, created_at);
        """
    )


def _create_schema_v15(conn: sqlite3.Connection) -> None:
    """v0.9.5 concurrency/stress hardening schema.

    These tables record synthetic maintenance tests for lock contention,
    multi-session writes, heartbeat idempotency, and larger local DB smoke
    runs. They do not create agent life facts for the real owner unless the
    caller explicitly chooses the real owner for a smoke run.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS concurrency_test_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          test_type TEXT NOT NULL,
          status TEXT NOT NULL,
          worker_count INTEGER NOT NULL DEFAULT 0,
          success_count INTEGER NOT NULL DEFAULT 0,
          failure_count INTEGER NOT NULL DEFAULT 0,
          duration_ms INTEGER NOT NULL DEFAULT 0,
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_concurrency_test_runs_owner_time
          ON concurrency_test_runs(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS stress_test_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          test_type TEXT NOT NULL,
          status TEXT NOT NULL,
          item_count INTEGER NOT NULL DEFAULT 0,
          duration_ms INTEGER NOT NULL DEFAULT 0,
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_stress_test_runs_owner_time
          ON stress_test_runs(owner_kind, owner_id, created_at);
        """
    )

def _create_schema_v16(conn: sqlite3.Connection) -> None:
    """v0.9.6 Hermes integration acceptance and API-freeze schema.

    These tables are release-hardening metadata only. They record adapter
    surface checks, API-freeze snapshots, and optional Hermes core patch drafts;
    they do not create Agent/User life facts.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS integration_test_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          test_type TEXT NOT NULL,
          status TEXT NOT NULL,
          checks_json TEXT NOT NULL DEFAULT '[]',
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_integration_test_runs_owner_time
          ON integration_test_runs(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS api_freeze_snapshots (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'freeze_candidate',
          snapshot_sha256 TEXT NOT NULL,
          snapshot_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_api_freeze_snapshots_owner_time
          ON api_freeze_snapshots(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS core_patch_drafts (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          patch_type TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          patch_path TEXT NOT NULL,
          patch_sha256 TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft',
          notes TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_core_patch_drafts_owner_time
          ON core_patch_drafts(owner_kind, owner_id, created_at);
        """
    )

def _create_schema_v17(conn: sqlite3.Connection) -> None:
    """v0.9.7 v1.0-rc acceptance scenario/report schema.

    These tables record synthetic end-to-end acceptance scenarios and generated
    acceptance reports. They are release-hardening metadata only; scenarios use
    synthetic owners and do not mutate the real owner life facts.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS acceptance_scenario_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          acceptance_run_id TEXT NOT NULL,
          scenario_key TEXT NOT NULL,
          scenario_title TEXT NOT NULL,
          status TEXT NOT NULL,
          duration_ms INTEGER NOT NULL DEFAULT 0,
          checks_json TEXT NOT NULL DEFAULT '[]',
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_acceptance_scenario_runs_owner_time
          ON acceptance_scenario_runs(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_acceptance_scenario_runs_run
          ON acceptance_scenario_runs(acceptance_run_id, scenario_key);

        CREATE TABLE IF NOT EXISTS acceptance_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          acceptance_run_id TEXT NOT NULL,
          status TEXT NOT NULL,
          summary_json TEXT NOT NULL DEFAULT '{}',
          report_markdown TEXT NOT NULL DEFAULT '',
          report_path TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_acceptance_reports_owner_time
          ON acceptance_reports(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_acceptance_reports_run
          ON acceptance_reports(acceptance_run_id);

        CREATE TABLE IF NOT EXISTS v1_rc_checklists (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          acceptance_report_id TEXT,
          status TEXT NOT NULL,
          checklist_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_v1_rc_checklists_owner_time
          ON v1_rc_checklists(owner_kind, owner_id, created_at);
        """
    )



def _create_schema_v18(conn: sqlite3.Connection) -> None:
    """v0.99 trace coverage hardening schema."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trace_coverage_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          checked_transactions INTEGER NOT NULL DEFAULT 0,
          checked_ops INTEGER NOT NULL DEFAULT 0,
          checked_receipts INTEGER NOT NULL DEFAULT 0,
          issues_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_trace_coverage_reports_owner_time
          ON trace_coverage_reports(owner_kind, owner_id, created_at);
        """
    )



def _create_schema_v19(conn: sqlite3.Connection) -> None:
    """v0.10.0 advisory FinalGate + human command surface metadata.

    The feedback queue is internal-to-agent context. It lets FinalGate warn the
    next model turn without replacing the user's visible response.  These tables
    are metadata only; they do not create life facts.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS final_gate_feedback_queue (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          report_id TEXT,
          source TEXT NOT NULL DEFAULT 'final_gate',
          status TEXT NOT NULL DEFAULT 'pending',
          message TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          delivered_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_final_gate_feedback_owner_status
          ON final_gate_feedback_queue(owner_kind, owner_id, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_final_gate_feedback_session_turn
          ON final_gate_feedback_queue(session_id, turn_id);

        CREATE TABLE IF NOT EXISTS failed_lifeops_audits (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          source TEXT,
          trace_id TEXT,
          error TEXT NOT NULL,
          ops_json TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_failed_lifeops_audits_owner_time
          ON failed_lifeops_audits(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS command_surface_profiles (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          profile_name TEXT NOT NULL,
          commands_json TEXT NOT NULL,
          notes TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_command_surface_profiles_owner_time
          ON command_surface_profiles(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS v010_release_notes (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          notes_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_v010_release_notes_owner_time
          ON v010_release_notes(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS release_readiness_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          summary_json TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_release_readiness_reports_owner_time
          ON release_readiness_reports(owner_kind, owner_id, created_at);
        """
    )
