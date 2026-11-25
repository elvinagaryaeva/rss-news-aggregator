import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.core.config import settings
from app.db.session import SessionLocal
from app.services.rss_service import RSSService
from app.services.seed import seed_default_data
from app.test_rss import router as test_rss_router
from app.users import router as users_router
from app.web import router as web_router
import asyncio
from app import events as events_router
from app.services.event_bus import set_main_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def scheduled_refresh() -> None:
    db = SessionLocal()
    try:
        results = RSSService(db).refresh_active_feeds()
        logger.info("Scheduled RSS refresh completed: %s feeds", len(results))
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    set_main_loop(asyncio.get_running_loop())
    if settings.session_secret == "development-only-session-secret":
        logger.warning("Using the development SESSION_SECRET; set a private value for shared deployments")
    db = SessionLocal()
    try:
        seed_default_data(db)
    finally:
        db.close()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        scheduled_refresh,
        trigger="date",
        run_date=datetime.now(UTC) + timedelta(seconds=5),
        id="initial_rss_refresh",
        replace_existing=True,
    )
    scheduler.add_job(scheduled_refresh, trigger="interval", seconds=settings.rss_fetch_interval_seconds, id="refresh_rss_feeds", replace_existing=True)
    scheduler.start()
    logger.info("RSS scheduler started with %s second interval", settings.rss_fetch_interval_seconds)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="RSS Aggregator", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api_router)
app.include_router(test_rss_router)
app.include_router(users_router)
app.include_router(web_router)
app.include_router(events_router.router)
