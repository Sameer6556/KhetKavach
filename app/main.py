"""FastAPI application: wires the database, live-data client, Claude agent, sessions and sentinel together."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from anthropic import AsyncAnthropic
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agent.loop import FarmAgent
from .agent.sessions import SessionStore
from .api.routes import router
from .config import get_settings
from .db import Database
from .http import MAX_STALE_S, DataClient
from .sentinel.monitor import Sentinel
from .sources import register_policies
from .tools.base import ToolContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db = Database(settings.db_path)
    db.cache_prune(MAX_STALE_S)
    dc = DataClient(db, settings.user_agent, settings.http_timeout_s)
    register_policies(dc)
    ctx = ToolContext(dc=dc, db=db, settings=settings)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=120.0) if settings.llm_enabled else None
    agent = FarmAgent(settings, client)
    sentinel = Sentinel(ctx, agent)

    app.state.settings, app.state.ctx, app.state.agent = settings, ctx, agent
    app.state.sessions, app.state.sentinel = SessionStore(ctx), sentinel
    background = None
    if settings.sentinel_interval_minutes > 0:
        background = asyncio.create_task(sentinel.run_forever(settings.sentinel_interval_minutes))
    try:
        yield
    finally:
        if background:
            background.cancel()
        await dc.aclose()
        if client is not None:
            await client.close()
        db.close()


app = FastAPI(title="KhetKavach", version=__version__, lifespan=lifespan,
              description="Agentic climate-risk shield for Indian farms, built on live open data and Claude.")
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
