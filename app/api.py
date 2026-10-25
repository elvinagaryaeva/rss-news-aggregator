from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Article, RSSFeed
from app.models import ArticleFilterMatch, Filter
from app.schemas import ArticleMatchRead, ArticleRead, FeedCreate, FeedRead, FilterCreate, FilterMutationResult, FilterRead, FilterUpdate, FilteredArticleRead, RefreshResult
from app.services.filter_service import FilterService, KEYWORD_FILTER_TYPE, SEMANTIC_FILTER_TYPE, MATCH_ALL, MATCH_ANY
from app.services.rss_service import RSSService
from app.services.user_service import UserService, current_user

router = APIRouter(prefix="/api")


def _filter_or_404(db: Session, filter_id: int, user_id: int) -> Filter:
    filter_ = db.scalar(select(Filter).where(Filter.id == filter_id, Filter.user_id == user_id))
    if filter_ is None:
        raise HTTPException(status_code=404, detail="Filter was not found")
    return filter_


def _validate_match_mode(value: str) -> str:
    mode = value.upper()
    if mode not in {MATCH_ANY, MATCH_ALL}:
        raise HTTPException(status_code=422, detail="match_mode must be ANY or ALL")
    return mode


def _evaluate_existing(service: FilterService, filter_: Filter, db: Session) -> int:
    filter_type = filter_.filter_type
    try:
        return service.evaluate_filter_against_existing_articles(filter_)
    except Exception as exc:
        db.rollback()
        if filter_type == SEMANTIC_FILTER_TYPE:
            raise HTTPException(status_code=503, detail=f"Semantic model is unavailable: {exc}") from exc
        raise


