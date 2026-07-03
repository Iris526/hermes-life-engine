"""Agent 之间的 durable outbox 与 truth-layered delivery。

本模块实现轴五的数据通道基础：A 的讲述先进入 `inter_agent_outbox` 账本，delivery
再把它作为 B 世界里的 `rumor_unverified` 和 `peer_agent` 社会实体落地。它不调用
生成模型，不把跨 agent 讲述写成事实记忆；所有 B 世界业务写入都委托给既有
LifeOps/Social ops。
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from typing import Any

from .canon import ensure_control, get_active_canon
from .db import transaction
from .jsonutil import dumps, loads
from .trace import new_id


INTER_AGENT_TRUTH_LAYER = "rumor_unverified"
INTER_AGENT_OFF_MODES = {"off", "disabled", "manual", "false", ""}
SHAREABLE_PROACTIVE_PRIVACY_LEVELS = {"safe_to_share", "user_visible", "public", "shareable"}
SHAREABLE_DIARY_PRIVACY_LEVELS = {"safe_to_share", "user_visible", "public", "shareable"}
SHAREABLE_PROACTIVE_TARGET_TYPES = {"user", "self_journal"}
SHAREABLE_PROACTIVE_STATUSES = {"generated", "queued", "sent"}


@contextlib.contextmanager
def _transaction_if_needed(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """在已有外层事务时复用事务，否则开启独立写事务。

    输入是 LifeEngine SQLite connection；输出是可写 connection。调用方是
    inter-agent outbox 入队、claim 和状态更新。该 helper 的作用域只限本模块，
    用来让轴五-A 公开 API 既能被普通 worker 单独调用，也能被 heartbeat 的大事务
    调用；失败时外层事务负责回滚，独立调用时本 helper 负责回滚。
    """
    if conn.in_transaction:
        yield conn
    else:
        with transaction(conn):
            yield conn


def inter_agent_gate_enabled(control: dict[str, Any] | None) -> bool:
    """判断跨 Agent 讲述 gate 是否允许自动运行。

    输入是 owner control；输出布尔值。调用方是 heartbeat sharing runner 和
    peer-targeted proactive routing。默认值是关闭，只有 `on`/`auto` 等非关闭 mode
    才允许跨 owner 入队和投递，保证单 Agent 安装默认不改变行为。
    """
    gates = (control or {}).get("module_gates") or {}
    mode = str(gates.get("inter_agent", "off") or "off").strip().lower()
    return mode not in INTER_AGENT_OFF_MODES


def _row_dict(row) -> dict[str, Any]:
    """把 sqlite row 转成带 payload 的 outbox dict。

    输入是 `inter_agent_outbox` 行；输出用于 API 返回和 delivery 组装 LifeOps。
    函数只做 JSON 解码，不写数据库；payload_json 损坏时退化为空对象，让调用方
    可以按 failed 处理而不是在查询阶段崩溃。
    """
    if not row:
        return {}
    data = dict(row)
    data["payload"] = loads(data.pop("payload_json", None), {})
    return data


def _owner_pair(owner: Any, *, prefix: str = "") -> tuple[str, str]:
    """解析 owner 引用为 `(owner_kind, owner_id)`。

    输入可以是二元 tuple/list，或包含 owner_kind/owner_id 的 dict。`prefix` 供
    读取 outbox 行里的 from_/to_ 字段。输出用于 enqueue、delivery 和 evidence。
    失败会抛出 ValueError，避免把跨 owner 消息写进错误生活域。
    """
    if isinstance(owner, dict):
        owner_kind = owner.get(f"{prefix}owner_kind") or owner.get("owner_kind")
        owner_id = owner.get(f"{prefix}owner_id") or owner.get("owner_id")
    elif isinstance(owner, (tuple, list)) and len(owner) == 2:
        owner_kind, owner_id = owner
    else:
        raise ValueError("owner must be a dict or (owner_kind, owner_id)")
    owner_kind = str(owner_kind or "").strip()
    owner_id = str(owner_id or "").strip()
    if not owner_kind or not owner_id:
        raise ValueError("owner_kind and owner_id are required")
    return owner_kind, owner_id


def _agent_name(conn, owner_kind: str, owner_id: str) -> str:
    """读取来源 agent 的 Canon display name。

    输入是来源 owner；输出优先使用 active Canon identity.name，缺失时退回 owner_id。
    调用方是 peer entity 和 rumor 文案组装。函数只读 Canon，不写入任何目标世界。
    """
    canon = get_active_canon(conn, owner_kind, owner_id)
    identity = canon.get("identity") if isinstance(canon, dict) else {}
    return str((identity or {}).get("name") or owner_id)


def _payload_text(kind: str, payload: dict[str, Any]) -> str:
    """把 telling payload 收敛成可写入 rumor.content 的短文本。

    输入是 outbox kind 与 payload；输出不做生成，只取 content/text/message/summary
    等显式字段，缺失时用 JSON 摘要。调用方是 inter-agent delivery。函数无数据库
    副作用，保证跨 agent 文本来源可追踪。
    """
    for key in ("content", "text", "message", "summary", "telling"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{kind}: {dumps(payload)}"


def _existing_peer_entity(conn, owner_kind: str, owner_id: str,
                          peer_owner_kind: str, peer_owner_id: str) -> dict[str, Any] | None:
    """查找目标世界里已经代表来源 agent 的 peer_agent 实体。

    输入是接收方 owner 和来源 agent owner；输出是匹配 metadata cross-owner 引用的
    entity dict 或 None。函数只读 `world_entities`，用于 delivery 幂等地避免重复
    创建 peer 实体；真正创建仍必须走 LifeOps。
    """
    rows = conn.execute(
        """SELECT * FROM world_entities
            WHERE owner_kind=? AND owner_id=? AND entity_kind='peer_agent' AND status='active'
            ORDER BY created_at ASC""",
        (owner_kind, owner_id),
    ).fetchall()
    for row in rows:
        metadata = loads(row["metadata_json"], {})
        if (
            metadata.get("peer_owner_kind") == peer_owner_kind
            and metadata.get("peer_owner_id") == peer_owner_id
        ):
            return dict(row)
    return None


def _slot_exists(conn, owner_kind: str, owner_id: str, slot_type: str, key: str) -> bool:
    """检查目标世界是否已有指定社会槽位。

    输入是 owner、slot_type 和 key；输出布尔值。调用方是 delivery 组装 LifeOps，
    只在缺失时通过 `SOCIAL_DEFINE_SLOT` 补槽，避免反复更新槽位账本。
    """
    row = conn.execute(
        """SELECT 1 FROM worldview_slot_definitions
            WHERE owner_kind=? AND owner_id=? AND slot_type=? AND key=? AND status='active'""",
        (owner_kind, owner_id, slot_type, key),
    ).fetchone()
    return bool(row)


def _rumor_exists_for_outbox(conn, owner_kind: str, owner_id: str, outbox_id: str) -> bool:
    """检查某 outbox 是否已经落成目标世界的 rumor。

    输入是接收方 owner 与 outbox id；输出布尔值。调用方是 delivery 幂等保护：
    如果进程在 LifeOps 成功后、标记 outbox delivered 前崩溃，重新投递不会写入
    第二条同一来源的 rumor。
    """
    row = conn.execute(
        """SELECT 1 FROM rumors
            WHERE owner_kind=? AND owner_id=?
              AND target_kind='inter_agent_outbox'
              AND target_id=?
              AND status!='archived'
            LIMIT 1""",
        (owner_kind, owner_id, outbox_id),
    ).fetchone()
    return bool(row)


def enqueue_inter_agent(conn, from_owner: Any, to_owner: Any, kind: str, payload: dict[str, Any],
                        truth_layer: str = INTER_AGENT_TRUTH_LAYER,
                        dedup_key: str | None = None) -> dict[str, Any]:
    """把跨 agent 讲述写入 durable outbox。

    输入是来源/目标 owner、业务 kind、结构化 payload、truth_layer 和可选 dedup_key；
    输出是 outbox 行。副作用只写 `inter_agent_outbox` 投递账本。truth_layer 当前
    只允许 `rumor_unverified`，防止来源 agent 的叙事被接收方当作事实持久化。
    dedup_key 非空时使用唯一索引保持入队幂等。
    """
    from_owner_kind, from_owner_id = _owner_pair(from_owner)
    to_owner_kind, to_owner_id = _owner_pair(to_owner)
    kind = str(kind or "").strip()
    if not kind:
        raise ValueError("kind is required")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if truth_layer != INTER_AGENT_TRUTH_LAYER:
        raise ValueError("inter-agent tellings must use truth_layer=rumor_unverified")
    with _transaction_if_needed(conn):
        if dedup_key:
            existing = conn.execute(
                "SELECT * FROM inter_agent_outbox WHERE dedup_key=?",
                (dedup_key,),
            ).fetchone()
            if existing:
                return {**_row_dict(existing), "enqueued": False}
        outbox_id = new_id("iaout")
        conn.execute(
            """INSERT INTO inter_agent_outbox(
                 id, from_owner_kind, from_owner_id, to_owner_kind, to_owner_id,
                 kind, payload_json, truth_layer, status, dedup_key
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                outbox_id,
                from_owner_kind,
                from_owner_id,
                to_owner_kind,
                to_owner_id,
                kind,
                dumps(payload),
                truth_layer,
                "queued",
                dedup_key,
            ),
        )
        row = conn.execute("SELECT * FROM inter_agent_outbox WHERE id=?", (outbox_id,)).fetchone()
        return {**_row_dict(row), "enqueued": True}


