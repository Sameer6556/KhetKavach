"""The KhetKavach agent loop on the Claude API (manual tool-use loop with streaming).

One user turn:
  1. Claude plans and requests tools (often several in parallel).
  2. We validate each input, run the tools concurrently against live data, and return results
     (errors include hints so Claude can self-correct, e.g. retry with the right commodity name).
  3. When Claude answers, the grounding verifier checks every data figure against the evidence.
     If something does not trace back, Claude gets one chance to revise (self-correction).
  4. If the API is unreachable, times out or rejects the key, we fall back to the rule-based advisor.

Every step is streamed to the UI as an event so judges and farmers can watch the reasoning.
History is append-only (thinking blocks are passed back unchanged), and a failed turn is rolled
back to its starting point so the session stays valid.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable

import anthropic
from anthropic import AsyncAnthropic

from ..config import Settings
from ..tools.registry import tool_schemas
from .fallback import rule_based_turn, tool_end_event
from .prompts import SYSTEM_PROMPT, farm_context_block, verifier_feedback
from .sessions import ChatSession
from .verifier import verify

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
MAX_REVISIONS = 1


class AIUnavailable(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0

    def add(self, usage: Any) -> None:
        self.llm_calls += 1
        if usage is None:
            return
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


class FarmAgent:
    def __init__(self, settings: Settings, client: AsyncAnthropic | None):
        self.settings = settings
        self.client = client
        self._compat = False  # True once the API rejected optional request features
        self._tools_full = tool_schemas()
        self._tools_compat = [{k: v for k, v in t.items() if k != "eager_input_streaming"} for t in self._tools_full]

    async def run_turn(self, session: ChatSession, question: str, emit: Emit) -> dict[str, Any]:
        if self.client is None:
            return await rule_based_turn(session, question, emit, "no ANTHROPIC_API_KEY configured")
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
        first_turn = not session.started
        user_text = question
        if first_turn:
            user_text = farm_context_block({**session.farm.model_dump(mode="json")}, date.today()) + "\n\n" + question
        session.messages.append({"role": "user", "content": user_text})
        usage, revisions = Usage(), 0

        for step in range(1, self.settings.max_agent_steps + 1):
            await emit({"type": "step", "n": step})
            last_step = step == self.settings.max_agent_steps
            message = await self._call(session.messages, emit, usage, force_answer=last_step)
            session.messages.append({"role": "assistant", "content": message.content})
            if any(getattr(b, "type", "") == "fallback" for b in message.content):
                await emit({"type": "notice", "message": f"Served by fallback model {message.model}."})

            if message.stop_reason == "refusal":
                text = "I can't help with that request. I can help with weather, crops, irrigation, prices and climate risks for your farm."
                return await self._final(emit, session, text, usage, message.model, verify(text, session.ledger))
            if message.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in message.content if b.type == "tool_use"]
            if tool_uses:
                if message.stop_reason == "max_tokens":
                    raise AIUnavailable("model output was cut off mid tool call")
                session.messages.append({"role": "user", "content": await self._run_tools(session, tool_uses, emit, usage)})
                continue

            text = "".join(b.text for b in message.content if b.type == "text").strip()
            if not text:
                raise AIUnavailable("model returned an empty answer")
            report = verify(text, session.ledger, [user_text])
            if not report.ok and revisions < MAX_REVISIONS and not last_step:
                revisions += 1
                await emit({"type": "verification", "report": report.to_dict(), "revising": True})
                session.messages.append({"role": "user", "content": verifier_feedback(
                    report.unsupported, report.unknown_citations, report.missing_citations)})
                continue
            return await self._final(emit, session, text, usage, message.model, report, revised=revisions > 0)
        raise AIUnavailable("step limit reached without an answer")

    async def _final(self, emit: Emit, session: ChatSession, text: str, usage: Usage, model: str,
                     report: Any, revised: bool = False) -> dict[str, Any]:
        event = {"type": "final", "text": text, "verification": report.to_dict(), "self_corrected": revised,
                 "evidence": [ev.public() for ev in session.ledger.all()], "usage": usage.to_dict(),
                 "mode": "ai", "model": model}
        await emit(event)
        return event

    async def _run_tools(self, session: ChatSession, tool_uses: list[Any], emit: Emit, usage: Usage) -> list[dict]:
        for block in tool_uses:
            await emit({"type": "tool_start", "id": block.id, "name": block.name, "input": block.input})
        usage.tool_calls += len(tool_uses)
        runs = await asyncio.gather(*(session.executor.run(b.name, b.input) for b in tool_uses))
        results = []
        for block, run in zip(tool_uses, runs):
            await emit(tool_end_event(block.id, run))
            results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": not run.ok,
                            "content": json.dumps(run.result_payload(), ensure_ascii=False, default=str)})
        return results  # all results in ONE user message, which keeps parallel tool use working well

    async def _call(self, messages: list[dict], emit: Emit, usage: Usage, force_answer: bool = False) -> Any:
        assert self.client is not None
        json_retries = 0
        while True:
            kwargs: dict[str, Any] = {
                "model": self.settings.model,
                "max_tokens": MAX_TOKENS,
                "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                "tools": self._tools_compat if self._compat else self._tools_full,
                "messages": messages,
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": self.settings.effort},
                "cache_control": {"type": "ephemeral"},  # also cache the growing conversation prefix
            }
            if force_answer:
                kwargs["tool_choice"] = {"type": "none"}
            if not self._compat:
                # Server-side fallback: if a safety classifier declines, Anthropic re-runs the request
                # on its recommended fallback model inside the same call.
                kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
            try:
                async with self.client.beta.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        await _forward(event, emit)
                    message = await stream.get_final_message()
                usage.add(message.usage)
                return message
            except ValueError:
                # Tool-input JSON the SDK could not parse (eager input streaming): re-issue the turn.
                json_retries += 1
                if json_retries > 2:
                    raise AIUnavailable("could not parse the model's tool input") from None
                continue
            except anthropic.BadRequestError as exc:
                if not self._compat:
                    log.warning("Request rejected (%s); retrying without optional features", exc.message)
                    self._compat = True
                    continue
                raise AIUnavailable(f"request rejected: {exc.message}") from None
            except anthropic.AuthenticationError:
                raise AIUnavailable("invalid Anthropic API key") from None
            except anthropic.PermissionDeniedError:
                raise AIUnavailable(f"API key has no access to {self.settings.model}") from None
            except anthropic.NotFoundError:
                raise AIUnavailable(f"model {self.settings.model} not found") from None
            except anthropic.RateLimitError:
                raise AIUnavailable("Anthropic rate limit reached") from None
            except anthropic.APIStatusError as exc:
                raise AIUnavailable(f"Anthropic API error {exc.status_code}") from None
            except anthropic.APIConnectionError:
                raise AIUnavailable("cannot reach the Anthropic API") from None


async def _forward(event: Any, emit: Emit) -> None:
    """Relay the parts of the Claude stream the UI shows live."""
    etype = getattr(event, "type", "")
    if etype == "thinking" and event.thinking:
        await emit({"type": "thinking", "delta": event.thinking})
    elif etype == "text" and event.text:
        await emit({"type": "text", "delta": event.text})
    elif etype == "content_block_start" and getattr(event.content_block, "type", "") == "tool_use":
        await emit({"type": "tool_planned", "id": event.content_block.id, "name": event.content_block.name})
