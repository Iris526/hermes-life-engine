"""The avatar is a pluggable skin: LifeEngine ships a default character (明灯)
but a host can override it by dropping files into
$HERMES_HOME/lifeengine/avatar/ — no code change. The engine itself does not
depend on any specific character."""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("sqlite_vec")
from fastapi.testclient import TestClient

from lifeengine.webui.server import create_app


def _life_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    life = tmp_path / "lifeengine"
    life.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(life / "lifeengine.db").close()  # bare DB is enough; avatar endpoint never touches it
    return life


def test_bundled_default_skin_served(tmp_path, monkeypatch):
    life = _life_dir(tmp_path, monkeypatch)
    c = TestClient(create_app(life_dir=str(life)))
    r = c.get("/api/avatar/sprite-idle.webp")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert len(r.content) > 1000  # real bundled image


def test_host_override_wins(tmp_path, monkeypatch):
    life = _life_dir(tmp_path, monkeypatch)
    avatar = life / "avatar"; avatar.mkdir()
    (avatar / "sprite-idle.webp").write_bytes(b"HOST_SKIN_OVERRIDE")
    c = TestClient(create_app(life_dir=str(life)))
    r = c.get("/api/avatar/sprite-idle.webp")
    assert r.status_code == 200 and r.content == b"HOST_SKIN_OVERRIDE"
    # a file with no override still falls back to the bundled default
    r2 = c.get("/api/avatar/sprite-work.webp")
    assert r2.status_code == 200 and len(r2.content) > 1000


def test_avatar_path_traversal_blocked(tmp_path, monkeypatch):
    life = _life_dir(tmp_path, monkeypatch)
    c = TestClient(create_app(life_dir=str(life)))
    assert c.get("/api/avatar/sprite-nope.webp").status_code == 404
    assert c.get("/api/avatar/notes.txt").status_code == 404  # disallowed extension