def _claim_inter_agent_rows(conn, limit: int) -> list[dict[str, Any]]:
    """原子领取一批 queued inter-agent outbox。

    输入是批量上限；输出是本 worker 成功占用的行。函数复用 delivery.py 的核心
    并发模式：`BEGIN IMMEDIATE` 事务中先读 queued，再用 `WHERE status='queued'`
    条件更新为 claimed。并发调用会被 SQLite 写锁串行化，后到者不会重复投递同一行。
    """
    claimed: list[dict[str, Any]] = []
    with _transaction_if_needed(conn):
        rows = conn.execute(
            """SELECT * FROM inter_agent_outbox
                WHERE status='queued'
                ORDER BY created_at ASC
                LIMIT ?""",
            (max(1, int(limit)),),
        ).fetchall()
        for row in rows:
            changed = conn.execute(
                """UPDATE inter_agent_outbox
                      SET status='claimed', claimed_at=datetime('now')
                    WHERE id=? AND status='queued'""",
                (row["id"],),
            ).rowcount
            if changed:
                claimed_row = conn.execute(
                    "SELECT * FROM inter_agent_outbox WHERE id=?",
                    (row["id"],),
                ).fetchone()
                claimed.append(_row_dict(claimed_row))
    return claimed


def _runtime_for_conn(conn: sqlite3.Connection) -> Any:
    """创建复用现有连接的 runtime 代理。

    输入是已迁移 SQLite connection；输出只用于调用 `commit_ops` 的 runtime 实例。
    函数不打开新连接、不接管 close，确保 inter-agent delivery 和测试共享同一事务库。
    """
    from .runtime import LifeEngineRuntime

    runtime = LifeEngineRuntime.__new__(LifeEngineRuntime)
    runtime.conn = conn
    return runtime


