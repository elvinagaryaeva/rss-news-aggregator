from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Article, ArticleEmbedding, ArticleFilterMatch, FeedSubscription, Filter, FilterEmbedding, RSSFeed, User
from app.services import semantic_service
from app.services.filter_service import FilterService
from app.services.rss_service import RSSService
from app.services.seed import local_test_feed_url
from app.services.user_service import UserService


def add_article(database, feed: RSSFeed, title: str, fingerprint: str) -> Article:
    article = Article(feed_id=feed.id, title=title, fingerprint=fingerprint, fetched_at=datetime.now(UTC))
    database.add(article)
    database.commit()
    return article


def test_users_creation_duplicate_selection_and_deleted_session_fallback(database) -> None:
    with TestClient(app) as client:
        alice = client.post("/api/users", json={"name": "Alice"})
        bob = client.post("/api/users", json={"name": "Bob"})
        assert alice.status_code == bob.status_code == 201
        assert client.post("/api/users", json={"name": "alice"}).status_code == 409
        assert len(client.get("/api/users").json()) == 3  # seeded demo
        alice_id = alice.json()["id"]
        bob_id = bob.json()["id"]
        assert client.get(f"/api/users/{bob_id}").json()["name"] == "Bob"
        assert client.post("/users/select", data={"user_id": alice_id}).status_code == 200
        assert "Filtered Feed · Alice" in client.get("/").text
        assert "Текущий: Alice" in client.get("/users").text
        assert "No RSS feeds subscribed yet" in client.get("/feeds").text
        assert "No filters configured yet" in client.get("/filters").text
        assert client.delete(f"/api/users/{alice_id}").status_code == 204
        assert "Filtered Feed · demo" in client.get("/").text
        assert client.get(f"/api/users/{alice_id}").status_code == 404
        assert client.delete(f"/api/users/{bob_id}").status_code == 204
        demo_id = database.scalar(select(User.id).where(User.name == "demo"))
        assert client.delete(f"/api/users/{demo_id}").status_code == 422


def test_subscribe_historical_keyword_matches_and_unsubscribe_cleanup(database) -> None:
    with TestClient(app) as client:
        alice = client.post("/api/users", json={"name": "Alice"}).json()["id"]
        bob = client.post("/api/users", json={"name": "Bob"}).json()["id"]
        feed = RSSFeed(name="Shared", url="https://example.test/shared.xml")
        database.add(feed)
        database.commit()
        article = add_article(database, feed, "OpenAI Python release", "shared-article")
        filter_a = client.post("/api/filters", json={"user_id": alice, "name": "AI", "keywords": ["OpenAI"]}).json()["filter"]["id"]
        filter_b = client.post("/api/filters", json={"user_id": bob, "name": "Python", "keywords": ["Python"]}).json()["filter"]["id"]
        assert client.get(f"/api/articles/filtered?user_id={alice}").json() == []
        assert client.get(f"/api/articles/filtered?user_id={bob}").json() == []
        subscribed_a = client.post(f"/api/users/{alice}/feeds/{feed.id}")
        assert subscribed_a.status_code == 200
        assert subscribed_a.json()["matched_existing_articles"] == 1
        assert len(client.get(f"/api/articles/filtered?user_id={alice}").json()) == 1
        assert client.get(f"/api/articles/filtered?user_id={bob}").json() == []
        subscribed_b = client.post(f"/api/users/{bob}/feeds/{feed.id}")
        assert subscribed_b.json()["matched_existing_articles"] == 1
        assert client.post(f"/api/users/{bob}/feeds/{feed.id}").json()["created"] is False
        assert database.scalar(select(func.count()).select_from(FeedSubscription).where(FeedSubscription.feed_id == feed.id)) == 2
        assert database.scalar(select(func.count()).select_from(ArticleFilterMatch).where(ArticleFilterMatch.article_id == article.id)) == 2
        assert client.patch(f"/api/filters/{filter_b}?user_id={alice}", json={"name": "stolen"}).status_code == 404
        assert client.get(f"/api/filters?user_id={alice}").json()[0]["id"] == filter_a
        assert client.get(f"/api/filters?user_id={bob}").json()[0]["id"] == filter_b
        assert client.delete(f"/api/users/{bob}/feeds/{feed.id}").status_code == 204
        assert client.delete(f"/api/users/{bob}/feeds/{feed.id}").status_code == 404
        assert client.get(f"/api/articles/filtered?user_id={bob}").json() == []
        assert len(client.get(f"/api/articles/filtered?user_id={alice}").json()) == 1
        assert database.scalar(select(func.count()).select_from(ArticleFilterMatch).where(ArticleFilterMatch.article_id == article.id)) == 1
        assert database.get(Article, article.id) is not None
        assert database.get(RSSFeed, feed.id) is not None


