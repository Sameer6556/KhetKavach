"""Gemini agent loop tests with a scripted stand-in built from real google-genai types (no network)."""

from datetime import date, timedelta

import pytest
from google.genai import errors, types

from app.agent.base import BaseAgent
from app.agent.claude_agent import ClaudeAgent
from app.agent.factory import build_agent
from app.agent.gemini_agent import FALLBACK_MODELS, GeminiAgent, declaration
from app.agent.prompts import REFUSAL_TEXT, SYSTEM_PROMPT
from app.agent.sessions import SessionStore
from app.config import _resolve_provider
from app.schemas import FarmProfile
from app.tools.registry import TOOLS, TOOLS_BY_NAME
from tests.conftest import FARM, make_settings
from tests.fakes import ScriptedGemini, gemini_response, last_function_responses

SOWN = date.today() - timedelta(days=50)
CROP_ARGS = {**FARM, "crop": "onion", "sowing_date": SOWN.isoformat()}


def farm(**kw) -> FarmProfile:
    return FarmProfile(**{**FARM, "crop": "onion", "sowing_date": SOWN, "irrigation_method": "drip", **kw})


def settings(ctx, **kw):
    return make_settings(ctx.settings.db_path.parent, llm_provider="gemini", gemini_api_key="test",
                         gemini_model="gemini-test-model", **kw)


async def turn(agent, session, question="Should I irrigate this week?"):
    events = []

    async def emit(ev):
        events.append(ev)

    return await agent.run_turn(session, question, emit), events


def plan(model, contents, config):
    return gemini_response(
        types.Part(text="Need the forecast and the water balance.", thought=True),
        types.Part(function_call=types.FunctionCall(name="get_weather_outlook", args={**FARM, "days": 7}),
                   thought_signature=b"signature-1"),
        types.Part(function_call=types.FunctionCall(name="get_irrigation_plan", args={**CROP_ARGS, "irrigation_method": "drip"})))


def grounded(model, contents, config):
    responses = {}
    for content in reversed(contents):  # after verifier feedback the results are further back
        responses = {p.function_response.name: p.function_response.response for p in content.parts if p.function_response}
        if responses:
            break
    w, i = responses["get_weather_outlook"], responses["get_irrigation_plan"]
    text = (f"Rain of {w['data']['summary']['rain_total_mm']} mm is coming [{w['evidence_id']}]. "
            f"Your root zone is {i['data']['soil_water_today']['depletion_pct']}% depleted [{i['evidence_id']}].")
    return gemini_response(types.Part(text=text))


def hallucinated(model, contents, config):
    return gemini_response(types.Part(text="Expect 999 mm of rain [E1]."))


async def test_gemini_plans_parallel_tools_and_answers_with_verified_evidence(ctx):
    gemini = ScriptedGemini([plan, grounded])
    agent = GeminiAgent(settings(ctx), gemini)
    session = SessionStore(ctx).get_or_create(None, farm())
    final, events = await turn(agent, session)

    assert final["mode"] == "ai" and final["provider"] == "gemini" and final["verification"]["ok"]
    assert final["model"] == "gemini-test"
    assert [e["name"] for e in events if e["type"] == "tool_start"] == ["get_weather_outlook", "get_irrigation_plan"]
    assert any(e["type"] == "thinking" for e in events)

    roles = [c.role for c in session.messages]
    assert roles == ["user", "model", "user", "model"]
    assert session.messages[1].parts[1].thought_signature == b"signature-1"  # model content kept verbatim
    responses = last_function_responses(gemini.requests[1]["contents"])
    assert set(responses) == {"get_weather_outlook", "get_irrigation_plan"} and responses["get_weather_outlook"]["evidence_id"]

    cfg = gemini.requests[0]["config"]
    assert gemini.requests[0]["model"] == "gemini-test-model"
    assert cfg.system_instruction == SYSTEM_PROMPT
    assert len(cfg.tools[0].function_declarations) == len(TOOLS)
    assert cfg.automatic_function_calling.disable is True
    assert cfg.thinking_config.include_thoughts is True
    assert cfg.tool_config.function_calling_config.mode == "AUTO"
    assert final["usage"]["llm_calls"] == 2 and final["usage"]["tool_calls"] == 2


async def test_gemini_self_corrects_after_verifier(ctx):
    agent = GeminiAgent(settings(ctx), ScriptedGemini([plan, hallucinated, grounded]))
    session = SessionStore(ctx).get_or_create(None, farm())
    final, events = await turn(agent, session)
    revising = [e for e in events if e["type"] == "verification"]
    assert revising and "999 mm" in revising[0]["report"]["unsupported"]
    assert final["self_corrected"] and final["verification"]["ok"]