def _commit_delivery_ops(conn: sqlite3.Connection, row: dict[str, Any],
                         ops: list[dict[str, Any]]) -> dict[str, Any] | None:
    """通过 runtime/LifeOps 提交目标世界写入。

    输入是已领取 outbox 行和 Social LifeOps；输出是 commit 摘要或 None。调用方是
    `deliver_inter_agent`。若当前已有 heartbeat/outer LifeOps 事务，则复用同一连接
    的 `_commit_ops_locked` savepoint；独立 worker 调用时走公开 `commit_ops`。
    副作用始终是 LifeOps 写 B 世界，不直接写 memories/self_narrative/thoughts。
    """
    if not ops:
        return None
    to_owner_kind, to_owner_id = _owner_pair(row, prefix="to_")
    runtime = _runtime_for_conn(conn)
    if conn.in_transaction:
        return runtime._commit_ops_locked(  # noqa: SLF001 - 本模块是同包 delivery 编排层
            ops,
            owner_kind=to_owner_kind,
            owner_id=to_owner_id,
            source="inter_agent_delivery",
            session_id=None,
            turn_id=row["id"],
            trace=None,
            control=ensure_control(conn, to_owner_kind, to_owner_id),
        )
    return runtime.commit_ops(
        ops,
        owner_kind=to_owner_kind,
        owner_id=to_owner_id,
        source="inter_agent_delivery",
        session_id=None,
        turn_id=row["id"],
    )


