"""Gemini implementation of the KhetKavach agent loop (Google Gen AI SDK, function calling).

The turn is the same as the Claude agent's: plan, parallel tool calls, grounded answer,
verification, one self-correction, with `BaseAgent`'s rollback and rule-based fallback.
Gemini specifics:

* Tools are declared as FunctionDeclarations generated from the same pydantic input models.
* Automatic function calling is off: we run tools ourselves so they are validated, traced and
  recorded as evidence.
* The model's Content is appended to history exactly as returned. That keeps Gemini's thought
  signatures, which it needs to continue multi-step function calling.
* Thought summaries (include_thoughts) are relayed to the agent trace.
* If the configured model id is unavailable (404), the next model in the fallback chain is used.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from google.genai import errors, types

from ..config import Settings
from ..tools.registry import TOOLS, ToolSpec
from .base import MAX_REVISIONS, AIUnavailable, BaseAgent, Emit, Usage
from .prompts import REFUSAL_TEXT, SYSTEM_PROMPT, verifier_feedback
from .sessions import ChatSession
from .verifier import verify

log = logging.getLogger(__name__)

FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]
BLOCKED = {types.FinishReason.SAFETY, types.FinishReason.PROHIBITED_CONTENT, types.FinishReason.BLOCKLIST,
           types.FinishReason.SPII, types.FinishReason.RECITATION}
BROKEN_CALL = {types.FinishReason.MALFORMED_FUNCTION_CALL, types.FinishReason.UNEXPECTED_TOOL_CALL,
               types.FinishReason.TOO_MANY_TOOL_CALLS}


def _property_schema(prop: dict[str, Any]) -> types.Schema:
    """One pydantic JSON-schema property -> Gemini's OpenAPI-style Schema (Optional becomes nullable)."""
    nullable = False
    base = prop
    if "anyOf" in prop:
        options = [p for p in prop["anyOf"] if p.get("type") != "null"]
        nullable = len(options) < len(prop["anyOf"])
        base = {**(options[0] if options else {}), **{k: v for k, v in prop.items() if k != "anyOf"}}
    kwargs: dict[str, Any] = {"type": str(base.get("type", "string")).upper(), "description": base.get("description")}
    if "enum" in base:
        kwargs["enum"] = [str(v) for v in base["enum"]]
    for key, target in (("minimum", "minimum"), ("maximum", "maximum"),
                        ("minLength", "min_length"), ("maxLength", "max_length")):
        if key in base:
            kwargs[target] = base[key]
    if nullable:
        kwargs["nullable"] = True
    return types.Schema(**kwargs)


def declaration(spec: ToolSpec) -> types.FunctionDeclaration:
    js = spec.input_model.model_json_schema()
    params = types.Schema(type="OBJECT",
                          properties={name: _property_schema(p) for name, p in js.get("properties", {}).items()},
                          required=js.get("required", []))
    return types.FunctionDeclaration(name=spec.name, description=spec.description, parameters=params)


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def _describe(exc: errors.APIError, model: str) -> str:
    if exc.code in (401, 403) or "api key" in str(exc.message or "").lower():
        return "Gemini API key rejected (check GEMINI_API_KEY)"
    if exc.code == 404:
        return f"Gemini model {model} not found (set KK_GEMINI_MODEL)"
    if exc.code == 429:
        return "Gemini rate limit reached (free tier); try again in a minute"
    if exc.code == 400:
        return f"Gemini rejected the request: {exc.message}"
    return f"Gemini API error {exc.code}"


