from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import AnyHttpUrl
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models import Article, FeedSubscription, Filter, RSSFeed
from app.services.filter_service import FilterService, MATCH_ALL, MATCH_ANY, KEYWORD_FILTER_TYPE, SEMANTIC_FILTER_TYPE, DEFAULT_SEMANTIC_THRESHOLD
from app.services.rss_service import RSSService
from app.services.user_service import UserService, current_user

templates = Jinja2Templates(directory="app/templates")
router = APIRouter()


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    user = current_user(db, request)
    articles = db.scalars(
        select(Article).join(FeedSubscription, FeedSubscription.feed_id == Article.feed_id)
        .where(FeedSubscription.user_id == user.id).options(selectinload(Article.feed))
        .order_by(Article.published_at.desc().nullslast(), Article.id.desc()).limit(100)
    ).all()
    service = FilterService(db)
    grouped_matches: dict[int, list] = {}
    for match in service.filtered_matches_for_user(user):
        grouped_matches.setdefault(match.article_id, []).append(match)
    filtered_items = [{"article": matches[0].article, "matches": matches} for matches in grouped_matches.values()]
    has_filters = db.scalar(select(Filter.id).where(Filter.user_id == user.id).limit(1)) is not None
    has_subscriptions = db.scalar(select(FeedSubscription.id).where(FeedSubscription.user_id == user.id).limit(1)) is not None
    return templates.TemplateResponse(request, "index.html", {"articles": articles, "filtered_items": filtered_items, "current_user": user, "has_filters": has_filters, "has_subscriptions": has_subscriptions})


@router.get("/feeds")
def feeds_page(request: Request, notice: str | None = None, db: Session = Depends(get_db)):
    user = current_user(db, request)
    service = UserService(db)
    return templates.TemplateResponse(request, "feeds.html", {"my_feeds": service.subscribed_feeds(user), "available_feeds": service.available_feeds(user), "current_user": user, "notice": notice})


@router.post("/feeds")
def add_feed_from_form(request: Request, url: AnyHttpUrl = Form(), name: str | None = Form(default=None), db: Session = Depends(get_db)):
    user = current_user(db, request)
    normalized_url = str(url)
    feed = db.scalar(select(RSSFeed).where(RSSFeed.url == normalized_url))
    is_new = feed is None
    if feed is None:
        feed = RSSFeed(name=(name or normalized_url).strip(), url=normalized_url)
        db.add(feed)
        db.flush()
    result = UserService(db).subscribe(user, feed)
    if is_new:
        RSSService(db).refresh_feed(feed)
    notice = (
        f"Subscribed to feed; matched {result.matched_existing_articles} existing articles"
        if result.created else "Already subscribed to feed"
    )
    return RedirectResponse(f"/feeds?{urlencode({'notice': notice})}", status_code=303)


@router.post("/feeds/{feed_id}/subscribe")
def subscribe_feed_from_form(feed_id: int, request: Request, db: Session = Depends(get_db)):
    feed = db.get(RSSFeed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="RSS source was not found")
    result = UserService(db).subscribe(current_user(db, request), feed)
    return RedirectResponse(f"/feeds?notice=Subscribed%3A+matched+{result.matched_existing_articles}+existing+articles", status_code=303)


@router.post("/feeds/{feed_id}/unsubscribe")
def unsubscribe_feed_from_form(feed_id: int, request: Request, db: Session = Depends(get_db)):
    feed = db.get(RSSFeed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="RSS source was not found")
    if not UserService(db).unsubscribe(current_user(db, request), feed):
        raise HTTPException(status_code=404, detail="Subscription was not found")
    return RedirectResponse("/feeds?notice=Unsubscribed", status_code=303)


@router.post("/feeds/refresh")
def refresh_feeds_from_form(request: Request, db: Session = Depends(get_db)):
    current_user(db, request)
    RSSService(db).refresh_active_feeds()
    return RedirectResponse("/feeds", status_code=303)


