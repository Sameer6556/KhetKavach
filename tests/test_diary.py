"""Field diary: photo intake, AI photo analysis (Gemini and Claude), the agent tool, API and Sentinel alerts."""

import json
from datetime import date, timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import types
from PIL import Image

import app.main as main
from app.agent.base import BaseAgent
from app.agent.claude_agent import ClaudeAgent
from app.agent.gemini_agent import GeminiAgent
from app.config import get_settings
from app.diary.images import MAX_UPLOAD_BYTES, PhotoError, prepare_photo
from app.diary.models import Observations, PhotoFindings
from app.diary.service import compare, create_entry, diary_summary
from app.evidence import EvidenceLedger
from app.http import DataClient
from app.schemas import FarmProfile
from app.sentinel.monitor import diary_alerts
from app.tools.registry import ToolExecutor
from tests.conftest import FARM, make_settings
from tests.fakes import ScriptedClaude, ScriptedGemini, gemini_response

SOWN = date.today() - timedelta(days=50)
FINDINGS = {"photo_quality": "good", "matches_declared_crop": "yes", "observed_stage": "vegetative",
            "canopy_cover_pct": 45, "leaf_colour": "mixed", "water_stress_signs": "mild", "weed_pressure": "low",
            "symptoms": [{"name": "leaf spots", "where": "lower leaves", "likelihood": "high"}],
            "change_since_last_photo": "no previous photo", "summary": "निचली पत्तियों पर धब्बे दिख रहे हैं।",
            "next_checks": ["Look under the lower leaves for spots"]}


def farm(**kw) -> FarmProfile:
    return FarmProfile(**{**FARM, "crop": "onion", "sowing_date": SOWN, "irrigation_method": "drip",
                          "language": "hi", **kw})


def photo_bytes(width=3000, height=2000, orientation=6) -> bytes:
    img = Image.new("RGB", (width, height), (40, 140, 60))
    exif = Image.Exif()
    exif[0x0112] = orientation  # camera says: rotate 90° clockwise for display
    exif[0x010E] = "field near home, 20.02N 74.00E"  # identifying metadata that must not survive
    buf = BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


# ----------------------------------------------------------------------------------------- photo intake
def test_photo_is_rotated_shrunk_and_stripped_of_metadata():
    jpeg, width, height = prepare_photo(photo_bytes())
    assert (width, height) == (683, 1024)  # rotated portrait, longest side 1024
    out = Image.open(BytesIO(jpeg))
    assert out.format == "JPEG" and len(out.getexif()) == 0


def test_bad_photos_are_rejected():
    with pytest.raises(PhotoError):
        prepare_photo(b"definitely not an image")
    with pytest.raises(PhotoError):
        prepare_photo(b"\xff" * (MAX_UPLOAD_BYTES + 1))


def test_observation_dates_must_be_recent():
    with pytest.raises(ValueError):
        Observations(irrigated_on=date.today() + timedelta(days=1))
    assert Observations(note="  ").note is None


def test_week_over_week_comparison():
    prev = {**FINDINGS, "canopy_cover_pct": 30, "observed_stage": "germination/establishment",
            "symptoms": [{"name": "yellowing", "where": "tips", "likelihood": "low"}]}
    diff = compare(FINDINGS, prev)
    assert diff == {"canopy_cover_change_pct_points": 15, "new_symptoms": ["leaf spots"],
                    "symptoms_no_longer_seen": ["yellowing"], "stage_change": "germination/establishment -> vegetative"}
    assert compare(FINDINGS, None) is None


# ----------------------------------------------------------------------------------------- AI analysis
async def test_gemini_photo_analysis_compares_with_last_week(ctx):
    gemini = ScriptedGemini([lambda m, c, cfg: gemini_response(types.Part(text=json.dumps(FINDINGS)))])
    agent = GeminiAgent(make_settings(ctx.settings.db_path.parent, llm_provider="gemini", gemini_api_key="k"), gemini)
    first = await create_entry(ctx.db, agent, farm(), Observations(pests_seen="few"), photo_bytes())
    assert first["analysis_status"] == "ok" and first["analysis"]["symptoms"][0]["name"] == "leaf spots"
    assert first["photo_url"].startswith("/api/diary/photo/")

    cfg = gemini.requests[0]["config"]
    assert cfg.response_mime_type == "application/json" and cfg.response_schema is PhotoFindings
    image_parts = [p for p in gemini.requests[0]["contents"][0].parts if p.inline_data]
    assert len(image_parts) == 1 and image_parts[0].inline_data.mime_type == "image/jpeg"

    await create_entry(ctx.db, agent, farm(), Observations(irrigated_on=date.today()), photo_bytes())
    second_request = gemini.requests[1]["contents"][0].parts
    assert len([p for p in second_request if p.inline_data]) == 2  # last week's photo + this week's
    assert "previous photo" in second_request[-1].text

    summary = diary_summary(ctx.db, farm())["data"]
    assert summary["entries_count"] == 2 and summary["last_irrigation_reported"] == date.today().isoformat()


async def test_claude_photo_analysis_uses_structured_output(ctx):
    def parsed(_messages):
        return SimpleNamespace(stop_reason="end_turn", parsed_output=PhotoFindings.model_validate(FINDINGS))

    claude = ScriptedClaude([parsed])
    agent = ClaudeAgent(make_settings(ctx.settings.db_path.parent), claude)  # type: ignore[arg-type]
    entry = await create_entry(ctx.db, agent, farm(), Observations(), photo_bytes())
    assert entry["analysis_status"] == "ok"
    req = claude.requests[0]
    assert req["output_format"] is PhotoFindings and req["output_config"] == {"effort": "low"}
    assert req["messages"][0]["content"][0]["type"] == "image"


