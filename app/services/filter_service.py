"""Keyword and semantic filtering with persisted, explainable matches."""
from __future__ import annotations

import re
import unicodedata
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.models import Article, ArticleFilterMatch, FeedSubscription, Filter, FilterKeyword, User
from app.services import semantic_service

KEYWORD_FILTER_TYPE = "keyword"
SEMANTIC_FILTER_TYPE = "semantic"
DEFAULT_SEMANTIC_THRESHOLD = 0.20
MATCH_ANY = "ANY"
MATCH_ALL = "ALL"
SEARCHABLE_FIELDS = ("title", "summary", "content")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeywordEvaluation:
    matched: bool
    score: float
    matched_keywords: list[str]
    matched_fields: dict[str, list[str]]
    details: dict


def normalize_text(value: str | None) -> str:
    """Unicode-safe, case-insensitive text normalization without stemming."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def parse_keywords(values: Iterable[str]) -> list[tuple[str, str]]:
    """Return original and normalized unique keywords; commas and newlines are separators."""
    unique: dict[str, str] = {}
    for value in values:
        for item in re.split(r"[,\n\r]+", value):
            original = re.sub(r"\s+", " ", item).strip()
            normalized = normalize_text(original)
            if normalized:
                unique.setdefault(normalized, original)
    return [(original, normalized) for normalized, original in unique.items()]


def _keyword_occurs(keyword: str, text: str) -> bool:
    """Match complete Unicode words/phrases, preventing e.g. 'AI' inside 'chair'."""
    return re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text) is not None


def evaluate_keyword_filter(article: Article, filter_: Filter) -> KeywordEvaluation:
    if filter_.filter_type != KEYWORD_FILTER_TYPE:
        raise ValueError(f"Unsupported filter type: {filter_.filter_type}")
    keywords = list(filter_.keywords)
    if not keywords:
        return KeywordEvaluation(False, 0.0, [], {}, {"match_mode": filter_.match_mode, "total_keywords": 0, "matched_count": 0, "matches": {}})

    normalized_fields = {field: normalize_text(getattr(article, field)) for field in SEARCHABLE_FIELDS}
    matched_fields: dict[str, list[str]] = {}
    for keyword in keywords:
        fields = [field for field, text in normalized_fields.items() if _keyword_occurs(keyword.normalized_keyword, text)]
        if fields:
            matched_fields[keyword.keyword] = fields

    matched_keywords = list(matched_fields)
    matched_count = len(matched_keywords)
    match_mode = filter_.match_mode.upper()
    is_match = matched_count > 0 if match_mode == MATCH_ANY else matched_count == len(keywords)
    details = {
        "match_mode": match_mode,
        "total_keywords": len(keywords),
        "matched_count": matched_count,
        "matches": matched_fields,
    }
    return KeywordEvaluation(
        matched=is_match,
        score=matched_count / len(keywords),
        matched_keywords=matched_keywords,
        matched_fields=matched_fields,
        details=details,
    )


class FilterService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_user(self, user_id: int | None = None) -> User:
        if user_id is not None:
            user = self.db.get(User, user_id)
        else:
            user = self.db.scalar(select(User).where(User.name == "demo"))
        if user is None:
            raise LookupError("User was not found")
        return user

    def set_keywords(self, filter_: Filter, raw_keywords: Iterable[str]) -> None:
        parsed = parse_keywords(raw_keywords)
        if not parsed:
            raise ValueError("At least one non-empty keyword is required")
        existing = {item.normalized_keyword: item for item in list(filter_.keywords)}
        requested = {normalized for _, normalized in parsed}
        for normalized, item in existing.items():
            if normalized not in requested:
                filter_.keywords.remove(item)
        # Delete orphans before inserting replacements with a unique normalized key.
        self.db.flush()
        for original, normalized in parsed:
            if normalized in existing:
                existing[normalized].keyword = original
            else:
                filter_.keywords.append(FilterKeyword(keyword=original, normalized_keyword=normalized))

    @staticmethod
    def set_semantic_config(filter_: Filter, query: str | None, threshold: float | None) -> None:
        query = (query or "").strip()
        if not query:
            raise ValueError("Semantic query is required")
        if threshold is None or not 0.0 <= threshold <= 1.0:
            raise ValueError("Semantic threshold must be between 0 and 1")
        filter_.semantic_query = query
        filter_.semantic_threshold = threshold

    def _upsert_match(self, article: Article, filter_: Filter, result: KeywordEvaluation, method: str) -> bool:
        existing = self.db.scalar(select(ArticleFilterMatch).where(ArticleFilterMatch.article_id == article.id, ArticleFilterMatch.filter_id == filter_.id))
        if not result.matched:
            if existing is not None:
                self.db.delete(existing)
            return False
        values = {
            "method": method,
            "score": result.score,
            "matched_keywords": result.matched_keywords,
            "matched_fields": result.matched_fields,
            "details": result.details,
        }
        if existing is None:
            self.db.add(ArticleFilterMatch(article_id=article.id, filter_id=filter_.id, **values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        return True

    def evaluate_article_against_filter(self, article: Article, filter_: Filter) -> bool:
        if filter_.filter_type == KEYWORD_FILTER_TYPE:
            return self._upsert_match(article, filter_, evaluate_keyword_filter(article, filter_), KEYWORD_FILTER_TYPE)
        if filter_.filter_type == SEMANTIC_FILTER_TYPE:
            query_vector = semantic_service.filter_embedding(self.db, filter_)
            article_vector = semantic_service.article_embedding(self.db, article)
            similarity = semantic_service.cosine_similarity(query_vector, article_vector)
            threshold = filter_.semantic_threshold
            if threshold is None:
                raise ValueError("Semantic threshold is required")
            details = {
                "semantic_query": filter_.semantic_query,
                "similarity": similarity,
                "threshold": threshold,
                "model": semantic_service.settings.semantic_model_name,
            }
            result = KeywordEvaluation(similarity >= threshold, similarity, [], {}, details)
            return self._upsert_match(article, filter_, result, SEMANTIC_FILTER_TYPE)
        raise ValueError(f"Unsupported filter type: {filter_.filter_type}")

    def evaluate_filter_against_existing_articles(self, filter_: Filter) -> int:
        """Rebuild one filter against only the feeds subscribed by its owner."""
        self.db.execute(delete(ArticleFilterMatch).where(ArticleFilterMatch.filter_id == filter_.id))
        if not filter_.is_active:
            return 0
        if filter_.filter_type == SEMANTIC_FILTER_TYPE:
            semantic_service.filter_embedding(self.db, filter_)
        articles = self.db.scalars(
            select(Article)
            .join(FeedSubscription, FeedSubscription.feed_id == Article.feed_id)
            .where(FeedSubscription.user_id == filter_.user_id)
            .order_by(Article.id)
        ).all()
        matched_count = 0
        for article in articles:
            if self.evaluate_article_against_filter(article, filter_):
                matched_count += 1
        return matched_count

    def evaluate_new_article(self, article: Article) -> int:
        """Evaluate active filters relevant to this article's subscribed feed."""
        filters = self.db.scalars(
            select(Filter)
            .join(FeedSubscription, FeedSubscription.user_id == Filter.user_id)
            .options(selectinload(Filter.keywords))
            .where(
                FeedSubscription.feed_id == article.feed_id,
                Filter.is_active.is_(True),
                Filter.filter_type.in_((KEYWORD_FILTER_TYPE, SEMANTIC_FILTER_TYPE)),
            )
        ).unique().all()
        matched = 0
        for filter_ in filters:
            try:
                matched += self.evaluate_article_against_filter(article, filter_)
            except Exception:
                if filter_.filter_type != SEMANTIC_FILTER_TYPE:
                    raise
                logger.exception("Semantic evaluation failed for article %s and filter %s", article.id, filter_.id)
        return matched

    def filtered_matches_for_user(self, user: User) -> list[ArticleFilterMatch]:
        return list(
            self.db.scalars(
                select(ArticleFilterMatch)
                .join(ArticleFilterMatch.filter)
                .join(ArticleFilterMatch.article)
                .join(FeedSubscription, FeedSubscription.feed_id == Article.feed_id)
                .options(
                    selectinload(ArticleFilterMatch.article).selectinload(Article.feed),
                    selectinload(ArticleFilterMatch.filter).selectinload(Filter.keywords),
                )
                .where(
                    Filter.user_id == user.id,
                    Filter.is_active.is_(True),
                    FeedSubscription.user_id == user.id,
                )
                .order_by(Article.published_at.desc().nullslast(), Article.id.desc())
            ).all()
        )
