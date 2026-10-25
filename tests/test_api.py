from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Article, ArticleFilterMatch, User
from app.services.rss_service import RSSService


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_rss_and_keyword_api_lifecycle(database, monkeypatch) -> None:
    fixture = b"""<?xml version='1.0' encoding='utf-8'?>
    <rss version='2.0'><channel><title>Demo</title>
    <item><guid>ai-one</guid><title>OpenAI model release</title><link>https://example.test/one</link><description>New model</description></item>
    <item><guid>ai-two</guid><title>GPT research</title><link>https://example.test/two</link><description>Research note</description></item>
    <item><guid>other</guid><title>Weather report</title><link>https://example.test/three</link><description>Forecast</description></item>
    </channel></rss>"""

    def download(url: str) -> bytes:
        if url.endswith("/bad.xml"):
            raise RuntimeError("source unavailable")
        if url.endswith("/rss/test.xml"):
            return b"<rss version='2.0'><channel><title>Empty local feed</title></channel></rss>"
        return fixture

    monkeypatch.setattr(RSSService, "_download", staticmethod(download))
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/filters").status_code == 200

        added = client.post("/api/feeds", json={"name": "Demo", "url": "https://example.test/feed.xml"})
        assert added.status_code == 201
        assert added.json()["added"] == 3
        assert database.scalar(select(User).where(User.name == "demo")) is not None
        assert client.get("/api/feeds").json()[0]["name"] == "Demo"
        assert len(client.get("/api/articles").json()) == 3
        assert database.scalar(select(func.count()).select_from(Article)) == 3
        refreshed = client.post("/api/feeds/refresh").json()
        assert next(item for item in refreshed if item["feed_name"] == "Demo")["skipped"] == 3
        assert database.scalar(select(func.count()).select_from(Article)) == 3

        any_result = client.post("/api/filters", json={"name": "AI", "keywords": ["OpenAI", "GPT"], "match_mode": "ANY"})
        assert any_result.status_code == 201
        assert any_result.json()["matched_existing_articles"] == 2
        any_id = any_result.json()["filter"]["id"]
        filtered = client.get("/api/articles/filtered")
        assert filtered.status_code == 200
        assert len(filtered.json()) == 2
        assert all(item["matches"] for item in filtered.json())
        assert all(item["matches"][0]["method"] == "keyword" for item in filtered.json())
        assert all(item["matches"][0]["matched_keywords"] for item in filtered.json())
        assert all(item["matches"][0]["matched_fields"] for item in filtered.json())

        all_result = client.post("/api/filters", json={"name": "Both", "keywords": ["OpenAI", "model"], "match_mode": "ALL"})
        assert all_result.status_code == 201
        assert all_result.json()["matched_existing_articles"] == 1
        all_id = all_result.json()["filter"]["id"]
        assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 3

        updated = client.patch(f"/api/filters/{any_id}", json={"keywords": ["quantum"]})
        assert updated.status_code == 200
        assert updated.json()["matched_existing_articles"] == 0
        assert updated.json()["filter"]["updated_at"] != any_result.json()["filter"]["updated_at"]
        repeated = client.patch(f"/api/filters/{any_id}", json={"keywords": ["quantum"]})
        assert repeated.status_code == 200
        assert repeated.json()["matched_existing_articles"] == 0
        assert len(client.get("/api/articles/filtered").json()) == 1
        assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 1

        assert client.delete(f"/api/filters/{all_id}").status_code == 204
        assert client.get("/api/articles/filtered").json() == []
        assert database.scalar(select(func.count()).select_from(ArticleFilterMatch)) == 0

        bad = client.post("/api/feeds", json={"name": "Bad", "url": "https://example.test/bad.xml"})
        assert bad.status_code == 201
        assert bad.json()["success"] is False
        assert client.get("/api/health").json() == {"status": "ok"}
