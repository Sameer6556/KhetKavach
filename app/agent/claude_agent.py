"""Claude implementation of the KhetKavach agent loop (Anthropic Messages API, streaming tool use).

One user turn:
  1. Claude plans and requests tools (often several in parallel).
  2. We validate each input, run the tools concurrently against live data, and return results
     (errors include hints so Claude can self-correct, e.g. retry with the right commodity name).
  3. When Claude answers, the grounding verifier checks every data figure against the evidence.
     If something does not trace back, Claude gets one chance to revise (self-correction).
  4. If the API is unreachable, times out or rejects the key, `BaseAgent` falls back to the
     rule-based advisor and rolls the turn back so the session stays valid.

Every step is streamed to the UI. History is append-only: thinking blocks are passed back unchanged.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from ..config import Settings
from ..diary.models import PhotoFindings
from ..tools.registry import tool_schemas
from .base import MAX_REVISIONS, AIUnavailable, BaseAgent, Emit, Usage
from .prompts import REFUSAL_TEXT, SYSTEM_PROMPT, VISION_SYSTEM, verifier_feedback
from .sessions import ChatSession
from .verifier import verify

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


class ClaudeAgent(BaseAgent):
    provider = "claude"

    def __init__(self, settings: Settings, client: AsyncAnthropic | None):
        super().__init__(settings)
        self.client = client
        self._compat = False  # True once the API rejected optional request features
        self._tools_full = tool_schemas()
        self._tools_compat = [{k: v for k, v in t.items() if k != "eager_input_streaming"} for t in self._tools_full]

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @property
    def model(self) -> str:
        return self.settings.model

    async def _agent_turn(self, session: ChatSession, question: str, emit: Emit) -> dict[str, Any]:
        user_text = self.opening_text(session, question)
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
                return await self.finish(emit, session, REFUSAL_TEXT, usage, message.model, verify(REFUSAL_TEXT, session.ledger))
            if message.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in message.content if b.type == "tool_use"]
            if tool_uses:
                if message.stop_reason == "max_tokens":
                    raise AIUnavailable("model output was cut off mid tool call")
                runs = await self.run_tools(session, [(b.id, b.name, b.input) for b in tool_uses], emit, usage)
                # All results go back in ONE user message, which keeps parallel tool use working well.
                session.messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "is_error": not run.ok,
                     "content": json.dumps(run.result_payload(), ensure_ascii=False, default=str)}
                    for b, run in zip(tool_uses, runs)]})
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
            return await self.finish(emit, session, text, usage, message.model, report, revised=revisions > 0)
        raise AIUnavailable("step limit reached without an answer")

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
                u = message.usage
                usage.add(getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0),
                          getattr(u, "cache_read_input_tokens", 0), getattr(u, "cache_creation_input_tokens", 0))
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

    async def write_short(self, system: str, prompt: str) -> str | None:
        if self.client is None:
            return None
        kwargs: dict[str, Any] = {"model": self.settings.model, "max_tokens": 2048, "system": system,
                                  "output_config": {"effort": "low"},
                                  "messages": [{"role": "user", "content": prompt}]}
        if not self._compat:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        try:
            msg = await self.client.beta.messages.create(**kwargs)
        except anthropic.APIError as exc:
            log.info("Short completion unavailable: %s", type(exc).__name__)
            return None
        if msg.stop_reason == "refusal":
            return None
        return "".join(b.text for b in msg.content if b.type == "text").strip() or None

    async def analyze_photo(self, photos: list[bytes], prompt: str) -> PhotoFindings | None:
        if self.client is None:
            return None
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(p).decode("ascii")}} for p in photos]
        content.append({"type": "text", "text": prompt})
        kwargs: dict[str, Any] = {"model": self.settings.model, "max_tokens": 4096, "system": VISION_SYSTEM,
                                  "output_format": PhotoFindings, "output_config": {"effort": "low"},
                                  "messages": [{"role": "user", "content": content}]}
        if not self._compat:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        try:
            msg = await self.client.beta.messages.parse(**kwargs)
        except anthropic.APIError as exc:
            log.info("Photo analysis unavailable: %s", type(exc).__name__)
            return None
        except ValueError:  # the structured output did not validate
            log.warning("Photo analysis returned invalid JSON")
            return None
        if msg.stop_reason in ("refusal", "max_tokens"):
            return None
        return msg.parsed_output

    async def aclose(self) -> None:
        if self.client is not None and hasattr(self.client, "close"):
            await self.client.close()


async def _forward(event: Any, emit: Emit) -> None:
    """Relay the parts of the Claude stream the UI shows live."""
    etype = getattr(event, "type", "")
    if etype == "thinking" and event.thinking:
        await emit({"type": "thinking", "delta": event.thinking})
    elif etype == "text" and event.text:
        await emit({"type": "text", "delta": event.text})
    elif etype == "content_block_start" and getattr(event.content_block, "type", "") == "tool_use":
        await emit({"type": "tool_planned", "id": event.content_block.id, "name": event.content_block.name})
