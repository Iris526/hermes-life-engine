from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from lifeengine.paths import db_path
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.webui.reader import LifeEngineReader
from lifeengine.webui.server import create_app


def test_journal_changefeed_resource_delta_cursor_and_no_replay(tmp_path, monkeypatch):
    """验证 WebUI changefeed 读取真实 LifeOps 资源变更。

    输入是临时 Hermes home 中由 LifeEngineRuntime 提交的 RESOURCE_DELTA；输出断言
    reader 与 endpoint 都能返回 monotonic rowid cursor、resource category 与
    resource_key ref_id。调用方是 fast pytest suite；副作用只写测试临时 SQLite。
    第二次使用返回 cursor 读取时必须为空，确保前端不会重放旧动画。
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes_home_changefeed"))
    rt = LifeEngineRuntime()
    try:
        rt.setup("测试 Agent，资源变更用于 WebUI changefeed。")
        rt.commit_canon()
        rt.control("resume")
        rt.resources("define", key="energy", display_name="精力", initial=10)
        rt.resources("delta", resource_key="energy", delta=-3, operation="consume", reason="changefeed test")
    finally:
        rt.close()

    reader = LifeEngineReader(str(db_path()))
    first = reader.journal_changefeed("agent", "default-agent", since_rowid=0, limit=200)

    assert first["cursor"] > 0
    rowids = [event["rowid"] for event in first["events"]]
    assert rowids == sorted(rowids)
    assert first["cursor"] == rowids[-1]
    resource_events = [
        event for event in first["events"]
        if event["entry_type"] == "resource_delta" and event.get("ref_id") == "energy"
    ]
    assert resource_events
    assert resource_events[-1]["category"] == "resource"
    assert "energy" in resource_events[-1].get("summary", "")

    prime = reader.journal_changefeed("agent", "default-agent", since_rowid=0, limit=0)
    assert prime == {"cursor": first["cursor"], "events": []}

    again = reader.journal_changefeed("agent", "default-agent", since_rowid=first["cursor"], limit=200)
    assert again == {"cursor": first["cursor"], "events": []}

    client = TestClient(create_app(str(db_path())))
    response = client.get(
        "/api/changefeed",
        params={"owner_kind": "agent", "owner_id": "default-agent", "since": 0, "limit": 200},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["cursor"] == first["cursor"]
    assert any(event.get("category") == "resource" and event.get("ref_id") == "energy" for event in payload["events"])


def test_journal_changefeed_missing_life_journal_is_empty(tmp_path):
    """验证旧库缺少 life_journal 时 changefeed 降级为空。

    输入是一个没有 `life_journal` 表的最小 SQLite 文件；输出保持 `{cursor, events}`
    形状且 cursor 不倒退。调用方是 WebUI 旧库兼容测试；副作用只创建临时 DB。
    """
    db = tmp_path / "lifeengine.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE controls(owner_kind TEXT, owner_id TEXT)")

    assert LifeEngineReader(str(db)).journal_changefeed("agent", "iris", since_rowid=7) == {
        "cursor": 7,
        "events": [],
    }