class GeminiAgent(BaseAgent):
    provider = "gemini"

    def __init__(self, settings: Settings, client: Any):
        super().__init__(settings)
        self.client = client
        self._models = list(dict.fromkeys([settings.gemini_model, *FALLBACK_MODELS]))
        self._model_index = 0
        self._thinking = True  # switched off if a model rejects thinking_config
        self._tools = [types.Tool(function_declarations=[declaration(t) for t in TOOLS])]

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @property
    def model(self) -> str:
        return self._models[self._model_index]

    async def _agent_turn(self, session: ChatSession, question: str, emit: Emit) -> dict[str, Any]:
        user_text = self.opening_text(session, question)
        session.messages.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        usage, revisions = Usage(), 0

        for step in range(1, self.settings.max_agent_steps + 1):
            await emit({"type": "step", "n": step})
            last_step = step == self.settings.max_agent_steps
            response = await self._generate(session.messages, usage, force_answer=last_step)
            candidate = response.candidates[0] if response.candidates else None
            finish = candidate.finish_reason if candidate else None
            content = candidate.content if candidate else None

            if candidate is None or finish in BLOCKED:
                # Safety block: answer politely and keep the history alternating user/model.
                session.messages.append(types.Content(role="model", parts=[types.Part(text=REFUSAL_TEXT)]))
                return await self.finish(emit, session, REFUSAL_TEXT, usage, self.model, verify(REFUSAL_TEXT, session.ledger))
            if finish in BROKEN_CALL:
                raise AIUnavailable(f"Gemini produced an invalid function call ({finish.value})")
            if content is None or not content.parts:
                raise AIUnavailable("Gemini returned an empty response")
            session.messages.append(content)  # unchanged: keeps thought signatures
            for part in content.parts:
                if part.thought and part.text:
                    await emit({"type": "thinking", "delta": part.text})

            calls = [p.function_call for p in content.parts if p.function_call]
            if calls:
                if finish == types.FinishReason.MAX_TOKENS:
                    raise AIUnavailable("model output was cut off mid function call")
                ids = [fc.id or f"gemini-{step}-{i}" for i, fc in enumerate(calls)]
                runs = await self.run_tools(session, [(cid, fc.name, dict(fc.args or {})) for cid, fc in zip(ids, calls)],
                                            emit, usage)
                session.messages.append(types.Content(role="user", parts=[
                    types.Part(function_response=types.FunctionResponse(
                        id=fc.id, name=fc.name, response=_jsonable(run.result_payload())))
                    for fc, run in zip(calls, runs)]))
                continue

            text = "".join(p.text for p in content.parts if p.text and not p.thought).strip()
            if not text:
                raise AIUnavailable("Gemini returned an empty answer")
            await emit({"type": "text", "delta": text})
            report = verify(text, session.ledger, [user_text])
            if not report.ok and revisions < MAX_REVISIONS and not last_step:
                revisions += 1
                await emit({"type": "verification", "report": report.to_dict(), "revising": True})
                session.messages.append(types.Content(role="user", parts=[types.Part(text=verifier_feedback(
                    report.unsupported, report.unknown_citations, report.missing_citations))]))
                continue
            return await self.finish(emit, session, text, usage, response.model_version or self.model, report,
                                     revised=revisions > 0)
        raise AIUnavailable("step limit reached without an answer")

    async def _generate(self, contents: list[types.Content], usage: Usage,
                        force_answer: bool = False) -> types.GenerateContentResponse:
        assert self.client is not None
        while True:
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=self._tools,
                tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                    mode="NONE" if force_answer else "AUTO")),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=types.ThinkingConfig(include_thoughts=True) if self._thinking else None,
            )
            try:
                response = await self.client.aio.models.generate_content(model=self.model, contents=contents,
                                                                         config=config)
            except errors.ClientError as exc:
                if exc.code == 404 and self._model_index + 1 < len(self._models):
                    log.warning("Gemini model %s unavailable; trying %s", self.model, self._models[self._model_index + 1])
                    self._model_index += 1
                    continue
                if exc.code == 400 and self._thinking and "think" in str(exc.message or "").lower():
                    log.warning("Model %s rejected thinking_config; retrying without it", self.model)
                    self._thinking = False
                    continue
                raise AIUnavailable(_describe(exc, self.model)) from None
            except errors.APIError as exc:
                raise AIUnavailable(_describe(exc, self.model)) from None
            except httpx.HTTPError:
                raise AIUnavailable("cannot reach the Gemini API") from None
            meta = response.usage_metadata
            if meta is not None:
                cached = meta.cached_content_token_count or 0
                usage.add((meta.prompt_token_count or 0) - cached,
                          (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0), cached)
            else:
                usage.add()
            return response

    async def write_short(self, system: str, prompt: str) -> str | None:
        if self.client is None:
            return None
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model, contents=prompt, config=types.GenerateContentConfig(system_instruction=system))
        except (errors.APIError, httpx.HTTPError) as exc:
            log.info("Short completion unavailable: %s", type(exc).__name__)
            return None
        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.finish_reason in BLOCKED or not candidate.content or not candidate.content.parts:
            return None
        return "".join(p.text for p in candidate.content.parts if p.text and not p.thought).strip() or None

    async def aclose(self) -> None:
        aio = getattr(self.client, "aio", None)
        if aio is not None and hasattr(aio, "aclose"):
            await aio.aclose()
