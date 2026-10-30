"""HTTP API tests: the real FastAPI app with its lifespan, wired to the offline fake upstream."""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import get_settings
from app.http import DataClient
from tests.conftest import FARM

FARM_BODY = {**FARM, "crop": "onion", "sowing_date": (date.today() - timedelta(days=50)).isoformat(),
             "irrigation_method": "drip", "language": "en"}


@pytest.fixture
def client(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("KK_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("KK_SENTINEL_INTERVAL_MIN", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "test-key")
    monkeypatch.setattr(main, "DataClient", lambda db, ua, timeout: DataClient(db, ua, timeout, transport=upstream.transport()))
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        yield c
    get_settings.cache_clear()


def events(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_index_health_and_meta(client):
    assert "KhetKavach" in client.get("/").text
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["ai"]["enabled"] is False
    meta = client.get("/api/meta").json()
    assert len(meta["crops"]) == 16 and {"en", "hi", "ta"} <= {lang["code"] for lang in meta["languages"]}


def test_dashboard_streams_every_section(client):
    resp = client.post("/api/dashboard", json=FARM_BODY)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    evs = events(resp)
    sections = [e for e in evs if e["type"] == "section"]
    assert len(sections) == 12
    failed = [s["tool"] for s in sections if not s["ok"]]
    assert failed == [], failed
    summary = evs[-1]
    assert summary["type"] == "summary" and summary["top_risks"]


def test_chat_streams_rule_based_answer_without_key(client):
    resp = client.post("/api/chat", json={"message": "Should I irrigate?", "farm": FARM_BODY})
    evs = events(resp)
    assert evs[0]["type"] == "session" and evs[0]["session_id"]
    final = evs[-1]
    assert final["type"] == "final" and final["mode"] == "rules" and final["verification"]["ok"]


def test_validation_errors_are_422(client):
    assert client.post("/api/chat", json={"message": "hi", "farm": {**FARM_BODY, "crop": "dragonfruit"}}).status_code == 422
    assert client.post("/api/dashboard", json={**FARM_BODY, "lat": 200}).status_code == 422
    assert client.post("/api/dashboard", json={**FARM_BODY, "sowing_date": "2001-01-01"}).status_code == 422
    assert client.post("/api/chat", json={"message": "", "farm": FARM_BODY}).status_code == 422


def test_geocode_and_reverse(client):
    assert client.get("/api/geocode", params={"q": "Nashik"}).json()["results"][0]["state"] == "Maharashtra"
    assert client.get("/api/reverse", params=FARM).json()["district"] == "Nashik"


def test_farm_registry_and_sentinel_alerts(client):
    farm = client.post("/api/farms", json={**FARM_BODY, "name": "Test plot"}).json()
    assert client.get("/api/farms").json()["farms"][0]["name"] == "Test plot"

    first = client.post("/api/sentinel/scan", params={"farm_id": farm["id"]}).json()
    kinds = {a["kind"] for a in first["alerts"]}
    assert "weather:heavy rain" in kinds and any(k.startswith("disaster:") for k in kinds)
    assert all(a["message"] for a in first["alerts"])

    again = client.post("/api/sentinel/scan").json()
    assert again["new_alerts"] == 0  # de-duplicated per farm/kind/day
    assert len(client.get("/api/alerts").json()["alerts"]) == first["new_alerts"]

    assert client.delete(f"/api/farms/{farm['id']}").status_code == 200
    assert client.delete(f"/api/farms/{farm['id']}").status_code == 404
