from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Iterable

import jieba


DOMAIN_WORDS = ["LangGraph", "PostgreSQL", "幂等键", "FastAPI", "pgvector", "rubric", "checkpoint"]
for word in DOMAIN_WORDS:
    jieba.add_word(word)

DIM = 32


def tokenize(text: str) -> list[str]:
    return [tok.strip().lower() for tok in jieba.cut(text) if tok.strip() and not tok.isspace()]


def _clean_token(tok: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.+-]+", "", tok)


def tsvector_document(text: str) -> str:
    """Space-joined jieba tokens. Feed this to PostgreSQL to_tsvector('simple', ...)."""
    return " ".join(t for t in (_clean_token(tok) for tok in tokenize(text)) if t)


def tsquery_string(query: str, *, op: str = "&") -> str:
    tokens = [t for t in (_clean_token(tok) for tok in tokenize(query)) if t][:12]
    joiner = f" {op} "
    return joiner.join(tokens)


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def lexical_score(query: str, body: str) -> float:
    q = set(tokenize(query))
    b = set(tokenize(body))
    if not q or not b:
        return 0.0
    return len(q & b) / len(q)


def embed(text: str, dim: int = DIM) -> list[float]:
    vec = [0.0] * dim
    for tok in tokenize(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def rrf_fuse(lexical: list[str], vector: list[str], k: int = 60, limit: int = 5) -> list[str]:
    scores: dict[str, float] = {}
    for rank, chunk_id in enumerate(lexical, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    for rank, chunk_id in enumerate(vector, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return [cid for cid, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]]


def search_chunks(chunks: Iterable[dict[str, Any]], query: str, skill: str | None = None) -> list[dict[str, Any]]:
    filtered = [c for c in chunks if not skill or skill in (c.get("skill_tags") or [])]
    lex_ranked = sorted(filtered, key=lambda c: lexical_score(query, c["text"]), reverse=True)[:20]
    qv = embed(query)
    vec_ranked = sorted(filtered, key=lambda c: cosine(qv, c.get("vector") or embed(c["text"])), reverse=True)[:20]
    fused_ids = rrf_fuse([c["id"] for c in lex_ranked], [c["id"] for c in vec_ranked])
    by_id = {c["id"]: c for c in filtered}
    return [by_id[i] for i in fused_ids if i in by_id]
