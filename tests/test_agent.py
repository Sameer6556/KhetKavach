"""Agent loop tests with a scripted Claude stand-in (no network, no API spend)."""

from datetime import date, timedelta

import anthropic
import httpx

from app.agent.loop import FALLBACK_BETA, FarmAgent
from app.agent.sessions import SessionStore
from app.schemas import FarmProfile
from tests.conftest import FARM, make_settings
from tests.fakes import Block, Message, ScriptedClaude, last_tool_results

SOWN = date.today() - timedelta(days=50)
CROP_ARGS = {**FARM, "crop": "onion", "sowing_date": SOWN.isoformat()}


def farm(**kw) -> FarmProfile:
    return FarmProfile(**{**FARM, "crop": "onion", "sowing_date": SOWN, "irrigation_method": "drip", **kw})


async def turn(agent, session, question="Should I irrigate this week?"):
    events = []

    async def emit(ev):
        events.append(ev)

    final = await agent.run_turn(session, question, emit)
    return final, events


def plan(_messages):
    return Message([Block("thinking", thinking="Need the forecast and the water balance."),
                    Block("tool_use", id="t1", name="get_weather_outlook", input={**FARM, "days": 7}),
                    Block("tool_use", id="t2", name="get_irrigation_plan", input={**CROP_ARGS, "irrigation_method": "drip"})],
                   "tool_use")


def grounded(messages):
    res = last_tool_results(messages) if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list) else {}
    if not res:  # after verifier feedback the tool results are further back
        for m in reversed(messages):
            if m["role"] == "user" and isinstance(m["content"], list):
                res = {r["tool_use_id"]: __import__("json").loads(r["content"]) for r in m["content"]}
                break
    w, i = res["t1"], res["t2"]
    rain = w["data"]["summary"]["rain_total_mm"]
    dep = i["data"]["soil_water_today"]["depletion_pct"]
    return Message([Block("text", text=f"Rain of {rain} mm is coming this week [{w['evidence_id']}]. "
                                       f"Your root zone is {dep}% depleted [{i['evidence_id']}].")], "end_turn")


def hallucinated(_messages):
    return Message([Block("text", text="Expect 999 mm of rain [E1].")], "end_turn")


