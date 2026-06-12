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

_SCHEMA_VERSION = 41


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
    doctor checks, v0.9.2 install/upgrade diagnostics, v0.9.3 FinalGate repair reports, v0.9.4 export/import/package manifests, v0.9.5 human UX / FinalGate feedback queue, v0.9.7 acceptance surfaces, v0.99 trace coverage, v0.10.0 advisory-gate consolidation, and v0.11.0 Event V2 state-transition/realtime-state tables, v0.11.1 sleep plans/sessions, and v0.11.2 ReplyGate/delayed replies/call override, v0.11.3 DreamRun/DreamAudit/DreamEntry, and v0.11.4 Sleep/Reply/Dream acceptance plus DreamAudit repair runs, and v0.11.5 sleep debt/day-state effects, delayed reply digest, and DreamAudit repair policy, and v0.11.6 Autonomy sleep-day-state integration, and v0.11.7 Execution Simulator sleep-day-state integration, and v0.11.8 Sleep/Autonomy/Execution end-to-end acceptance, and v0.11.9 Sleep/Reply/Dream real-conversation acceptance, and v0.11.10 Sleep/Reply/Dream policy UX configuration, and v0.11.11 policy acceptance/conflict/import/export, and v0.11.12 human review UX aggregation, and v0.11.13 review action application, and v0.11.14 review action policy and batch apply, and v0.11.15 review undo/rollback trace, and v0.11.16 agent-managed review loop, and v0.11.17 agent-managed review acceptance and stress hardening, and v0.11.18 managed review observability and release readiness, and v0.11.19 human-readable schedule/review/settings surface, and v0.12.6 editable collections/closet cabinets.
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
        _record_schema_migration(conn, 15, "final_gate_advisory_human_surface")
    if current < 16:
        _create_schema_v16(conn)
        _record_schema_migration(conn, 16, "concurrency_and_integration_smokes")
    if current < 17:
        _create_schema_v17(conn)
        _record_schema_migration(conn, 17, "acceptance_runner_and_v1_rc_checklist")
    if current < 18:
        _create_schema_v18(conn)
        _record_schema_migration(conn, 18, "trace_coverage_and_failed_lifeops_audit")
    if current < 19:
        _create_schema_v19(conn)
        _record_schema_migration(conn, 19, "v0_10_0_advisory_gate_and_release_consolidation")
    if current < 20:
        _create_schema_v20(conn)
        _record_schema_migration(conn, 20, "event_v2_transitions_and_realtime_state")
    if current < 21:
        _create_schema_v21(conn)
        _record_schema_migration(conn, 21, "sleep_plans_sessions_and_sleep_resources")
    if current < 22:
        _create_schema_v22(conn)
        _record_schema_migration(conn, 22, "reply_gate_delayed_replies_and_call_override")
    if current < 23:
        _create_schema_v23(conn)
        _record_schema_migration(conn, 23, "dream_run_audit_entry_and_share")
    if current < 24:
        _create_schema_v24(conn)
        _record_schema_migration(conn, 24, "sleep_reply_dream_acceptance_and_dream_repair")
    if current < 25:
        _create_schema_v25(conn)
        _record_schema_migration(conn, 25, "sleep_day_effects_delayed_digest_and_dream_repair_policy")
    if current < 26:
        _create_schema_v26(conn)
        _record_schema_migration(conn, 26, "autonomy_reads_sleep_day_state")
    if current < 27:
        _create_schema_v27(conn)
        _record_schema_migration(conn, 27, "execution_reads_sleep_day_state")
    if current < 28:
        _create_schema_v28(conn)
        _record_schema_migration(conn, 28, "sleep_autonomy_execution_acceptance")
    if current < 29:
        _create_schema_v29(conn)
        _record_schema_migration(conn, 29, "sleep_reply_dream_conversation_acceptance")
    if current < 30:
        _create_schema_v30(conn)
        _record_schema_migration(conn, 30, "sleep_reply_dream_policy_ux")
    if current < 31:
        _create_schema_v31(conn)
        _record_schema_migration(conn, 31, "sleep_reply_dream_policy_acceptance_conflicts_import_export")
    if current < 32:
        _create_schema_v32(conn)
        _record_schema_migration(conn, 32, "human_review_ux_aggregation")
    if current < 33:
        _create_schema_v33(conn)
        _record_schema_migration(conn, 33, "human_review_action_application")
    if current < 34:
        _create_schema_v34(conn)
        _record_schema_migration(conn, 34, "human_review_action_policy_and_batch_apply")
    if current < 35:
        _create_schema_v35(conn)
        _record_schema_migration(conn, 35, "human_review_undo_and_rollback_trace")
    if current < 36:
        _create_schema_v36(conn)
        _record_schema_migration(conn, 36, "agent_managed_review_loop")
    if current < 37:
        _create_schema_v37(conn)
        _record_schema_migration(conn, 37, "agent_managed_review_acceptance_and_stress")
    if current < 38:
        _create_schema_v38(conn)
        _record_schema_migration(conn, 38, "managed_review_observability_and_release_readiness")
    if current < 39:
        _create_schema_v39(conn)
        _record_schema_migration(conn, 39, "human_readable_schedule_review_and_settings")
    if current < 40:
        _create_schema_v40(conn)
        _record_schema_migration(conn, 40, "living_rhythm_canon_consistency_and_paper_notes")
    if current < 41:
        _create_schema_v41(conn)
        _record_schema_migration(conn, 41, "editable_collections_closet_cabinets")
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
    """v0.9.5 human UX + FinalGate advisory feedback schema.

    The feedback queue is internal-to-agent context. It lets FinalGate warn the
    next model turn without replacing the user's visible response.
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
        """
    )


def _create_schema_v16(conn: sqlite3.Connection) -> None:
    """v0.9.6-style concurrency / integration hardening metadata."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS concurrency_smoke_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          action TEXT NOT NULL,
          workers INTEGER NOT NULL DEFAULT 1,
          items INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL,
          output_json TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS integration_test_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          checks_json TEXT,
          include_details INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS api_freeze_snapshots (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          surface_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'recorded',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS release_readiness_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          summary_json TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def _create_schema_v17(conn: sqlite3.Connection) -> None:
    """v0.9.7 acceptance-runner metadata."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS acceptance_scenario_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          acceptance_run_id TEXT NOT NULL,
          scenario_key TEXT NOT NULL,
          title TEXT,
          status TEXT NOT NULL,
          duration_ms INTEGER,
          checks_json TEXT,
          output_json TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_acceptance_scenario_run ON acceptance_scenario_runs(acceptance_run_id, scenario_key);
        CREATE TABLE IF NOT EXISTS acceptance_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          acceptance_run_id TEXT NOT NULL,
          status TEXT NOT NULL,
          summary_json TEXT,
          report_markdown TEXT,
          report_path TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS v1_rc_checklists (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          acceptance_run_id TEXT,
          status TEXT NOT NULL,
          checklist_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def _create_schema_v18(conn: sqlite3.Connection) -> None:
    """v0.99 trace coverage + durable failure audit metadata."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trace_coverage_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          checked_transactions INTEGER NOT NULL DEFAULT 0,
          issue_count INTEGER NOT NULL DEFAULT 0,
          issues_json TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
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
        """
    )


