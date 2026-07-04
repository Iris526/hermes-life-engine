from __future__ import annotations

import pytest

from lifeengine.constants import VECTOR_DIM
from lifeengine.db import _SCHEMA_VERSION, connect
from lifeengine.embeddings import embed_text, register_embedder
from lifeengine.memory import create_memory, search_memories
from lifeengine.upgrade import rebuild_memory_indexes, verify_memory_indexes


@pytest.fixture(autouse=True)
def reset_embedder():
    """保证本文件的宿主 embedding hook 不污染同 worker 的其它测试。

    输入来自 pytest fixture 生命周期；输出为空。调用方是本文件所有测试；副作用是
    在测试前后把进程内 embedder 清空，维持默认 FTS-only 合同。
    """
    register_embedder(None)
    try:
        yield
    finally:
        register_embedder(None)


def test_default_memory_path_is_fts_only_and_does_not_write_vectors(tmp_path):
    """默认配置只写 FTS、不写 memory_vec，搜索结果来自 BM25。

    输入是临时 DB；输出为空。调用方是 pytest。副作用限于临时 SQLite，验证默认
    `embed_text` 返回 None、创建 memory 后 `memory_vec` 仍为空、FTS 查询仍能命中。
    """
    conn = connect(tmp_path / "lifeengine.db")
    try:
        assert _SCHEMA_VERSION == 71
        assert embed_text("rainy walks") is None
        create_memory(conn, "agent", "a1", "User prefers rainy walks after dinner", importance=72)

        vec_count = conn.execute("SELECT COUNT(*) FROM memory_vec").fetchone()[0]
        assert vec_count == 0

        results = search_memories(conn, "agent", "a1", "rainy", limit=5)
        assert results
        assert results[0]["content"] == "User prefers rainy walks after dinner"
        assert results[0]["source"] == "fts"

        verified = verify_memory_indexes(conn, "agent", "a1")
        assert verified["ok"] is True
        assert verified["vector_mode"] == "disabled"
        assert verified["vec_count"] == 0
        assert verified["missing_vec"] == []
    finally:
        conn.close()


def test_search_degrades_when_fts_or_vec_tables_are_missing(tmp_path):
    """FTS/vec 缺表时搜索不崩溃，并用诚实词面 fallback 返回结果。

    输入是临时 DB；输出为空。调用方是 pytest。副作用是在临时库中删除索引虚表，
    验证 `search_memories` 能跳过缺失的 `memory_vec`，并在 `memory_fts` 缺失时用
    memories.content 的 LIKE 降级。
    """
    conn = connect(tmp_path / "lifeengine.db")
    try:
        create_memory(conn, "agent", "a1", "Fallback keyword memory about coffee", importance=40)
        conn.execute("DROP TABLE memory_vec")
        fts_results = search_memories(conn, "agent", "a1", "coffee", limit=5)
        assert fts_results
        assert fts_results[0]["source"] == "fts"

        conn.execute("DROP TABLE memory_fts")
        fallback_results = search_memories(conn, "agent", "a1", "coffee", limit=5)
        assert fallback_results
        assert fallback_results[0]["content"] == "Fallback keyword memory about coffee"
        assert fallback_results[0]["source"] == "lexical_fallback"
    finally:
        conn.close()


def test_registered_real_embedder_populates_and_uses_memory_vec(tmp_path):
    """宿主注册真实 embedder 后，memory_vec seam 会写入并参与检索。

    输入是临时 DB 和一个测试专用 embedder；输出为空。调用方是 pytest。副作用是
    进程内注册 embedder 并写临时 SQLite。测试 embedder 只存在于测试中，用来证明
    seam 可插拔；生产默认不会生成假向量。
    """
    def test_embedder(text: str) -> list[float]:
        vector = [0.0] * VECTOR_DIM
        lowered = text.lower()
        vector[0] = 1.0 if ("alpha" in lowered or "hidden" in lowered) else -1.0
        return vector

    register_embedder(test_embedder)
    conn = connect(tmp_path / "lifeengine.db")
    try:
        create_memory(conn, "agent", "a1", "alpha private marker", importance=20)
        create_memory(conn, "agent", "a1", "beta public marker", importance=80)

        vec_count = conn.execute("SELECT COUNT(*) FROM memory_vec").fetchone()[0]
        assert vec_count == 2

        results = search_memories(conn, "agent", "a1", "hidden", limit=1)
        assert results
        assert results[0]["content"] == "alpha private marker"
        assert results[0]["source"] == "vec"

        rebuilt = rebuild_memory_indexes(conn, "agent", "a1")
        assert rebuilt["ok"] is True
        assert rebuilt["vector_mode"] == "registered"
        assert rebuilt["vec_indexed"] == 2

        verified = verify_memory_indexes(conn, "agent", "a1")
        assert verified["ok"] is True
        assert verified["vector_index_required"] is True
        assert verified["missing_vec"] == []
    finally:
        conn.close()