def test_existing_url_subscribes_without_duplicate_feed(database, monkeypatch) -> None:
    monkeypatch.setattr(RSSService, "_download", staticmethod(lambda _: b"<rss version='2.0'><channel><title>x</title></channel></rss>"))
    with TestClient(app) as client:
        alice = client.post("/api/users", json={"name": "Alice"}).json()["id"]
        bob = client.post("/api/users", json={"name": "Bob"}).json()["id"]
        url = "https://example.test/new.xml"
        first = client.post(f"/api/users/{alice}/feeds", json={"name": "New", "url": url})
        assert first.status_code == 200
        second = client.post(f"/api/users/{bob}/feeds", json={"name": "Another name", "url": url})
        assert second.status_code == 200
        assert database.scalar(select(func.count()).select_from(RSSFeed).where(RSSFeed.url == url)) == 1
        assert len(client.get(f"/api/users/{alice}/feeds").json()) == 1
        assert len(client.get(f"/api/users/{bob}/feeds").json()) == 1


def test_shared_semantic_embedding_and_user_deletion_preserves_global_data(database, monkeypatch) -> None:
    calls: list[str] = []

    def fake_encode(text: str) -> list[float]:
        calls.append(text)
        return [1.0, 0.0]

    monkeypatch.setattr(semantic_service, "encode_text", fake_encode)
    with TestClient(app) as client:
        alice = client.post("/api/users", json={"name": "Alice"}).json()["id"]
        bob = client.post("/api/users", json={"name": "Bob"}).json()["id"]
        feed = RSSFeed(name="Shared", url="https://example.test/semantic-shared.xml")
        database.add(feed)
        database.commit()
        article = add_article(database, feed, "Football striker signs", "shared-semantic")
        filter_a = client.post("/api/filters", json={"user_id": alice, "name": "A", "filter_type": "semantic", "semantic_query": "football transfers", "semantic_threshold": 0.2}).json()["filter"]["id"]
        filter_b = client.post("/api/filters", json={"user_id": bob, "name": "B", "filter_type": "semantic", "semantic_query": "football recruitment", "semantic_threshold": 0.2}).json()["filter"]["id"]
        assert client.post(f"/api/users/{alice}/feeds/{feed.id}").json()["matched_existing_articles"] == 1
        assert client.post(f"/api/users/{bob}/feeds/{feed.id}").json()["matched_existing_articles"] == 1
        assert database.scalar(select(func.count()).select_from(ArticleEmbedding).where(ArticleEmbedding.article_id == article.id)) == 1
        assert database.scalar(select(func.count()).select_from(ArticleFilterMatch).where(ArticleFilterMatch.article_id == article.id)) == 2
        assert len(calls) == 3  # two topics, one shared Article
        assert client.delete(f"/api/users/{alice}").status_code == 204
        assert database.get(Article, article.id) is not None
        assert database.get(RSSFeed, feed.id) is not None
        assert database.scalar(select(ArticleEmbedding).where(ArticleEmbedding.article_id == article.id)) is not None
        assert database.get(Filter, filter_a) is None
        assert database.scalar(select(FilterEmbedding).where(FilterEmbedding.filter_id == filter_a)) is None
        assert database.get(Filter, filter_b) is not None
        assert len(client.get(f"/api/articles/filtered?user_id={bob}").json()) == 1