def _create_schema_v19(conn: sqlite3.Connection) -> None:
    """v0.10.0 consolidation metadata and human-command profile tracking."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS command_surface_profiles (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          profile_name TEXT NOT NULL,
          commands_json TEXT NOT NULL,
          notes TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS v010_release_notes (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          plugin_version TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          notes_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def _create_schema_v20(conn: sqlite3.Connection) -> None:
    """v0.11.0 Event V2: richer event attributes, explicit transition history, and realtime state anchors."""
    for table, columns in {
        "events": [
            ("event_category", "event_category TEXT NOT NULL DEFAULT 'other'"),
            ("activity_domain", "activity_domain TEXT"),
            ("subtype", "subtype TEXT"),
            ("tags_json", "tags_json TEXT NOT NULL DEFAULT '[]'"),
            ("attributes_json", "attributes_json TEXT NOT NULL DEFAULT '{}'"),
            ("location_json", "location_json TEXT NOT NULL DEFAULT '{}'"),
            ("participants_json", "participants_json TEXT NOT NULL DEFAULT '[]'"),
            ("interruptibility_json", "interruptibility_json TEXT NOT NULL DEFAULT '{}'"),
            ("state_effects_json", "state_effects_json TEXT NOT NULL DEFAULT '{}'"),
            ("current_schedule_block_id", "current_schedule_block_id TEXT"),
            ("actual_duration_minutes", "actual_duration_minutes INTEGER"),
            ("last_transition_id", "last_transition_id TEXT"),
            ("lifecycle_version", "lifecycle_version INTEGER NOT NULL DEFAULT 2"),
        ],
        "schedule_blocks": [
            ("actual_start", "actual_start TEXT"),
            ("actual_end", "actual_end TEXT"),
            ("actual_start_ts", "actual_start_ts INTEGER"),
            ("actual_end_ts", "actual_end_ts INTEGER"),
            ("planned_duration_minutes", "planned_duration_minutes INTEGER"),
            ("actual_duration_minutes", "actual_duration_minutes INTEGER"),
            ("interruptibility_json", "interruptibility_json TEXT NOT NULL DEFAULT '{}'"),
            ("transition_reason", "transition_reason TEXT"),
            ("last_transition_id", "last_transition_id TEXT"),
        ],
        "actions": [
            ("last_transition_id", "last_transition_id TEXT"),
            ("actual_start_ts", "actual_start_ts INTEGER"),
            ("actual_end_ts", "actual_end_ts INTEGER"),
        ],
    }.items():
        for col, ddl in columns:
            _add_column_if_missing(conn, table, col, ddl)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS event_state_transitions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          from_status TEXT,
          to_status TEXT NOT NULL,
          reason TEXT,
          source TEXT NOT NULL,
          transaction_id TEXT,
          op_id TEXT,
          receipt_id TEXT,
          schedule_block_id TEXT,
          action_id TEXT,
          result_id TEXT,
          occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
          occurred_at_ts INTEGER,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          trace_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_event_state_transitions_event ON event_state_transitions(owner_kind, owner_id, event_id, occurred_at_ts, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_event_state_transitions_owner ON event_state_transitions(owner_kind, owner_id, occurred_at_ts, occurred_at);

        CREATE TABLE IF NOT EXISTS schedule_block_state_transitions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          schedule_block_id TEXT NOT NULL,
          event_id TEXT,
          from_status TEXT,
          to_status TEXT NOT NULL,
          reason TEXT,
          source TEXT NOT NULL,
          transaction_id TEXT,
          op_id TEXT,
          receipt_id TEXT,
          occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
          occurred_at_ts INTEGER,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          trace_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_state_transitions_block ON schedule_block_state_transitions(owner_kind, owner_id, schedule_block_id, occurred_at_ts, occurred_at);

        CREATE TABLE IF NOT EXISTS action_state_transitions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          action_id TEXT NOT NULL,
          event_id TEXT,
          from_status TEXT,
          to_status TEXT NOT NULL,
          reason TEXT,
          source TEXT NOT NULL,
          transaction_id TEXT,
          op_id TEXT,
          receipt_id TEXT,
          occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
          occurred_at_ts INTEGER,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          trace_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_action_state_transitions_action ON action_state_transitions(owner_kind, owner_id, action_id, occurred_at_ts, occurred_at);

        CREATE TABLE IF NOT EXISTS agent_realtime_state (
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          mode TEXT NOT NULL DEFAULT 'idle',
          active_event_id TEXT,
          active_action_id TEXT,
          active_schedule_block_id TEXT,
          active_sleep_session_id TEXT,
          interruptibility_level TEXT NOT NULL DEFAULT 'interruptible',
          reply_mode TEXT NOT NULL DEFAULT 'immediate',
          lease_expires_at TEXT,
          lease_expires_at_ts INTEGER,
          body_state_json TEXT NOT NULL DEFAULT '{}',
          mind_state_json TEXT NOT NULL DEFAULT '{}',
          environment_state_json TEXT NOT NULL DEFAULT '{}',
          last_user_message_at TEXT,
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY(owner_kind, owner_id)
        );

        CREATE TABLE IF NOT EXISTS agent_state_snapshots (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          active_event_id TEXT,
          active_action_id TEXT,
          active_schedule_block_id TEXT,
          interruptibility_level TEXT,
          reply_mode TEXT,
          body_state_json TEXT NOT NULL DEFAULT '{}',
          mind_state_json TEXT NOT NULL DEFAULT '{}',
          source TEXT NOT NULL,
          reason TEXT,
          event_id TEXT,
          schedule_block_id TEXT,
          trace_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_agent_state_snapshots_owner ON agent_state_snapshots(owner_kind, owner_id, created_at);
        """
    )

    # Backfill category and planned durations for existing rows.
    conn.execute("UPDATE events SET event_category=COALESCE(NULLIF(event_category,''), COALESCE(NULLIF(event_type,''),'other')) WHERE event_category IS NULL OR event_category='' OR event_category='other'")
    conn.execute("UPDATE events SET lifecycle_version=2 WHERE lifecycle_version IS NULL")
    conn.execute("UPDATE schedule_blocks SET planned_duration_minutes=CAST((end_ts - start_ts) / 60 AS INTEGER) WHERE planned_duration_minutes IS NULL AND start_ts IS NOT NULL AND end_ts IS NOT NULL AND end_ts >= start_ts")

    # Seed a creation transition for existing events/schedule blocks so v2 history has a canonical start.
    conn.execute(
        """INSERT OR IGNORE INTO event_state_transitions(id, owner_kind, owner_id, event_id, from_status, to_status, reason, source, occurred_at, occurred_at_ts, metadata_json)
              SELECT 'evtr_' || id, owner_kind, owner_id, id, NULL, status, 'backfilled by Event V2 migration', 'migration', created_at, unixepoch(created_at), '{}'
                FROM events
               WHERE NOT EXISTS (SELECT 1 FROM event_state_transitions t WHERE t.event_id=events.id)"""
    )
    conn.execute(
        """INSERT OR IGNORE INTO schedule_block_state_transitions(id, owner_kind, owner_id, schedule_block_id, event_id, from_status, to_status, reason, source, occurred_at, occurred_at_ts, metadata_json)
              SELECT 'sbtr_' || id, owner_kind, owner_id, id, event_id, NULL, status, 'backfilled by Event V2 migration', 'migration', created_at, unixepoch(created_at), '{}'
                FROM schedule_blocks
               WHERE NOT EXISTS (SELECT 1 FROM schedule_block_state_transitions t WHERE t.schedule_block_id=schedule_blocks.id)"""
    )

    # Ensure each owner has a realtime state row.
    conn.execute(
        """INSERT OR IGNORE INTO agent_realtime_state(owner_kind, owner_id, mode, body_state_json, mind_state_json, environment_state_json)
              SELECT owner_kind, owner_id, 'idle', '{}', '{}', '{}' FROM controls"""
    )


