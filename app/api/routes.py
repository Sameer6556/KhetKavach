"""REST and Server-Sent-Event endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .. import __version__
from ..agent.prompts import LANGUAGES
from ..agronomy.crops import CROPS, IRRIGATION_METHODS
from ..evidence import EvidenceLedger
from ..http import SourceError
from ..schemas import ChatRequest, FarmCreate, FarmProfile
from ..sources import nominatim, open_meteo
from ..tools.registry import ToolExecutor
from .dashboard import stream_dashboard

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
HEARTBEAT_S = 15


class RateLimiter:
    """Sliding-window limit per client IP (protects the public demo's LLM budget)."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


chat_limiter = RateLimiter(limit=20, window_s=600)
dashboard_limiter = RateLimiter(limit=40, window_s=600)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def sse_response(producer: Callable[[Callable[[dict], Awaitable[None]]], Awaitable[Any]]) -> StreamingResponse:
    """Run `producer(emit)` in a task and stream every emitted event as SSE, with heartbeats."""
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def emit(event: dict) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            await producer(emit)
        except Exception as exc:  # report, never hang the stream
            log.exception("Stream producer failed")
            await queue.put({"type": "error", "message": f"Server error: {type(exc).__name__}"})
        finally:
            await queue.put(None)

    async def body() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    st = request.app.state
    return {
        "status": "ok", "version": __version__,
        "ai": {"enabled": st.settings.llm_enabled, "model": st.settings.model if st.settings.llm_enabled else None,
               "effort": st.settings.effort},
        "satellite_enabled": st.settings.satellite_enabled,
        "mandi_key": "sample (limited)" if st.settings.using_sample_mandi_key else "personal",
        "telegram": bool(st.settings.telegram_bot_token),
        "sentinel": {"interval_min": st.settings.sentinel_interval_minutes, "last_scan": st.sentinel.last_scan},
        "data_sources": st.ctx.dc.health(),
    }


@router.get("/meta")
async def meta(request: Request) -> dict[str, Any]:
    return {
        "crops": [{"key": c.key, "name": c.name, "hindi": c.hindi, "season": c.season, "season_days": c.season_days}
                  for c in CROPS.values()],
        "languages": [{"code": k, "name": v[0], "speech": v[1]} for k, v in LANGUAGES.items()],
        "irrigation_methods": list(IRRIGATION_METHODS),
        "ai_enabled": request.app.state.settings.llm_enabled,
    }


@router.get("/geocode")
async def geocode(request: Request, q: str = Query(min_length=2, max_length=80)) -> dict[str, Any]:
    dc = request.app.state.ctx.dc
    try:
        results, _ = await open_meteo.geocode(dc, q)
        if not results:
            results, _ = await open_meteo.geocode(dc, q, country_code=None)
    except SourceError as exc:
        raise HTTPException(503, f"Place search unavailable: {exc.message}") from None
    return {"results": results}


@router.get("/reverse")
async def reverse(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)) -> dict[str, Any]:
    try:
        place, _ = await nominatim.reverse(request.app.state.ctx.dc, lat, lon)
    except SourceError as exc:
        raise HTTPException(503, f"Place lookup unavailable: {exc.message}") from None
    return place


@router.post("/dashboard")
async def dashboard(request: Request, farm: FarmProfile) -> StreamingResponse:
    if not dashboard_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "Too many analyses from this address; please wait a few minutes.")
    executor = ToolExecutor(request.app.state.ctx, EvidenceLedger(), farm.lat, farm.lon)
    return sse_response(lambda emit: stream_dashboard(executor, farm, emit))


@router.post("/chat")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    if not chat_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "Too many questions from this address; please wait a few minutes.")
    st = request.app.state
    session = st.sessions.get_or_create(body.session_id, body.farm)

    async def produce(emit: Callable[[dict], Awaitable[None]]) -> None:
        async with session.lock:  # one turn at a time per conversation
            await emit({"type": "session", "session_id": session.id, "ai_enabled": st.settings.llm_enabled})
            await st.agent.run_turn(session, body.message.strip(), emit)

    return sse_response(produce)


@router.get("/farms")
async def list_farms(request: Request) -> dict[str, Any]:
    return {"farms": request.app.state.ctx.db.list_farms()}


@router.post("/farms", status_code=201)
async def create_farm(request: Request, farm: FarmCreate) -> dict[str, Any]:
    db = request.app.state.ctx.db
    if len(db.list_farms()) >= 200:
        raise HTTPException(409, "Farm registry is full on this demo server.")
    row = db.add_farm({"name": farm.name, "lat": farm.lat, "lon": farm.lon, "crop": farm.crop,
                       "sowing_date": farm.sowing_date.isoformat(), "irrigation": farm.irrigation_method,
                       "language": farm.language, "telegram_chat_id": farm.telegram_chat_id})
    return row


@router.delete("/farms/{farm_id}")
async def delete_farm(request: Request, farm_id: int) -> dict[str, Any]:
    if not request.app.state.ctx.db.delete_farm(farm_id):
        raise HTTPException(404, "Farm not found")
    return {"deleted": farm_id}


@router.post("/sentinel/scan")
async def sentinel_scan(request: Request, farm_id: int | None = None) -> dict[str, Any]:
    st = request.app.state
    if farm_id is None:
        return await st.sentinel.scan_all()
    farm = st.ctx.db.get_farm(farm_id)
    if farm is None:
        raise HTTPException(404, "Farm not found")
    alerts = await st.sentinel.scan_farm(farm)
    return {"farms": 1, "new_alerts": len(alerts), "alerts": alerts}


@router.get("/alerts")
async def alerts(request: Request, farm_id: int | None = None) -> dict[str, Any]:
    return {"alerts": request.app.state.ctx.db.list_alerts(farm_id)}
