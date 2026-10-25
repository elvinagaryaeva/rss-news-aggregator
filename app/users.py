"""User selection UI and explicit user/subscription API for the demo app."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import RSSFeed, User
from app.schemas import FeedCreate, FeedRead, SubscriptionRead, UserCreate, UserRead
from app.services.rss_service import RSSService
from app.services.user_service import DuplicateUserError, UserService, current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User was not found")
    return user


def _feed_or_404(db: Session, feed_id: int) -> RSSFeed:
    feed = db.get(RSSFeed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="RSS source was not found")
    return feed


@router.get("/users")
def users_page(request: Request, notice: str | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "users.html", {
        "users": db.scalars(select(User).order_by(User.name)).all(),
        "current_user": current_user(db, request), "notice": notice,
    })


@router.post("/users")
def create_user_from_form(name: str = Form(), db: Session = Depends(get_db)):
    try:
        user = UserService(db).create_user(name)
    except DuplicateUserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/users?{urlencode({'notice': f'Created user {user.name}'})}", status_code=303)


@router.post("/users/select")
def select_user_from_form(request: Request, user_id: int = Form(), db: Session = Depends(get_db)):
    user = _user_or_404(db, user_id)
    request.session["current_user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user_from_form(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user_or_404(db, user_id)
    try:
        UserService(db).delete_user(user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.session.get("current_user_id") == user_id:
        request.session.pop("current_user_id", None)
    return RedirectResponse("/users?notice=User+deleted", status_code=303)


@router.get("/api/users", response_model=list[UserRead])
def api_list_users(db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.name)).all())


@router.post("/api/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def api_create_user(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    try:
        return UserService(db).create_user(payload.name)
    except DuplicateUserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/users/{user_id}", response_model=UserRead)
def api_get_user(user_id: int, db: Session = Depends(get_db)) -> User:
    return _user_or_404(db, user_id)


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_user(user_id: int, request: Request, db: Session = Depends(get_db)) -> None:
    user = _user_or_404(db, user_id)
    try:
        UserService(db).delete_user(user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.session.get("current_user_id") == user_id:
        request.session.pop("current_user_id", None)


@router.get("/api/users/{user_id}/feeds", response_model=list[FeedRead])
def api_user_feeds(user_id: int, db: Session = Depends(get_db)) -> list[RSSFeed]:
    return UserService(db).subscribed_feeds(_user_or_404(db, user_id))


@router.post("/api/users/{user_id}/feeds/{feed_id}", response_model=SubscriptionRead)
def api_subscribe(user_id: int, feed_id: int, db: Session = Depends(get_db)) -> SubscriptionRead:
    user = _user_or_404(db, user_id)
    feed = _feed_or_404(db, feed_id)
    result = UserService(db).subscribe(user, feed)
    return SubscriptionRead(user_id=user.id, feed_id=feed.id, **result.__dict__)


@router.delete("/api/users/{user_id}/feeds/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_unsubscribe(user_id: int, feed_id: int, db: Session = Depends(get_db)) -> None:
    user = _user_or_404(db, user_id)
    feed = _feed_or_404(db, feed_id)
    if not UserService(db).unsubscribe(user, feed):
        raise HTTPException(status_code=404, detail="Subscription was not found")


@router.post("/api/users/{user_id}/feeds", response_model=SubscriptionRead)
def api_add_user_feed(user_id: int, payload: FeedCreate, db: Session = Depends(get_db)) -> SubscriptionRead:
    user = _user_or_404(db, user_id)
    url = str(payload.url)
    feed = db.scalar(select(RSSFeed).where(RSSFeed.url == url))
    is_new = feed is None
    if feed is None:
        feed = RSSFeed(name=payload.name or url, url=url)
        db.add(feed)
        db.flush()
    result = UserService(db).subscribe(user, feed)
    if is_new:
        RSSService(db).refresh_feed(feed)
    return SubscriptionRead(user_id=user.id, feed_id=feed.id, **result.__dict__)
