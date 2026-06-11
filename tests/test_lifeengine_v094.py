from __future__ import annotations

import json
import os
import shutil
import zipfile

from lifeengine.db import _SCHEMA_VERSION
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.tools import life_upgrade

def fresh_home(tmp_path):
    home = tmp_path / "hermes_home_v094"
    os.environ["HERMES_HOME"] = str(home)
    shutil.rmtree(home, ignore_errors=True)
    return home


def activate(rt: LifeEngineRuntime):
    rt.setup("v0.9.4 maintenance test Agent。")
    rt.commit_canon()
    rt.control("resume")


def test_v094_export_inspect_import_and_staged_restore(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        assert rt.conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION and _SCHEMA_VERSION >= 29
        activate(rt)
        # Make the export non-empty and ensure memory indexes can be packaged with the DB.
        rt.memory("remember", content="v0.9.4 export smoke memory", memory_type="episodic")
        export = rt.upgrade("export", destination=str(tmp_path / "exports"))
        assert export["ok"] is True
        archive = export["export_path"]
        with zipfile.ZipFile(archive, "r") as zf:
            assert {"lifeengine.db", "manifest.json", "checksums.sha256"}.issubset(set(zf.namelist()))
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["schema_version"] >= 29
            assert manifest["db_sha256"] == export["db_sha256"]
        inspected = rt.upgrade("inspect_export", archive_path=archive)
        assert inspected["ok"] is True
        imported = rt.upgrade("import", archive_path=archive)
        assert imported["ok"] is True
        assert imported["status"] == "staged"
        restore = rt.upgrade("restore", archive_path=archive)
        assert restore["ok"] is True
        assert restore["status"] == "staged"
        assert "Stop Hermes" in restore["instructions"]
        rows = rt.conn.execute("SELECT COUNT(*) FROM profile_exports").fetchone()[0]
        assert rows >= 1
        staged_rows = rt.conn.execute("SELECT COUNT(*) FROM restore_staging").fetchone()[0]
        assert staged_rows >= 1
    finally:
        rt.close()


def test_v094_package_manifest_and_index_verification(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        rt.memory("remember", content="index verification memory", memory_type="episodic")
        rebuilt = rt.upgrade("rebuild_memory")
        assert rebuilt["ok"] is True
        verified = rt.upgrade("verify_memory")
        assert verified["ok"] is True
        assert verified["memory_count"] >= 1
        package = rt.upgrade("package_check")
        assert package["ok"] is True
        assert package["file_count"] > 0
        assert package["manifest_sha256"]
        row_count = rt.conn.execute("SELECT COUNT(*) FROM package_manifests").fetchone()[0]
        assert row_count >= 1
    finally:
        rt.close()


def test_v094_large_smoke_and_tool_surface(tmp_path):
    fresh_home(tmp_path)
    rt = LifeEngineRuntime()
    try:
        activate(rt)
        smoke = rt.upgrade("large_smoke", memories=12)
        assert smoke["ok"] is True
        assert smoke["memories"] == 12
        assert smoke["verified"]["ok"] is True
    finally:
        rt.close()

    result = json.loads(life_upgrade({"action": "package_check"}))
    assert "ok" in result
