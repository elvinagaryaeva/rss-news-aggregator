"""Demo user selection and per-user RSS subscriptions."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Article, ArticleFilterMatch, FeedSubscription, Filter, RSSFeed, User
from app.services.filter_service import FilterService


@dataclass(frozen=True)
class SubscriptionResult:
    created: bool
    matched_existing_articles: int = 0


class DuplicateUserError(ValueError):
    """A user with the same name already exists."""


def current_user(db: Session, request: Request) -> User:
    """Use a valid selected user, otherwise restore the seeded demo user."""
    selected_id = request.session.get("current_user_id")
    user = db.get(User, selected_id) if isinstance(selected_id, int) else None
    if user is None:
        user = db.scalar(select(User).where(User.name == "demo"))
        if user is None:
            raise LookupError("Default demo user was not found")
        request.session["current_user_id"] = user.id
    return user


class UserService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_user(self, name: str) -> User:
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError("User name must contain 1–120 characters")
        if any(user.name.casefold() == name.casefold() for user in self.db.scalars(select(User)).all()):
            raise DuplicateUserError("User name already exists")
        user = User(name=name)
        self.db.add(user)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise DuplicateUserError("User name already exists") from exc
        self.db.refresh(user)
        return user

    def delete_user(self, user: User) -> None:
        if user.name == "demo":
            raise ValueError("The default demo user cannot be deleted")
        self.db.delete(user)
        self.db.commit()

    def subscribed_feeds(self, user: User) -> list[RSSFeed]:
        return list(self.db.scalars(
            select(RSSFeed).join(FeedSubscription, FeedSubscription.feed_id == RSSFeed.id)
            .where(FeedSubscription.user_id == user.id).order_by(RSSFeed.name)
        ).all())

    def available_feeds(self, user: User) -> list[RSSFeed]:
        subscribed_ids = select(FeedSubscription.feed_id).where(FeedSubscription.user_id == user.id)
        return list(self.db.scalars(select(RSSFeed).where(RSSFeed.id.not_in(subscribed_ids)).order_by(RSSFeed.name)).all())

    def subscribe(self, user: User, feed: RSSFeed) -> SubscriptionResult:
        existing = self.db.scalar(select(FeedSubscription).where(
            FeedSubscription.user_id == user.id, FeedSubscription.feed_id == feed.id
        ))
        if existing is not None:
            return SubscriptionResult(created=False)
        try:
            self.db.add(FeedSubscription(user_id=user.id, feed_id=feed.id))
            self.db.flush()
            filters = self.db.scalars(select(Filter).options(selectinload(Filter.keywords)).where(
                Filter.user_id == user.id, Filter.is_active.is_(True)
            )).all()
            articles = self.db.scalars(select(Article).where(Article.feed_id == feed.id).order_by(Article.id)).all()
            matching = FilterService(self.db)
            count = sum(matching.evaluate_article_against_filter(article, filter_) for article in articles for filter_ in filters)
            self.db.commit()
            return SubscriptionResult(created=True, matched_existing_articles=count)
        except Exception:
            self.db.rollback()
            raise

    def unsubscribe(self, user: User, feed: RSSFeed) -> bool:
        subscription = self.db.scalar(select(FeedSubscription).where(
            FeedSubscription.user_id == user.id, FeedSubscription.feed_id == feed.id
        ))
        if subscription is None:
            return False
        filter_ids = select(Filter.id).where(Filter.user_id == user.id)
        article_ids = select(Article.id).where(Article.feed_id == feed.id)
        self.db.execute(delete(ArticleFilterMatch).where(
            ArticleFilterMatch.filter_id.in_(filter_ids), ArticleFilterMatch.article_id.in_(article_ids)
        ))
        self.db.delete(subscription)
        self.db.commit()
        return True
