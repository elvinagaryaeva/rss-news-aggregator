from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Article, ArticleEmbedding, ArticleFilterMatch, FeedSubscription, Filter, FilterEmbedding, RSSFeed, User
from app.services import semantic_service
from app.services.filter_service import FilterService
from app.services.rss_service import RSSService


@pytest.fixture
def fake_encoder(monkeypatch):
    calls: list[str] = []

    def encode(text: str) -> list[float]:
        calls.append(text)
        if "football" in text.lower() or "striker" in text.lower():
            return [1.0, 0.0]
        return [0.0, 1.0]

    monkeypatch.setattr(semantic_service, "encode_text", encode)
    return calls


def context(database):
    user = User(name="reader")
    feed = RSSFeed(name="Subscribed", url="https://example.test/semantic.xml")
    other = RSSFeed(name="Other", url="https://example.test/other.xml")
    database.add_all([user, feed, other])
    database.flush()
    database.add(FeedSubscription(user_id=user.id, feed_id=feed.id))
    database.commit()
    return user, feed, other


def article(database, feed, title, fingerprint):
    item = Article(feed_id=feed.id, title=title, fingerprint=fingerprint, fetched_at=datetime.now(UTC))
    database.add(item)
    database.flush()
    return item


def semantic_filter(database, user, query="football transfers", threshold=0.6):
    item = Filter(user_id=user.id, name="Sports", filter_type="semantic", match_mode="ANY", semantic_query=query, semantic_threshold=threshold, is_active=True)
    database.add(item)
    database.flush()
    return item


def test_similarity_threshold_and_explanation(database, fake_encoder):
    user, feed, _ = context(database)
    passing = article(database, feed, "Football striker signs", "sports")
    failing = article(database, feed, "Windows security patch", "windows")
    filter_ = semantic_filter(database, user)
    service = FilterService(database)
    assert service.evaluate_filter_against_existing_articles(filter_) == 1
    database.commit()
    match = database.scalar(select(ArticleFilterMatch))
    assert match.article_id == passing.id
    assert match.method == "semantic"
    assert match.score == 1.0
    assert match.matched_keywords == []
    assert match.matched_fields == {}
    assert match.details == {
        "semantic_query": "football transfers", "similarity": 1.0, "threshold": 0.6,
        "model": semantic_service.settings.semantic_model_name,
    }
    assert database.scalar(select(func.count()).select_from(ArticleEmbedding)) == 2
    assert database.scalar(select(func.count()).select_from(FilterEmbedding)) == 1
    assert service.evaluate_filter_against_existing_articles(filter_) == 1
    database.commit()
    assert len(fake_encoder) == 3  # topic and each article encoded only once
    assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 1
    assert failing.id != match.article_id


def test_equal_threshold_matches_and_query_threshold_updates(database, fake_encoder):
    user, feed, _ = context(database)
    article(database, feed, "Football striker signs", "sports")
    filter_ = semantic_filter(database, user, threshold=1.0)
    service = FilterService(database)
    assert service.evaluate_filter_against_existing_articles(filter_) == 1  # equality passes
    database.commit()
    filter_.semantic_threshold = 0.99
    assert service.evaluate_filter_against_existing_articles(filter_) == 1
    database.commit()
    assert len(fake_encoder) == 2  # threshold change uses cached vectors
    filter_.semantic_query = "Windows security"
    assert service.evaluate_filter_against_existing_articles(filter_) == 0
    database.commit()
    assert len(fake_encoder) == 3  # changed topic re-encoded; article reused
    assert database.scalar(select(FilterEmbedding)).query_text == "Windows security"
    assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 0
    filter_.semantic_threshold = 0.0
    assert service.evaluate_filter_against_existing_articles(filter_) == 1
    database.commit()


