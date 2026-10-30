import asyncio
import time

import httpx
import pytest

from app.db import Database
from app.http import DataClient, SourceError, SourcePolicy


def client(tmp_path, handler, **policy):
    db = Database(tmp_path / "http.db")
    dc = DataClient(db, "tests", 5.0, transport=httpx.MockTransport(handler))
    dc.register("src", SourcePolicy(timeout_s=2, retries=policy.get("retries", 2),
                                    failure_threshold=policy.get("threshold", 4), cooldown_s=60))
    return dc


async def test_retries_transient_errors_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("app.http._backoff", lambda attempt, exc: 0)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    dc = client(tmp_path, handler)
    res = await dc.get("src", "https://example.org/data", {"a": 1}, ttl_s=60)
    assert res.data == {"ok": True} and calls["n"] == 3
    await dc.aclose()


async def test_client_errors_are_not_retried(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, text="bad parameter")

    dc = client(tmp_path, handler)
    with pytest.raises(SourceError) as err:
        await dc.get("src", "https://example.org/data", ttl_s=60)
    assert err.value.status == 400 and calls["n"] == 1
    await dc.aclose()


async def test_cache_hit_and_stale_if_error(tmp_path, monkeypatch):
    monkeypatch.setattr("app.http._backoff", lambda attempt, exc: 0)
    state = {"down": False}

    def handler(request):
        return httpx.Response(503) if state["down"] else httpx.Response(200, json={"v": 42})

    dc = client(tmp_path, handler)
    first = await dc.get("src", "https://example.org/x", ttl_s=60)
    assert not first.from_cache
    assert (await dc.get("src", "https://example.org/x", ttl_s=60)).from_cache

    # Expire the entry, take the upstream down: the last good value is served, flagged stale.
    dc._db._conn.execute("UPDATE http_cache SET expires_at = ?", (time.time() - 10,))
    state["down"] = True
    stale = await dc.get("src", "https://example.org/x", ttl_s=60)
    assert stale.stale and stale.data == {"v": 42}
    await dc.aclose()


async def test_circuit_breaker_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr("app.http._backoff", lambda attempt, exc: 0)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    dc = client(tmp_path, handler, retries=0, threshold=2)
    for _ in range(2):
        with pytest.raises(SourceError):
            await dc.get("src", f"https://example.org/{calls['n']}", ttl_s=0)
    before = calls["n"]
    with pytest.raises(SourceError, match="circuit open"):
        await dc.get("src", "https://example.org/again", ttl_s=0)
    assert calls["n"] == before
    assert dc.health()["src"]["status"] == "circuit_open"
    await dc.aclose()


async def test_concurrent_identical_requests_are_coalesced(tmp_path):
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"n": calls["n"]})

    dc = client(tmp_path, handler)
    results = await asyncio.gather(*(dc.get("src", "https://example.org/same", ttl_s=0) for _ in range(5)))
    assert calls["n"] == 1 and all(r.data == {"n": 1} for r in results)
    await dc.aclose()


async def test_api_keys_are_redacted_from_provenance(tmp_path):
    dc = client(tmp_path, lambda request: httpx.Response(200, json={}))
    res = await dc.get("src", "https://example.org/r", {"api-key": "SECRET", "q": "x"}, ttl_s=60)
    assert "SECRET" not in res.url and "api-key=%2A%2A%2A" in res.url
    await dc.aclose()
