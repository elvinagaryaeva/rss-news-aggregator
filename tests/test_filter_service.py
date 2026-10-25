from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import Article, ArticleFilterMatch, FeedSubscription, Filter, RSSFeed, User
from app.services.filter_service import FilterService, evaluate_keyword_filter
from app.services.rss_service import RSSService


def make_filter(database, user: User, keywords: list[str], mode: str = "ANY") -> Filter:
    filter_ = Filter(user_id=user.id, name="Test filter", filter_type="keyword", match_mode=mode, is_active=True)
    database.add(filter_)
    database.flush()
    FilterService(database).set_keywords(filter_, keywords)
    database.flush()
    return filter_


def make_article(database, feed: RSSFeed, title: str = "", summary: str | None = None, content: str | None = None, fingerprint: str = "test-fingerprint") -> Article:
    article = Article(feed_id=feed.id, fingerprint=fingerprint, title=title or "Untitled", summary=summary, content=content, fetched_at=datetime.now(UTC))
    database.add(article)
    database.flush()
    return article


@pytest.fixture
def subscribed_context(database):
    user = User(name="reader")
    feed = RSSFeed(name="Subscribed", url="https://example.test/subscribed.xml")
    database.add_all([user, feed])
    database.flush()
    database.add(FeedSubscription(user_id=user.id, feed_id=feed.id))
    database.commit()
    return user, feed


def test_any_matches_one_keyword(database, subscribed_context) -> None:
    user, feed = subscribed_context
    article = make_article(database, feed, title="Python release", fingerprint="any")
    filter_ = make_filter(database, user, ["python", "java"], "ANY")
    result = evaluate_keyword_filter(article, filter_)
    assert result.matched is True
    assert result.matched_keywords == ["python"]
    assert result.matched_fields == {"python": ["title"]}
    assert result.score == 0.5


def test_all_requires_every_keyword(database, subscribed_context) -> None:
    user, feed = subscribed_context
    filter_ = make_filter(database, user, ["python", "fastapi"], "ALL")
    only_python = make_article(database, feed, title="Python release", fingerprint="all-one")
    both = make_article(database, feed, title="Python with FastAPI", fingerprint="all-two")
    assert evaluate_keyword_filter(only_python, filter_).matched is False
    assert evaluate_keyword_filter(both, filter_).matched is True


def test_case_insensitive_phrase_and_each_article_field(database, subscribed_context) -> None:
    user, feed = subscribed_context
    filter_ = make_filter(database, user, ["MACHINE learning"], "ANY")
    title_article = make_article(database, feed, title="Machine Learning report", fingerprint="field-title")
    summary_article = make_article(database, feed, summary="new MACHINE LEARNING news", fingerprint="field-summary")
    content_article = make_article(database, feed, content="about machine learning systems", fingerprint="field-content")
    assert evaluate_keyword_filter(title_article, filter_).matched_fields == {"MACHINE learning": ["title"]}
    assert evaluate_keyword_filter(summary_article, filter_).matched_fields == {"MACHINE learning": ["summary"]}
    assert evaluate_keyword_filter(content_article, filter_).matched_fields == {"MACHINE learning": ["content"]}


def test_short_keyword_does_not_match_inside_a_larger_word(database, subscribed_context) -> None:
    user, feed = subscribed_context
    filter_ = make_filter(database, user, ["AI"])
    article = make_article(database, feed, title="A chair review", fingerprint="word-boundary")
    assert evaluate_keyword_filter(article, filter_).matched is False


def test_explainability_and_match_persistence_are_idempotent(database, subscribed_context) -> None:
    user, feed = subscribed_context
    article = make_article(database, feed, title="Python news", summary="FastAPI guide", content="FASTAPI examples", fingerprint="explain")
    filter_ = make_filter(database, user, ["python", "fastapi"], "ALL")
    service = FilterService(database)
    assert service.evaluate_filter_against_existing_articles(filter_) == 1
    database.commit()
    match = database.scalar(select(ArticleFilterMatch))
    assert match is not None
    assert match.method == "keyword"
    assert match.score == 1.0
    assert match.matched_keywords == ["python", "fastapi"]
    assert match.matched_fields == {"python": ["title"], "fastapi": ["summary", "content"]}
    assert match.details["match_mode"] == "ALL"
    assert service.evaluate_filter_against_existing_articles(filter_) == 1
    database.commit()
    assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 1


def test_filter_update_rebuilds_and_delete_cascades_matches(database, subscribed_context) -> None:
    user, feed = subscribed_context
    make_article(database, feed, title="Python news", fingerprint="update")
    filter_ = make_filter(database, user, ["python"])
    service = FilterService(database)
    assert service.evaluate_filter_against_existing_articles(filter_) == 1
    database.commit()
    service.set_keywords(filter_, ["java"])
    database.flush()
    assert service.evaluate_filter_against_existing_articles(filter_) == 0
    database.commit()
    assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 0
    service.set_keywords(filter_, ["python"])
    database.flush()
    service.evaluate_filter_against_existing_articles(filter_)
    database.commit()
    database.delete(filter_)
    database.commit()
    assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 0


def test_subscription_scope_excludes_other_feed(database) -> None:
    user = User(name="user-a")
    feed_a = RSSFeed(name="A", url="https://example.test/a.xml")
    feed_b = RSSFeed(name="B", url="https://example.test/b.xml")
    database.add_all([user, feed_a, feed_b])
    database.flush()
    database.add(FeedSubscription(user_id=user.id, feed_id=feed_a.id))
    database.commit()
    make_article(database, feed_b, title="Python article", fingerprint="unsubscribed")
    filter_ = make_filter(database, user, ["python"])
    service = FilterService(database)
    assert service.evaluate_filter_against_existing_articles(filter_) == 0
    database.commit()
    assert service.filtered_matches_for_user(user) == []


def test_new_rss_article_is_matched_during_ingestion(database, subscribed_context) -> None:
    user, feed = subscribed_context
    make_filter(database, user, ["python"])
    database.commit()
    rss = b"""<?xml version='1.0'?><rss version='2.0'><channel><title>x</title><item><guid>new-python</guid><title>Python from RSS</title><link>https://example.test/new</link></item></channel></rss>"""
    result = RSSService(database, downloader=lambda _: rss).refresh_feed(feed)
    assert result.added == 1
    assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 1
