"""LifeEngine 记忆向量的宿主扩展接口。

LifeEngine 默认不内置 embedding 模型，也不再用词袋/哈希伪向量冒充语义
embedding。默认检索由 memory_fts 的 BM25 负责；只有宿主显式调用
`register_embedder()` 注册真实 embedding 模型后，`memory_vec` 才会被写入并
参与检索。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .constants import VECTOR_DIM

EmbeddingVector = Sequence[float]
RealEmbedder = Callable[[str], EmbeddingVector | None]

_registered_embedder: RealEmbedder | None = None


def register_embedder(embedder: RealEmbedder | None) -> None:
    """注册或清除宿主提供的真实 embedding 模型。

    输入是一个可调用对象：接收原始文本，返回长度等于 `VECTOR_DIM` 的浮点向量；
    传入 None 表示关闭向量能力。调用方是 Hermes/LifeEngine 的宿主适配层或测试，
    本模块只保存进程内 hook，不持久化配置、不调用网络、不自行构造假向量。失败
    处理由调用方和 memory 写入/检索路径共同兜底：未注册或返回 None 时走 FTS-only。
    """
    global _registered_embedder
    _registered_embedder = embedder


def get_embedder() -> RealEmbedder | None:
    """返回当前进程已注册的真实 embedding 模型。

    输出是宿主注册的 callable 或 None；调用方用它判断向量索引是否应被维护。
    该函数只读模块级状态，没有数据库或网络副作用。
    """
    return _registered_embedder


def has_embedder() -> bool:
    """判断当前是否启用了宿主真实 embedding。

    输出为布尔值；调用方主要是维护/校验命令，用于区分“默认 FTS-only”与“宿主
    已启用真实向量”。函数只读进程内 hook，不代表数据库里一定已有向量行。
    """
    return _registered_embedder is not None


def embed_text(text: str, dims: int = VECTOR_DIM) -> list[float] | None:
    """通过宿主真实模型生成 embedding；默认返回 None。

    输入是待索引或待检索文本，以及期望维度；输出是浮点向量或 None。调用方是
    memory 写入、检索和维护索引路径。未注册 embedder、宿主选择跳过、或返回 None
    时表示禁用向量能力；维度不匹配会抛出 ValueError，避免把错误向量写入
    `memory_vec`。本函数不会做哈希、词袋或其它伪 embedding 计算。
    """
    embedder = _registered_embedder
    if embedder is None:
        return None
    raw = embedder(text)
    if raw is None:
        return None
    vector = [float(value) for value in raw]
    if len(vector) != int(dims):
        raise ValueError(f"registered embedder returned {len(vector)} dims; expected {int(dims)}")
    return vector


def serialize_embedding(vec: EmbeddingVector) -> bytes:
    """把真实 embedding 序列化为 sqlite-vec 的 float32 blob。

    输入是已由宿主真实模型生成并通过维度校验的向量；输出是 sqlite-vec 可写入
    `memory_vec.embedding` 的 bytes。调用方是 memory 写入/重建/检索路径；副作用
    仅为按需导入 sqlite_vec 扩展，若运行环境缺少扩展则向调用方抛出异常并由上层降级。
    """
    import sqlite_vec  # type: ignore

    return sqlite_vec.serialize_float32(list(vec))


__all__ = [
    "EmbeddingVector",
    "RealEmbedder",
    "embed_text",
    "get_embedder",
    "has_embedder",
    "register_embedder",
    "serialize_embedding",
]
