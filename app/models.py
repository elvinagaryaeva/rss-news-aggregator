from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    subscriptions: Mapped[list[FeedSubscription]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    filters: Mapped[list[Filter]] = relationship(back_populates="user", cascade="all, delete-orphan")


class RSSFeed(TimestampMixin, Base):
    __tablename__ = "rss_feeds"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    subscriptions: Mapped[list[FeedSubscription]] = relationship(
        back_populates="feed", cascade="all, delete-orphan"
    )
    articles: Mapped[list[Article]] = relationship(back_populates="feed", cascade="all, delete-orphan")


class FeedSubscription(TimestampMixin, Base):
    __tablename__ = "feed_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "feed_id", name="uq_subscription_user_feed"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    feed_id: Mapped[int] = mapped_column(ForeignKey("rss_feeds.id", ondelete="CASCADE"), nullable=False)
    user: Mapped[User] = relationship(back_populates="subscriptions")
    feed: Mapped[RSSFeed] = relationship(back_populates="subscriptions")


class Article(TimestampMixin, Base):
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("rss_feeds.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(2048))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048))
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feed: Mapped[RSSFeed] = relationship(back_populates="articles")
    filter_matches: Mapped[list[ArticleFilterMatch]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list[ArticleEmbedding]] = relationship(back_populates="article", cascade="all, delete-orphan")


class Filter(TimestampMixin, Base):
    __tablename__ = "filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    filter_type: Mapped[str] = mapped_column(String(32), default="keyword", nullable=False)
    match_mode: Mapped[str] = mapped_column(String(16), default="ANY", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    semantic_query: Mapped[str | None] = mapped_column(Text)
    semantic_threshold: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    user: Mapped[User] = relationship(back_populates="filters")
    keywords: Mapped[list[FilterKeyword]] = relationship(
        back_populates="filter", cascade="all, delete-orphan", order_by="FilterKeyword.id"
    )
    article_matches: Mapped[list[ArticleFilterMatch]] = relationship(
        back_populates="filter", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list[FilterEmbedding]] = relationship(back_populates="filter", cascade="all, delete-orphan")


class FilterKeyword(TimestampMixin, Base):
    __tablename__ = "filter_keywords"
    __table_args__ = (UniqueConstraint("filter_id", "normalized_keyword", name="uq_filter_keyword_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filter_id: Mapped[int] = mapped_column(ForeignKey("filters.id", ondelete="CASCADE"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    filter: Mapped[Filter] = relationship(back_populates="keywords")


class ArticleFilterMatch(TimestampMixin, Base):
    __tablename__ = "article_filter_matches"
    __table_args__ = (UniqueConstraint("article_id", "filter_id", name="uq_article_filter_match"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    filter_id: Mapped[int] = mapped_column(ForeignKey("filters.id", ondelete="CASCADE"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    matched_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    matched_fields: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    article: Mapped[Article] = relationship(back_populates="filter_matches")
    filter: Mapped[Filter] = relationship(back_populates="article_matches")


class ArticleEmbedding(TimestampMixin, Base):
    __tablename__ = "article_embeddings"
    __table_args__ = (UniqueConstraint("article_id", "model_name", name="uq_article_embedding_model"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    article: Mapped[Article] = relationship(back_populates="embeddings")


class FilterEmbedding(TimestampMixin, Base):
    __tablename__ = "filter_embeddings"
    __table_args__ = (UniqueConstraint("filter_id", "model_name", name="uq_filter_embedding_model"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filter_id: Mapped[int] = mapped_column(ForeignKey("filters.id", ondelete="CASCADE"), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    filter: Mapped[Filter] = relationship(back_populates="embeddings")


class TestRSSPost(TimestampMixin, Base):
    """Published source item; Article is created only by RSS ingestion."""

    __tablename__ = "test_rss_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