def _filtered_response(service: FilterService, user_id: int | None) -> list[FilteredArticleRead]:
    try:
        user = service.get_user(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    grouped: dict[int, list[ArticleFilterMatch]] = {}
    for match in service.filtered_matches_for_user(user):
        grouped.setdefault(match.article_id, []).append(match)
    results: list[FilteredArticleRead] = []
    for matches in grouped.values():
        article = matches[0].article
        base = ArticleRead.model_validate(article).model_dump()
        results.append(FilteredArticleRead(**base, feed_name=article.feed.name, matches=[ArticleMatchRead.model_validate(match) for match in matches]))
    return results


def _scope_user_id(db: Session, request: Request, explicit_user_id: int | None) -> int:
    return explicit_user_id if explicit_user_id is not None else current_user(db, request).id


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/feeds", response_model=list[FeedRead])
def list_feeds(db: Session = Depends(get_db)) -> list[RSSFeed]:
    return list(db.scalars(select(RSSFeed).order_by(RSSFeed.name)).all())


@router.get("/articles", response_model=list[ArticleRead])
def list_articles(limit: int = 50, db: Session = Depends(get_db)) -> list[Article]:
    limit = min(max(limit, 1), 200)
    return list(db.scalars(select(Article).order_by(Article.published_at.desc().nullslast(), Article.id.desc()).limit(limit)).all())


@router.post("/feeds", response_model=RefreshResult, status_code=status.HTTP_201_CREATED)
def add_feed(payload: FeedCreate, request: Request, user_id: int | None = None, db: Session = Depends(get_db)) -> RefreshResult:
    url = str(payload.url)
    try:
        user = FilterService(db).get_user(_scope_user_id(db, request, user_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    feed = db.scalar(select(RSSFeed).where(RSSFeed.url == url))
    is_new = feed is None
    if feed is None:
        feed = RSSFeed(name=payload.name or url, url=url)
        db.add(feed)
        db.flush()
    UserService(db).subscribe(user, feed)
    if is_new:
        return RSSService(db).refresh_feed(feed)
    return RefreshResult(feed_id=feed.id, feed_name=feed.name, success=True)


@router.post("/feeds/refresh", response_model=list[RefreshResult])
def refresh_feeds(db: Session = Depends(get_db)) -> list[RefreshResult]:
    return RSSService(db).refresh_active_feeds()


@router.get("/filters", response_model=list[FilterRead])
def list_filters(request: Request, user_id: int | None = None, db: Session = Depends(get_db)) -> list[Filter]:
    service = FilterService(db)
    try:
        user = service.get_user(_scope_user_id(db, request, user_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return list(db.scalars(select(Filter).where(Filter.user_id == user.id).order_by(Filter.created_at.desc())).all())


@router.post("/filters", response_model=FilterMutationResult, status_code=status.HTTP_201_CREATED)
def create_filter(payload: FilterCreate, request: Request, db: Session = Depends(get_db)) -> FilterMutationResult:
    service = FilterService(db)
    try:
        user = service.get_user(_scope_user_id(db, request, payload.user_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    if payload.filter_type == KEYWORD_FILTER_TYPE:
        mode = _validate_match_mode(payload.match_mode)
    else:
        if payload.match_mode != MATCH_ANY:
            raise HTTPException(status_code=422, detail="match_mode applies only to keyword filters")
        mode = MATCH_ANY
    filter_ = Filter(user_id=user.id, name=payload.name.strip(), filter_type=payload.filter_type, match_mode=mode, is_active=payload.is_active)
    db.add(filter_)
    db.flush()
    try:
        if payload.filter_type == KEYWORD_FILTER_TYPE:
            service.set_keywords(filter_, payload.keywords)
        else:
            service.set_semantic_config(filter_, payload.semantic_query, payload.semantic_threshold)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.flush()
    matched = _evaluate_existing(service, filter_, db)
    db.commit()
    db.refresh(filter_)
    return FilterMutationResult(filter=FilterRead.model_validate(filter_), matched_existing_articles=matched)


@router.patch("/filters/{filter_id}", response_model=FilterMutationResult)
def update_filter(filter_id: int, payload: FilterUpdate, request: Request, user_id: int | None = None, db: Session = Depends(get_db)) -> FilterMutationResult:
    service = FilterService(db)
    try:
        user = service.get_user(_scope_user_id(db, request, user_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filter_ = _filter_or_404(db, filter_id, user.id)
    if payload.filter_type is not None and payload.filter_type != filter_.filter_type:
        raise HTTPException(status_code=422, detail="Filter type cannot be changed; create a new filter")
    if filter_.filter_type == KEYWORD_FILTER_TYPE and (payload.semantic_query is not None or payload.semantic_threshold is not None):
        raise HTTPException(status_code=422, detail="Keyword filters cannot have semantic settings")
    if filter_.filter_type == SEMANTIC_FILTER_TYPE and (payload.keywords is not None or payload.match_mode is not None):
        raise HTTPException(status_code=422, detail="Semantic filters do not use keywords or match mode")
    if payload.name is not None:
        filter_.name = payload.name.strip()
    if payload.match_mode is not None:
        filter_.match_mode = _validate_match_mode(payload.match_mode)
    if payload.is_active is not None:
        filter_.is_active = payload.is_active
    if payload.keywords is not None:
        try:
            service.set_keywords(filter_, payload.keywords)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if filter_.filter_type == SEMANTIC_FILTER_TYPE:
        try:
            service.set_semantic_config(
                filter_,
                payload.semantic_query if payload.semantic_query is not None else filter_.semantic_query,
                payload.semantic_threshold if payload.semantic_threshold is not None else filter_.semantic_threshold,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    filter_.updated_at = datetime.now(UTC)
    db.flush()
    matched = _evaluate_existing(service, filter_, db)
    db.commit()
    db.refresh(filter_)
    return FilterMutationResult(filter=FilterRead.model_validate(filter_), matched_existing_articles=matched)


@router.delete("/filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_filter(filter_id: int, request: Request, user_id: int | None = None, db: Session = Depends(get_db)) -> None:
    service = FilterService(db)
    try:
        user = service.get_user(_scope_user_id(db, request, user_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.delete(_filter_or_404(db, filter_id, user.id))
    db.commit()


@router.get("/articles/filtered", response_model=list[FilteredArticleRead])
def filtered_articles(request: Request, user_id: int | None = None, db: Session = Depends(get_db)) -> list[FilteredArticleRead]:
    return _filtered_response(FilterService(db), _scope_user_id(db, request, user_id))


@router.get("/articles/{article_id}/matches", response_model=list[ArticleMatchRead])
def article_matches(article_id: int, request: Request, user_id: int | None = None, db: Session = Depends(get_db)) -> list[ArticleFilterMatch]:
    service = FilterService(db)
    try:
        user = service.get_user(_scope_user_id(db, request, user_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [match for match in service.filtered_matches_for_user(user) if match.article_id == article_id]
