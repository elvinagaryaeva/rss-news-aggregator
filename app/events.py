from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.event_bus import event_stream
from app.services.user_service import current_user

from app.db.session import get_db

router = APIRouter()


@router.get("/api/events")
async def sse_events(request: Request, db: Session = Depends(get_db)):
    if not getattr(settings, "sse_enabled", True):
        return StreamingResponse(iter(()), media_type="text/plain", status_code=404)
    user = current_user(db, request)
    return StreamingResponse(
        event_stream(user_id=user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )