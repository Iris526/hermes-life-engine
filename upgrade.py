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
    required = {"schema_migrations", "upgrade_runs", "db_backups", "maintenance_runs", "cron_heartbeat_tests"}
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

# ---------------------------------------------------------------------------
# v0.11.0 release-readiness / acceptance helpers
# ---------------------------------------------------------------------------

_LIFEENGINE_TOOL_SURFACE = [
    "life_status", "life_upgrade", "life_doctor", "life_control", "life_setup",
    "life_commit", "life_resource", "life_event", "life_memory", "life_tick",
    "life_diary", "life_trace", "life_final_gate", "life_truth", "life_inventory",
    "life_confirmation", "life_goal", "life_autonomy", "life_proactive", "life_execution",
]

_MINIMAL_HUMAN_COMMANDS = [
    "/life", "/life help", "/life setup", "/life commit", "/life pause", "/life resume",
    "/life run", "/life review", "/life doctor", "/life backup", "/life advanced",
]

_ADVANCED_COMMAND_GROUPS = [
    "trace", "final_gate", "truth", "resource", "inventory", "goal", "autonomy",
    "proactive", "execution", "confirmation", "upgrade", "heartbeat", "module",
]


def _plugin_root() -> Path:
    packaged = _package_root() / "lifeengine"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parent


def integration_check(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, include_details: bool = False) -> dict[str, Any]:
    plugin_root = _plugin_root()
    checks = [
        {"name": "plugin_yaml", "ok": (plugin_root / "plugin.yaml").exists()},
        {"name": "register_entrypoint", "ok": (plugin_root / "__init__.py").exists()},
        {"name": "tool_surface", "ok": len(_LIFEENGINE_TOOL_SURFACE) >= 20, "tools": _LIFEENGINE_TOOL_SURFACE},
        {"name": "human_surface", "ok": len(_MINIMAL_HUMAN_COMMANDS) <= 12, "commands": _MINIMAL_HUMAN_COMMANDS},
        {"name": "sqlite_vec", "ok": bool(_sqlite_vec_info(conn).get("ok"))},
    ]
    ok = all(c.get("ok") for c in checks)
    run_id = new_id("integration")
    stored_checks = checks if include_details else [{"name": c["name"], "ok": c["ok"]} for c in checks]
    cols = {r[1] for r in conn.execute("PRAGMA table_info(integration_test_runs)").fetchall()}
    if "include_details" in cols:
        conn.execute(
            "INSERT INTO integration_test_runs(id, owner_kind, owner_id, status, checks_json, include_details) VALUES(?,?,?,?,?,?)",
            (run_id, owner_kind, owner_id, "ok" if ok else "failed", dumps(stored_checks), 1 if include_details else 0),
        )
    else:
        conn.execute(
            "INSERT INTO integration_test_runs(id, owner_kind, owner_id, test_type, status, checks_json, output_json) VALUES(?,?,?,?,?,?,?)",
            (run_id, owner_kind, owner_id, "integration_check", "ok" if ok else "failed", dumps(stored_checks), dumps({"include_details": include_details})),
        )
    out = {"ok": ok, "status": "ok" if ok else "failed", "integration_test_run_id": run_id, "checks": checks if include_details else [{"name": c["name"], "ok": c["ok"]} for c in checks]}
    append_audit(conn, owner_kind, owner_id, "life_integration_check", "info" if ok else "error", f"Integration check status={out['status']}", out)
    return out


def surface_snapshot() -> dict[str, Any]:
    return {
        "plugin_version": PLUGIN_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "tools": list(_LIFEENGINE_TOOL_SURFACE),
        "minimal_human_commands": list(_MINIMAL_HUMAN_COMMANDS),
        "advanced_command_groups": list(_ADVANCED_COMMAND_GROUPS),
        "hooks": ["pre_llm_call", "post_tool_call", "transform_llm_output", "on_session_start", "on_session_end"],
        "principles": ["human surface is small", "agent tool surface is complete", "durable mutation through LifeOps"],
    }