def _ops_for_delivery(conn, row: dict[str, Any]) -> list[dict[str, Any]]:
    """把已领取 outbox 转成目标世界的 Social LifeOps。

    输入是 claimed outbox 行；输出是要提交给 B owner 的 Social ops。副作用仅为只读
    查询现有 slot/entity/rumor 幂等状态。返回 ops 会确保 `peer_agent` 槽位、可选
    peer entity 和 `rumor_unverified` 流言都通过 LifeOps 写入目标世界。
    """
    from_owner_kind, from_owner_id = _owner_pair(row, prefix="from_")
    to_owner_kind, to_owner_id = _owner_pair(row, prefix="to_")
    from_name = _agent_name(conn, from_owner_kind, from_owner_id)
    ops: list[dict[str, Any]] = []
    if not _slot_exists(conn, to_owner_kind, to_owner_id, "entity_kind", "peer_agent"):
        ops.append({
            "type": "SOCIAL_DEFINE_SLOT",
            "payload": {
                "slot_type": "entity_kind",
                "key": "peer_agent",
                "label": "同行 Agent",
                "description": "代表出现在当前 owner 社会世界里的另一个 LifeEngine agent。",
                "config": {"cross_owner_reference": True},
                "source": "inter_agent_delivery",
            },
        })
    if not _slot_exists(conn, to_owner_kind, to_owner_id, "rumor_channel", "inter_agent"):
        ops.append({
            "type": "SOCIAL_DEFINE_SLOT",
            "payload": {
                "slot_type": "rumor_channel",
                "key": "inter_agent",
                "label": "跨 Agent 讲述",
                "description": "来自另一个 LifeEngine agent 的未证实讲述。",
                "config": {"truth_layer": INTER_AGENT_TRUTH_LAYER},
                "source": "inter_agent_delivery",
            },
        })

    peer = _existing_peer_entity(conn, to_owner_kind, to_owner_id, from_owner_kind, from_owner_id)
    if peer is None:
        ops.append({
            "type": "SOCIAL_CREATE_ENTITY",
            "payload": {
                "entity_kind": "peer_agent",
                "display_name": from_name,
                "summary": f"{from_name} 是另一个 LifeEngine agent；其讲述进入本世界时保持未证实状态。",
                "traits": {"agent_relationship": "peer"},
                "metadata": {
                    "peer_owner_kind": from_owner_kind,
                    "peer_owner_id": from_owner_id,
                    "reference_type": "lifeengine_owner",
                },
                "source": "inter_agent_delivery",
            },
        })

    if not _rumor_exists_for_outbox(conn, to_owner_kind, to_owner_id, row["id"]):
        text = _payload_text(str(row.get("kind") or "telling"), row.get("payload") or {})
        ops.append({
            "type": "SOCIAL_RECORD_RUMOR",
            "payload": {
                "content": f"{from_name}传来一条未证实讲述：{text}",
                "channel": "inter_agent",
                "target_kind": "inter_agent_outbox",
                "target_id": row["id"],
                "heat": 0.35,
                "credibility": 0.25,
                "sentiment": "neutral",
                "visibility": "local",
                "truth_layer": INTER_AGENT_TRUTH_LAYER,
                "evidence": {
                    "source": "inter_agent_outbox",
                    "outbox_id": row["id"],
                    "dedup_key": row.get("dedup_key"),
                    "from_owner_kind": from_owner_kind,
                    "from_owner_id": from_owner_id,
                    "kind": row.get("kind"),
                    "payload": row.get("payload") or {},
                },
                "source": "inter_agent_delivery",
            },
        })
    return ops


def deliver_inter_agent(conn, *, limit: int = 10) -> dict[str, Any]:
    """投递 queued inter-agent outbox 到目标 agent 的社会世界。

    输入是 SQLite connection 和批量上限；输出包含 claimed/delivered/failed 摘要。
    调用方式是 cron、测试或后续 worker 显式调用。副作用分两段：先原子 claim
    `inter_agent_outbox`，再通过 `LifeEngineRuntime.commit_ops` 把目标世界写入
    Social LifeOps。失败时只把 outbox 标为 failed；不会把跨 agent 讲述写入
    memories/self_narrative，也不会把 truth_layer 升格为事实。
    """
    claimed = _claim_inter_agent_rows(conn, limit)
    delivered: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for row in claimed:
        try:
            ops = _ops_for_delivery(conn, row)
            commit = _commit_delivery_ops(conn, row, ops)
            with _transaction_if_needed(conn):
                conn.execute(
                    """UPDATE inter_agent_outbox
                          SET status='delivered', delivered_at=datetime('now')
                        WHERE id=? AND status='claimed'""",
                    (row["id"],),
                )
            delivered.append({"outbox_id": row["id"], "op_count": len(ops), "commit": commit})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            with _transaction_if_needed(conn):
                conn.execute(
                    "UPDATE inter_agent_outbox SET status='failed' WHERE id=? AND status='claimed'",
                    (row["id"],),
                )
            failed.append({"outbox_id": row.get("id"), "error": error})
    return {
        "ok": not failed,
        "status": "delivered" if delivered and not failed else ("partial" if delivered else ("failed" if failed else "noop")),
        "claimed_count": len(claimed),
        "delivered": delivered,
        "failed": failed,
    }