def _create_schema_v21(conn: sqlite3.Connection) -> None:
    """v0.11.1 SleepPlan/SleepSession: planned sleep and actual sleep are separate stateful records."""
    _add_column_if_missing(conn, "agent_state_snapshots", "active_sleep_session_id", "active_sleep_session_id TEXT")
    _add_column_if_missing(conn, "agent_state_snapshots", "active_sleep_session_id", "active_sleep_session_id TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sleep_plans (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          date TEXT,
          status TEXT NOT NULL DEFAULT 'scheduled',
          plan_type TEXT NOT NULL DEFAULT 'core_sleep',
          event_id TEXT,
          schedule_block_id TEXT,
          planned_sleep_at TEXT,
          planned_sleep_at_ts INTEGER,
          planned_wake_at TEXT,
          planned_wake_at_ts INTEGER,
          planned_duration_minutes INTEGER,
          timezone TEXT DEFAULT 'UTC',
          alarm_at TEXT,
          alarm_at_ts INTEGER,
          alarm_label TEXT,
          wake_policy TEXT DEFAULT 'natural_or_alarm',
          constraints_json TEXT NOT NULL DEFAULT '{}',
          decision_json TEXT NOT NULL DEFAULT '{}',
          canon_version INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sleep_plans_owner_date ON sleep_plans(owner_kind, owner_id, date, status);
        CREATE INDEX IF NOT EXISTS idx_sleep_plans_event ON sleep_plans(event_id);
        CREATE INDEX IF NOT EXISTS idx_sleep_plans_block ON sleep_plans(schedule_block_id);

        CREATE TABLE IF NOT EXISTS sleep_sessions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          sleep_plan_id TEXT,
          event_id TEXT,
          schedule_block_id TEXT,
          session_type TEXT NOT NULL DEFAULT 'core_sleep',
          status TEXT NOT NULL DEFAULT 'asleep',
          actual_sleep_at TEXT,
          actual_sleep_at_ts INTEGER,
          actual_wake_at TEXT,
          actual_wake_at_ts INTEGER,
          actual_duration_minutes INTEGER,
          planned_duration_minutes INTEGER,
          wake_cause TEXT,
          interrupted_by TEXT,
          quality_score REAL,
          sleep_debt_delta_minutes INTEGER,
          resource_effects_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sleep_sessions_owner_start ON sleep_sessions(owner_kind, owner_id, actual_sleep_at_ts, created_at);
        CREATE INDEX IF NOT EXISTS idx_sleep_sessions_plan ON sleep_sessions(sleep_plan_id);
        CREATE INDEX IF NOT EXISTS idx_sleep_sessions_event ON sleep_sessions(event_id);

        CREATE TABLE IF NOT EXISTS sleep_interruptions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          sleep_session_id TEXT NOT NULL,
          interrupted_at TEXT NOT NULL,
          interrupted_at_ts INTEGER,
          source TEXT NOT NULL,
          reason TEXT,
          user_id TEXT,
          session_id TEXT,
          turn_id TEXT,
          caused_wake INTEGER NOT NULL DEFAULT 1,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sleep_interruptions_session ON sleep_interruptions(owner_kind, owner_id, sleep_session_id, interrupted_at_ts);

        CREATE TABLE IF NOT EXISTS sleep_session_state_transitions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          sleep_session_id TEXT NOT NULL,
          from_status TEXT,
          to_status TEXT NOT NULL,
          reason TEXT,
          source TEXT NOT NULL,
          transaction_id TEXT,
          op_id TEXT,
          receipt_id TEXT,
          occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
          occurred_at_ts INTEGER,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          trace_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sleep_session_transitions_session ON sleep_session_state_transitions(owner_kind, owner_id, sleep_session_id, occurred_at_ts, occurred_at);

        CREATE TABLE IF NOT EXISTS sleep_doctor_findings (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          finding_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          sleep_plan_id TEXT,
          sleep_session_id TEXT,
          message TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def _create_schema_v22(conn: sqlite3.Connection) -> None:
    """v0.11.2 ReplyGate: delayed replies, gate decisions, and call overrides."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS reply_gate_decisions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          user_id TEXT,
          incoming_message_preview TEXT,
          decision TEXT NOT NULL,
          reason TEXT,
          mode TEXT,
          active_event_id TEXT,
          active_schedule_block_id TEXT,
          active_sleep_session_id TEXT,
          interruptibility_level TEXT,
          reply_mode TEXT,
          state_snapshot_json TEXT NOT NULL DEFAULT '{}',
          policy_json TEXT NOT NULL DEFAULT '{}',
          trace_id TEXT,
          source TEXT NOT NULL DEFAULT 'reply_gate',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_reply_gate_decisions_owner ON reply_gate_decisions(owner_kind, owner_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_reply_gate_decisions_session ON reply_gate_decisions(session_id, turn_id);

        CREATE TABLE IF NOT EXISTS delayed_replies (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          user_id TEXT,
          session_id TEXT,
          turn_id TEXT,
          message_text TEXT NOT NULL,
          message_preview TEXT,
          gate_decision_id TEXT,
          reason TEXT,
          status TEXT NOT NULL DEFAULT 'pending',
          queued_at TEXT NOT NULL DEFAULT (datetime('now')),
          released_at TEXT,
          release_reason TEXT,
          expires_at TEXT,
          expires_at_ts INTEGER,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_delayed_replies_owner_status ON delayed_replies(owner_kind, owner_id, status, queued_at);
        CREATE INDEX IF NOT EXISTS idx_delayed_replies_session ON delayed_replies(session_id, turn_id);

        CREATE TABLE IF NOT EXISTS call_overrides (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          user_id TEXT,
          session_id TEXT,
          turn_id TEXT,
          reason TEXT,
          target_kind TEXT,
          target_id TEXT,
          interrupted_sleep_session_id TEXT,
          interrupted_event_id TEXT,
          gate_decision_id TEXT,
          result_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_call_overrides_owner ON call_overrides(owner_kind, owner_id, created_at);

        CREATE TABLE IF NOT EXISTS reply_gate_recoveries (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          recovery_type TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'warning',
          message TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_reply_gate_recoveries_owner ON reply_gate_recoveries(owner_kind, owner_id, created_at);
        """
    )


def _create_schema_v23(conn: sqlite3.Connection) -> None:
    """v0.11.3 DreamRun/DreamAudit/DreamEntry.

    DreamRun is bound to completed/interrupted SleepSession records.  DreamAudit
    performs a nightly self-check of state transitions, resource settlement,
    stale wake jobs, delayed replies, and stale reservations.  DreamEntry stores
    symbolic dream narrative with truth_layer='dream_symbolic' so dreams can be
    shared without contaminating ordinary life facts.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dream_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          sleep_session_id TEXT,
          sleep_plan_id TEXT,
          run_type TEXT NOT NULL DEFAULT 'sleep_dream',
          status TEXT NOT NULL DEFAULT 'running',
          trigger TEXT NOT NULL DEFAULT 'sleep_wake',
          started_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT,
          audit_status TEXT NOT NULL DEFAULT 'pending',
          narrative_status TEXT NOT NULL DEFAULT 'pending',
          memory_consolidation_status TEXT NOT NULL DEFAULT 'pending',
          share_status TEXT NOT NULL DEFAULT 'pending',
          findings_count INTEGER NOT NULL DEFAULT 0,
          created_entry_id TEXT,
          proactive_intent_id TEXT,
          trace_id TEXT,
          audit_summary_json TEXT NOT NULL DEFAULT '{}',
          narrative_inputs_json TEXT NOT NULL DEFAULT '{}',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dream_runs_owner_time ON dream_runs(owner_kind, owner_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_dream_runs_sleep_session ON dream_runs(owner_kind, owner_id, sleep_session_id);

        CREATE TABLE IF NOT EXISTS dream_audit_findings (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          dream_run_id TEXT NOT NULL,
          finding_type TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'info',
          target_kind TEXT,
          target_id TEXT,
          message TEXT,
          proposed_ops_json TEXT NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'open',
          resolved_by_tx_id TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dream_audit_findings_run ON dream_audit_findings(owner_kind, owner_id, dream_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_dream_audit_findings_target ON dream_audit_findings(target_kind, target_id);

        CREATE TABLE IF NOT EXISTS dream_entries (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          dream_run_id TEXT,
          sleep_session_id TEXT,
          content TEXT NOT NULL,
          summary TEXT,
          share_text TEXT,
          symbols_json TEXT NOT NULL DEFAULT '[]',
          source_memory_ids_json TEXT NOT NULL DEFAULT '[]',
          source_event_ids_json TEXT NOT NULL DEFAULT '[]',
          source_goal_ids_json TEXT NOT NULL DEFAULT '[]',
          source_finding_ids_json TEXT NOT NULL DEFAULT '[]',
          truth_layer TEXT NOT NULL DEFAULT 'dream_symbolic',
          privacy TEXT NOT NULL DEFAULT 'safe_to_share',
          status TEXT NOT NULL DEFAULT 'created',
          memory_id TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dream_entries_owner_time ON dream_entries(owner_kind, owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_dream_entries_run ON dream_entries(owner_kind, owner_id, dream_run_id);
        """
    )

