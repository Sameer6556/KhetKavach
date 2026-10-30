#!/usr/bin/env python3
"""Live smoke test: calls every real data source and every tool for a few Indian farms.

Run it before a demo (and after deploying) to prove the live integrations work:

    python scripts/smoke_test.py            # data sources + tools
    python scripts/smoke_test.py --llm      # also send one tiny request to Claude

Exits with status 1 if a core source fails. Uses a throwaway cache so every call hits the network.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.evidence import EvidenceLedger  # noqa: E402
from app.http import DataClient  # noqa: E402
from app.sources import register_policies  # noqa: E402
from app.tools.base import ToolContext  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402

FARMS = [
    ("Niphad, Nashik (onion)", 20.08, 74.11, "onion", "Maharashtra"),
    ("Ludhiana (wheat)", 30.90, 75.85, "wheat", "Punjab"),
    ("Patna, near the Ganga (rice)", 25.60, 85.10, "rice", "Bihar"),
]
CORE = {"get_location_context", "get_weather_outlook", "get_climate_anomaly", "get_irrigation_plan",
        "get_crop_risk", "get_spray_windows", "get_flood_risk", "get_soil_profile"}


async def check_farm(executor: ToolExecutor, name: str, lat: float, lon: float, crop: str, state: str) -> list[str]:
    sown = (date.today() - timedelta(days=45)).isoformat()
    base = {"lat": lat, "lon": lon}
    calls = {
        "get_location_context": base,
        "get_weather_outlook": {**base, "days": 7},
        "get_climate_anomaly": base,
        "get_soil_profile": base,
        "get_irrigation_plan": {**base, "crop": crop, "sowing_date": sown, "irrigation_method": "flood"},
        "get_crop_risk": {**base, "crop": crop, "sowing_date": sown},
        "get_spray_windows": {**base, "hours": 72},
        "get_flood_risk": base,
        "get_air_quality": base,
        "get_disaster_alerts": {**base, "radius_km": 800},
        "get_mandi_prices": {"commodity": crop, "state": state},
        "get_satellite_crop_health": base,
    }
    print(f"\n=== {name}  ({lat}, {lon})")
    failures = []
    started = time.perf_counter()
    runs = await asyncio.gather(*(executor.run(tool, args) for tool, args in calls.items()))
    for run in runs:
        if run.ok:
            stale = any(s.get("stale") for s in run.evidence.sources) if run.evidence else False
            print(f"  ✔ {run.name:<28} {run.duration_ms:>6} ms  {run.output.headline[:90]}{'  [STALE]' if stale else ''}")
        else:
            print(f"  ✘ {run.name:<28} {run.duration_ms:>6} ms  {run.error.get('message')}  {run.error.get('hint') or ''}")
            if run.name in CORE:
                failures.append(f"{name}: {run.name}")
    print(f"  total {time.perf_counter() - started:.1f} s")
    return failures


async def check_llm(ctx: ToolContext) -> bool:
    """Run one real agent turn (planning, live tool calls, verification) with the configured provider."""
    from app.agent.factory import build_agent
    from app.agent.sessions import SessionStore
    from app.schemas import FarmProfile

    settings = get_settings()
    if not settings.llm_enabled:
        print("\n✘ No GEMINI_API_KEY or ANTHROPIC_API_KEY: the app will run in rule-based mode.")
        return False
    agent = build_agent(settings)
    farm = FarmProfile(lat=20.08, lon=74.11, crop="onion", sowing_date=date.today() - timedelta(days=45),
                       irrigation_method="drip", language="hi")
    session = SessionStore(ctx).get_or_create(None, farm)
    print(f"\n=== Agent turn with {agent.provider} ({agent.model}), answer in Hindi")

    async def emit(ev: dict) -> None:
        if ev["type"] == "tool_start":
            print(f"  → {ev['name']}")
        elif ev["type"] == "tool_end":
            print(f"  {'✔' if ev['ok'] else '✘'} {ev['name']} {ev['evidence_id'] or (ev['error'] or {}).get('message', '')}")
        elif ev["type"] in ("notice", "verification"):
            print(f"  ! {ev.get('message') or ev.get('report')}")

    try:
        final = await agent.run_turn(session, "Should I irrigate my onion field this week?", emit)
    finally:
        await agent.aclose()
    v = final["verification"]
    print(f"\n{final['text']}\n\n  mode={final['mode']} model={final.get('model')} "
          f"verified={v['supported']}/{v['data_figures']} usage={final.get('usage')}")
    if final["mode"] != "ai":
        print(f"\n✘ {agent.provider} did not answer (see the notice above); the app fell back to rules.")
        return False
    print(f"\n✔ {agent.provider} agent works end to end.")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="also run one real agent turn with the configured AI key")
    args = parser.parse_args()
    settings = get_settings()
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "smoke.db")
        dc = DataClient(db, settings.user_agent, settings.http_timeout_s)
        register_policies(dc)
        ctx = ToolContext(dc=dc, db=db, settings=settings)
        failures: list[str] = []
        for name, lat, lon, crop, state in FARMS:
            failures += await check_farm(ToolExecutor(ctx, EvidenceLedger(), lat, lon), name, lat, lon, crop, state)
        print("\nSource health:")
        for source, health in dc.health().items():
            print(f"  {source:<22} {health['status']:<12} calls={health['calls']:<3} last_error={health['last_error'] or '-'}")
        llm_ok = await check_llm(ctx) if args.llm else True
        await dc.aclose()
        db.close()
    if settings.using_sample_mandi_key:
        print("\nNote: DATA_GOV_IN_API_KEY not set; mandi prices use the public sample key (10 records max).")
    if failures or not llm_ok:
        print(f"\nFAILED: {', '.join(failures) or 'LLM check'}")
        return 1
    print("\nAll core live integrations OK.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