async def test_agent_plans_parallel_tools_and_answers_with_verified_evidence(ctx):
    claude = ScriptedClaude([plan, grounded])
    agent = FarmAgent(make_settings(ctx.settings.db_path.parent), claude)  # type: ignore[arg-type]
    session = SessionStore(ctx).get_or_create(None, farm())
    final, events = await turn(agent, session)

    assert final["mode"] == "ai" and final["verification"]["ok"]
    assert final["verification"]["data_figures"] == 2
    starts = [e for e in events if e["type"] == "tool_start"]
    assert [e["name"] for e in starts] == ["get_weather_outlook", "get_irrigation_plan"]
    assert any(e["type"] == "thinking" for e in events)

    # History: user -> assistant(tool_use x2) -> ONE user message with both results -> assistant(text)
    roles = [m["role"] for m in session.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert len(session.messages[2]["content"]) == 2
    assert "<farm_context>" in session.messages[0]["content"]

    req = claude.requests[0]
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert req["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert req["output_config"] == {"effort": "medium"}
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert all(t["eager_input_streaming"] for t in req["tools"])


async def test_verifier_triggers_one_self_correction(ctx):
    claude = ScriptedClaude([plan, hallucinated, grounded])
    agent = FarmAgent(make_settings(ctx.settings.db_path.parent), claude)  # type: ignore[arg-type]
    session = SessionStore(ctx).get_or_create(None, farm())
    final, events = await turn(agent, session)

    revising = [e for e in events if e["type"] == "verification"]
    assert revising and "999 mm" in revising[0]["report"]["unsupported"]
    assert final["self_corrected"] and final["verification"]["ok"]
    feedback = session.messages[4]["content"]
    assert "999 mm" in feedback and "<verification_feedback>" in feedback


async def test_tool_error_hint_lets_agent_retry(ctx):
    def ask_saffron(_m):
        return Message([Block("tool_use", id="m1", name="get_mandi_prices", input={"commodity": "saffron", "state": "Maharashtra"})], "tool_use")

    def retry_onion(messages):
        err = last_tool_results(messages)["m1"]
        assert "Onion" in err["error"]["hint"]
        return Message([Block("tool_use", id="m2", name="get_mandi_prices", input={"commodity": "Onion", "state": "Maharashtra"})], "tool_use")

    def answer(messages):
        res = last_tool_results(messages)["m2"]
        best = res["data"]["best_markets"][0]
        return Message([Block("text", text=f"{best['market']} pays ₹{best['modal_price']}/quintal [{res['evidence_id']}].")], "end_turn")

    agent = FarmAgent(make_settings(ctx.settings.db_path.parent), ScriptedClaude([ask_saffron, retry_onion, answer]))  # type: ignore[arg-type]
    session = SessionStore(ctx).get_or_create(None, farm())
    final, events = await turn(agent, session, "Where should I sell?")
    ends = [e for e in events if e["type"] == "tool_end"]
    assert [e["ok"] for e in ends] == [False, True]
    assert "Mumbai" in final["text"] and final["verification"]["ok"]


async def test_api_outage_falls_back_to_rules_and_rolls_back_history(ctx):
    def down(_m):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    agent = FarmAgent(make_settings(ctx.settings.db_path.parent), ScriptedClaude([down]))  # type: ignore[arg-type]
    session = SessionStore(ctx).get_or_create(None, farm(language="hi"))
    final, events = await turn(agent, session)
    assert final["mode"] == "rules"
    assert session.messages == []  # incomplete turn rolled back, session still valid
    assert any(e["type"] == "notice" for e in events)
    assert "सिंचाई" in final["text"]  # Hindi rule-based template
    assert final["verification"]["ok"]


async def test_unexpected_crash_still_answers_with_rules(ctx):
    def buggy(_m):
        raise KeyError("something unexpected")

    agent = FarmAgent(make_settings(ctx.settings.db_path.parent), ScriptedClaude([buggy]))  # type: ignore[arg-type]
    session = SessionStore(ctx).get_or_create(None, farm())
    final, _ = await turn(agent, session)
    assert final["mode"] == "rules" and session.messages == []


async def test_bad_request_retries_in_compat_mode(ctx):
    calls = {"n": 0}

    def flaky(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            raise anthropic.BadRequestError(
                message="fallbacks: unsupported", body=None,
                response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")))
        return Message([Block("text", text="I can help with weather, crops and prices for your farm.")], "end_turn")

    claude = ScriptedClaude([flaky, flaky])
    agent = FarmAgent(make_settings(ctx.settings.db_path.parent), claude)  # type: ignore[arg-type]
    final, _ = await turn(agent, SessionStore(ctx).get_or_create(None, farm()), "hello")
    assert final["mode"] == "ai"
    assert "betas" not in claude.requests[1] and "fallbacks" not in claude.requests[1]
    assert "eager_input_streaming" not in claude.requests[1]["tools"][0]


async def test_no_api_key_uses_rule_based_advisor(ctx):
    agent = FarmAgent(make_settings(ctx.settings.db_path.parent), None)
    session = SessionStore(ctx).get_or_create(None, farm())
    final, events = await turn(agent, session, "What is the onion price in the mandi?")
    assert final["mode"] == "rules"
    assert "get_mandi_prices" in [e["name"] for e in events if e["type"] == "tool_start"]


async def test_session_is_reused_only_for_the_same_farm(ctx):
    store = SessionStore(ctx)
    a = store.get_or_create(None, farm())
    assert store.get_or_create(a.id, farm()) is a
    assert store.get_or_create(a.id, farm(crop="wheat")) is not a
