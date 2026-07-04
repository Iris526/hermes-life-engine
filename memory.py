"""结构化记忆、FTS5 检索，以及可选真实 embedding 向量检索。

默认路径只依赖 `memory_fts` 的 BM25；`memory_vec` 只在宿主显式注册真实
embedding 后写入和检索。未注册 embedder、表缺失或 sqlite-vec 不可用时，本模块
会降级到诚实的 FTS/词面检索，不再生成或使用哈希伪向量。
"""

from __future__ import annotations

import logging
from typing import Any

from .embeddings import embed_text, serialize_embedding
from .trace import append_journal, new_id

logger = logging.getLogger(__name__)


def _table_exists(conn, table_name: str) -> bool:
    """判断当前连接是否存在指定 SQLite 表或虚表。

    输入是 SQLite 连接和表名；输出为布尔值。调用方是记忆索引写入、检索和降级
    分支；副作用仅为读取 sqlite_master。失败时返回 False，使缺表旧库可以继续走
    可用的降级路径。
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=? AND type IN ('table','virtual table') LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _insert_fts_index(conn, rowid: int, owner_kind: str, owner_id: str, content: str) -> bool:
    """为一条 memory 写入 FTS5 索引。

    输入是 memory 的 rowid、owner 和正文；输出表示是否成功写入。调用方是
    `create_memory` 与维护重建；副作用限于 `memory_fts`。缺表或 FTS 异常时返回
    False，主 memory 记录仍保留，后续检索可降级到 LIKE。
    """
    if not _table_exists(conn, "memory_fts"):
        return False
    try:
        conn.execute(
            "INSERT INTO memory_fts(memory_rowid, owner_kind, owner_id, content) VALUES(?,?,?,?)",
            (rowid, owner_kind, owner_id, content),
        )
        return True
    except Exception as exc:
        logger.warning("memory FTS index skipped: %s: %s", type(exc).__name__, exc)
        return False


def _insert_vector_index(conn, rowid: int, content: str) -> bool:
    """在宿主真实 embedding 已启用时写入 memory_vec。

    输入是 memory rowid 与正文；输出表示是否写入了向量行。调用方是 memory 创建
    与索引重建。未注册真实 embedder、embedder 返回 None、`memory_vec` 缺表或
    sqlite-vec 写入失败时返回 False，不影响 FTS-only 主路径。本函数不会生成任何
    本地哈希/词袋伪向量。
    """
    if not _table_exists(conn, "memory_vec"):
        return False
    try:
        vector = embed_text(content)
        if vector is None:
            return False
        conn.execute(
            "INSERT INTO memory_vec(rowid, embedding) VALUES(?, ?)",
            (rowid, serialize_embedding(vector)),
        )
        return True
    except Exception as exc:
        logger.warning("memory vector index skipped: %s: %s", type(exc).__name__, exc)
        return False


def _fts_query(query: str) -> str:
    """把用户查询收敛成 FTS5 MATCH 可接受的词面查询。

    输入是原始搜索文本；输出是用 OR 连接的保守词项。调用方是 FTS 检索分支；该
    处理只清理引号与空白，不做语义扩展，保证默认检索仍是诚实关键词检索。
    """
    terms = [part for part in query.replace('"', " ").split() if part]
    return " OR ".join(terms) if terms else query.strip()


def _search_fts(conn, owner_kind: str, owner_id: str, query: str, limit: int) -> tuple[dict[int, dict[str, Any]], bool]:
    """用 `memory_fts` BM25 搜索记忆。

    输入是 owner、查询词和数量上限；输出为 rowid 到结果对象的映射，以及 FTS 分支
    是否实际可用。调用方是 `search_memories`。缺表或 MATCH 异常时不抛出，返回
    空结果并让上层进入词面 LIKE 降级。
    """
    if not _table_exists(conn, "memory_fts"):
        return {}, False
    try:
        rows = conn.execute(
            """SELECT m.rowid AS rid, m.*, bm25(memory_fts) AS bm25_score
                   FROM memory_fts JOIN memories m ON m.rowid = memory_fts.memory_rowid
                  WHERE memory_fts MATCH ? AND memory_fts.owner_kind=? AND memory_fts.owner_id=?
                  ORDER BY bm25_score LIMIT ?""",
            (_fts_query(query), owner_kind, owner_id, limit),
        ).fetchall()
    except Exception as exc:
        logger.warning("memory FTS search skipped: %s: %s", type(exc).__name__, exc)
        return {}, False

    scores: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        rid = int(item.pop("rid"))
        bm25_score = float(item.pop("bm25_score"))
        scores[rid] = item | {"score": -bm25_score, "source": "fts"}
    return scores, True


def _search_lexical_fallback(conn, owner_kind: str, owner_id: str, query: str, limit: int) -> dict[int, dict[str, Any]]:
    """在 FTS 表缺失时用 memories.content 的 LIKE 做保守词面降级。

    输入是 owner、查询文本和数量上限；输出为 rowid 到结果对象的映射。调用方只在
    `memory_fts` 不可用时使用它，目的是保证旧库/损坏索引仍能返回可理解结果。该
    分支不是语义搜索，只按可见文本片段匹配并用命中词数、importance、时间排序。
    """
    if not _table_exists(conn, "memories"):
        return {}
    terms = [part.lower() for part in query.replace('"', " ").split() if part] or [query.strip().lower()]
    predicates = " OR ".join(["LOWER(content) LIKE ?" for _ in terms])
    params: list[Any] = [f"%{term}%" for term in terms]
    try:
        rows = conn.execute(
            f"""SELECT rowid AS rid, * FROM memories
                  WHERE owner_kind=? AND owner_id=? AND ({predicates})
                  ORDER BY importance DESC, created_at DESC LIMIT ?""",
            (owner_kind, owner_id, *params, limit),
        ).fetchall()
    except Exception as exc:
        logger.warning("memory lexical fallback skipped: %s: %s", type(exc).__name__, exc)
        return {}

    scores: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        rid = int(item.pop("rid"))
        content = str(item.get("content") or "").lower()
        hit_count = sum(1 for term in terms if term and term in content)
        scores[rid] = item | {
            "score": float(hit_count) + (float(item.get("importance") or 0) / 100.0),
            "source": "lexical_fallback",
        }
    return scores


def _search_vectors(conn, owner_kind: str, owner_id: str, query: str, limit: int) -> dict[int, dict[str, Any]]:
    """在宿主真实 embedding 已启用时查询 memory_vec。

    输入是 owner、查询文本和数量上限；输出为 rowid 到结果对象的映射。调用方是
    `search_memories` 的可选混合分支。未注册真实 embedder、`memory_vec` 缺表/空表
    或 sqlite-vec 查询失败时返回空结果；默认安装因此不会触发任何伪语义排名。
    """
    if not _table_exists(conn, "memory_vec"):
        return {}
    try:
        query_vector = embed_text(query)
        if query_vector is None:
            return {}
        serialized_query = serialize_embedding(query_vector)
        vec_rows = conn.execute(
            """SELECT rowid, distance FROM memory_vec
                  WHERE embedding MATCH ?
                  ORDER BY distance LIMIT ?""",
            (serialized_query, limit * 3),
        ).fetchall()
    except Exception as exc:
        logger.warning("memory vector search skipped: %s: %s", type(exc).__name__, exc)
        return {}

    scores: dict[int, dict[str, Any]] = {}
    for row in vec_rows:
        rid = int(row["rowid"])
        mem = conn.execute(
            "SELECT * FROM memories WHERE rowid=? AND owner_kind=? AND owner_id=?",
            (rid, owner_kind, owner_id),
        ).fetchone()
        if not mem:
            continue
        distance = float(row["distance"])
        scores[rid] = dict(mem) | {
            "score": 1.0 / (1.0 + distance),
            "source": "vec",
        }
    return scores


def create_memory(conn, owner_kind: str, owner_id: str, content: str,
                  memory_type: str = "episodic", source: str = "life_commit",
                  event_id: str | None = None, action_id: str | None = None,
                  result_id: str | None = None, importance: int = 50,
                  emotional_weight: int = 0, confidence: float = 1.0,
                  canon_version: int | None = None) -> dict[str, Any]:
    """创建结构化 memory，并维护诚实检索索引。

    输入是 owner、正文、类型、来源和可选关联对象；输出是刚创建的 memory 行。
    调用方包括 LifeOps `CREATE_MEMORY`、life_memory 工具、梦境/观点等内部写入。
    副作用是写 `memories`、尽力写 `memory_fts`，并仅在宿主注册真实 embedding 时
    写 `memory_vec`；未注册时不会产生任何向量。内容为空会抛出 ValueError。
    """
    normalized_content = content.strip()
    if not normalized_content:
        raise ValueError("memory content is required")
    mem_id = new_id("mem")
    conn.execute(
        """INSERT INTO memories(id, owner_kind, owner_id, memory_type, content, event_id, action_id,
               result_id, importance, emotional_weight, source, confidence, canon_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mem_id, owner_kind, owner_id, memory_type, normalized_content, event_id, action_id, result_id,
         int(importance), int(emotional_weight), source, float(confidence), canon_version),
    )
    rowid = int(conn.execute("SELECT rowid FROM memories WHERE id=?", (mem_id,)).fetchone()[0])
    _insert_fts_index(conn, rowid, owner_kind, owner_id, normalized_content)
    _insert_vector_index(conn, rowid, normalized_content)
    append_journal(conn, owner_kind, owner_id, "memory_created", {"memory_id": mem_id, "memory_type": memory_type}, source, canon_version=canon_version)
    return get_memory(conn, mem_id)