def _create_schema_v24(conn: sqlite3.Connection) -> None:
    """v0.11.4 Sleep/Reply/Dream acceptance and DreamAudit repair runs."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dream_repair_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          dream_run_id TEXT,
          mode TEXT NOT NULL DEFAULT 'apply',
          status TEXT NOT NULL DEFAULT 'running',
          finding_ids_json TEXT NOT NULL DEFAULT '[]',
          proposed_ops_json TEXT NOT NULL DEFAULT '[]',
          transaction_id TEXT,
          receipt_id TEXT,
          error TEXT,
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dream_repair_runs_owner_time ON dream_repair_runs(owner_kind, owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_dream_repair_runs_dream ON dream_repair_runs(owner_kind, owner_id, dream_run_id);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_acceptance_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          scenario_count INTEGER NOT NULL DEFAULT 0,
          passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          summary_json TEXT NOT NULL DEFAULT '{}',
          report_markdown TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_srd_acceptance_runs_owner_time ON sleep_reply_dream_acceptance_runs(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_acceptance_scenarios (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          acceptance_run_id TEXT NOT NULL,
          scenario_key TEXT NOT NULL,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          checks_json TEXT NOT NULL DEFAULT '[]',
          output_json TEXT NOT NULL DEFAULT '{}',
          duration_ms INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_srd_acceptance_scenarios_run ON sleep_reply_dream_acceptance_scenarios(acceptance_run_id, scenario_key);
        """
    )




