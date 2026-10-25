from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import FeedSubscription, RSSFeed, User

LOCAL_TEST_FEED_NAME = "Local Test RSS"


def local_test_feed_url() -> str:
    return f"{settings.internal_base_url.rstrip('/')}/rss/test.xml"


def ensure_local_test_feed(db: Session) -> RSSFeed:
    """Register the app's XML endpoint as a normal RSS source."""
    url = local_test_feed_url()
    feed = db.scalar(select(RSSFeed).where(RSSFeed.url == url))
    if feed is None:
        feed = RSSFeed(name=LOCAL_TEST_FEED_NAME, url=url)
        db.add(feed)
        db.flush()
    return feed


def seed_default_data(db: Session) -> None:
    """Create the demo user, configurable demo feeds, and its subscriptions."""
    user = db.scalar(select(User).where(User.name == "demo"))
    if user is None:
        user = User(name="demo")
        db.add(user)
        db.flush()
    for name, url in settings.parsed_demo_feeds():
        feed = db.scalar(select(RSSFeed).where(RSSFeed.url == url))
        if feed is None:
            feed = RSSFeed(name=name, url=url)
            db.add(feed)
            db.flush()
        subscription = db.scalar(select(FeedSubscription).where(FeedSubscription.user_id == user.id, FeedSubscription.feed_id == feed.id))
        if subscription is None:
            db.add(FeedSubscription(user_id=user.id, feed_id=feed.id))
    local_feed = ensure_local_test_feed(db)
    local_subscription = db.scalar(
        select(FeedSubscription).where(
            FeedSubscription.user_id == user.id,
            FeedSubscription.feed_id == local_feed.id,
        )
    )
    if local_subscription is None:
        db.add(FeedSubscription(user_id=user.id, feed_id=local_feed.id))
    db.commit()


def subscribe_demo_user_to_feed(db: Session, feed: RSSFeed) -> None:
    """Keep newly added Phase 1 feeds compatible with future personal feeds."""
    user = db.scalar(select(User).where(User.name == "demo"))
    if user is None:
        user = User(name="demo")
        db.add(user)
        db.flush()
    existing = db.scalar(
        select(FeedSubscription).where(
            FeedSubscription.user_id == user.id, FeedSubscription.feed_id == feed.id
        )
    )
    if existing is None:
        db.add(FeedSubscription(user_id=user.id, feed_id=feed.id))
        db.commit()
