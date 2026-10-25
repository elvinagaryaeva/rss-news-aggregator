from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import feedparser
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Article, ArticleFilterMatch, FeedSubscription, RSSFeed, TestRSSPost as RSSPost, User
from app.services.rss_service import RSSService
from app.services.seed import local_test_feed_url


def test_empty_local_rss_and_seed(database) -> None:
    with TestClient(app) as client:
        response = client.get("/rss/test.xml")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/rss+xml")
        root = ET.fromstring(response.content)
        assert root.tag == "rss"
        assert root.find("channel/title").text == "Local Test RSS"
        assert root.findall("channel/item") == []

    feed = database.scalar(select(RSSFeed).where(RSSFeed.url == local_test_feed_url()))
    demo = database.scalar(select(User).where(User.name == "demo"))
    assert feed is not None
    assert demo is not None
    assert database.scalar(
        select(FeedSubscription).where(
            FeedSubscription.user_id == demo.id, FeedSubscription.feed_id == feed.id
        )
    ) is not None


def test_xml_escapes_special_characters_and_preserves_guid(database) -> None:
    with TestClient(app) as client:
        post = RSSPost(
            title='OpenAI & <model> "release"',
            description="A & B < C > D",
            content="Plain text & <tags>",
            author="A & B",
            published_at=datetime.now(UTC),
        )
        database.add(post)
        database.commit()
        database.refresh(post)

        response = client.get("/rss/test.xml")
        assert b"&amp;" in response.content
        assert b"&lt;model&gt;" in response.content
        item = ET.fromstring(response.content).find("channel/item")
        assert item is not None
        assert item.find("title").text == post.title
        assert item.find("description").text == post.description
        assert item.find("guid").text == post.guid
        assert item.find("link").text.endswith(f"/test-rss/posts/{post.id}")
        assert item.find("pubDate") is not None
        assert feedparser.parse(response.content).entries[0].published_parsed is not None
        assert client.get(f"/test-rss/posts/{post.id}").status_code == 200
        assert client.get(f"/api/test-rss/posts/{post.id}").json()["guid"] == post.guid


def test_publishing_uses_rss_ingestion_filtering_and_deduplication(database, monkeypatch) -> None:
    with TestClient(app) as client:
        def download(url: str) -> bytes:
            assert url == local_test_feed_url()
            response = client.get("/rss/test.xml")
            assert response.status_code == 200
            return response.content

        monkeypatch.setattr(RSSService, "_download", staticmethod(download))
        created_filter = client.post(
            "/api/filters",
            json={"name": "Football", "keywords": ["Spartak"], "match_mode": "ANY"},
        )
        assert created_filter.status_code == 201

        published = client.post(
            "/api/test-rss/posts",
            json={
                "title": "Spartak signs a player",
                "description": "A new midfielder has joined.",
                "content": "Additional details of the signing.",
                "author": "Sports desk",
            },
        )
        assert published.status_code == 201
        payload = published.json()
        assert payload["refresh"]["success"] is True
        assert payload["refresh"]["added"] == 1
        assert payload["post"]["guid"]
        assert payload["post"]["published_at"]
        assert client.get("/api/test-rss/posts").json()[0]["guid"] == payload["post"]["guid"]

        feed = database.scalar(select(RSSFeed).where(RSSFeed.url == local_test_feed_url()))
        article = database.scalar(select(Article).where(Article.external_id == payload["post"]["guid"]))
        assert article is not None
        assert article.feed_id == feed.id
        assert article.content == "Additional details of the signing."
        match = database.scalar(select(ArticleFilterMatch).where(ArticleFilterMatch.article_id == article.id))
        assert match is not None
        assert match.method == "keyword"
        assert match.matched_keywords == ["Spartak"]
        assert match.matched_fields == {"Spartak": ["title"]}
        assert len(client.get("/api/articles/filtered").json()) == 1
        assert "Spartak" in client.get("/").text

        second = client.post(
            "/api/test-rss/posts",
            json={"title": "Weather report", "description": "Sunny today."},
        )
        assert second.status_code == 201
        assert second.json()["refresh"]["added"] == 1
        assert second.json()["post"]["guid"] != payload["post"]["guid"]
        assert database.scalar(select(func.count()).select_from(RSSPost)) == 2
        assert database.scalar(select(func.count()).select_from(Article)) == 2
        assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 1
        assert len(client.get("/api/articles/filtered").json()) == 1

        for _ in range(5):
            result = RSSService(database).refresh_feed(feed)
            assert result.success is True
            assert result.added == 0
            assert result.skipped == 2
        assert database.scalar(select(func.count()).select_from(Article)) == 2

        form_result = client.post(
            "/test-rss",
            data={"title": "Spartak wins again", "description": "Another win."},
            follow_redirects=False,
        )
        assert form_result.status_code == 303
        assert database.scalar(select(func.count()).select_from(RSSPost)) == 3
        assert database.scalar(select(func.count()).select_from(Article)) == 3
        assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 2
        assert "Загружена агрегатором" in client.get("/test-rss").text