@router.get("/filters")
def filters_page(request: Request, notice: str | None = None, db: Session = Depends(get_db)):
    service = FilterService(db)
    try:
        user = current_user(db, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filters = db.scalars(select(Filter).where(Filter.user_id == user.id).order_by(Filter.created_at.desc())).all()
    return templates.TemplateResponse(request, "filters.html", {"filters": filters, "notice": notice, "current_user": user})


@router.post("/filters")
def create_filter_from_form(request: Request, name: str = Form(), filter_type: str = Form(default=KEYWORD_FILTER_TYPE), keywords: str = Form(default=""), match_mode: str = Form(default=MATCH_ANY), semantic_query: str = Form(default=""), semantic_threshold: float = Form(default=DEFAULT_SEMANTIC_THRESHOLD), db: Session = Depends(get_db)):
    service = FilterService(db)
    try:
        user = current_user(db, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    mode = match_mode.upper()
    if not name.strip() or filter_type not in {KEYWORD_FILTER_TYPE, SEMANTIC_FILTER_TYPE}:
        raise HTTPException(status_code=422, detail="Name and a valid filter type are required")
    if filter_type == KEYWORD_FILTER_TYPE and mode not in {MATCH_ANY, MATCH_ALL}:
        raise HTTPException(status_code=422, detail="A valid ANY/ALL mode is required")
    if filter_type == SEMANTIC_FILTER_TYPE and keywords.strip():
        raise HTTPException(status_code=422, detail="Semantic filters do not use keywords")
    filter_ = Filter(user_id=user.id, name=name.strip(), filter_type=filter_type, match_mode=mode if filter_type == KEYWORD_FILTER_TYPE else MATCH_ANY, is_active=True)
    db.add(filter_)
    db.flush()
    try:
        if filter_type == KEYWORD_FILTER_TYPE:
            service.set_keywords(filter_, [keywords])
        else:
            service.set_semantic_config(filter_, semantic_query, semantic_threshold)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.flush()
    try:
        matched = service.evaluate_filter_against_existing_articles(filter_)
    except Exception as exc:
        db.rollback()
        if filter_type == SEMANTIC_FILTER_TYPE:
            raise HTTPException(status_code=503, detail=f"Semantic model is unavailable: {exc}") from exc
        raise
    db.commit()
    return RedirectResponse(f"/filters?notice=Filter+created%3A+matched+{matched}+existing+articles", status_code=303)


@router.post("/filters/{filter_id}")
def update_filter_from_form(filter_id: int, request: Request, name: str = Form(), keywords: str = Form(default=""), match_mode: str = Form(default=MATCH_ANY), semantic_query: str = Form(default=""), semantic_threshold: float = Form(default=DEFAULT_SEMANTIC_THRESHOLD), is_active: bool = Form(default=False), db: Session = Depends(get_db)):
    service = FilterService(db)
    try:
        user = current_user(db, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filter_ = db.scalar(select(Filter).where(Filter.id == filter_id, Filter.user_id == user.id))
    if filter_ is None:
        raise HTTPException(status_code=404, detail="Filter was not found")
    mode = match_mode.upper()
    if not name.strip() or (filter_.filter_type == KEYWORD_FILTER_TYPE and mode not in {MATCH_ANY, MATCH_ALL}):
        raise HTTPException(status_code=422, detail="Name and a valid ANY/ALL mode are required")
    filter_.name = name.strip()
    if filter_.filter_type == KEYWORD_FILTER_TYPE:
        filter_.match_mode = mode
    filter_.is_active = is_active
    try:
        if filter_.filter_type == KEYWORD_FILTER_TYPE:
            service.set_keywords(filter_, [keywords])
        else:
            if keywords.strip():
                raise ValueError("Semantic filters do not use keywords")
            service.set_semantic_config(filter_, semantic_query, semantic_threshold)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filter_.updated_at = datetime.now(UTC)
    db.flush()
    filter_type = filter_.filter_type
    try:
        matched = service.evaluate_filter_against_existing_articles(filter_)
    except Exception as exc:
        db.rollback()
        if filter_type == SEMANTIC_FILTER_TYPE:
            raise HTTPException(status_code=503, detail=f"Semantic model is unavailable: {exc}") from exc
        raise
    db.commit()
    return RedirectResponse(f"/filters?notice=Filter+updated%3A+matched+{matched}+existing+articles", status_code=303)


@router.post("/filters/{filter_id}/delete")
def delete_filter_from_form(filter_id: int, request: Request, db: Session = Depends(get_db)):
    service = FilterService(db)
    try:
        user = current_user(db, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filter_ = db.scalar(select(Filter).where(Filter.id == filter_id, Filter.user_id == user.id))
    if filter_ is None:
        raise HTTPException(status_code=404, detail="Filter was not found")
    db.delete(filter_)
    db.commit()
    return RedirectResponse("/filters?notice=Filter+deleted", status_code=303)