def test_subscription_scope_and_new_rss_article(database, fake_encoder):
    user, feed, other = context(database)
    article(database, other, "Football striker elsewhere", "unsubscribed")
    filter_ = semantic_filter(database, user)
    service = FilterService(database)
    assert service.evaluate_filter_against_existing_articles(filter_) == 0
    database.commit()
    assert database.scalar(select(func.count()).select_from(ArticleEmbedding)) == 0
    rss = b"""<rss version='2.0'><channel><title>x</title><item><guid>new</guid><title>Football striker signs</title></item></channel></rss>"""
    assert RSSService(database, downloader=lambda _: rss).refresh_feed(feed).added == 1
    assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 1
    assert len(service.filtered_matches_for_user(user)) == 1


def test_new_article_embedding_is_shared_across_semantic_filters(database, fake_encoder):
    user, feed, _ = context(database)
    semantic_filter(database, user, query="football transfers")
    semantic_filter(database, user, query="football recruitment")
    database.commit()
    rss = b"""<rss version='2.0'><channel><title>x</title><item><guid>shared</guid><title>Football striker signs</title></item></channel></rss>"""
    assert RSSService(database, downloader=lambda _: rss).refresh_feed(feed).added == 1
    assert database.scalar(select(func.count()).select_from(ArticleEmbedding)) == 1
    assert database.scalar(select(func.count()).select_from(FilterEmbedding)) == 2
    assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 2
    assert len(fake_encoder) == 3  # two topics, one article


def test_unavailable_semantic_model_does_not_break_keyword_ingestion(database, monkeypatch):
    user, feed, _ = context(database)
    semantic_filter(database, user)
    keyword = Filter(user_id=user.id, name="Keyword", filter_type="keyword", match_mode="ANY", is_active=True)
    database.add(keyword)
    database.flush()
    FilterService(database).set_keywords(keyword, ["football"])
    database.commit()

    def unavailable(_text):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(semantic_service, "encode_text", unavailable)
    rss = b"""<rss version='2.0'><channel><title>x</title><item><guid>offline</guid><title>Football striker signs</title></item></channel></rss>"""
    assert RSSService(database, downloader=lambda _: rss).refresh_feed(feed).success is True
    matches = database.scalars(select(ArticleFilterMatch)).all()
    assert len(matches) == 1
    assert matches[0].method == "keyword"


def test_semantic_api_and_ui_with_fake_encoder(database, fake_encoder):
    with TestClient(app) as client:
        created = client.post("/api/filters", json={"name": "Sports", "filter_type": "semantic", "semantic_query": "football transfers", "semantic_threshold": 0.6})
        assert created.status_code == 201, created.text
        filter_id = created.json()["filter"]["id"]
        assert created.json()["filter"]["semantic_query"] == "football transfers"
        assert client.get("/api/filters").json()[0]["semantic_threshold"] == 0.6
        assert "Semantic-фильтр" in client.get("/filters").text
        assert "football transfers" in client.get("/filters").text
        assert client.patch(f"/api/filters/{filter_id}", json={"semantic_query": "Windows security", "semantic_threshold": 0.75}).status_code == 200
        assert client.post("/api/filters", json={"name": "bad", "filter_type": "semantic", "keywords": ["football"], "semantic_query": "topic"}).status_code == 422
        assert client.post("/api/filters", json={"name": "bad", "filter_type": "semantic", "semantic_query": "topic", "semantic_threshold": 1.2}).status_code == 422
        assert client.post("/api/filters", json={"name": "bad", "filter_type": "keyword", "keywords": []}).status_code == 422
        defaulted = client.post("/api/filters", json={"name": "Default threshold", "filter_type": "semantic", "semantic_query": "football"})
        assert defaulted.status_code == 201
        assert defaulted.json()["filter"]["semantic_threshold"] == 0.20
        assert client.delete(f"/api/filters/{filter_id}").status_code == 204
        assert database.scalar(select(FilterEmbedding).where(FilterEmbedding.filter_id == filter_id)) is None


def test_cosine_similarity_boundaries():
    assert semantic_service.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert semantic_service.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert semantic_service.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0