def _shareable_recent_tellings(conn: sqlite3.Connection, from_owner: str, *,
                               limit: int = 25, recent_hours: int = 72) -> list[dict[str, Any]]:
    """读取当前 agent 已经可公开讲述的近期内容。

    输入是来源 agent id、最大条数和近期窗口；输出是规范化 telling 列表。该函数只读
    两个公开 surface：`proactive_intents` 中 target_type 为 `user/self_journal`、
    intent_type 为 `idle_share` 且 privacy_level 可分享的行；以及 `diary_entries`
    中 privacy 显式为 public/safe_to_share/user_visible/shareable 的行。它不读取
    `thoughts`、`memories`、`agent_opinions`，也不读取 `agent_private` intent/diary。
    """
    window = f"-{max(1, int(recent_hours))} hours"
    per_source_limit = max(1, int(limit))
    tellings: list[dict[str, Any]] = []
    proactive_rows = conn.execute(
        f"""SELECT id, intent_type, summary, emotional_tone, importance, status,
                  target_type, target_id, privacy_level, generated_by, created_at
             FROM proactive_intents
            WHERE agent_id=?
              AND intent_type='idle_share'
              AND status IN ({",".join("?" for _ in SHAREABLE_PROACTIVE_STATUSES)})
              AND target_type IN ({",".join("?" for _ in SHAREABLE_PROACTIVE_TARGET_TYPES)})
              AND COALESCE(privacy_level, 'safe_to_share') IN ({",".join("?" for _ in SHAREABLE_PROACTIVE_PRIVACY_LEVELS)})
              AND created_at >= datetime('now', ?)
              AND (expires_at_ts IS NULL OR expires_at_ts > CAST(strftime('%s','now') AS INTEGER))
            ORDER BY created_at DESC
            LIMIT ?""",
        (
            from_owner,
            *sorted(SHAREABLE_PROACTIVE_STATUSES),
            *sorted(SHAREABLE_PROACTIVE_TARGET_TYPES),
            *sorted(SHAREABLE_PROACTIVE_PRIVACY_LEVELS),
            window,
            per_source_limit,
        ),
    ).fetchall()
    for row in proactive_rows:
        summary = str(row["summary"] or "").strip()
        if not summary:
            continue
        telling_id = f"proactive:{row['id']}"
        tellings.append({
            "telling_id": telling_id,
            "source": "proactive_intent",
            "source_id": row["id"],
            "content": summary,
            "created_at": row["created_at"],
            "payload": {
                "source": "proactive_intent",
                "source_id": row["id"],
                "intent_type": row["intent_type"],
                "summary": summary,
                "content": summary,
                "emotional_tone": row["emotional_tone"],
                "importance": row["importance"],
                "target_type": row["target_type"],
                "privacy_level": row["privacy_level"],
                "status": row["status"],
                "created_at": row["created_at"],
            },
        })

    diary_rows = conn.execute(
        f"""SELECT id, diary_type, date, content, privacy, created_at
             FROM diary_entries
            WHERE owner_kind='agent'
              AND owner_id=?
              AND COALESCE(privacy, 'agent_private') IN ({",".join("?" for _ in SHAREABLE_DIARY_PRIVACY_LEVELS)})
              AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC
            LIMIT ?""",
        (
            from_owner,
            *sorted(SHAREABLE_DIARY_PRIVACY_LEVELS),
            window,
            per_source_limit,
        ),
    ).fetchall()
    for row in diary_rows:
        content = str(row["content"] or "").strip()
        if not content:
            continue
        telling_id = f"diary:{row['id']}"
        tellings.append({
            "telling_id": telling_id,
            "source": "diary_entry",
            "source_id": row["id"],
            "content": content,
            "created_at": row["created_at"],
            "payload": {
                "source": "diary_entry",
                "source_id": row["id"],
                "diary_type": row["diary_type"],
                "date": row["date"],
                "summary": content,
                "content": content,
                "privacy": row["privacy"],
                "created_at": row["created_at"],
            },
        })
    tellings.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return tellings[:per_source_limit]