def test_session_switches_filters_feeds_and_test_rss_visibility(database, monkeypatch) -> None:
    with TestClient(app) as client:
        def download(url: str) -> bytes:
            assert url == local_test_feed_url()
            return client.get("/rss/test.xml").content

        monkeypatch.setattr(RSSService, "_download", staticmethod(download))
        alice = client.post("/api/users", json={"name": "Alice"}).json()["id"]
        bob = client.post("/api/users", json={"name": "Bob"}).json()["id"]
        local = database.scalar(select(RSSFeed).where(RSSFeed.url == local_test_feed_url()))
        assert client.post(f"/api/users/{alice}/feeds/{local.id}").status_code == 200
        client.post("/api/filters", json={"user_id": alice, "name": "OpenAI", "keywords": ["OpenAI"]})
        client.post("/api/filters", json={"user_id": bob, "name": "Python", "keywords": ["Python"]})
        assert client.post("/users/select", data={"user_id": alice}).status_code == 200
        published = client.post("/test-rss", data={"title": "OpenAI announces model", "description": "New release"})
        assert published.status_code == 200
        assert "OpenAI announces model" in client.get("/").text
        assert "Filtered Feed · Alice" in client.get("/").text
        assert "Local Test RSS" in client.get("/feeds").text
        assert 'value="OpenAI"' in client.get("/filters").text
        assert 'value="Python"' not in client.get("/filters").text
        assert client.post("/users/select", data={"user_id": bob}).status_code == 200
        assert "Filtered Feed · Bob" in client.get("/").text
        assert "OpenAI announces model" not in client.get("/").text
        assert "No RSS feeds subscribed yet" in client.get("/feeds").text
        assert 'value="Python"' in client.get("/filters").text
        assert 'value="OpenAI"' not in client.get("/filters").text
        assert "не подписан на Local Test RSS" in client.get("/test-rss").text
        assert client.post("/users/select", data={"user_id": alice}).status_code == 200
        assert "OpenAI announces model" in client.get("/").text


def test_filter_forms_follow_selected_user_and_reject_cross_user_edits(database, monkeypatch) -> None:
    monkeypatch.setattr(semantic_service, "encode_text", lambda _text: [1.0, 0.0])
    with TestClient(app) as client:
        alice = client.post("/api/users", json={"name": "Alice"}).json()["id"]
        bob = client.post("/api/users", json={"name": "Bob"}).json()["id"]
        client.post("/users/select", data={"user_id": alice})
        created = client.post("/filters", data={"name": "Alice keyword", "filter_type": "keyword", "keywords": "OpenAI", "match_mode": "ANY"})
        assert created.status_code == 200
        alice_filter = client.get("/api/filters").json()[0]
        assert alice_filter["user_id"] == alice
        client.post("/users/select", data={"user_id": bob})
        assert client.get("/api/filters").json() == []
        assert client.post(f"/filters/{alice_filter['id']}", data={"name": "stolen", "keywords": "Python", "match_mode": "ANY"}).status_code == 404
        assert client.post(f"/filters/{alice_filter['id']}/delete").status_code == 404
        semantic = client.post("/filters", data={"name": "Bob semantic", "filter_type": "semantic", "semantic_query": "football transfers", "semantic_threshold": "0.20"})
        assert semantic.status_code == 200
        assert client.get("/api/filters").json()[0]["user_id"] == bob
        client.post("/users/select", data={"user_id": alice})
        assert [item["name"] for item in client.get("/api/filters").json()] == ["Alice keyword"]