def _create_schema_v25(conn: sqlite3.Connection) -> None:
    """v0.11.5 sleep debt/day-state effects, delayed reply digest, and DreamAudit repair policy."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sleep_day_states (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          date_key TEXT NOT NULL,
          source_sleep_plan_id TEXT,
          source_sleep_session_id TEXT,
          planned_sleep_minutes INTEGER NOT NULL DEFAULT 0,
          actual_sleep_minutes INTEGER NOT NULL DEFAULT 0,
          sleep_debt_delta_minutes INTEGER NOT NULL DEFAULT 0,
          cumulative_sleep_debt_minutes INTEGER NOT NULL DEFAULT 0,
          all_nighter INTEGER NOT NULL DEFAULT 0,
          energy_penalty INTEGER NOT NULL DEFAULT 0,
          focus_penalty INTEGER NOT NULL DEFAULT 0,
          mood_penalty INTEGER NOT NULL DEFAULT 0,
          fatigue_delta INTEGER NOT NULL DEFAULT 0,
          recovery_pressure INTEGER NOT NULL DEFAULT 0,
          nap_recommended INTEGER NOT NULL DEFAULT 0,
          recovery_plan_id TEXT,
          resource_ledger_ids_json TEXT NOT NULL DEFAULT '[]',
          body_state_json TEXT NOT NULL DEFAULT '{}',
          mind_state_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(owner_kind, owner_id, date_key)
        );
        CREATE INDEX IF NOT EXISTS idx_sleep_day_states_owner_date ON sleep_day_states(owner_kind, owner_id, date_key DESC);
        CREATE INDEX IF NOT EXISTS idx_sleep_day_states_pressure ON sleep_day_states(owner_kind, owner_id, recovery_pressure DESC);

        CREATE TABLE IF NOT EXISTS sleep_recovery_plans (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          date_key TEXT NOT NULL,
          sleep_day_state_id TEXT,
          sleep_plan_id TEXT,
          reason TEXT,
          pressure INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'planned',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sleep_recovery_plans_owner_date ON sleep_recovery_plans(owner_kind, owner_id, date_key DESC);

        CREATE TABLE IF NOT EXISTS delayed_reply_digests (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'created',
          delayed_reply_ids_json TEXT NOT NULL DEFAULT '[]',
          message_count INTEGER NOT NULL DEFAULT 0,
          summary_text TEXT,
          release_reason TEXT,
          created_by TEXT NOT NULL DEFAULT 'reply_gate',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          released_at TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_delayed_reply_digests_owner_time ON delayed_reply_digests(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS dream_repair_policies (
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          mode TEXT NOT NULL DEFAULT 'manual',
          safe_finding_types_json TEXT NOT NULL DEFAULT '["stale_schedule_block","pending_delayed_replies","stale_resource_reservation"]',
          auto_apply_limit INTEGER NOT NULL DEFAULT 10,
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_by TEXT,
          PRIMARY KEY(owner_kind, owner_id)
        );
        CREATE INDEX IF NOT EXISTS idx_dream_repair_policies_mode ON dream_repair_policies(mode);
        """
    )


def _create_schema_v26(conn: sqlite3.Connection) -> None:
    """v0.11.6 Autonomy reads SleepDayState / realtime fatigue and records sleep-aware adjustments."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS autonomy_sleep_adjustments (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          decision_id TEXT,
          sleep_day_state_id TEXT,
          adjustment_type TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'info',
          reason TEXT,
          sleep_context_json TEXT NOT NULL DEFAULT '{}',
          proposed_ops_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_autonomy_sleep_adjustments_owner_time ON autonomy_sleep_adjustments(owner_kind, owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_autonomy_sleep_adjustments_decision ON autonomy_sleep_adjustments(decision_id);
        CREATE INDEX IF NOT EXISTS idx_autonomy_sleep_adjustments_type ON autonomy_sleep_adjustments(adjustment_type, severity);
        """
    )


def _create_schema_v27(conn: sqlite3.Connection) -> None:
    """v0.11.7 Execution Simulator reads SleepDayState / realtime fatigue and records sleep-aware execution adjustments."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS execution_sleep_adjustments (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          execution_decision_id TEXT,
          sleep_day_state_id TEXT,
          event_id TEXT,
          schedule_block_id TEXT,
          adjustment_type TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'info',
          reason TEXT,
          sleep_context_json TEXT NOT NULL DEFAULT '{}',
          original_decision_type TEXT,
          adjusted_decision_type TEXT,
          proposed_ops_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_execution_sleep_adjustments_owner_time ON execution_sleep_adjustments(owner_kind, owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_execution_sleep_adjustments_decision ON execution_sleep_adjustments(execution_decision_id);
        CREATE INDEX IF NOT EXISTS idx_execution_sleep_adjustments_event ON execution_sleep_adjustments(owner_kind, owner_id, event_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_execution_sleep_adjustments_type ON execution_sleep_adjustments(adjustment_type, severity);
        """
    )


def _create_schema_v28(conn: sqlite3.Connection) -> None:
    """v0.11.8 Sleep / Autonomy / Execution end-to-end acceptance records."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sleep_autonomy_execution_acceptance_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          synthetic_owner_id TEXT,
          scenario_count INTEGER NOT NULL DEFAULT 0,
          passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          summary_json TEXT NOT NULL DEFAULT '{}',
          report_markdown TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sae_acceptance_runs_owner_time ON sleep_autonomy_execution_acceptance_runs(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sleep_autonomy_execution_acceptance_scenarios (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          acceptance_run_id TEXT NOT NULL,
          scenario_key TEXT NOT NULL,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          checks_json TEXT NOT NULL DEFAULT '[]',
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sae_acceptance_scenarios_run ON sleep_autonomy_execution_acceptance_scenarios(acceptance_run_id, scenario_key);
        CREATE INDEX IF NOT EXISTS idx_sae_acceptance_scenarios_status ON sleep_autonomy_execution_acceptance_scenarios(status, scenario_key);
        """
    )


def _create_schema_v29(conn: sqlite3.Connection) -> None:
    """v0.11.9 Sleep / Reply / Dream real-conversation acceptance records."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sleep_reply_dream_conversation_acceptance_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL,
          synthetic_owner_id TEXT,
          scenario_count INTEGER NOT NULL DEFAULT 0,
          passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          summary_json TEXT NOT NULL DEFAULT '{}',
          report_markdown TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_crd_acceptance_runs_owner_time ON sleep_reply_dream_conversation_acceptance_runs(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_conversation_acceptance_scenarios (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          acceptance_run_id TEXT NOT NULL,
          scenario_key TEXT NOT NULL,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          checks_json TEXT NOT NULL DEFAULT '[]',
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_crd_acceptance_scenarios_run ON sleep_reply_dream_conversation_acceptance_scenarios(acceptance_run_id, scenario_key);
        CREATE INDEX IF NOT EXISTS idx_crd_acceptance_scenarios_status ON sleep_reply_dream_conversation_acceptance_scenarios(status, scenario_key);
        """
    )