def run_inter_agent_sharing_for_tick(conn: sqlite3.Connection, from_owner: Any, *,
                                     control: dict[str, Any] | None = None,
                                     limit: int = 25,
                                     recent_hours: int = 72,
                                     deliver_limit: int | None = None) -> dict[str, Any] | None:
    """把本 agent 的公开讲述转入 active peer agents 的世界。

    输入是来源 owner、当前 control、候选上限和近期窗口；输出是 sharing 摘要。调用方
    是 heartbeat registry，也可被测试直接调用。gate `inter_agent` 关闭时返回 None，
    让默认 tick 输出保持不新增模块段；gate 开启但无 peer/无可分享内容时只返回 no-op。
    副作用仅为：通过 `enqueue_inter_agent` 写 outbox 账本，再调用
    `deliver_inter_agent` 让轴五-A 用 Social LifeOps 写接收方的 `rumor_unverified`
    和 `peer_agent`。本函数不生成新内容，不读取或转发 thoughts/memories/
    agent_opinions/agent_private intent。
    """
    from_owner_kind, from_owner_id = _owner_pair(from_owner)
    if from_owner_kind != "agent":
        return None
    if not inter_agent_gate_enabled(control):
        return None

    from .registry import active_agents

    agents = active_agents(conn)
    peers = [
        agent for agent in agents
        if not (
            str(agent.get("owner_kind") or "") == from_owner_kind
            and str(agent.get("owner_id") or "") == from_owner_id
        )
    ]
    sources = ["proactive_intents.idle_share:user/self_journal", "diary_entries:public"]
    if not peers:
        return {"ok": True, "status": "skipped", "reason": "no active peers", "sources": sources}

    tellings = _shareable_recent_tellings(
        conn, from_owner_id, limit=limit, recent_hours=recent_hours,
    )
    if not tellings:
        return {
            "ok": True,
            "status": "noop",
            "reason": "no shareable tellings",
            "peer_count": len(peers),
            "sources": sources,
        }

    enqueued: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    for peer in peers:
        to_owner = {"owner_kind": str(peer["owner_kind"]), "owner_id": str(peer["owner_id"])}
        for telling in tellings:
            dedup_key = f"share:{telling['telling_id']}:{to_owner['owner_kind']}:{to_owner['owner_id']}"
            payload = {
                **telling["payload"],
                "from_owner_kind": from_owner_kind,
                "from_owner_id": from_owner_id,
                "to_owner_kind": to_owner["owner_kind"],
                "to_owner_id": to_owner["owner_id"],
            }
            outbox = enqueue_inter_agent(
                conn,
                (from_owner_kind, from_owner_id),
                (to_owner["owner_kind"], to_owner["owner_id"]),
                "telling",
                payload,
                dedup_key=dedup_key,
            )
            item = {
                "outbox_id": outbox.get("id"),
                "dedup_key": dedup_key,
                "to_owner_id": to_owner["owner_id"],
                "telling_id": telling["telling_id"],
                "status": outbox.get("status"),
            }
            if outbox.get("enqueued"):
                enqueued.append(item)
            else:
                existing.append(item)

    pending_count = len(enqueued) + sum(1 for item in existing if item.get("status") == "queued")
    delivery = {"ok": True, "status": "noop", "claimed_count": 0, "delivered": [], "failed": []}
    if pending_count:
        delivery = deliver_inter_agent(conn, limit=deliver_limit or max(10, pending_count))
    return {
        "ok": bool(delivery.get("ok", True)),
        "status": "ok" if delivery.get("ok", True) else "partial",
        "peer_count": len(peers),
        "telling_count": len(tellings),
        "enqueued_count": len(enqueued),
        "existing_count": len(existing),
        "delivered_count": len(delivery.get("delivered") or []),
        "failed_count": len(delivery.get("failed") or []),
        "sources": sources,
        "enqueued": enqueued,
        "delivery": delivery,
    }
