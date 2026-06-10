"""Deterministic local embeddings for sqlite-vec.

This keeps LifeEngine self-cycling without an external embedding service.  The
vector is lexical/hash-based, not a semantic model, but it is stable and works
with sqlite-vec for recall ranking.  A future adapter can replace this while
keeping the same memory_vec schema.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

from .constants import VECTOR_DIM


def _tokens(text: str) -> Iterable[str]:
    lower = text.lower()
    # Latin words / numbers plus individual CJK chars; this is lightweight and locale-safe.
    for m in re.finditer(r"[a-z0-9_\-]+|[\u4e00-\u9fff]", lower):
        yield m.group(0)


def embed_text(text: str, dims: int = VECTOR_DIM) -> list[float]:
    vec = [0.0] * dims
    toks = list(_tokens(text)) or [text[:32] or "empty"]
    for tok in toks:
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
        # Add simple bi-token-ish spread so short text is not too sparse.
        idx2 = int.from_bytes(digest[4:], "little") % dims
        vec[idx2] += 0.5 * sign
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [float(x / norm) for x in vec]


def serialize_embedding(vec: list[float]) -> bytes:
    import sqlite_vec  # type: ignore
    return sqlite_vec.serialize_float32(vec)
