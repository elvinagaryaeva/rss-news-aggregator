from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from time import struct_time
from typing import Any

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Article, RSSFeed
from app.schemas import RefreshResult
from app.services.filter_service import FilterService
from app.models import Article, ArticleFilterMatch, Filter, RSSFeed
from app.services import event_bus

logger = logging.getLogger(__name__)


def _clean_text(value: Any) -> str | None:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", str(value))
    return re.sub(r"\s+", " ", unescape(text)).strip() or None


def _entry_datetime(entry: Any) -> datetime | None:
    parsed_value = entry.get("published_parsed") or entry.get("updated_parsed")
    if isinstance(parsed_value, struct_time):
        return datetime(*parsed_value[:6], tzinfo=UTC)
    raw_value = entry.get("published") or entry.get("updated")
    if raw_value:
        try:
            return parsedate_to_datetime(raw_value).astimezone(UTC)
        except (TypeError, ValueError):
            return None
    return None


def article_fingerprint(feed_url: str, entry: Any) -> str:
    """Derive an ID: guid first, canonical URL second, content fallback last."""
    guid = _clean_text(entry.get("id") or entry.get("guid"))
    url = _clean_text(entry.get("link"))
    if guid:
        identity = f"guid|{feed_url}|{guid}"
    elif url:
        identity = f"url|{url.rstrip('/')}"
    else:
        identity = "fallback|{}|{}|{}".format(feed_url, _clean_text(entry.get("title")) or "", _clean_text(entry.get("published") or entry.get("updated")) or "")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def parse_feed_content(content: bytes) -> Any:
    parsed = feedparser.parse(content)
    # A valid RSS channel may be empty before its first publication.
    if not parsed.version:
        details = getattr(parsed, "bozo_exception", None)
        raise ValueError(f"Invalid RSS/Atom feed{f': {details}' if details else ''}")
    return parsed


class RSSService:
    def __init__(self, db: Session, downloader: Callable[[str], bytes] | None = None) -> None:
        self.db = db
        self.downloader = downloader or self._download

    @staticmethod
    def _download(url: str) -> bytes:
        response = httpx.get(url, timeout=settings.rss_request_timeout_seconds, follow_redirects=True, headers={"User-Agent": "Phase1-RSS-Aggregator/1.0"})
        response.raise_for_status()
        return response.content

    def refresh_feed(self, feed: RSSFeed) -> RefreshResult:
        now = datetime.now(UTC)
        feed.last_fetched_at = now
        try:
            parsed = parse_feed_content(self.downloader(feed.url))
            added = 0
            skipped = 0
            new_articles: list[Article] = []
            for entry in parsed.entries:
                fingerprint = article_fingerprint(feed.url, entry)
                exists = self.db.scalar(select(Article.id).where(Article.fingerprint == fingerprint))
                if exists is not None:
                    skipped += 1
                    continue
                content_items = entry.get("content") or []
                content = content_items[0].get("value") if content_items else None
                article = Article(feed_id=feed.id, external_id=_clean_text(entry.get("id") or entry.get("guid")), fingerprint=fingerprint, title=_clean_text(entry.get("title")) or "Untitled article", url=_clean_text(entry.get("link")), summary=_clean_text(entry.get("summary") or entry.get("description")), content=_clean_text(content), author=_clean_text(entry.get("author")), published_at=_entry_datetime(entry), fetched_at=now)
                self.db.add(article)
                new_articles.append(article)
                added += 1
            # A refresh only evaluates its newly persisted articles, never the full corpus.
                        # A refresh only evaluates its newly persisted articles, never the full corpus.
            self.db.flush()
            matching_service = FilterService(self.db)
            for article in new_articles:
                matching_service.evaluate_new_article(article)
            self.db.flush()

            # Collect SSE payloads BEFORE commit (while identity map / lazy loads are cheap),
            # but publish them only AFTER a successful commit — otherwise we'd announce
            # articles that were rolled back.
            pending_events = []
            for article in new_articles:
                user_ids = sorted(
                    self.db.scalars(
                        select(Filter.user_id)
                        .join(ArticleFilterMatch, ArticleFilterMatch.filter_id == Filter.id)
                        .where(ArticleFilterMatch.article_id == article.id)
                        .distinct()
                    ).all()
                )
                if user_ids:
                    pending_events.append(
                        {
                            "type": "article.created",
                            "article_id": article.id,
                            "feed_id": article.feed_id,
                            "title": article.title,
                            "user_ids": user_ids,
                        }
                    )

            feed.last_success_at = now
            feed.last_error = None
            self.db.commit()

            for event in pending_events:
                event_bus.publish_from_thread(event)

            return RefreshResult(feed_id=feed.id, feed_name=feed.name, success=True, added=added, skipped=skipped)
        except Exception as exc:  # One source failing must not stop other feeds.
            self.db.rollback()
            feed.last_fetched_at = now
            feed.last_error = str(exc)[:2000]
            self.db.commit()
            logger.warning("RSS refresh failed for %s: %s", feed.url, exc)
            return RefreshResult(feed_id=feed.id, feed_name=feed.name, success=False, error=str(exc))

    def refresh_active_feeds(self) -> list[RefreshResult]:
        feeds = self.db.scalars(select(RSSFeed).where(RSSFeed.is_active.is_(True))).all()
        return [self.refresh_feed(feed) for feed in feeds]
