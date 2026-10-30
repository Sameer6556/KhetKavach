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
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"
    llm_provider: str = "rules"  # resolved provider: "claude" | "gemini" | "rules"

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "rules"

    @property
    def active_model(self) -> str | None:
        return {"claude": self.model, "gemini": self.gemini_model}.get(self.llm_provider)

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

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip() or None
    gemini_key = (os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")).strip() or None

    settings = Settings(
        anthropic_api_key=anthropic_key,
        gemini_api_key=gemini_key,
        gemini_model=os.getenv("KK_GEMINI_MODEL", "gemini-flash-latest").strip(),
        llm_provider=_resolve_provider(os.getenv("KK_LLM_PROVIDER", "auto"), anthropic_key, gemini_key),
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
    if settings.llm_enabled:
        log.info("AI agent: %s (%s)", settings.llm_provider, settings.active_model)
    else:
        log.warning("No GEMINI_API_KEY or ANTHROPIC_API_KEY: the AI agent is off; rule-based advisories still work.")
    return settings


def _resolve_provider(requested: str, anthropic_key: str | None, gemini_key: str | None) -> str:
    """KK_LLM_PROVIDER=auto picks Claude if its key is set, else Gemini, else the rule-based advisor."""
    choice = requested.strip().lower()
    available = {"claude": bool(anthropic_key), "gemini": bool(gemini_key)}
    if choice in available:
        if not available[choice]:
            log.warning("KK_LLM_PROVIDER=%s but its API key is missing; using rule-based advisories.", choice)
            return "rules"
        return choice
    if choice not in ("auto", "", "rules"):
        log.warning("Unknown KK_LLM_PROVIDER=%r; using auto.", requested)
    if choice == "rules":
        return "rules"
    return "claude" if available["claude"] else "gemini" if available["gemini"] else "rules"
