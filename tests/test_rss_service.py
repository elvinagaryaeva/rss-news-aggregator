from sqlalchemy import func, select

from app.models import Article, RSSFeed
from app.services.rss_service import RSSService, parse_feed_content


def test_parses_rss_fixture(sample_rss: bytes) -> None:
    parsed = parse_feed_content(sample_rss)
    assert len(parsed.entries) == 2
    assert parsed.entries[0].title == "First & useful article"


def test_saves_new_articles_and_skips_duplicates(database, sample_rss: bytes) -> None:
    feed = RSSFeed(name="Fixture", url="https://example.test/rss.xml")
    database.add(feed)
    database.commit()

    service = RSSService(database, downloader=lambda _: sample_rss)
    first = service.refresh_feed(feed)
    second = service.refresh_feed(feed)

    assert first.success is True
    assert first.added == 2
    assert second.success is True
    assert second.added == 0
    assert second.skipped == 2
    assert database.scalar(select(func.count()).select_from(Article)) == 2


def test_invalid_source_is_recorded_without_raising(database) -> None:
    feed = RSSFeed(name="Broken", url="https://example.test/broken.xml")
    database.add(feed)
    database.commit()

    result = RSSService(database, downloader=lambda _: (_ for _ in ()).throw(RuntimeError("network unavailable"))).refresh_feed(feed)
    database.refresh(feed)
    assert result.success is False
    assert "network unavailable" in (feed.last_error or "")