def get_memory(conn, memory_id: str) -> dict[str, Any]:
    """按 memory id 读取结构化记忆。

    输入是数据库连接和 memory id；输出是该 memory 的字典行。调用方是创建后的回读
    与其它模块的精确读取。找不到记录时抛出 ValueError；函数只读 `memories`。
    """
    row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    if not row:
        raise ValueError(f"Memory not found: {memory_id}")
    return dict(row)


def search_memories(conn, owner_kind: str, owner_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """搜索 memory：默认 FTS-only，真实 embedder 存在时才混合向量。

    输入是 owner、查询文本和上限；输出是带 score/source 的 memory 列表。空查询返回
    最近 memory。非空查询优先使用 `memory_fts` BM25；FTS 缺失时用 LIKE 降级；
    只有宿主注册真实 embedding 且 `memory_vec` 可用时才额外合并向量得分。缺失
    FTS/vec 表或空向量表都不会抛错，最多返回空结果。
    """
    safe_limit = max(1, int(limit))
    normalized_query = (query or "").strip()
    if not _table_exists(conn, "memories"):
        return []
    if not normalized_query:
        try:
            rows = conn.execute(
                "SELECT * FROM memories WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
                (owner_kind, owner_id, safe_limit),
            ).fetchall()
        except Exception:
            return []
        return [dict(row) | {"score": 0.0, "source": "recent"} for row in rows]

    scores, fts_available = _search_fts(conn, owner_kind, owner_id, normalized_query, safe_limit)
    if not fts_available:
        scores.update(_search_lexical_fallback(conn, owner_kind, owner_id, normalized_query, safe_limit))

    for rid, vector_item in _search_vectors(conn, owner_kind, owner_id, normalized_query, safe_limit).items():
        if rid in scores:
            base = scores[rid]
            base["score"] = float(base.get("score", 0.0)) + float(vector_item.get("score", 0.0))
            base["source"] = "hybrid"
        else:
            scores[rid] = vector_item

    result = sorted(scores.values(), key=lambda item: (item.get("score", 0), item.get("importance", 0)), reverse=True)[:safe_limit]
    for item in result:
        try:
            conn.execute("UPDATE memories SET last_accessed_at=datetime('now') WHERE id=?", (item["id"],))
        except Exception:
            pass
    return result
