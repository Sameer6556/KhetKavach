"""Resilient HTTP access to the live public data sources.

Every connector goes through `DataClient`, which adds:

* bounded retries with exponential backoff + jitter (timeouts, connection errors, 429, 5xx),
* `Retry-After` handling for rate limits,
* a per-source circuit breaker so a dead upstream fails fast instead of slowing every request,
* a SQLite TTL cache, and
* stale-if-error: when an upstream is down, the last good response is served and clearly
  flagged `stale=True` (real data that is older than usual, never invented data).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from .db import Database

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
SECRET_PARAMS = {"api-key", "api_key", "apikey", "key", "token"}
MAX_STALE_S = 14 * 24 * 3600


class SourceError(Exception):
    """A live data source could not provide data (after retries and without a usable cache)."""

    def __init__(self, source: str, message: str, *, retryable: bool = True, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


@dataclass
class Fetched:
    data: Any
    url: str
    fetched_at: datetime
    from_cache: bool = False
    stale: bool = False

    def provenance(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "url": self.url,
            "retrieved_at": self.fetched_at.replace(microsecond=0).isoformat(),
            "cached": self.from_cache,
            "stale": self.stale,
        }


@dataclass
class _SourceState:
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_ok: float | None = None
    last_error: str | None = None
    last_latency_ms: int | None = None
    calls: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_call: float = 0.0


@dataclass(frozen=True)
class SourcePolicy:
    timeout_s: float = 20.0
    retries: int = 2
    min_interval_s: float = 0.0  # politeness spacing between calls (e.g. Nominatim: 1 req/s)
    failure_threshold: int = 4
    cooldown_s: float = 60.0


def _redact(params: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [(k, "***" if k.lower() in SECRET_PARAMS else v) for k, v in params]


def _as_pairs(params: dict[str, Any] | list[tuple[str, Any]] | None) -> list[tuple[str, Any]]:
    if params is None:
        return []
    items = params.items() if isinstance(params, dict) else params
    return [(k, v) for k, v in items if v is not None]


class DataClient:
    def __init__(self, db: Database, user_agent: str, default_timeout_s: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._db = db
        self._default_timeout = default_timeout_s
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "application/json, application/xml;q=0.9, */*;q=0.5"},
            follow_redirects=True,
            timeout=default_timeout_s,
            transport=transport,
        )
        self._states: dict[str, _SourceState] = {}
        self._policies: dict[str, SourcePolicy] = {}
        # Request coalescing: concurrent identical requests share one upstream call.
        self._inflight: dict[str, asyncio.Future] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    def register(self, source: str, policy: SourcePolicy) -> None:
        self._policies[source] = policy

    def _state(self, source: str) -> _SourceState:
        return self._states.setdefault(source, _SourceState())

    def _policy(self, source: str) -> SourcePolicy:
        return self._policies.get(source, SourcePolicy(timeout_s=self._default_timeout))

    def health(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        report = {}
        for source, st in sorted(self._states.items()):
            report[source] = {
                "status": "circuit_open" if st.open_until > now else ("ok" if st.consecutive_failures == 0 else "degraded"),
                "last_ok": datetime.fromtimestamp(st.last_ok, timezone.utc).isoformat() if st.last_ok else None,
                "last_error": st.last_error,
                "last_latency_ms": st.last_latency_ms,
                "calls": st.calls,
            }
        return report

    async def get(self, source: str, url: str, params: dict[str, Any] | list[tuple[str, Any]] | None = None,
                  *, ttl_s: float, as_text: bool = False) -> Fetched:
        return await self._request("GET", source, url, _as_pairs(params), None, ttl_s, as_text)

    async def post_json(self, source: str, url: str, body: dict[str, Any], *, ttl_s: float) -> Fetched:
        return await self._request("POST", source, url, [], body, ttl_s, False)

    async def _request(self, method: str, source: str, url: str, params: list[tuple[str, Any]],
                       body: dict[str, Any] | None, ttl_s: float, as_text: bool) -> Fetched:
        key_material = json.dumps([method, url, _redact(params), body], sort_keys=True, default=str)
        cache_key = hashlib.sha256(key_material.encode()).hexdigest()
        task = self._inflight.get(cache_key)
        if task is None:
            task = asyncio.ensure_future(self._fetch(method, source, url, params, body, ttl_s, as_text, cache_key))
            self._inflight[cache_key] = task
            task.add_done_callback(lambda t, k=cache_key: self._finish_inflight(k, t))
        # shield(): a caller that gives up (e.g. a timed-out tool) must not cancel the shared request.
        return await asyncio.shield(task)

    def _finish_inflight(self, key: str, task: asyncio.Future) -> None:
        self._inflight.pop(key, None)
        if not task.cancelled():
            task.exception()  # mark as retrieved; callers that awaited it already received it

    async def _fetch(self, method: str, source: str, url: str, params: list[tuple[str, Any]],
                     body: dict[str, Any] | None, ttl_s: float, as_text: bool, cache_key: str) -> Fetched:
        public_url = url + ("?" + urlencode(_redact(params)) if params else "")
        cached = self._db.cache_get(cache_key) if ttl_s > 0 else None
        if cached and cached["expires_at"] > time.time():
            return Fetched(_decode(cached["payload"], as_text), cached["url"],
                           datetime.fromtimestamp(cached["fetched_at"], timezone.utc), from_cache=True)

        state, policy = self._state(source), self._policy(source)
        if state.open_until > time.time():
            return self._stale_or_raise(source, cached, as_text, "temporarily unavailable (circuit open after repeated failures)")

        last_exc: SourceError | None = None
        for attempt in range(policy.retries + 1):
            try:
                payload_text = await self._send(method, source, url, params, body, policy)
                try:
                    data = _decode(payload_text, as_text)
                except ValueError as exc:
                    raise SourceError(source, f"unexpected response format ({exc})", retryable=False) from None
            except SourceError as exc:
                last_exc = exc
                if not exc.retryable or attempt == policy.retries:
                    break
                await asyncio.sleep(_backoff(attempt, exc))
                continue
            self._record_success(state)
            if ttl_s > 0:
                self._db.cache_put(cache_key, payload_text, public_url, ttl_s)
            return Fetched(data, public_url, datetime.now(timezone.utc))

        assert last_exc is not None
        self._record_failure(state, policy, last_exc)
        return self._stale_or_raise(source, cached, as_text, last_exc.message, last_exc)

    async def _send(self, method: str, source: str, url: str, params: list[tuple[str, Any]],
                    body: dict[str, Any] | None, policy: SourcePolicy) -> str:
        state = self._state(source)
        if policy.min_interval_s:
            async with state.lock:
                wait = state.last_call + policy.min_interval_s - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                state.last_call = time.monotonic()
        state.calls += 1
        started = time.perf_counter()
        try:
            resp = await self._client.request(method, url, params=params or None, json=body, timeout=policy.timeout_s)
        except httpx.TimeoutException:
            raise SourceError(source, f"timed out after {policy.timeout_s:.0f}s") from None
        except httpx.TransportError as exc:
            raise SourceError(source, f"network error: {type(exc).__name__}") from None
        finally:
            state.last_latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code >= 400:
            raise SourceError(source, f"HTTP {resp.status_code}: {resp.text[:200].strip()}",
                              retryable=resp.status_code in RETRYABLE_STATUS, status=resp.status_code,
                              retry_after=_retry_after(resp))
        return resp.text

    def _record_success(self, state: _SourceState) -> None:
        state.consecutive_failures = 0
        state.open_until = 0.0
        state.last_ok = time.time()
        state.last_error = None

    def _record_failure(self, state: _SourceState, policy: SourcePolicy, exc: SourceError) -> None:
        state.consecutive_failures += 1
        state.last_error = exc.message
        if state.consecutive_failures >= policy.failure_threshold:
            state.open_until = time.time() + policy.cooldown_s
            log.warning("Circuit opened for %s for %.0fs: %s", exc.source, policy.cooldown_s, exc.message)

    def _stale_or_raise(self, source: str, cached: Any, as_text: bool, reason: str,
                        exc: SourceError | None = None) -> Fetched:
        if cached and cached["expires_at"] > time.time() - MAX_STALE_S:
            log.info("Serving stale cache for %s (%s)", source, reason)
            return Fetched(_decode(cached["payload"], as_text), cached["url"],
                           datetime.fromtimestamp(cached["fetched_at"], timezone.utc), from_cache=True, stale=True)
        if exc is not None:
            raise exc
        raise SourceError(source, reason)


def _decode(payload: str, as_text: bool) -> Any:
    """Parse a response body; raises ValueError (JSONDecodeError) on malformed JSON."""
    return payload if as_text else json.loads(payload)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _backoff(attempt: int, exc: SourceError) -> float:
    if exc.retry_after is not None:
        return min(exc.retry_after, 5.0)
    return min(0.6 * (2 ** attempt), 4.0) + random.uniform(0, 0.3)
