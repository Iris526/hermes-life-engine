"""多 Agent 注册表与薄 tick 循环。

本模块只负责发现已经有 active Canon 且运行态为 active 的 agent，并把现有
`LifeEngineRuntime.tick()` 按 owner 逐个调用。它不生成内容、不规划关系，也不改变
单 agent 的 tick 语义；cron 或测试只把它当成多 agent 调度入口。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .jsonutil import loads


def _conn_from_runtime_or_conn(rt_or_conn: Any) -> sqlite3.Connection:
    """从 runtime 或原生连接中取出 SQLite connection。

    输入可以是 `LifeEngineRuntime` 或 sqlite3 connection；输出是现有连接对象。
    调用方是 registry 查询和 `tick_all_active`。函数只读对象属性，不打开新库；
    失败时抛出 TypeError，避免 cron 在未知对象上静默跳过。
    """
    if isinstance(rt_or_conn, sqlite3.Connection):
        return rt_or_conn
    conn = getattr(rt_or_conn, "conn", None)
    if isinstance(conn, sqlite3.Connection):
        return conn
    raise TypeError("rt_or_conn must be a sqlite3.Connection or LifeEngineRuntime-like object")


def _runtime_from_runtime_or_conn(rt_or_conn: Any) -> Any:
    """把输入统一成可调用 `.tick(...)` 的 runtime。

    输入可以是已有 runtime，也可以是已经迁移好的连接。输出复用同一连接的轻量
    runtime 代理，调用方是 `tick_all_active`。函数不拥有连接生命周期，因此不会
    close；tick 内部副作用完全来自既有单 agent tick 实现。
    """
    if hasattr(rt_or_conn, "tick") and hasattr(rt_or_conn, "conn"):
        return rt_or_conn
    conn = _conn_from_runtime_or_conn(rt_or_conn)
    from .runtime import LifeEngineRuntime

    runtime = LifeEngineRuntime.__new__(LifeEngineRuntime)
    runtime.conn = conn
    return runtime


def active_agents(conn) -> list[dict[str, Any]]:
    """列出当前可被多 Agent cron 调度的 active agent。

    输入是 LifeEngine SQLite connection；输出是 `{owner_kind, owner_id, name,
    engine_state}` 列表。来源只看 committed active Canon 与 controls.engine_state；
    只有 owner_kind='agent'、Canon status='active' 且 engine_state='active' 的 owner
    会被返回。函数只读数据库，供 cron、测试和后续 constellation 调度入口使用。
    """
    rows = conn.execute(
        """SELECT cv.owner_kind, cv.owner_id, cv.data_json, c.engine_state
             FROM canon_versions cv
             JOIN controls c
               ON c.owner_kind=cv.owner_kind
              AND c.owner_id=cv.owner_id
              AND c.active_canon_version=cv.version
            WHERE cv.owner_kind='agent'
              AND cv.status='active'
              AND c.engine_state='active'
            ORDER BY cv.owner_id"""
    ).fetchall()
    agents: list[dict[str, Any]] = []
    for row in rows:
        canon = loads(row["data_json"], {})
        identity = canon.get("identity") if isinstance(canon, dict) else {}
        name = (identity or {}).get("name") or row["owner_id"]
        agents.append({
            "owner_kind": row["owner_kind"],
            "owner_id": row["owner_id"],
            "name": name,
            "engine_state": row["engine_state"],
        })
    return agents


def tick_all_active(rt_or_conn, *, now: str | None = None, manual: bool = False) -> dict[str, Any]:
    """按现有单 agent tick 路径轮询所有 active agent。

    输入是 runtime 或连接，以及可选逻辑时间和 manual 标志；输出包含每个 agent 的
    tick 摘要。调用方式是 cron/测试显式触发。副作用与 `LifeEngineRuntime.tick`
    完全一致，本函数只负责发现 active agents 并逐个调用；单个 agent 失败会记录在
    返回值中，后续 agent 仍继续，避免一个坏 owner 阻断整个 constellation tick。
    """
    runtime = _runtime_from_runtime_or_conn(rt_or_conn)
    conn = _conn_from_runtime_or_conn(runtime)
    agents = active_agents(conn)
    results: dict[str, Any] = {}
    for agent in agents:
        owner_id = str(agent["owner_id"])
        try:
            results[owner_id] = runtime.tick(
                owner_kind=agent["owner_kind"],
                owner_id=owner_id,
                now=now,
                manual=manual,
            )
        except Exception as exc:
            results[owner_id] = {
                "ok": False,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
    return {"ok": all(bool(item.get("ok", False)) for item in results.values()), "agents": agents, "results": results}