async def test_gemini_switches_model_when_configured_one_is_missing(ctx):
    def missing(model, contents, config):
        raise errors.ClientError(404, {"error": {"code": 404, "message": f"models/{model} is not found", "status": "NOT_FOUND"}})

    gemini = ScriptedGemini([missing, lambda m, c, cfg: gemini_response(types.Part(text="I can help with your farm."))])
    agent = GeminiAgent(settings(ctx), gemini)
    final, _ = await turn(agent, SessionStore(ctx).get_or_create(None, farm()), "hello")
    assert final["mode"] == "ai"
    assert gemini.requests[1]["model"] == FALLBACK_MODELS[0] and agent.model == FALLBACK_MODELS[0]


async def test_gemini_rate_limit_falls_back_to_rules_and_rolls_back(ctx):
    def limited(model, contents, config):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "Resource has been exhausted", "status": "RESOURCE_EXHAUSTED"}})

    agent = GeminiAgent(settings(ctx), ScriptedGemini([limited]))
    session = SessionStore(ctx).get_or_create(None, farm())
    final, events = await turn(agent, session)
    assert final["mode"] == "rules" and session.messages == []
    assert any("rate limit" in e.get("message", "") for e in events if e["type"] == "notice")


async def test_gemini_retries_without_thinking_when_model_rejects_it(ctx):
    def no_thinking(model, contents, config):
        if config.thinking_config is not None:
            raise errors.ClientError(400, {"error": {"code": 400, "message": "Thinking is not supported for this model.",
                                                     "status": "INVALID_ARGUMENT"}})
        return gemini_response(types.Part(text="I can help with your farm."))

    gemini = ScriptedGemini([no_thinking])
    final, _ = await turn(GeminiAgent(settings(ctx), gemini), SessionStore(ctx).get_or_create(None, farm()), "hello")
    assert final["mode"] == "ai" and gemini.requests[1]["config"].thinking_config is None


async def test_gemini_safety_block_answers_politely_and_keeps_history_valid(ctx):
    agent = GeminiAgent(settings(ctx), ScriptedGemini([lambda m, c, cfg: gemini_response(finish="SAFETY")]))
    session = SessionStore(ctx).get_or_create(None, farm())
    final, _ = await turn(agent, session, "something unsafe")
    assert final["text"] == REFUSAL_TEXT
    assert [c.role for c in session.messages] == ["user", "model"]


async def test_gemini_last_step_forces_an_answer(ctx):
    gemini = ScriptedGemini([plan, plan, grounded])
    agent = GeminiAgent(settings(ctx, max_agent_steps=2), gemini)
    final, _ = await turn(agent, SessionStore(ctx).get_or_create(None, farm()))
    assert gemini.requests[-1]["config"].tool_config.function_calling_config.mode == "NONE"
    assert final["mode"] in ("ai", "rules")


async def test_gemini_write_short(ctx):
    ok = GeminiAgent(settings(ctx), ScriptedGemini([lambda m, c, cfg: gemini_response(types.Part(text="Heavy rain on Mon 2 Nov."))]))
    assert await ok.write_short("system", "prompt") == "Heavy rain on Mon 2 Nov."

    def down(model, contents, config):
        raise errors.ServerError(503, {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}})

    assert await GeminiAgent(settings(ctx), ScriptedGemini([down])).write_short("system", "prompt") is None


def test_function_declarations_follow_tool_schemas():
    decl = declaration(TOOLS_BY_NAME["get_irrigation_plan"])
    params = decl.parameters
    assert params.type == types.Type.OBJECT
    assert {"lat", "lon", "crop", "sowing_date"} <= set(params.required)
    assert params.properties["irrigation_method"].enum == ["drip", "sprinkler", "furrow", "flood", "rainfed"]
    assert params.properties["last_irrigation_date"].nullable is True
    assert params.properties["lat"].minimum == -90
    assert declaration(TOOLS_BY_NAME["get_weather_outlook"]).parameters.properties["days"].maximum == 16
    assert all(declaration(t).name == t.name for t in TOOLS)


@pytest.mark.parametrize("requested, anthropic_key, gemini_key, expected", [
    ("auto", None, "g", "gemini"),
    ("auto", "a", "g", "claude"),
    ("auto", None, None, "rules"),
    ("gemini", "a", "g", "gemini"),
    ("gemini", "a", None, "rules"),
    ("rules", "a", "g", "rules"),
])
def test_provider_resolution(requested, anthropic_key, gemini_key, expected):
    assert _resolve_provider(requested, anthropic_key, gemini_key) == expected


def test_factory_builds_the_configured_agent(tmp_path):
    assert isinstance(build_agent(make_settings(tmp_path, llm_provider="gemini", gemini_api_key="k")), GeminiAgent)
    assert isinstance(build_agent(make_settings(tmp_path, llm_provider="claude", anthropic_api_key="k")), ClaudeAgent)
    rules = build_agent(make_settings(tmp_path))
    assert type(rules) is BaseAgent and not rules.enabled
