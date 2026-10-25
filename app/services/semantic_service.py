"""Local multilingual embeddings and database-backed reuse for articles and topics."""
from __future__ import annotations

import math
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Article, ArticleEmbedding, Filter, FilterEmbedding


@lru_cache(maxsize=2)
def _load_model(model_name: str):
    # Import and download only when a semantic filter is actually used.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device="cpu")


def encode_text(text: str) -> list[float]:
    vector = _load_model(settings.semantic_model_name).encode(text)
    return [float(value) for value in vector]


def build_article_semantic_text(article: Article) -> str:
    parts = [f"Title: {article.title.strip()}"]
    summary = (article.summary or "").strip()
    content = (article.content or "").strip()
    if summary:
        parts.append(f"Summary: {summary[:800]}")
    if content and content != summary:
        parts.append(f"Content: {content[:1800]}")
    return "\n".join(parts)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Embeddings must have the same non-zero length")
    dot = sum(a * b for a, b in zip(left, right))
    magnitude = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return dot / magnitude if magnitude else 0.0


def article_embedding(db: Session, article: Article) -> list[float]:
    model_name = settings.semantic_model_name
    cached = db.scalar(select(ArticleEmbedding).where(ArticleEmbedding.article_id == article.id, ArticleEmbedding.model_name == model_name))
    if cached is not None:
        return cached.embedding
    vector = encode_text(build_article_semantic_text(article))
    db.add(ArticleEmbedding(article_id=article.id, model_name=model_name, embedding=vector))
    db.flush()
    return vector


def filter_embedding(db: Session, filter_: Filter) -> list[float]:
    if not filter_.semantic_query:
        raise ValueError("Semantic query is required")
    model_name = settings.semantic_model_name
    query = filter_.semantic_query.strip()
    cached = db.scalar(select(FilterEmbedding).where(FilterEmbedding.filter_id == filter_.id, FilterEmbedding.model_name == model_name))
    if cached is not None and cached.query_text == query:
        return cached.embedding
    vector = encode_text(query)
    if cached is None:
        db.add(FilterEmbedding(filter_id=filter_.id, model_name=model_name, query_text=query, embedding=vector))
    else:
        cached.query_text = query
        cached.embedding = vector
    db.flush()
    return vector
