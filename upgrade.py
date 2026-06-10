"""LifeEngine install/upgrade/maintenance helpers for v0.9.2.

These helpers are intentionally embedded and SQLite-first.  They do not call a
model and do not mutate Agent life state; they only inspect, back up, rebuild
indexes, or record upgrade/maintenance diagnostics.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import PLUGIN_VERSION
from .db import _SCHEMA_VERSION
from .embeddings import embed_text, serialize_embedding
from .jsonutil import dumps
from .paths import db_path, exports_dir, hermes_home
from .trace import append_audit, new_id


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sqlite_vec_info(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        sqlite_version, vec_version = conn.execute("SELECT sqlite_version(), vec_version()").fetchone()
        return {"ok": True, "sqlite_version": sqlite_version, "sqlite_vec_version": vec_version}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def migration_history(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM schema_migrations ORDER BY created_at DESC, to_version DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def run_upgrade_check(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, include_details: bool = False, write_audit: bool = True) -> dict[str, Any]:
    """Record and return an upgrade/install status report."""
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    vec = _sqlite_vec_info(conn)
    history = migration_history(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')").fetchall()}
    required = {"schema_migrations", "upgrade_runs", "db_backups", "maintenance_runs", "cron_heartbeat_tests", "integration_test_runs", "api_freeze_snapshots", "core_patch_drafts", "acceptance_scenario_runs", "acceptance_reports", "v1_rc_checklists"}
    missing = sorted(required - tables)
    checks = [
        {"name": "schema_version", "ok": user_version == _SCHEMA_VERSION, "message": f"user_version={user_version}, expected={_SCHEMA_VERSION}"},
        {"name": "sqlite_vec", "ok": bool(vec.get("ok")), "message": json.dumps(vec, ensure_ascii=False, sort_keys=True)},
        {"name": "migration_history", "ok": any(h.get("to_version") == _SCHEMA_VERSION for h in history), "message": f"{len(history)} migration row(s)"},
        {"name": "upgrade_tables", "ok": not missing, "message": "all upgrade tables present" if not missing else f"missing: {missing}"},
    ]
    ok = all(c["ok"] for c in checks)
    out: dict[str, Any] = {
        "ok": ok,
        "status": "ok" if ok else "error",
        "plugin_version": PLUGIN_VERSION,
        "db_user_version": user_version,
        "expected_schema_version": _SCHEMA_VERSION,
        "checks": checks,
    }
    if include_details:
        out["migration_history"] = history
        out["tables"] = sorted(tables)
        out["sqlite_vec"] = vec
    run_id = new_id("upgrade")
    conn.execute(
        "INSERT INTO upgrade_runs(id, owner_kind, owner_id, status, plugin_version, db_user_version, expected_schema_version, checks_json) VALUES(?,?,?,?,?,?,?,?)",
        (run_id, owner_kind, owner_id, out["status"], PLUGIN_VERSION, user_version, _SCHEMA_VERSION, dumps(checks)),
    )
    out["upgrade_run_id"] = run_id
    if write_audit:
        append_audit(conn, owner_kind, owner_id, "life_upgrade_check", "info" if ok else "error", f"LifeEngine upgrade check status={out['status']}", out)
    return out


def backup_database(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, reason: str = "manual", destination: str | None = None) -> dict[str, Any]:
    """Create a consistent SQLite backup using sqlite3.Connection.backup()."""
    backup_dir = Path(destination).expanduser() if destination else exports_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"lifeengine-{_utc_stamp()}.db"
    # Ensure WAL content is visible to the backup API and persisted.
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    except Exception:
        pass
    dest = sqlite3.connect(str(backup_path))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    size = backup_path.stat().st_size if backup_path.exists() else 0
    backup_id = new_id("backup")
    conn.execute(
        "INSERT INTO db_backups(id, owner_kind, owner_id, backup_path, size_bytes, reason, status) VALUES(?,?,?,?,?,?,?)",
        (backup_id, owner_kind, owner_id, str(backup_path), size, reason, "ok" if size > 0 else "warning"),
    )
    out = {"ok": size > 0, "backup_id": backup_id, "backup_path": str(backup_path), "size_bytes": size, "reason": reason}
    append_audit(conn, owner_kind, owner_id, "life_backup", "info" if out["ok"] else "warning", f"LifeEngine DB backup created: {backup_path}", out)
    return out


def list_backups(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM db_backups WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "backups": [dict(r) for r in rows]}


def rebuild_memory_indexes(conn: sqlite3.Connection, owner_kind: str, owner_id: str) -> dict[str, Any]:
    """Rebuild FTS5 and sqlite-vec memory indexes for a workspace."""
    rows = conn.execute(
        "SELECT rowid, id, content FROM memories WHERE owner_kind=? AND owner_id=? ORDER BY rowid",
        (owner_kind, owner_id),
    ).fetchall()
    rowids = [int(r["rowid"]) for r in rows]
    for rowid in rowids:
        conn.execute("DELETE FROM memory_fts WHERE memory_rowid=?", (rowid,))
        conn.execute("DELETE FROM memory_vec WHERE rowid=?", (rowid,))
    for r in rows:
        rowid = int(r["rowid"])
        content = r["content"]
        conn.execute(
            "INSERT INTO memory_fts(memory_rowid, owner_kind, owner_id, content) VALUES(?,?,?,?)",
            (rowid, owner_kind, owner_id, content),
        )
        conn.execute(
            "INSERT INTO memory_vec(rowid, embedding) VALUES(?, ?)",
            (rowid, serialize_embedding(embed_text(content))),
        )
    out = {"ok": True, "rebuilt_memories": len(rows)}
    run_id = new_id("maint")
    conn.execute(
        "INSERT INTO maintenance_runs(id, owner_kind, owner_id, action, status, output_json) VALUES(?,?,?,?,?,?)",
        (run_id, owner_kind, owner_id, "rebuild_memory_indexes", "ok", dumps(out)),
    )
    out["maintenance_run_id"] = run_id
    append_audit(conn, owner_kind, owner_id, "life_rebuild_memory_indexes", "info", f"Rebuilt {len(rows)} memory indexes", out)
    return out


def list_maintenance_runs(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM maintenance_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "maintenance_runs": [dict(r) for r in rows]}



def _subprocess_env() -> dict[str, str]:
    """Return a clean child env for heartbeat smoke tests.

    Pytest exposes PYTEST_CURRENT_TEST to subprocesses; some host/plugin stacks
    alter behavior when that variable is present.  Cron scripts should run like
    real no-agent cron jobs, so strip pytest-only variables while preserving
    HERMES_HOME and user configuration.
    """
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST_"):
            env.pop(key, None)
    return env

def run_tick_script_test(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, script_path: str, timeout: int = 30) -> dict[str, Any]:
    """Run the generated heartbeat script once and record the result."""
    path = Path(script_path).expanduser()
    if not path.exists():
        out = {"ok": False, "status": "error", "error": f"script not found: {path}", "script_path": str(path)}
        return _record_cron_test(conn, owner_kind, owner_id, out, mode="script_test")
    proc = subprocess.run([sys.executable, str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env={**_subprocess_env(), "HERMES_HOME": str(hermes_home())})
    out = {
        "ok": proc.returncode == 0,
        "status": "ok" if proc.returncode == 0 else "error",
        "script_path": str(path),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    return _record_cron_test(conn, owner_kind, owner_id, out, mode="script_test")


def _record_cron_test(conn: sqlite3.Connection, owner_kind: str, owner_id: str, out: dict[str, Any], *, mode: str) -> dict[str, Any]:
    test_id = new_id("crontest")
    conn.execute(
        "INSERT INTO cron_heartbeat_tests(id, owner_kind, owner_id, mode, status, script_path, returncode, stdout, stderr) VALUES(?,?,?,?,?,?,?,?,?)",
        (test_id, owner_kind, owner_id, mode, out.get("status", "unknown"), out.get("script_path"), out.get("returncode"), out.get("stdout"), out.get("stderr")),
    )
    out["cron_test_id"] = test_id
    append_audit(conn, owner_kind, owner_id, "life_cron_heartbeat_test", "info" if out.get("ok") else "error", f"Heartbeat cron script test status={out.get('status')}", out)
    return out

# ---------------------------------------------------------------------------
# v0.9.4 export / import / package-manifest helpers
# ---------------------------------------------------------------------------

import hashlib
import tempfile
import zipfile


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _is_release_file(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = set(rel.parts)
    if "__pycache__" in parts or ".pytest_cache" in parts:
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    if rel.parts and rel.parts[0] in {".git", ".mypy_cache", ".ruff_cache"}:
        return False
    return path.is_file()


def build_package_manifest(root: str | None = None) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve() if root else _package_root().resolve()
    files: list[dict[str, Any]] = []
    for path in sorted(root_path.rglob("*")):
        if not _is_release_file(path, root_path):
            continue
        rel = path.relative_to(root_path).as_posix()
        stat = path.stat()
        files.append({"path": rel, "size": stat.st_size, "sha256": _sha256_path(path)})
    manifest = {
        "format": "lifeengine-package-manifest-v1",
        "plugin_version": PLUGIN_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "root_path": str(root_path),
        "file_count": len(files),
        "total_bytes": sum(int(f["size"]) for f in files),
        "files": files,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    manifest["manifest_sha256"] = _sha256_bytes(payload)
    return manifest


def record_package_manifest(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, root: str | None = None) -> dict[str, Any]:
    manifest = build_package_manifest(root)
    status = "ok" if manifest.get("file_count", 0) > 0 else "warning"
    manifest_id = new_id("pkgmanifest")
    conn.execute(
        "INSERT INTO package_manifests(id, owner_kind, owner_id, plugin_version, root_path, file_count, total_bytes, manifest_sha256, manifest_json, status) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            manifest_id,
            owner_kind,
            owner_id,
            PLUGIN_VERSION,
            manifest["root_path"],
            int(manifest["file_count"]),
            int(manifest["total_bytes"]),
            manifest["manifest_sha256"],
            dumps(manifest),
            status,
        ),
    )
    out = {"ok": status == "ok", "status": status, "package_manifest_id": manifest_id, **{k: v for k, v in manifest.items() if k != "files"}, "files_sample": manifest["files"][:10]}
    append_audit(conn, owner_kind, owner_id, "life_package_manifest", "info" if out["ok"] else "warning", f"Package manifest generated: {manifest['file_count']} files", out)
    return out


def _copy_db_backup(conn: sqlite3.Connection, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    except Exception:
        pass
    dest = sqlite3.connect(str(dest_path))
    try:
        conn.backup(dest)
    finally:
        dest.close()


def export_profile_archive(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, destination: str | None = None, include_package_manifest: bool = True) -> dict[str, Any]:
    export_dir = Path(destination).expanduser() if destination else exports_dir() / "profile_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_id = new_id("export")
    stamp = _utc_stamp()
    tmp_db = export_dir / f"{export_id}-lifeengine.db"
    archive_path = export_dir / f"lifeengine-profile-{stamp}-{export_id}.zip"
    _copy_db_backup(conn, tmp_db)
    db_sha = _sha256_path(tmp_db)
    package_manifest = build_package_manifest() if include_package_manifest else None
    manifest: dict[str, Any] = {
        "format": "lifeengine-profile-export-v1",
        "export_id": export_id,
        "plugin_version": PLUGIN_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "db_filename": "lifeengine.db",
        "db_sha256": db_sha,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if package_manifest:
        manifest["package_manifest"] = {k: v for k, v in package_manifest.items() if k != "files"}
        manifest["package_files"] = package_manifest.get("files", [])
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    manifest_sha = _sha256_bytes(manifest_bytes)
    manifest["manifest_sha256"] = manifest_sha
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp_db, "lifeengine.db")
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("checksums.sha256", f"{db_sha}  lifeengine.db\n{_sha256_bytes(manifest_bytes)}  manifest.json\n")
    try:
        tmp_db.unlink()
    except Exception:
        pass
    size = archive_path.stat().st_size
    conn.execute(
        "INSERT INTO profile_exports(id, owner_kind, owner_id, export_path, db_sha256, manifest_sha256, size_bytes, status, manifest_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (export_id, owner_kind, owner_id, str(archive_path), db_sha, _sha256_bytes(manifest_bytes), size, "ok", dumps(manifest)),
    )
    out = {"ok": True, "export_id": export_id, "export_path": str(archive_path), "size_bytes": size, "db_sha256": db_sha, "manifest_sha256": _sha256_bytes(manifest_bytes), "manifest": {k: v for k, v in manifest.items() if k not in {"package_files"}}}
    append_audit(conn, owner_kind, owner_id, "life_export_profile", "info", f"LifeEngine profile export created: {archive_path}", out)
    return out


def list_profile_exports(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM profile_exports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "exports": [dict(r) for r in rows]}


def inspect_profile_export(archive_path: str) -> dict[str, Any]:
    path = Path(archive_path).expanduser()
    if not path.exists():
        return {"ok": False, "status": "error", "error": f"export archive not found: {path}"}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names or "lifeengine.db" not in names:
                return {"ok": False, "status": "error", "error": "archive must contain manifest.json and lifeengine.db", "names": sorted(names)}
            manifest_bytes = zf.read("manifest.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            db_bytes = zf.read("lifeengine.db")
            db_sha = _sha256_bytes(db_bytes)
            # manifest_sha256 is self-descriptive: it is computed over the
            # manifest payload with the manifest_sha256 field omitted.  This
            # avoids an impossible self-referential fixed point while still
            # protecting all substantive fields.
            manifest_no_self = dict(manifest)
            expected_manifest_sha = manifest_no_self.pop("manifest_sha256", None)
            manifest_canonical = json.dumps(manifest_no_self, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            manifest_sha = _sha256_bytes(manifest_canonical)
            expected_db_sha = manifest.get("db_sha256")
            ok = (expected_db_sha in {None, db_sha}) and (expected_manifest_sha in {None, manifest_sha})
            return {
                "ok": bool(ok),
                "status": "ok" if ok else "error",
                "archive_path": str(path),
                "size_bytes": path.stat().st_size,
                "manifest": manifest,
                "db_sha256": db_sha,
                "manifest_sha256": manifest_sha,
                "checks": [
                    {"name": "db_sha256", "ok": expected_db_sha in {None, db_sha}, "expected": expected_db_sha, "actual": db_sha},
                    {"name": "manifest_sha256", "ok": expected_manifest_sha in {None, manifest_sha}, "expected": expected_manifest_sha, "actual": manifest_sha},
                ],
            }
    except Exception as exc:
        return {"ok": False, "status": "error", "archive_path": str(path), "error": f"{type(exc).__name__}: {exc}"}


def stage_profile_import(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, archive_path: str) -> dict[str, Any]:
    inspected = inspect_profile_export(archive_path)
    import_id = new_id("import")
    staging = exports_dir() / "imports" / import_id
    if inspected.get("ok"):
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(Path(archive_path).expanduser(), "r") as zf:
            zf.extract("lifeengine.db", staging)
            zf.extract("manifest.json", staging)
        status = "staged"
        notes = "staged for offline restore"
    else:
        status = "failed"
        notes = inspected.get("error", "inspection failed")
    manifest = inspected.get("manifest") or {}
    conn.execute(
        "INSERT INTO profile_imports(id, owner_kind, owner_id, archive_path, staging_dir, db_sha256, manifest_sha256, status, manifest_json, notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (import_id, owner_kind, owner_id, str(Path(archive_path).expanduser()), str(staging) if inspected.get("ok") else None, inspected.get("db_sha256"), inspected.get("manifest_sha256"), status, dumps(manifest), notes),
    )
    out = {"ok": bool(inspected.get("ok")), "import_id": import_id, "status": status, "staging_dir": str(staging) if inspected.get("ok") else None, "inspection": inspected, "notes": notes}
    append_audit(conn, owner_kind, owner_id, "life_import_profile", "info" if out["ok"] else "error", f"Profile import {status}: {archive_path}", out)
    return out


def stage_restore_plan(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, archive_path: str) -> dict[str, Any]:
    """Stage a restore but do not replace the running DB file.

    Replacing an open WAL-backed SQLite database from inside a running Hermes
    plugin is unsafe.  v0.9.4 therefore stages the DB and returns explicit
    offline instructions.  A future standalone `hermes lifeengine restore`
    command can perform the replace before opening LifeEngine.
    """
    staged_import = stage_profile_import(conn, owner_kind, owner_id, archive_path=archive_path)
    restore_id = new_id("restore")
    if not staged_import.get("ok"):
        return {"ok": False, "status": "failed", "restore_id": restore_id, "import": staged_import}
    staged_db = Path(staged_import["staging_dir"]) / "lifeengine.db"
    current = db_path()
    backup = backup_database(conn, owner_kind, owner_id, reason="pre staged restore backup")
    instructions = (
        "Stop Hermes/gateway first. Then replace the current DB with the staged DB, e.g.\n"
        f"  cp {staged_db} {current}\n"
        "Then restart Hermes and run `/life doctor` and `/life trace verify`."
    )
    conn.execute(
        "INSERT INTO restore_staging(id, owner_kind, owner_id, import_id, staged_db_path, current_db_path, pre_restore_backup_path, status, instructions) VALUES(?,?,?,?,?,?,?,?,?)",
        (restore_id, owner_kind, owner_id, staged_import["import_id"], str(staged_db), str(current), backup.get("backup_path"), "staged", instructions),
    )
    out = {"ok": True, "status": "staged", "restore_id": restore_id, "import": staged_import, "staged_db_path": str(staged_db), "current_db_path": str(current), "pre_restore_backup": backup, "instructions": instructions}
    append_audit(conn, owner_kind, owner_id, "life_restore_staged", "warning", "Restore staged; offline DB replacement required", out)
    return out


def verify_memory_indexes(conn: sqlite3.Connection, owner_kind: str, owner_id: str) -> dict[str, Any]:
    memories = conn.execute("SELECT rowid FROM memories WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)).fetchall()
    mem_rowids = {int(r["rowid"]) for r in memories}
    fts_rows = conn.execute("SELECT memory_rowid FROM memory_fts WHERE owner_kind=? AND owner_id=?", (owner_kind, owner_id)).fetchall()
    fts_rowids = {int(r["memory_rowid"]) for r in fts_rows}
    vec_rows = conn.execute("SELECT rowid FROM memory_vec").fetchall()
    vec_rowids = {int(r["rowid"]) for r in vec_rows if int(r["rowid"]) in mem_rowids}
    missing_fts = sorted(mem_rowids - fts_rowids)
    missing_vec = sorted(mem_rowids - vec_rowids)
    extra_fts = sorted(fts_rowids - mem_rowids)
    ok = not missing_fts and not missing_vec and not extra_fts
    out = {
        "ok": ok,
        "status": "ok" if ok else "warning",
        "memory_count": len(mem_rowids),
        "fts_count": len(fts_rowids),
        "vec_count": len(vec_rowids),
        "missing_fts": missing_fts[:50],
        "missing_vec": missing_vec[:50],
        "extra_fts": extra_fts[:50],
    }
    run_id = new_id("maint")
    conn.execute(
        "INSERT INTO maintenance_runs(id, owner_kind, owner_id, action, status, output_json) VALUES(?,?,?,?,?,?)",
        (run_id, owner_kind, owner_id, "verify_memory_indexes", out["status"], dumps(out)),
    )
    out["maintenance_run_id"] = run_id
    append_audit(conn, owner_kind, owner_id, "life_verify_memory_indexes", "info" if ok else "warning", f"Memory index verification status={out['status']}", out)
    return out


def large_db_smoke(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, memories: int = 250) -> dict[str, Any]:
    """Run a bounded maintenance smoke test in a temporary LifeEngine DB."""
    with tempfile.TemporaryDirectory(prefix="lifeengine-smoke-") as td:
        from .db import connect as _connect
        tmp_path = Path(td) / "lifeengine-smoke.db"
        tconn = _connect(tmp_path)
        try:
            smoke_owner_kind = "agent"
            smoke_owner_id = "smoke-agent"
            for i in range(int(memories)):
                content = f"smoke memory {i} for LifeEngine maintenance test"
                tconn.execute(
                    "INSERT INTO memories(id, owner_kind, owner_id, memory_type, content, importance, source, confidence) VALUES(?,?,?,?,?,?,?,?)",
                    (f"smokemem_{i}", smoke_owner_kind, smoke_owner_id, "episodic", content, 1, "system", 1.0),
                )
            tconn.commit()
            rebuilt = rebuild_memory_indexes(tconn, smoke_owner_kind, smoke_owner_id)
            verified = verify_memory_indexes(tconn, smoke_owner_kind, smoke_owner_id)
            exported = export_profile_archive(tconn, smoke_owner_kind, smoke_owner_id, destination=str(Path(td) / "exports"), include_package_manifest=False)
        finally:
            tconn.close()
    out = {"ok": bool(rebuilt.get("ok") and verified.get("ok") and exported.get("ok")), "status": "ok", "memories": int(memories), "rebuilt": rebuilt, "verified": verified, "exported": {k: v for k, v in exported.items() if k != "manifest"}}
    if not out["ok"]:
        out["status"] = "warning"
    run_id = new_id("maint")
    conn.execute(
        "INSERT INTO maintenance_runs(id, owner_kind, owner_id, action, status, output_json) VALUES(?,?,?,?,?,?)",
        (run_id, owner_kind, owner_id, "large_db_smoke", out["status"], dumps(out)),
    )
    out["maintenance_run_id"] = run_id
    append_audit(conn, owner_kind, owner_id, "life_large_db_smoke", "info" if out["ok"] else "warning", f"Large DB smoke status={out['status']}", out)
    return out
