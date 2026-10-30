"""Kavach Sentinel: periodically re-analyses every registered farm and raises alerts on its own.

For each farm it runs the same live-data tools as the agent, applies alert rules (IMD hazards,
crop stress, irrigation timing, river flood, nearby disasters), writes each new alert in the
farmer's language (the active LLM, verified against the alert facts; template fallback), stores it,
and pushes it to Telegram when the farm has a chat ID. Alerts are de-duplicated per farm/kind/day.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from ..agent.base import BaseAgent
from ..agent.fallback import tool_inputs
from ..agent.prompts import ALERT_SYSTEM, language_name
from ..agent.verifier import verify
from ..evidence import EvidenceLedger
from ..schemas import FarmProfile
from ..tools.base import LEVEL_ORDER, ToolContext
from ..tools.farm_tools import nice_date
from ..tools.registry import ToolExecutor, ToolRun
from .notify import send_telegram

log = logging.getLogger(__name__)
SCAN_TOOLS = ["get_weather_outlook", "get_crop_risk", "get_irrigation_plan", "get_flood_risk", "get_disaster_alerts"]
TEMPLATES = {
    "en": "KhetKavach alert ({severity}): {title}.",
    "hi": "खेत कवच चेतावनी ({severity}): {title}।",
}


def _days_until(day: str) -> int:
    return (date.fromisoformat(day[:10]) - date.today()).days


def _ok(run: ToolRun | None) -> bool:
    return bool(run and run.ok and run.output)


def derive_alerts(runs: dict[str, ToolRun]) -> list[dict[str, Any]]:
    """Alert rules over tool outputs. Pure function (unit-tested)."""
    alerts: list[dict[str, Any]] = []
    today = date.today().isoformat()

    w = runs.get("get_weather_outlook")
    if _ok(w):
        daily = {r["date"]: r for r in w.output.data.get("daily", [])}  # type: ignore[union-attr]
        for hz in w.output.data.get("hazards", []):  # type: ignore[union-attr]
            if 0 <= _days_until(hz["date"]) <= 3:
                row = daily.get(hz["date"], {})
                alerts.append({
                    "kind": f"weather:{hz['hazard']}", "valid_for": hz["date"],
                    "severity": "severe" if hz["hazard"].startswith(("severe", "extremely")) else "high",
                    "title": f"{hz['hazard'].capitalize()} expected on {nice_date(hz['date'])}",
                    "facts": {"hazard": hz["hazard"], "date": hz["date"], "rain_mm": row.get("rain_mm"),
                              "tmax_c": row.get("tmax_c"), "tmin_c": row.get("tmin_c")},
                })

    c = runs.get("get_crop_risk")
    if _ok(c) and LEVEL_ORDER.get(c.output.level, 0) >= LEVEL_ORDER["high"]:  # type: ignore[union-attr]
        d = c.output.data  # type: ignore[union-attr]
        alerts.append({
            "kind": "crop_risk", "valid_for": today, "severity": c.output.level,  # type: ignore[union-attr]
            "title": f"{d['crop']}: {c.output.headline}",  # type: ignore[union-attr]
            "facts": {k: d.get(k) for k in ("crop", "stage_now", "heat_stress_days", "cold_stress_days",
                                             "heavy_rain_days", "late_blight_hutton_periods")
                      if d.get(k)} | {"diseases": [x for x in d.get("disease_weather", []) if x["risk"] != "low"]},
        })

    i = runs.get("get_irrigation_plan")
    if _ok(i):
        d = i.output.data  # type: ignore[union-attr]
        deferrals, nxt = d.get("rain_deferrals") or [], d.get("next_irrigation")
        if deferrals and _days_until(deferrals[0]["date"]) <= 2:
            alerts.append({"kind": "irrigation_hold", "valid_for": deferrals[0]["date"], "severity": "info",
                           "title": f"Hold irrigation: {deferrals[0]['rain_next_2_days_mm']} mm rain expected",
                           "facts": {"deficit_mm": deferrals[0]["deficit_mm"],
                                     "rain_next_2_days_mm": deferrals[0]["rain_next_2_days_mm"],
                                     "litres_saved_per_acre": d.get("water_saved_by_waiting_for_rain_litres_per_acre")}})
        elif nxt and _days_until(nxt["date"]) <= 2:
            alerts.append({"kind": "irrigation_due", "valid_for": nxt["date"], "severity": "moderate",
                           "title": f"Irrigate on {nice_date(nxt['date'])}: {nxt['net_mm']} mm needed",
                           "facts": {"date": nxt["date"], "net_mm": nxt["net_mm"], "gross_mm": nxt.get("gross_mm"),
                                     "method": d.get("irrigation_method")}})

    f = runs.get("get_flood_risk")
    if _ok(f) and f.output.level in ("moderate", "high", "severe"):  # type: ignore[union-attr]
        d = f.output.data  # type: ignore[union-attr]
        alerts.append({"kind": "flood", "valid_for": d["forecast_peak"]["date"], "severity": f.output.level,  # type: ignore[union-attr]
                       "title": f"River flood risk: {f.output.headline}",  # type: ignore[union-attr]
                       "facts": {"peak": d["forecast_peak"], "return_levels_m3s": d["return_levels_m3s"],
                                 "reason": d["reason"], "river_distance_km": d["river_cell"]["distance_km"]}})

    g = runs.get("get_disaster_alerts")
    if _ok(g) and g.output.level in ("high", "severe"):  # type: ignore[union-attr]
        ev = g.output.data["nearby_events"][0]  # type: ignore[union-attr]
        alerts.append({"kind": f"disaster:{ev['name']}", "valid_for": today, "severity": g.output.level,  # type: ignore[union-attr]
                       "title": f"{ev['type_name']} ({ev['alert_level']}) {ev['distance_km']} km away",
                       "facts": ev})
    return alerts


class Sentinel:
    def __init__(self, ctx: ToolContext, agent: BaseAgent):
        self.ctx = ctx
        self.agent = agent
        self.last_scan: dict[str, Any] | None = None

    async def scan_farm(self, farm_row: dict[str, Any]) -> list[dict[str, Any]]:
        farm = FarmProfile(lat=farm_row["lat"], lon=farm_row["lon"], crop=farm_row["crop"],
                           sowing_date=farm_row["sowing_date"], irrigation_method=farm_row["irrigation"],
                           language=farm_row["language"])
        executor = ToolExecutor(self.ctx, EvidenceLedger(), farm.lat, farm.lon)
        inputs = tool_inputs(farm, SCAN_TOOLS)
        inputs["get_disaster_alerts"]["radius_km"] = 300
        runs = await asyncio.gather(*(executor.run(n, inputs[n]) for n in SCAN_TOOLS))
        created = []
        for alert in derive_alerts({r.name: r for r in runs}):
            alert["message"] = await self.compose(alert, farm.language)
            row = self.ctx.db.insert_alert({**alert, "farm_id": farm_row["id"]})
            if row is None:
                continue  # already raised today
            created.append(row)
            if self.ctx.settings.telegram_bot_token and farm_row.get("telegram_chat_id"):
                text = f"🌾 {farm_row['name']}: {row['message']}"
                if await send_telegram(self.ctx.settings.telegram_bot_token, farm_row["telegram_chat_id"], text):
                    self.ctx.db.mark_delivered(row["id"], "telegram")
        return created

    async def scan_all(self) -> dict[str, Any]:
        farms = self.ctx.db.list_farms()
        new_alerts: list[dict[str, Any]] = []
        failures = 0
        for farm in farms:
            try:
                new_alerts += await self.scan_farm(farm)
            except Exception:  # one bad farm must not stop the sweep
                failures += 1
                log.exception("Sentinel scan failed for farm %s", farm.get("id"))
        self.last_scan = {"at": date.today().isoformat(), "farms": len(farms), "new_alerts": len(new_alerts),
                          "failures": failures}
        return {**self.last_scan, "alerts": new_alerts}

    async def run_forever(self, interval_minutes: int) -> None:
        await asyncio.sleep(30)  # let the app finish starting
        while True:
            try:
                summary = await self.scan_all()
                log.info("Sentinel sweep: %s", {k: v for k, v in summary.items() if k != "alerts"})
            except Exception:
                log.exception("Sentinel sweep crashed")
            await asyncio.sleep(max(interval_minutes, 15) * 60)

    async def compose(self, alert: dict[str, Any], language: str) -> str:
        template = TEMPLATES.get(language, TEMPLATES["en"]).format(severity=alert["severity"], title=alert["title"])
        facts = json.dumps(alert["facts"], ensure_ascii=False, default=str)
        prompt = f"Language: {language_name(language)}\nAlert: {alert['title']}\n<facts>{facts}</facts>"
        try:
            text = await self.agent.write_short(ALERT_SYSTEM, prompt)
        except Exception:  # wording is a nicety: never let it block an alert
            log.exception("Alert wording failed; using template")
            return template
        if not text:
            return template
        ledger = EvidenceLedger()
        ledger.add("alert_facts", alert["facts"] | {"title": alert["title"]}, [])
        report = verify(text, ledger)
        return text if not report.unsupported else template
