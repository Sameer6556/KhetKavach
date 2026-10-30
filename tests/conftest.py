from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.evidence import EvidenceLedger
from app.http import DataClient
from app.sources import register_policies
from app.tools.base import ToolContext
from app.tools.registry import ToolExecutor
from tests.fakes import FakeUpstream, make_cogs

FARM = {"lat": 20.02, "lon": 74.0}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(anthropic_api_key=None, model="claude-opus-5-5", effort="medium", data_gov_in_api_key="test-key",
                using_sample_mandi_key=False, telegram_bot_token=None, db_path=tmp_path / "test.db",
                sentinel_interval_minutes=0, satellite_enabled=True, http_timeout_s=5.0, agent_timeout_s=30.0,
                max_agent_steps=6, contact="tests")
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def upstream(tmp_path: Path) -> FakeUpstream:
    return FakeUpstream(cog_dir=make_cogs(tmp_path / "cogs", FARM["lat"], FARM["lon"]))


@pytest.fixture
async def ctx(tmp_path: Path, upstream: FakeUpstream):
    db = Database(tmp_path / "test.db")
    dc = DataClient(db, "KhetKavach-tests", 5.0, transport=upstream.transport())
    register_policies(dc)
    yield ToolContext(dc=dc, db=db, settings=make_settings(tmp_path))
    await dc.aclose()
    db.close()


@pytest.fixture
def executor(ctx: ToolContext) -> ToolExecutor:
    return ToolExecutor(ctx, EvidenceLedger(), FARM["lat"], FARM["lon"])