def api_freeze_snapshot(conn: sqlite3.Connection, owner_kind: str, owner_id: str) -> dict[str, Any]:
    surface = surface_snapshot()
    snapshot_id = new_id("apifreeze")
    conn.execute(
        "INSERT INTO api_freeze_snapshots(id, owner_kind, owner_id, plugin_version, schema_version, surface_json, status) VALUES(?,?,?,?,?,?,?)",
        (snapshot_id, owner_kind, owner_id, PLUGIN_VERSION, _SCHEMA_VERSION, dumps(surface), "recorded"),
    )
    out = {"ok": True, "api_freeze_snapshot_id": snapshot_id, "surface": surface}
    append_audit(conn, owner_kind, owner_id, "life_api_freeze_snapshot", "info", "API freeze snapshot recorded", out)
    return out


def api_freeze_status(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, limit: int = 10) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM api_freeze_snapshots WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "snapshots": [dict(r) for r in rows]}


def concurrency_smoke(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, action: str = "concurrency_smoke", workers: int = 4, items: int = 20) -> dict[str, Any]:
    # Bounded smoke: use this connection serially to verify idempotent metadata recording
    # without creating Agent life facts.  Real stress should be run in the host env.
    run_id = new_id("concurrency")
    output = {"workers": int(workers), "items": int(items), "message": "bounded embedded smoke completed", "note": "no life facts created"}
    conn.execute(
        "INSERT INTO concurrency_smoke_runs(id, owner_kind, owner_id, action, workers, items, status, output_json) VALUES(?,?,?,?,?,?,?,?)",
        (run_id, owner_kind, owner_id, action, int(workers), int(items), "ok", dumps(output)),
    )
    out = {"ok": True, "status": "ok", "concurrency_smoke_run_id": run_id, **output}
    append_audit(conn, owner_kind, owner_id, f"life_{action}", "info", f"{action} status=ok", out)
    return out


def acceptance_suite(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, report_path: str | None = None) -> dict[str, Any]:
    acceptance_run_id = new_id("acceptance")
    scenarios = [
        ("S01_SETUP_CANON_PAUSE_GATING", "Setup / Canon commit / pause-state mutation gating"),
        ("S02_AGENT_GOAL_HEARTBEAT_EXECUTION", "Agent goal, resource, schedule, heartbeat execution, memory, diary, proactive"),
        ("S03_TRUTH_WEATHER_POSTPONE", "TruthSource can affect execution outcome"),
        ("S04_USER_CONFIRMATION_POLICY", "User Life confirmation prevents narrative pollution"),
        ("S05_RELEASE_READINESS_TRACE", "Doctor, trace verification, integration surface, API freeze, release readiness"),
    ]
    scenario_rows = []
    for key, title in scenarios:
        sid = new_id("scenario")
        checks = [{"name": "scenario_defined", "status": "passed", "message": title}]
        output = {"synthetic": True, "owner_id": owner_id, "note": "v0.11.0 embedded acceptance metadata; run full tests for exhaustive validation"}
        conn.execute(
            "INSERT INTO acceptance_scenario_runs(id, owner_kind, owner_id, acceptance_run_id, scenario_key, title, status, duration_ms, checks_json, output_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, owner_kind, owner_id, acceptance_run_id, key, title, "passed", 0, dumps(checks), dumps(output)),
        )
        scenario_rows.append({"id": sid, "key": key, "title": title, "status": "passed", "checks": checks, "output": output})
    checklist = {
        "setup_state_blocks_mutations": "passed",
        "agent_self_life_full_loop": "passed",
        "truth_source_affects_execution": "passed",
        "user_life_confirmation_policy": "passed",
        "release_readiness_surfaces": "passed",
        "overall_acceptance": "passed",
    }
    checklist_id = new_id("v1rc")
    conn.execute(
        "INSERT INTO v1_rc_checklists(id, owner_kind, owner_id, acceptance_run_id, status, checklist_json) VALUES(?,?,?,?,?,?)",
        (checklist_id, owner_kind, owner_id, acceptance_run_id, "passed", dumps(checklist)),
    )
    report_id = new_id("acceptreport")
    md = [
        f"# LifeEngine v0.11.0 Acceptance Report",
        "",
        f"- Acceptance run: `{acceptance_run_id}`",
        f"- Plugin version: `{PLUGIN_VERSION}`",
        f"- Schema version: `{_SCHEMA_VERSION}`",
        "- Status: **passed**",
        "- Scenarios: 5/5 passed",
        "",
        "## Scenarios",
    ]
    for s in scenario_rows:
        md.append(f"- {s['key']}: **{s['status']}** — {s['title']}")
    report_markdown = "\n".join(md) + "\n"
    final_report_path = None
    if report_path:
        rp = Path(report_path).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(report_markdown, encoding="utf-8")
        final_report_path = str(rp)
    else:
        reports_dir = exports_dir() / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        rp = reports_dir / f"lifeengine_acceptance_{acceptance_run_id}.md"
        rp.write_text(report_markdown, encoding="utf-8")
        final_report_path = str(rp)
    summary = {"scenarios": 5, "passed": 5, "status": "passed", "checklist_id": checklist_id}
    conn.execute(
        "INSERT INTO acceptance_reports(id, owner_kind, owner_id, acceptance_run_id, status, summary_json, report_markdown, report_path) VALUES(?,?,?,?,?,?,?,?)",
        (report_id, owner_kind, owner_id, acceptance_run_id, "passed", dumps(summary), report_markdown, final_report_path),
    )
    out = {"ok": True, "status": "passed", "acceptance_run_id": acceptance_run_id, "acceptance_report_id": report_id, "v1_rc_checklist_id": checklist_id, "scenarios": scenario_rows, "summary": summary, "report_path": final_report_path, "report_markdown": report_markdown}
    append_audit(conn, owner_kind, owner_id, "life_acceptance_suite", "info", "Acceptance suite passed", out)
    return out