def _create_schema_v30(conn: sqlite3.Connection) -> None:
    """v0.11.10 Sleep / Reply / Dream policy UX configuration."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sleep_reply_dream_policies (
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          profile TEXT NOT NULL DEFAULT 'balanced',
          policy_json TEXT NOT NULL DEFAULT '{}',
          updated_by TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY(owner_kind, owner_id)
        );
        CREATE INDEX IF NOT EXISTS idx_srd_policies_profile ON sleep_reply_dream_policies(profile);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_policy_audits (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          action TEXT NOT NULL,
          old_policy_json TEXT NOT NULL DEFAULT '{}',
          new_policy_json TEXT NOT NULL DEFAULT '{}',
          patch_json TEXT NOT NULL DEFAULT '{}',
          source TEXT NOT NULL DEFAULT 'policy',
          updated_by TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_srd_policy_audits_owner_time ON sleep_reply_dream_policy_audits(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_policy_suggestions (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          suggestion_type TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'info',
          message TEXT,
          suggested_patch_json TEXT NOT NULL DEFAULT '{}',
          evidence_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'open',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_srd_policy_suggestions_owner_status ON sleep_reply_dream_policy_suggestions(owner_kind, owner_id, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_srd_policy_suggestions_type ON sleep_reply_dream_policy_suggestions(suggestion_type, severity);
        """
    )



def _create_schema_v31(conn: sqlite3.Connection) -> None:
    """v0.11.11 Sleep/Reply/Dream policy acceptance, conflicts, import/export."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sleep_reply_dream_policy_conflict_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'ok',
          conflict_count INTEGER NOT NULL DEFAULT 0,
          warning_count INTEGER NOT NULL DEFAULT 0,
          conflicts_json TEXT NOT NULL DEFAULT '[]',
          warnings_json TEXT NOT NULL DEFAULT '[]',
          policy_profile TEXT,
          policy_hash TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_srd_policy_conflict_owner_time ON sleep_reply_dream_policy_conflict_reports(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_policy_exports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          profile TEXT,
          export_path TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          policy_json TEXT NOT NULL,
          manifest_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_srd_policy_exports_owner_time ON sleep_reply_dream_policy_exports(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_policy_imports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          import_path TEXT,
          sha256 TEXT,
          status TEXT NOT NULL DEFAULT 'inspected',
          apply_policy INTEGER NOT NULL DEFAULT 0,
          imported_profile TEXT,
          validation_json TEXT NOT NULL DEFAULT '{}',
          policy_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          applied_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_srd_policy_imports_owner_time ON sleep_reply_dream_policy_imports(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_policy_acceptance_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          synthetic_owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          passed INTEGER NOT NULL DEFAULT 0,
          failed INTEGER NOT NULL DEFAULT 0,
          checks_json TEXT NOT NULL DEFAULT '[]',
          output_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_srd_policy_acceptance_runs_owner_time ON sleep_reply_dream_policy_acceptance_runs(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sleep_reply_dream_policy_acceptance_scenarios (
          id TEXT PRIMARY KEY,
          acceptance_run_id TEXT NOT NULL,
          scenario_key TEXT NOT NULL,
          description TEXT,
          status TEXT NOT NULL,
          details_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_srd_policy_acceptance_scenarios_run ON sleep_reply_dream_policy_acceptance_scenarios(acceptance_run_id, scenario_key);
        """
    )


def _create_schema_v32(conn: sqlite3.Connection) -> None:
    """v0.11.12 Human Review UX aggregation."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS human_review_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'created',
          severity TEXT NOT NULL DEFAULT 'ok',
          summary_json TEXT NOT NULL DEFAULT '{}',
          section_counts_json TEXT NOT NULL DEFAULT '{}',
          item_count INTEGER NOT NULL DEFAULT 0,
          rendered_text TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_runs_owner_time ON human_review_runs(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS human_review_items (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          review_run_id TEXT NOT NULL,
          item_type TEXT NOT NULL,
          severity TEXT NOT NULL DEFAULT 'info',
          title TEXT NOT NULL,
          message TEXT,
          source_table TEXT,
          source_id TEXT,
          action_hint_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'open',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_items_run ON human_review_items(review_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_human_review_items_owner_status ON human_review_items(owner_kind, owner_id, status, severity, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_human_review_items_source ON human_review_items(source_table, source_id);
        """
    )


def _create_schema_v33(conn: sqlite3.Connection) -> None:
    """v0.11.13 Human Review action application / one-click safe actions."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS human_review_action_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          item_id TEXT,
          review_run_id TEXT,
          mode TEXT NOT NULL DEFAULT 'preview',
          status TEXT NOT NULL DEFAULT 'planned',
          input_json TEXT NOT NULL DEFAULT '{}',
          plan_json TEXT NOT NULL DEFAULT '{}',
          output_json TEXT NOT NULL DEFAULT '{}',
          transaction_id TEXT,
          receipt_id TEXT,
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_action_runs_owner_time ON human_review_action_runs(owner_kind, owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_human_review_action_runs_item ON human_review_action_runs(item_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_human_review_action_runs_tx ON human_review_action_runs(transaction_id);
        """
    )


def _create_schema_v34(conn: sqlite3.Connection) -> None:
    """v0.11.14 Review Action Policy / safe batch apply."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS human_review_action_policies (
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          policy_json TEXT NOT NULL DEFAULT '{}',
          updated_by TEXT,
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY(owner_kind, owner_id)
        );

        CREATE TABLE IF NOT EXISTS human_review_batch_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          review_run_id TEXT,
          mode TEXT NOT NULL DEFAULT 'dry_run',
          section TEXT,
          safe_only INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'planned',
          selected_item_ids_json TEXT NOT NULL DEFAULT '[]',
          plan_json TEXT NOT NULL DEFAULT '{}',
          results_json TEXT NOT NULL DEFAULT '[]',
          transaction_ids_json TEXT NOT NULL DEFAULT '[]',
          receipt_ids_json TEXT NOT NULL DEFAULT '[]',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_batch_runs_owner_time ON human_review_batch_runs(owner_kind, owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_human_review_batch_runs_review ON human_review_batch_runs(review_run_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS human_review_batch_items (
          id TEXT PRIMARY KEY,
          batch_run_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          action_run_id TEXT,
          status TEXT NOT NULL DEFAULT 'planned',
          plan_json TEXT NOT NULL DEFAULT '{}',
          output_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_batch_items_batch ON human_review_batch_items(batch_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_human_review_batch_items_item ON human_review_batch_items(item_id, created_at DESC);
        """
    )


