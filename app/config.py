"""Runtime configuration, read once from environment variables (and an optional .env file)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# data.gov.in publishes this key on every resource page for trying the API. It is
# rate-limited and returns at most 10 records per call, so production deployments
# should register for a free personal key and set DATA_GOV_IN_API_KEY.
DATA_GOV_IN_SAMPLE_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        log.warning("Ignoring non-integer %s=%r", name, raw)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        log.warning("Ignoring non-numeric %s=%r", name, raw)
        return default


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    model: str
    effort: str
    data_gov_in_api_key: str
    using_sample_mandi_key: bool
    telegram_bot_token: str | None
    db_path: Path
    sentinel_interval_minutes: int
    satellite_enabled: bool
    http_timeout_s: float
    agent_timeout_s: float
    max_agent_steps: int
    contact: str

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def user_agent(self) -> str:
        # Nominatim's usage policy requires an identifying User-Agent with contact info.
        return f"KhetKavach/1.0 (+{self.contact})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    mandi_key = os.getenv("DATA_GOV_IN_API_KEY", "").strip()
    effort = os.getenv("KK_EFFORT", "medium").strip().lower()
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        log.warning("Unknown KK_EFFORT=%r, using 'medium'", effort)
        effort = "medium"

    settings = Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip() or None,
        model=os.getenv("KK_MODEL", "claude-opus-5-5").strip(),
        effort=effort,
        data_gov_in_api_key=mandi_key or DATA_GOV_IN_SAMPLE_KEY,
        using_sample_mandi_key=not mandi_key,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
        db_path=Path(os.getenv("KK_DB_PATH", str(PROJECT_ROOT / "data" / "khetkavach.db"))),
        sentinel_interval_minutes=_env_int("KK_SENTINEL_INTERVAL_MIN", 180),
        satellite_enabled=_env_bool("KK_SATELLITE", True),
        http_timeout_s=_env_float("KK_HTTP_TIMEOUT", 20.0),
        agent_timeout_s=_env_float("KK_AGENT_TIMEOUT", 180.0),
        max_agent_steps=_env_int("KK_MAX_AGENT_STEPS", 8),
        contact=os.getenv("KK_CONTACT", "https://github.com/Sameer6556/code-benders").strip(),
    )
    if settings.using_sample_mandi_key:
        log.warning("DATA_GOV_IN_API_KEY not set: using data.gov.in's public sample key (max 10 records/call).")
    if not settings.llm_enabled:
        log.warning("ANTHROPIC_API_KEY not set: the AI agent is disabled; deterministic advisories still work.")
    return settings
