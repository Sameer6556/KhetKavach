"""Rule-based advisory used when the LLM is unavailable (no key, outage, timeout).

The same live-data tools run and their deterministic headlines are assembled into an advisory,
so the farmer still gets a grounded answer and the demo never shows a blank screen.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ..agent.verifier import verify
from ..tools.base import LEVEL_ORDER
from ..tools.registry import ToolRun

Emit = Callable[[dict[str, Any]], Awaitable[None]]

TITLES = {
    "en": {"get_weather_outlook": "Weather (7 days)", "get_crop_risk": "Crop risk", "get_irrigation_plan": "Irrigation",
           "get_spray_windows": "Spraying", "get_flood_risk": "River flood", "get_mandi_prices": "Mandi prices",
           "get_satellite_crop_health": "Satellite crop health", "get_disaster_alerts": "Disaster alerts",
           "get_climate_anomaly": "Rainfall vs normal",
           "intro": "Rule-based advisory from live data (AI assistant unavailable: {reason}).",
           "priority": "Top priority", "unavailable": "data unavailable right now"},
    "hi": {"get_weather_outlook": "मौसम (7 दिन)", "get_crop_risk": "फसल जोखिम", "get_irrigation_plan": "सिंचाई",
           "get_spray_windows": "छिड़काव", "get_flood_risk": "नदी बाढ़", "get_mandi_prices": "मंडी भाव",
           "get_satellite_crop_health": "उपग्रह से फसल स्वास्थ्य", "get_disaster_alerts": "आपदा चेतावनी",
           "get_climate_anomaly": "सामान्य से तुलना",
           "intro": "लाइव डेटा पर आधारित नियम-आधारित सलाह (AI सहायक उपलब्ध नहीं: {reason})।",
           "priority": "सबसे ज़रूरी", "unavailable": "डेटा अभी उपलब्ध नहीं"},
}
KEYWORDS = {
    "get_mandi_prices": ("price", "mandi", "sell", "rate", "market", "bhav", "भाव", "मंडी", "दाम", "बेच"),
    "get_flood_risk": ("flood", "river", "बाढ़", "नदी"),
    "get_satellite_crop_health": ("satellite", "ndvi", "health", "growth", "उपग्रह", "सेहत"),
    "get_disaster_alerts": ("cyclone", "earthquake", "disaster", "तूफान", "चक्रवात", "आपदा"),
    "get_climate_anomaly": ("normal", "drought", "deficit", "सूखा", "सामान्य"),
}
CORE_TOOLS = ["get_weather_outlook", "get_crop_risk", "get_irrigation_plan", "get_spray_windows"]


def tool_inputs(farm: Any, names: list[str]) -> dict[str, dict[str, Any]]:
    base = {"lat": farm.lat, "lon": farm.lon}
    crop = {**base, "crop": farm.crop, "sowing_date": farm.sowing_date.isoformat()}
    table = {
        "get_weather_outlook": {**base, "days": 7},
        "get_crop_risk": crop,
        "get_irrigation_plan": {**crop, "irrigation_method": farm.irrigation_method,
                                "last_irrigation_date": farm.last_irrigation_date.isoformat() if farm.last_irrigation_date else None},
        "get_spray_windows": {**base, "hours": 72},
        "get_flood_risk": base,
        "get_mandi_prices": {"commodity": farm.crop},
        "get_satellite_crop_health": base,
        "get_disaster_alerts": {**base, "radius_km": 500},
        "get_climate_anomaly": base,
    }
    return {n: table[n] for n in names}


def select_tools(question: str) -> list[str]:
    q = question.lower()
    extra = [name for name, words in KEYWORDS.items() if any(w in q for w in words)]
    return CORE_TOOLS + [n for n in extra if n not in CORE_TOOLS]


def compose(runs: list[ToolRun], language: str, reason: str) -> str:
    t = TITLES.get(language, TITLES["en"])
    ok = [r for r in runs if r.ok and r.output]
    lines = [t["intro"].format(reason=reason)]
    if ok:
        top = max(ok, key=lambda r: LEVEL_ORDER.get(r.output.level, 0))  # type: ignore[union-attr]
        if LEVEL_ORDER.get(top.output.level, 0) >= LEVEL_ORDER["moderate"]:  # type: ignore[union-attr]
            lines.append(f"{t['priority']}: {t.get(top.name, top.name)}: {top.output.headline} [{top.evidence.id}]")  # type: ignore[union-attr]
    for r in runs:
        title = t.get(r.name, r.name)
        if r.ok and r.output and r.evidence:
            lines.append(f"• {title}: {r.output.headline} [{r.evidence.id}]")
        else:
            lines.append(f"• {title}: {t['unavailable']}")
    return "\n".join(lines)


async def rule_based_turn(session: Any, question: str, emit: Emit, reason: str) -> dict[str, Any]:
    await emit({"type": "notice", "message": f"AI unavailable ({reason}). Using the rule-based advisor on live data."})
    names = select_tools(question)
    inputs = tool_inputs(session.farm, names)
    for i, name in enumerate(names):
        await emit({"type": "tool_start", "id": f"rule-{i}", "name": name, "input": inputs[name]})
    runs = await asyncio.gather(*(session.executor.run(n, inputs[n]) for n in names))
    for i, run in enumerate(runs):
        await emit(tool_end_event(f"rule-{i}", run))
    text = compose(list(runs), session.farm.language, reason)
    report = verify(text, session.ledger)
    final = {"type": "final", "text": text, "verification": report.to_dict(), "mode": "rules",
             "evidence": [ev.public() for ev in session.ledger.all()], "usage": None}
    await emit(final)
    return final


def tool_end_event(tool_id: str, run: ToolRun) -> dict[str, Any]:
    return {
        "type": "tool_end", "id": tool_id, "name": run.name, "ok": run.ok, "duration_ms": run.duration_ms,
        "evidence_id": run.evidence.id if run.evidence else None,
        "headline": run.output.headline if run.output else None,
        "level": run.output.level if run.output else None,
        "sources": run.evidence.sources if run.evidence else [],
        "error": run.error,
    }
