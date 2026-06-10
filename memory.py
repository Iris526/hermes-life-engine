"""Structured + FTS5 + sqlite-vec memory operations."""

from __future__ import annotations

from typing import Any

from .embeddings import embed_text, serialize_embedding
from .trace import append_journal, new_id


def create_memory(conn, owner_kind: str, owner_id: str, content: str,
                  memory_type: str = "episodic", source: str = "life_commit",
                  event_id: str | None = None, action_id: str | None = None,
                  result_id: str | None = None, importance: int = 50,
                  emotional_weight: int = 0, confidence: float = 1.0,
                  canon_version: int | None = None) -> dict[str, Any]:
    if not content.strip():
        raise ValueError("memory content is required")
    mem_id = new_id("mem")
    conn.execute(
        """INSERT INTO memories(id, owner_kind, owner_id, memory_type, content, event_id, action_id,
               result_id, importance, emotional_weight, source, confidence, canon_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mem_id, owner_kind, owner_id, memory_type, content.strip(), event_id, action_id, result_id,
         int(importance), int(emotional_weight), source, float(confidence), canon_version),
    )
    rowid = conn.execute("SELECT rowid FROM memories WHERE id=?", (mem_id,)).fetchone()[0]
    conn.execute(
        "INSERT INTO memory_fts(memory_rowid, owner_kind, owner_id, content) VALUES(?,?,?,?)",
        (rowid, owner_kind, owner_id, content.strip()),
    )
    conn.execute(
        "INSERT INTO memory_vec(rowid, embedding) VALUES(?, ?)",
        (rowid, serialize_embedding(embed_text(content))),
    )
    append_journal(conn, owner_kind, owner_id, "memory_created", {"memory_id": mem_id, "memory_type": memory_type}, source, canon_version=canon_version)
    return get_memory(conn, mem_id)


def get_memory(conn, memory_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    if not row:
        raise ValueError(f"Memory not found: {memory_id}")
    return dict(row)


def search_memories(conn, owner_kind: str, owner_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    if not query.strip():
        rows = conn.execute(
            "SELECT * FROM memories WHERE owner_kind=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_kind, owner_id, limit),
        ).fetchall()
        return [dict(r) | {"score": 0.0, "source": "recent"} for r in rows]

    scores: dict[int, dict[str, Any]] = {}
    # FTS first.
    try:
        safe_query = " OR ".join([p for p in query.replace('"', ' ').split() if p]) or query
        for r in conn.execute(
            """SELECT m.rowid AS rid, m.*, bm25(memory_fts) AS bm25_score
                   FROM memory_fts JOIN memories m ON m.rowid = memory_fts.memory_rowid
                  WHERE memory_fts MATCH ? AND memory_fts.owner_kind=? AND memory_fts.owner_id=?
                  ORDER BY bm25_score LIMIT ?""",
            (safe_query, owner_kind, owner_id, limit),
        ).fetchall():
            d = dict(r)
            rid = d.pop("rid")
            bm = float(d.pop("bm25_score"))
            scores[rid] = d | {"score": -bm, "source": "fts"}
    except Exception:
        pass

    # sqlite-vec vector rank.
    qv = serialize_embedding(embed_text(query))
    for r in conn.execute(
        """SELECT rowid, distance FROM memory_vec
              WHERE embedding MATCH ?
              ORDER BY distance LIMIT ?""",
        (qv, limit * 3),
    ).fetchall():
        rid = int(r["rowid"])
        mem = conn.execute(
            "SELECT * FROM memories WHERE rowid=? AND owner_kind=? AND owner_id=?",
            (rid, owner_kind, owner_id),
        ).fetchone()
        if not mem:
            continue
        distance = float(r["distance"])
        base = scores.get(rid, dict(mem) | {"score": 0.0, "source": "vec"})
        base["score"] = float(base.get("score", 0.0)) + (1.0 / (1.0 + distance))
        base["source"] = "hybrid" if base.get("source") == "fts" else "vec"
        scores[rid] = base

    result = sorted(scores.values(), key=lambda x: (x.get("score", 0), x.get("importance", 0)), reverse=True)[:limit]
    for item in result:
        conn.execute("UPDATE memories SET last_accessed_at=datetime('now') WHERE id=?", (item["id"],))
    return result
