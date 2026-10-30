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


async def check_llm() -> bool:
    import anthropic

    settings = get_settings()
    if not settings.llm_enabled:
        print("\n✘ ANTHROPIC_API_KEY is not set: the app will run in rule-based mode.")
        return False
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        msg = await client.messages.create(model=settings.model, max_tokens=1024, output_config={"effort": "low"},
                                           messages=[{"role": "user", "content": "Reply with the single word OK."}])
    except anthropic.APIError as exc:
        print(f"\n✘ Claude ({settings.model}): {type(exc).__name__}: {exc}")
        return False
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    print(f"\n✔ Claude ({msg.model}) replied: {text!r}; usage {msg.usage.input_tokens} in / {msg.usage.output_tokens} out")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="also verify the Anthropic API key and model")
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
        await dc.aclose()
        db.close()
    llm_ok = await check_llm() if args.llm else True
    if settings.using_sample_mandi_key:
        print("\nNote: DATA_GOV_IN_API_KEY not set; mandi prices use the public sample key (10 records max).")
    if failures or not llm_ok:
        print(f"\nFAILED: {', '.join(failures) or 'LLM check'}")
        return 1
    print("\nAll core live integrations OK.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
