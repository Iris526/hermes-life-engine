"""WebUI reader surfaces durable goals and concrete daily rhythm."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from lifeengine.constants import DEFAULT_AGENT_ID
from lifeengine.paths import db_path
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.webui.reader import LifeEngineReader
from lifeengine.webui.server import create_app


def _seed_goals_and_rhythm(tmp_path: Path, monkeypatch) -> tuple[str, str]:
    """用 runtime 写入目标与日常节律，返回可供 read-only reader 打开的 DB 路径。

    输入是 pytest 临时目录和 monkeypatch；输出是 SQLite DB 路径与目标 id。调用方是
    本文件的 WebUI 读层测试。副作用仅限隔离 HERMES_HOME 下创建 LifeEngine 数据库，
    写入路径全部走 runtime/commit_ops，避免测试手工构造与 engine 合同漂移。
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes_home_goals_rhythm"))
    rt = LifeEngineRuntime()
    try:
        goal_commit = rt.goals(
            "create",
            title="完成河灯会的画摊委托",
            goal_type="creative_work",
            priority=86,
            progress=35,
            target_date="2030-01-05T23:00:00+08:00",
        )
        goal_id = goal_commit["results"][0]["result"]["id"]
        rt.commit_ops(
            [
                {
                    "type": "CREATE_GOAL_MILESTONE",
                    "payload": {
                        "goal_id": goal_id,
                        "title": "完成封面样张和摊位价目",
                        "due_at": "2030-01-04T18:00:00+08:00",
                        "status": "completed",
                    },
                }
            ],
            source="webui_reader_test",
        )
        rt.goals(
            "progress",
            goal_id=goal_id,
            progress_delta=12,
            reason="封面收尾完成，河灯会画摊清单已列出。",
            source="webui_reader_test",
        )
        rhythm = rt.living("day_rhythm", date="2030-01-02")
        assert rhythm["ok"] is True
        assert rhythm["event_ids"]
        return str(db_path()), goal_id
    finally:
        rt.close()


def test_reader_and_endpoints_surface_goals_and_daily_rhythm(tmp_path, monkeypatch) -> None:
    """验证 WebUI reader 与 HTTP endpoint 都按稳定 shape 暴露目标和日常节律。"""
    db, goal_id = _seed_goals_and_rhythm(tmp_path, monkeypatch)
    reader = LifeEngineReader(db)

    goals = reader.goals("agent", DEFAULT_AGENT_ID)
    assert goals == {
        "goals": [
            {
                "id": goal_id,
                "title": "完成河灯会的画摊委托",
                "status": "active",
                "kind": "creative_work",
                "priority": 86,
                "created_at": goals["goals"][0]["created_at"],
                "milestones": [
                    {
                        "id": goals["goals"][0]["milestones"][0]["id"],
                        "title": "完成封面样张和摊位价目",
                        "done": True,
                        "target_date": "2030-01-04T18:00:00+08:00",
                    }
                ],
                "progress": [
                    {
                        "id": goals["goals"][0]["progress"][0]["id"],
                        "note": "封面收尾完成，河灯会画摊清单已列出。",
                        "delta": 12.0,
                        "created_at": goals["goals"][0]["progress"][0]["created_at"],
                    }
                ],
            }
        ]
    }

    rhythm = reader.daily_rhythm("agent", DEFAULT_AGENT_ID)
    assert rhythm["date"] == "2030-01-02"
    assert rhythm["items"]
    assert rhythm["items"][0] == {
        "id": rhythm["items"][0]["id"],
        "title": "归明观晨巡与开观",
        "start": "2030-01-02T07:30:00+09:00",
        "end": "2030-01-02T08:05:00+09:00",
        "kind": "maintenance",
        "status": "planned",
        "note": None,
    }
    assert [item["start"] for item in rhythm["items"]] == sorted(item["start"] for item in rhythm["items"])

    client = TestClient(create_app(db))
    assert client.get("/api/goals").json() == goals
    assert client.get("/api/rhythm", params={"date": "2030-01-02"}).json() == rhythm
