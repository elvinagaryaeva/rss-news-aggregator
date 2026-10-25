"""Routes for publishing and serving the application's own RSS channel."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Article, FeedSubscription, TestRSSPost
from app.schemas import TestRSSPostCreate, TestRSSPostRead, TestRSSPublishResult
from app.services.seed import ensure_local_test_feed
from app.services.test_rss_service import list_test_posts, publish_test_post, render_test_rss
from app.services.user_service import current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/rss/test.xml")
def test_rss_xml(db: Session = Depends(get_db)) -> Response:
    return Response(
        content=render_test_rss(list_test_posts(db)),
        media_type="application/rss+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/test-rss")
def test_rss_page(request: Request, notice: str | None = None, db: Session = Depends(get_db)):
    user = current_user(db, request)
    posts = list_test_posts(db)
    feed = ensure_local_test_feed(db)
    subscribed = db.scalar(select(FeedSubscription.id).where(
        FeedSubscription.user_id == user.id, FeedSubscription.feed_id == feed.id
    )) is not None
    ingested_guids = set(
        db.scalars(select(Article.external_id).where(Article.feed_id == feed.id)).all()
    )
    return templates.TemplateResponse(
        request,
        "test_rss.html",
        {"posts": posts, "ingested_guids": ingested_guids, "notice": notice, "current_user": user, "subscribed": subscribed},
    )


@router.post("/test-rss")
def publish_test_rss_from_form(
    request: Request,
    title: str = Form(),
    description: str = Form(),
    content: str | None = Form(default=None),
    author: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    current_user(db, request)
    try:
        post, result = publish_test_post(
            db, title=title, description=description, content=content, author=author
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    notice = (
        f"Published post {post.id}; RSS ingestion added {result.added} article(s)."
        if result.success
        else f"Published post {post.id}; RSS ingestion failed: {result.error}. Retry refresh later."
    )
    return RedirectResponse(f"/test-rss?{urlencode({'notice': notice})}", status_code=303)


@router.get("/test-rss/posts/{post_id}")
def test_rss_post_page(post_id: int, request: Request, db: Session = Depends(get_db)):
    post = db.get(TestRSSPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Test RSS post was not found")
    return templates.TemplateResponse(request, "test_rss_post.html", {"post": post, "current_user": current_user(db, request)})


@router.get("/api/test-rss/posts", response_model=list[TestRSSPostRead])
def api_list_test_posts(db: Session = Depends(get_db)) -> list[TestRSSPost]:
    return list_test_posts(db)


@router.post(
    "/api/test-rss/posts",
    response_model=TestRSSPublishResult,
    status_code=status.HTTP_201_CREATED,
)
def api_publish_test_post(payload: TestRSSPostCreate, db: Session = Depends(get_db)) -> TestRSSPublishResult:
    try:
        post, result = publish_test_post(db, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TestRSSPublishResult(post=TestRSSPostRead.model_validate(post), refresh=result)


@router.get("/api/test-rss/posts/{post_id}", response_model=TestRSSPostRead)
def api_get_test_post(post_id: int, db: Session = Depends(get_db)) -> TestRSSPost:
    post = db.get(TestRSSPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Test RSS post was not found")
    return post
