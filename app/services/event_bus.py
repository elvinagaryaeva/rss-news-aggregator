"""In-process event bus for SSE. Single-worker only (see README)."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import logging
log = logging.getLogger(__name__)

_subscribers: set[asyncio.Queue] = set()
_lock = asyncio.Lock()
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from FastAPI lifespan — needed for thread → async publishing."""
    global _main_loop
    _main_loop = loop


async def _subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    async with _lock:
        _subscribers.add(q)
    return q


async def _unsubscribe(q: asyncio.Queue) -> None:
    async with _lock:
        _subscribers.discard(q)


async def publish(event: dict) -> None:
    async with _lock:
        targets = list(_subscribers)
    log.info("SSE publish: %s subscribers, type=%s", len(targets), event.get("type"))
    for q in targets:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def publish_from_thread(event: dict) -> None:
    log.info(
        "SSE publish_from_thread: loop_set=%s, closed=%s, type=%s",
        _main_loop is not None,
        _main_loop.is_closed() if _main_loop else None,
        event.get("type"),
    )
    if _main_loop is None or _main_loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(publish(event), _main_loop)
    except RuntimeError:
        pass


def _format_sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def event_stream(user_id: int, heartbeat_seconds: float = 15.0) -> AsyncIterator[str]:
    q = await _subscribe()
    try:
        yield _format_sse({"type": "hello"})
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            # events targeted at specific users (article matched their filters)
            if event.get("type") == "article.created":
                if user_id not in event.get("user_ids", []):
                    continue
            yield _format_sse(event)
    finally:
        await _unsubscribe(q)