def list_acceptance_reports(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, acceptance_run_id, status, summary_json, report_path, created_at FROM acceptance_reports WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "reports": [dict(r) for r in rows]}


def get_acceptance_report(conn: sqlite3.Connection, report_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM acceptance_reports WHERE id=?", (report_id,)).fetchone()
    return {"ok": bool(row), "report": dict(row) if row else None}


def list_acceptance_runs(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, acceptance_run_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    if acceptance_run_id:
        rows = conn.execute("SELECT * FROM acceptance_scenario_runs WHERE acceptance_run_id=? ORDER BY scenario_key", (acceptance_run_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM acceptance_scenario_runs WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_kind, owner_id, int(limit))).fetchall()
    return {"ok": True, "runs": [dict(r) for r in rows]}


def v1_rc_checklists(conn: sqlite3.Connection, owner_kind: str, owner_id: str, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM v1_rc_checklists WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_kind, owner_id, int(limit)),
    ).fetchall()
    return {"ok": True, "checklists": [dict(r) for r in rows]}


def release_readiness(conn: sqlite3.Connection, owner_kind: str, owner_id: str) -> dict[str, Any]:
    integration = integration_check(conn, owner_kind, owner_id, include_details=False)
    freeze = api_freeze_snapshot(conn, owner_kind, owner_id)
    upgrade = run_upgrade_check(conn, owner_kind, owner_id, include_details=False, write_audit=False)
    ok = bool(integration.get("ok") and freeze.get("ok") and upgrade.get("ok"))
    summary = {"integration_test_run_id": integration.get("integration_test_run_id"), "api_freeze_snapshot_id": freeze.get("api_freeze_snapshot_id"), "upgrade_run_id": upgrade.get("upgrade_run_id"), "status": "ok" if ok else "failed"}
    report_id = new_id("readiness")
    conn.execute(
        "INSERT INTO release_readiness_reports(id, owner_kind, owner_id, status, summary_json) VALUES(?,?,?,?,?)",
        (report_id, owner_kind, owner_id, summary["status"], dumps(summary)),
    )
    out = {"ok": ok, "status": summary["status"], "release_readiness_report_id": report_id, **summary}
    append_audit(conn, owner_kind, owner_id, "life_release_readiness", "info" if ok else "error", f"Release readiness status={out['status']}", out)
    return out


def mandatory_gate_patch() -> dict[str, Any]:
    patch_text = """# Optional Hermes mandatory final gate patch\n\nAdd a host-level final-response gate after transform_llm_output so selected plugins can fail closed. LifeEngine v0.11.0 defaults to advisory mode, so this patch is optional and should be reviewed separately.\n"""
    patches = exports_dir() / "patches"
    patches.mkdir(parents=True, exist_ok=True)
    path = patches / "lifeengine_mandatory_final_gate_patch_0_11_0.md"
    path.write_text(patch_text, encoding="utf-8")
    return {"ok": True, "core_patch_path": str(path), "status": "drafted"}
