"""Provider-independent agent core shared by the Claude and Gemini implementations.

`BaseAgent.run_turn` owns the turn lifecycle: timeout, history rollback on failure, and the
rule-based fallback. Subclasses implement `_agent_turn` (one provider's request/response loop) and
reuse `run_tools` (validated, concurrent tool execution with trace events) and `finish` (the final
event with the verification report).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable

from ..config import Settings
from ..tools.registry import ToolRun
from .fallback import rule_based_turn, tool_end_event
from .prompts import farm_context_block
from .sessions import ChatSession

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
MAX_REVISIONS = 1


class AIUnavailable(Exception):
    """The LLM cannot answer this turn (bad key, outage, rate limit, ...): use the rule-based advisor."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0

    def add(self, input_tokens: int | None = 0, output_tokens: int | None = 0,
            cache_read: int | None = 0, cache_write: int | None = 0) -> None:
        self.llm_calls += 1
        self.input_tokens += input_tokens or 0
        self.output_tokens += output_tokens or 0
        self.cache_read_tokens += cache_read or 0
        self.cache_write_tokens += cache_write or 0

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


class BaseAgent:
    """Turn lifecycle common to every provider. On its own it is the rule-based advisor (no LLM)."""

    provider = "rules"

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return False

    @property
    def model(self) -> str | None:
        return None

    async def run_turn(self, session: ChatSession, question: str, emit: Emit) -> dict[str, Any]:
        if not self.enabled:
            return await rule_based_turn(session, question, emit, "no AI key configured")
        checkpoint = len(session.messages)
        try:
            return await asyncio.wait_for(self._agent_turn(session, question, emit),
                                          timeout=self.settings.agent_timeout_s)
        except (AIUnavailable, asyncio.TimeoutError) as exc:
            del session.messages[checkpoint:]  # roll back the incomplete turn; earlier history is untouched
            reason = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)
            log.warning("Agent turn failed (%s); falling back to rules", reason)
            return await rule_based_turn(session, question, emit, reason)
        except asyncio.CancelledError:
            del session.messages[checkpoint:]  # client disconnected: keep the session valid
            raise
        except Exception:
            # An unexpected bug must not leave the farmer without an answer during a live demo.
            del session.messages[checkpoint:]
            log.exception("Agent turn crashed; falling back to rules")
            return await rule_based_turn(session, question, emit, "internal error")

    async def _agent_turn(self, session: ChatSession, question: str, emit: Emit) -> dict[str, Any]:
        raise NotImplementedError

    def opening_text(self, session: ChatSession, question: str) -> str:
        """The first turn carries the farm profile and today's date; later turns are just the question."""
        if session.started:
            return question
        return farm_context_block(session.farm.model_dump(mode="json"), date.today()) + "\n\n" + question

    async def run_tools(self, session: ChatSession, calls: list[tuple[str, str, Any]], emit: Emit,
                        usage: Usage) -> list[ToolRun]:
        """Run (call_id, tool_name, args) requests concurrently, emitting start/end trace events."""
        for call_id, name, args in calls:
            await emit({"type": "tool_start", "id": call_id, "name": name, "input": args})
        usage.tool_calls += len(calls)
        runs = await asyncio.gather(*(session.executor.run(name, args) for _, name, args in calls))
        for (call_id, _, _), run in zip(calls, runs):
            await emit(tool_end_event(call_id, run))
        return list(runs)

    async def finish(self, emit: Emit, session: ChatSession, text: str, usage: Usage, model: str | None,
                     report: Any, revised: bool = False) -> dict[str, Any]:
        event = {"type": "final", "text": text, "verification": report.to_dict(), "self_corrected": revised,
                 "evidence": [ev.public() for ev in session.ledger.all()], "usage": usage.to_dict(),
                 "mode": "ai", "provider": self.provider, "model": model}
        await emit(event)
        return event

    async def write_short(self, system: str, prompt: str) -> str | None:
        """One short, tool-free completion (used to word Sentinel alerts). None when unavailable."""
        return None

    async def aclose(self) -> None:
        return None
