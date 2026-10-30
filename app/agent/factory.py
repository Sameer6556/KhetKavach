"""Builds the agent for the configured LLM provider (Claude, Gemini, or the rule-based advisor)."""

from __future__ import annotations

from ..config import Settings
from .base import BaseAgent


def build_agent(settings: Settings) -> BaseAgent:
    # SDKs are imported lazily so a deployment only loads the one it uses.
    if settings.llm_provider == "claude":
        from anthropic import AsyncAnthropic

        from .claude_agent import ClaudeAgent

        return ClaudeAgent(settings, AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=120.0))
    if settings.llm_provider == "gemini":
        from google import genai
        from google.genai import types

        from .gemini_agent import GeminiAgent

        client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(
            timeout=120_000,  # milliseconds
            retry_options=types.HttpRetryOptions(attempts=3, http_status_codes=[429, 500, 502, 503, 504])))
        return GeminiAgent(settings, client)
    return BaseAgent(settings)
