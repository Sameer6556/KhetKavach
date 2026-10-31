"""Risk dashboard: runs every analysis concurrently and streams each section as soon as it is ready."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ..agent.fallback import tool_inputs
from ..schemas import FarmProfile
from ..tools.base import LEVEL_ORDER
from ..tools.registry import ToolExecutor, ToolRun

Emit = Callable[[dict[str, Any]], Awaitable[None]]

SECTIONS = ["get_location_context", "get_weather_outlook", "get_crop_risk", "get_irrigation_plan",
            "get_field_diary", "get_spray_windows", "get_climate_anomaly", "get_flood_risk", "get_soil_profile",
            "get_mandi_prices", "get_disaster_alerts", "get_air_quality", "get_satellite_crop_health"]


def section_event(run: ToolRun) -> dict[str, Any]:
    out = run.output
    return {
        "type": "section", "tool": run.name, "ok": run.ok, "duration_ms": run.duration_ms,
        "evidence_id": run.evidence.id if run.evidence else None,
        "level": out.level if out else "unknown", "headline": out.headline if out else None,
        "data": out.data if out else None, "chart": out.chart if out else None,
        "notes": out.notes if out else [], "sources": out.sources if out else [],
        "error": run.error,
    }


async def stream_dashboard(executor: ToolExecutor, farm: FarmProfile, emit: Emit) -> None:
    inputs = {"get_location_context": {"lat": farm.lat, "lon": farm.lon},
              "get_soil_profile": {"lat": farm.lat, "lon": farm.lon},
              "get_air_quality": {"lat": farm.lat, "lon": farm.lon}}
    inputs.update(tool_inputs(farm, [s for s in SECTIONS if s not in inputs]))
    await emit({"type": "start", "sections": SECTIONS})
    pending = [asyncio.create_task(executor.run(name, inputs[name])) for name in SECTIONS]
    levels: dict[str, str] = {}
    try:
        for next_done in asyncio.as_completed(pending):
            run = await next_done
            event = section_event(run)
            levels[run.name] = event["level"]
            await emit(event)
    finally:
        for task in pending:
            task.cancel()
    ranked = sorted(((n, lv) for n, lv in levels.items() if LEVEL_ORDER.get(lv, 0) >= LEVEL_ORDER["moderate"]),
                    key=lambda kv: -LEVEL_ORDER[kv[1]])
    await emit({"type": "summary", "levels": levels, "top_risks": [{"tool": n, "level": lv} for n, lv in ranked]})