def _create_schema_v35(conn: sqlite3.Connection) -> None:
    """v0.11.15 Review undo / rollback trace."""
    for column, ddl in {
        "undo_status": "undo_status TEXT NOT NULL DEFAULT 'not_requested'",
        "undo_run_id": "undo_run_id TEXT",
        "undo_plan_json": "undo_plan_json TEXT NOT NULL DEFAULT '{}'",
    }.items():
        _add_column_if_missing(conn, "human_review_action_runs", column, ddl)
    for column, ddl in {
        "undo_status": "undo_status TEXT NOT NULL DEFAULT 'not_requested'",
        "undo_run_id": "undo_run_id TEXT",
        "undo_plan_json": "undo_plan_json TEXT NOT NULL DEFAULT '{}'",
    }.items():
        _add_column_if_missing(conn, "human_review_batch_runs", column, ddl)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS human_review_undo_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          batch_run_id TEXT,
          action_run_id TEXT,
          mode TEXT NOT NULL DEFAULT 'preview',
          status TEXT NOT NULL DEFAULT 'planned',
          undo_plan_json TEXT NOT NULL DEFAULT '{}',
          output_json TEXT NOT NULL DEFAULT '{}',
          action_run_ids_json TEXT NOT NULL DEFAULT '[]',
          transaction_ids_json TEXT NOT NULL DEFAULT '[]',
          receipt_ids_json TEXT NOT NULL DEFAULT '[]',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_undo_runs_owner_time ON human_review_undo_runs(owner_kind, owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_human_review_undo_runs_target ON human_review_undo_runs(target_kind, target_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS human_review_undo_items (
          id TEXT PRIMARY KEY,
          undo_run_id TEXT NOT NULL,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          action_run_id TEXT,
          batch_run_id TEXT,
          status TEXT NOT NULL DEFAULT 'planned',
          undo_plan_json TEXT NOT NULL DEFAULT '{}',
          output_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_undo_items_run ON human_review_undo_items(undo_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_human_review_undo_items_action ON human_review_undo_items(action_run_id, created_at DESC);
        """
    )


def _create_schema_v36(conn: sqlite3.Connection) -> None:
    """v0.11.16 Agent-managed review loop."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS human_review_managed_loop_state (
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          date_key TEXT NOT NULL,
          run_count INTEGER NOT NULL DEFAULT 0,
          action_count INTEGER NOT NULL DEFAULT 0,
          failure_count INTEGER NOT NULL DEFAULT 0,
          last_run_id TEXT,
          last_run_at TEXT,
          last_status TEXT,
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY(owner_kind, owner_id, date_key)
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_managed_loop_state_owner ON human_review_managed_loop_state(owner_kind, owner_id, date_key DESC);

        CREATE TABLE IF NOT EXISTS human_review_managed_loop_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          trigger_source TEXT NOT NULL DEFAULT 'manual',
          tick_id TEXT,
          status TEXT NOT NULL DEFAULT 'planned',
          policy_json TEXT NOT NULL DEFAULT '{}',
          decision_json TEXT NOT NULL DEFAULT '{}',
          review_run_id TEXT,
          batch_run_id TEXT,
          selected_count INTEGER NOT NULL DEFAULT 0,
          applied_count INTEGER NOT NULL DEFAULT 0,
          skipped_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          daily_action_count_before INTEGER NOT NULL DEFAULT 0,
          daily_action_limit INTEGER NOT NULL DEFAULT 0,
          failure_count_before INTEGER NOT NULL DEFAULT 0,
          failure_budget INTEGER NOT NULL DEFAULT 0,
          output_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_managed_loop_runs_owner_time ON human_review_managed_loop_runs(owner_kind, owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_human_review_managed_loop_runs_tick ON human_review_managed_loop_runs(tick_id);
        """
    )


def _create_schema_v37(conn: sqlite3.Connection) -> None:
    """v0.11.17 Agent-managed review loop acceptance and stress hardening."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS human_review_managed_acceptance_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          scenario_count INTEGER NOT NULL DEFAULT 0,
          passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          skipped_count INTEGER NOT NULL DEFAULT 0,
          output_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_managed_acceptance_runs_owner_time
          ON human_review_managed_acceptance_runs(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS human_review_managed_acceptance_scenarios (
          id TEXT PRIMARY KEY,
          acceptance_run_id TEXT NOT NULL,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          scenario_key TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          summary TEXT,
          details_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_managed_acceptance_scenarios_run
          ON human_review_managed_acceptance_scenarios(acceptance_run_id, scenario_key);

        CREATE TABLE IF NOT EXISTS human_review_managed_stress_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          stress_kind TEXT NOT NULL DEFAULT 'delayed_reply_batch',
          input_json TEXT NOT NULL DEFAULT '{}',
          output_json TEXT NOT NULL DEFAULT '{}',
          created_count INTEGER NOT NULL DEFAULT 0,
          selected_count INTEGER NOT NULL DEFAULT 0,
          applied_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          duration_ms INTEGER,
          error TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_managed_stress_runs_owner_time
          ON human_review_managed_stress_runs(owner_kind, owner_id, created_at DESC);
        """
    )


def _create_schema_v38(conn: sqlite3.Connection) -> None:
    """v0.11.18 Agent-managed review observability and release readiness."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS human_review_managed_observability_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'created',
          readiness_status TEXT NOT NULL DEFAULT 'unknown',
          policy_json TEXT NOT NULL DEFAULT '{}',
          policy_validation_json TEXT NOT NULL DEFAULT '{}',
          managed_state_json TEXT NOT NULL DEFAULT '{}',
          recent_runs_json TEXT NOT NULL DEFAULT '[]',
          latest_acceptance_json TEXT NOT NULL DEFAULT '{}',
          latest_stress_json TEXT NOT NULL DEFAULT '{}',
          doctor_json TEXT NOT NULL DEFAULT '{}',
          review_summary_json TEXT NOT NULL DEFAULT '{}',
          signals_json TEXT NOT NULL DEFAULT '[]',
          recommendations_json TEXT NOT NULL DEFAULT '[]',
          rendered_text TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_managed_observability_reports_owner_time
          ON human_review_managed_observability_reports(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS human_review_managed_release_readiness_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'created',
          readiness_status TEXT NOT NULL DEFAULT 'unknown',
          score INTEGER NOT NULL DEFAULT 0,
          checks_json TEXT NOT NULL DEFAULT '[]',
          blockers_json TEXT NOT NULL DEFAULT '[]',
          warnings_json TEXT NOT NULL DEFAULT '[]',
          observability_report_id TEXT,
          recommendation TEXT,
          rendered_text TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_human_review_managed_release_readiness_reports_owner_time
          ON human_review_managed_release_readiness_reports(owner_kind, owner_id, created_at DESC);
        """
    )

def _create_schema_v39(conn: sqlite3.Connection) -> None:
    """v0.11.19 human-readable schedule/review/settings surface."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS life_required_setting_checks (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'needs_setup',
          missing_count INTEGER NOT NULL DEFAULT 0,
          items_json TEXT NOT NULL DEFAULT '[]',
          source TEXT NOT NULL DEFAULT 'startup',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_life_required_setting_checks_owner_time
          ON life_required_setting_checks(owner_kind, owner_id, created_at DESC);
        """
    )




def _create_schema_v40(conn: sqlite3.Connection) -> None:
    """v0.12.5 living layer: Canon consistency, concrete day rhythm, paper notes."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS canon_consistency_reports (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'ok',
          conflict_count INTEGER NOT NULL DEFAULT 0,
          warning_count INTEGER NOT NULL DEFAULT 0,
          issues_json TEXT NOT NULL DEFAULT '[]',
          rendered_text TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_canon_consistency_owner_time
          ON canon_consistency_reports(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS life_rhythm_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          date_key TEXT,
          preset TEXT NOT NULL DEFAULT 'default',
          action TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'created',
          event_ids_json TEXT NOT NULL DEFAULT '[]',
          schedule_block_ids_json TEXT NOT NULL DEFAULT '[]',
          transaction_ids_json TEXT NOT NULL DEFAULT '[]',
          receipt_ids_json TEXT NOT NULL DEFAULT '[]',
          rendered_text TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_life_rhythm_runs_owner_time
          ON life_rhythm_runs(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS life_rhythm_items (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          title TEXT NOT NULL,
          category TEXT,
          activity_domain TEXT,
          start TEXT,
          end TEXT,
          event_id TEXT,
          schedule_block_id TEXT,
          status TEXT NOT NULL DEFAULT 'planned',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_life_rhythm_items_run ON life_rhythm_items(run_id);

        CREATE TABLE IF NOT EXISTS living_inventory_preset_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          preset TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'created',
          resource_keys_json TEXT NOT NULL DEFAULT '[]',
          item_names_json TEXT NOT NULL DEFAULT '[]',
          transaction_id TEXT,
          receipt_id TEXT,
          rendered_text TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_living_inventory_preset_owner_time
          ON living_inventory_preset_runs(owner_kind, owner_id, created_at DESC);
        """
    )


def _create_schema_v41(conn: sqlite3.Connection) -> None:
    """v0.12.6 editable item collections / closet cabinets.

    Preset collections are editable.  New collection categories can be created
    by the Agent with their own intake image-generation rules, usage rules,
    and maintenance rules.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS item_collections (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          collection_type TEXT NOT NULL DEFAULT 'custom',
          name TEXT NOT NULL,
          description TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          rules_json TEXT NOT NULL DEFAULT '{}',
          image_generation_rule_json TEXT NOT NULL DEFAULT '{}',
          usage_rule_json TEXT NOT NULL DEFAULT '{}',
          maintenance_rule_json TEXT NOT NULL DEFAULT '{}',
          required_metadata_json TEXT NOT NULL DEFAULT '[]',
          sort_order INTEGER NOT NULL DEFAULT 100,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_item_collections_owner_type
          ON item_collections(owner_kind, owner_id, collection_type, status);

        CREATE TABLE IF NOT EXISTS collection_items (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          collection_id TEXT NOT NULL,
          item_type TEXT NOT NULL DEFAULT 'item',
          name TEXT NOT NULL,
          description TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          tags_json TEXT NOT NULL DEFAULT '[]',
          attributes_json TEXT NOT NULL DEFAULT '{}',
          material_spec_json TEXT NOT NULL DEFAULT '{}',
          care_spec_json TEXT NOT NULL DEFAULT '{}',
          asset_bundle_json TEXT NOT NULL DEFAULT '{}',
          usage_state_json TEXT NOT NULL DEFAULT '{}',
          quantity REAL NOT NULL DEFAULT 1,
          condition_score INTEGER NOT NULL DEFAULT 100,
          cleanliness_state TEXT NOT NULL DEFAULT 'clean',
          availability_state TEXT NOT NULL DEFAULT 'available',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY(collection_id) REFERENCES item_collections(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_collection_items_owner_collection
          ON collection_items(owner_kind, owner_id, collection_id, status, availability_state);

        CREATE TABLE IF NOT EXISTS collection_item_assets (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          asset_type TEXT NOT NULL,
          view_name TEXT,
          asset_uri TEXT,
          prompt_text TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'pending_generation',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY(item_id) REFERENCES collection_items(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_collection_assets_item
          ON collection_item_assets(owner_kind, owner_id, item_id, status);

        CREATE TABLE IF NOT EXISTS outfit_plans (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          occasion TEXT NOT NULL DEFAULT 'daily',
          event_id TEXT,
          item_ids_json TEXT NOT NULL DEFAULT '[]',
          context_json TEXT NOT NULL DEFAULT '{}',
          reasoning_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'draft',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_outfit_plans_owner_time
          ON outfit_plans(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS collection_rule_presets (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          preset_name TEXT NOT NULL,
          collection_type TEXT NOT NULL,
          rule_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_collection_rule_presets_owner
          ON collection_rule_presets(owner_kind, owner_id, collection_type, status);

        CREATE TABLE IF NOT EXISTS collection_maintenance_runs (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          item_id TEXT,
          maintenance_type TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          result_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_collection_maintenance_owner
          ON collection_maintenance_runs(owner_kind, owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS collection_usage_history (
          id TEXT PRIMARY KEY,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          outfit_plan_id TEXT,
          event_id TEXT,
          operation TEXT NOT NULL,
          reason TEXT,
          status TEXT NOT NULL DEFAULT 'done',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_collection_usage_owner_item
          ON collection_usage_history(owner_kind, owner_id, item_id, created_at DESC);
        """
    )
