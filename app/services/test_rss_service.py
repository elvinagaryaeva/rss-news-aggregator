"""Own RSS source storage, XML rendering, and targeted ingestion."""
from __future__ import annotations

from datetime import UTC
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import TestRSSPost
from app.schemas import RefreshResult
from app.services.rss_service import RSSService
from app.services.seed import ensure_local_test_feed

CONTENT_NAMESPACE = "http://purl.org/rss/1.0/modules/content/"
ET.register_namespace("content", CONTENT_NAMESPACE)


def _public_url(path: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}{path}"


def render_test_rss(posts: list[TestRSSPost]) -> bytes:
    """Build valid RSS 2.0; ElementTree escapes user text for XML."""
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Local Test RSS"
    ET.SubElement(channel, "link").text = _public_url("/test-rss")
    ET.SubElement(channel, "description").text = "Articles published through the RSS Aggregator demo interface"

    for post in posts:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = post.guid
        ET.SubElement(item, "title").text = post.title
        ET.SubElement(item, "link").text = _public_url(f"/test-rss/posts/{post.id}")
        ET.SubElement(item, "description").text = post.description
        published = post.published_at
        if published.tzinfo is None:  # SQLite tests do not retain timezone information.
            published = published.replace(tzinfo=UTC)
        ET.SubElement(item, "pubDate").text = format_datetime(published.astimezone(UTC), usegmt=True)
        if post.author:
            ET.SubElement(item, "author").text = post.author
        if post.content:
            ET.SubElement(item, f"{{{CONTENT_NAMESPACE}}}encoded").text = post.content

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def list_test_posts(db: Session) -> list[TestRSSPost]:
    return list(db.scalars(select(TestRSSPost).order_by(TestRSSPost.published_at.desc(), TestRSSPost.id.desc())).all())


def publish_test_post(
    db: Session,
    *,
    title: str,
    description: str,
    content: str | None = None,
    author: str | None = None,
) -> tuple[TestRSSPost, RefreshResult]:
    title = title.strip()
    description = description.strip()
    if not title or not description:
        raise ValueError("Title and description are required")

    feed = ensure_local_test_feed(db)
    post = TestRSSPost(
        title=title,
        description=description,
        content=content.strip() or None if content else None,
        author=author.strip() or None if author else None,
    )
    db.add(post)
    db.commit()  # The XML endpoint must see the post before HTTP ingestion starts.
    db.refresh(post)
    return post, RSSService(db).refresh_feed(feed)
