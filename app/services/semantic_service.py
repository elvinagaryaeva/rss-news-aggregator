"""Local multilingual embeddings and database-backed reuse for articles and topics."""
from __future__ import annotations

import math
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Article, ArticleEmbedding, Filter, FilterEmbedding


_E5_PREFIXES = ("intfloat/multilingual-e5",)


def _uses_e5_prefixes(model_name: str) -> bool:
    return any(model_name.startswith(p) for p in _E5_PREFIXES)


@lru_cache(maxsize=2)
def _load_model(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device="cpu")


def encode_text(text: str) -> list[float]:
    """
    Plain encoder. Single positional arg to stay compatible with tests that
    monkeypatch this function with `def encode(text): ...`.
    Task prefixes for e5 are applied by encode_query / encode_passage below.
    """
    model = _load_model(settings.semantic_model_name)
    try:
        vector = model.encode(text, normalize_embeddings=True)
    except TypeError:
        vector = model.encode(text)
    return [float(value) for value in vector]


def _apply_prefix(text: str, kind: str) -> str:
    if _uses_e5_prefixes(settings.semantic_model_name):
        return ("query: " if kind == "query" else "passage: ") + text
    return text


def encode_query(text: str) -> list[float]:
    """Encode a short search query (filter topic)."""
    return encode_text(_apply_prefix(text, "query"))


def encode_passage(text: str) -> list[float]:
    """Encode an indexed document (article title + summary)."""
    return encode_text(_apply_prefix(text, "passage"))


def build_article_semantic_text(article: Article) -> str:
    """Short, focused signal: title + summary (falls back to truncated content)."""
    title = (article.title or "").strip()
    summary = (article.summary or "").strip()
    if summary:
        return f"{title}. {summary}"
    content = (article.content or "").strip()
    if content:
        return f"{title}. {content[:500]}"
    return title


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Embeddings must have the same non-zero length")
    dot = sum(a * b for a, b in zip(left, right))
    magnitude = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return dot / magnitude if magnitude else 0.0


def article_embedding(db: Session, article: Article) -> list[float]:
    model_name = settings.semantic_model_name
    cached = db.scalar(
        select(ArticleEmbedding).where(
            ArticleEmbedding.article_id == article.id,
            ArticleEmbedding.model_name == model_name,
        )
    )
    if cached is not None:
        return cached.embedding
    vector = encode_passage(build_article_semantic_text(article))
    db.add(
        ArticleEmbedding(
            article_id=article.id,
            model_name=model_name,
            embedding=vector,
        )
    )
    db.flush()
    return vector


def filter_embedding(db: Session, filter_: Filter) -> list[float]:
    if not filter_.semantic_query:
        raise ValueError("Semantic query is required")
    model_name = settings.semantic_model_name
    query = filter_.semantic_query.strip()
    cached = db.scalar(
        select(FilterEmbedding).where(
            FilterEmbedding.filter_id == filter_.id,
            FilterEmbedding.model_name == model_name,
        )
    )
    if cached is not None and cached.query_text == query:
        return cached.embedding
    vector = encode_query(query)
    if cached is None:
        db.add(
            FilterEmbedding(
                filter_id=filter_.id,
                model_name=model_name,
                query_text=query,
                embedding=vector,
            )
        )
    else:
        cached.query_text = query
        cached.embedding = vector
    db.flush()
    return vector