async def test_without_ai_the_check_in_is_still_saved(ctx):
    entry = await create_entry(ctx.db, BaseAgent(ctx.settings), farm(), Observations(leaf_colour="yellowing"), photo_bytes())
    assert entry["analysis_status"] == "unavailable" and entry["analysis"] is None
    assert entry["observations"] == {"leaf_colour": "yellowing"}


async def test_failed_analysis_never_blocks_the_check_in(ctx):
    def broken(model, contents, config):
        return gemini_response(types.Part(text="not json"))

    agent = GeminiAgent(make_settings(ctx.settings.db_path.parent, llm_provider="gemini", gemini_api_key="k"),
                        ScriptedGemini([broken]))
    entry = await create_entry(ctx.db, agent, farm(), Observations(), photo_bytes())
    assert entry["analysis_status"] == "failed" and entry["has_photo"]


# ----------------------------------------------------------------------------------------- tool + irrigation
async def test_field_diary_tool_feeds_the_agent(ctx):
    executor = ToolExecutor(ctx, EvidenceLedger(), farm=farm())
    empty = await executor.run("get_field_diary", {})
    assert empty.ok and empty.output.data["entries_count"] == 0 and "No check-ins" in empty.output.headline

    gemini = ScriptedGemini([lambda m, c, cfg: gemini_response(types.Part(text=json.dumps(FINDINGS)))])
    agent = GeminiAgent(make_settings(ctx.settings.db_path.parent, llm_provider="gemini", gemini_api_key="k"), gemini)
    await create_entry(ctx.db, agent, farm(), Observations(pests_seen="few"), photo_bytes())
    run = await executor.run("get_field_diary", {"weeks": 4})
    assert run.ok and run.evidence is not None
    entry = run.output.data["entries"][0]
    assert entry["farmer_observations"] == {"pests_seen": "few"}
    assert entry["photo_findings"]["symptoms"][0]["likelihood"] == "high"
    assert run.output.level == "moderate" and "possible leaf spots" in run.output.headline


async def test_field_size_gives_litres_for_the_whole_field(executor):
    args = {**FARM, "crop": "onion", "sowing_date": SOWN.isoformat(), "irrigation_method": "drip", "area_acres": 2.5}
    out = (await executor.run("get_irrigation_plan", args)).output
    assert out.data["field_size_acres"] == 2.5
    nxt = out.data["next_irrigation"]
    if nxt:
        assert nxt["litres_for_field"] == pytest.approx(nxt["litres_per_acre"] * 2.5, rel=0.01)


# ----------------------------------------------------------------------------------------- Sentinel
def test_sentinel_diary_alerts():
    assert diary_alerts([])[0]["kind"] == "diary_reminder"
    old = {"id": 1, "created_at": (date.today() - timedelta(days=9)).isoformat() + "T08:00:00+00:00", "analysis": None}
    assert diary_alerts([old])[0]["kind"] == "diary_reminder"
    recent = {"id": 7, "created_at": date.today().isoformat() + "T08:00:00+00:00", "analysis": FINDINGS}
    alert = diary_alerts([recent])[0]
    assert alert["kind"] == "photo_symptom:7" and "leaf spots" in alert["title"]
    calm = {**recent, "analysis": {**FINDINGS, "symptoms": []}}
    assert diary_alerts([calm]) == []


# ----------------------------------------------------------------------------------------- HTTP API
@pytest.fixture
def client(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("KK_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("KK_SENTINEL_INTERVAL_MIN", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(main, "DataClient", lambda db, ua, timeout: DataClient(db, ua, timeout, transport=upstream.transport()))
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        yield c
    get_settings.cache_clear()


def test_diary_api_round_trip(client):
    profile = farm().model_dump(mode="json")
    resp = client.post("/api/diary", data={"farm": json.dumps(profile), "observations": json.dumps({"pests_seen": "none"})},
                       files={"photo": ("crop.jpg", photo_bytes(), "image/jpeg")})
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert entry["analysis_status"] == "unavailable" and entry["photo_url"]

    listing = client.get("/api/diary", params={"lat": FARM["lat"], "lon": FARM["lon"], "crop": "onion",
                                               "sowing_date": SOWN.isoformat()}).json()
    assert listing["summary"]["entries_count"] == 1 and listing["entries"][0]["token"] == entry["token"]

    photo = client.get(entry["photo_url"])
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"

    dash = [json.loads(line[6:]) for line in client.post("/api/dashboard", json=profile).text.splitlines()
            if line.startswith("data: ")]
    diary_card = next(e for e in dash if e.get("tool") == "get_field_diary")
    assert diary_card["ok"] and diary_card["chart"]["entries"][0]["photo_url"] == entry["photo_url"]

    assert client.delete(f"/api/diary/{entry['token']}").status_code == 200
    assert client.get(entry["photo_url"]).status_code == 404


def test_diary_api_rejects_bad_input(client):
    profile = json.dumps(farm().model_dump(mode="json"))
    assert client.post("/api/diary", data={"farm": profile}).status_code == 422  # nothing to record
    assert client.post("/api/diary", data={"farm": "{}"}).status_code == 422
    bad = client.post("/api/diary", data={"farm": profile}, files={"photo": ("x.jpg", b"nope", "image/jpeg")})
    assert bad.status_code == 422 and "Photo not accepted" in bad.text
