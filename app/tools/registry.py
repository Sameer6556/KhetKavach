"""Tool registry: JSON schemas for Claude, input validation, execution with timing, and evidence capture."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from ..evidence import Evidence, EvidenceLedger
from ..http import SourceError
from ..sources import nominatim
from . import farm_tools as ft
from .base import (AlertArgs, CropArgs, DiaryArgs, GeocodeArgs, IrrigationArgs, LatLon, MandiArgs, OutlookArgs,
                   SprayArgs, ToolContext, ToolError, ToolOutput)

if TYPE_CHECKING:
    from ..schemas import FarmProfile

log = logging.getLogger(__name__)
TOOL_TIMEOUT_S = 90


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    func: Callable[..., Awaitable[ToolOutput]]
    needs: str | None = None  # extra context passed to func: "place" (farm's district/state) or "farm" (profile)

    def schema(self) -> dict[str, Any]:
        js = _strip_titles(self.input_model.model_json_schema())
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {"type": "object", "properties": js.get("properties", {}),
                             "required": js.get("required", []), "additionalProperties": False},
            # The request is streamed, so tool inputs stream as they are generated; inputs are
            # validated with pydantic before execution (see ToolExecutor.run).
            "eager_input_streaming": True,
        }


def _strip_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


TOOLS: list[ToolSpec] = [
    ToolSpec("get_location_context",
             "Village/district/state, elevation, local time and current weather for a coordinate. Use first when "
             "you need the place name or the farm's state (e.g. for mandi prices).",
             LatLon, ft.get_location_context),
    ToolSpec("get_weather_outlook",
             "Daily forecast (up to 16 days) with rain, temperature, wind, reference evapotranspiration, the 1991-2020 "
             "normal temperature for each day and IMD hazard labels (heavy rain, heat wave, cold wave).",
             OutlookArgs, ft.get_weather_outlook),
    ToolSpec("get_climate_anomaly",
             "Rainfall over the last 30 days and the next 7/16 days compared with the 1991-2020 normal (IMD departure "
             "categories, percentile), plus temperature departures. Use for drought, excess-rain or 'is this normal?' questions.",
             LatLon, ft.get_climate_anomaly),
    ToolSpec("get_soil_profile",
             "Soil texture, pH, organic carbon, nitrogen, CEC and water-holding capacity from ISRIC SoilGrids (250 m).",
             LatLon, ft.get_soil_profile),
    ToolSpec("get_irrigation_plan",
             "FAO-56 crop water balance for the farm: growth stage, crop water use, root-zone depletion today, the date "
             "and depth of the next irrigation, and whether forecast rain lets the farmer skip irrigation.",
             IrrigationArgs, ft.get_irrigation_plan),
    ToolSpec("get_spray_windows",
             "Hour-by-hour suitability for spraying pesticide/fertiliser over the next hours (Delta T, wind, rain "
             "wash-off, heat) and the best spray windows.",
             SprayArgs, ft.get_spray_windows),
    ToolSpec("get_crop_risk",
             "Crop-specific weather risks for the next 7 days: heat/cold/frost stress vs the crop's thresholds and its "
             "growth stage, disease-favourable weather (incl. Hutton late-blight criteria for potato/tomato), heavy rain "
             "and humid heat for field workers.",
             CropArgs, ft.get_crop_risk),
    ToolSpec("get_flood_risk",
             "River flood outlook for the main river within ~5 km: GloFAS ensemble discharge forecast (30 days) compared "
             "with 2/5/20-year return levels derived from 1984-present reanalysis.",
             LatLon, ft.get_flood_risk),
    ToolSpec("get_mandi_prices",
             "Today's Agmarknet wholesale prices (Rs/quintal) for a commodity across the state's mandis, the farm's local "
             "markets, the best market and the price gap. If the commodity is not found, the error lists the "
             "commodities reported today so you can retry with the right name.",
             MandiArgs, ft.get_mandi_prices, needs="place"),
    ToolSpec("get_disaster_alerts",
             "Active GDACS disaster events (cyclones, floods, earthquakes, droughts, wildfires) within a radius of the farm.",
             AlertArgs, ft.get_disaster_alerts),
    ToolSpec("get_air_quality",
             "Current and 72-hour air quality (US AQI, PM2.5, PM10, ozone, NO2) from the CAMS forecast.",
             LatLon, ft.get_air_quality),
    ToolSpec("get_satellite_crop_health",
             "Copernicus Sentinel-2 NDVI (crop vigour) for the farm over the last ~4 months and vs the same period last "
             "year, cloud-masked. Slower (10-60 s); use when the user asks about crop health/growth or field condition.",
             LatLon, ft.get_satellite_crop_health),
    ToolSpec("geocode_place",
             "Find coordinates for a village/town/district name the user mentions (India first, then worldwide).",
             GeocodeArgs, ft.geocode_place),
    ToolSpec("get_field_diary",
             "The farmer's own weekly field diary: crop photos analysed by AI (growth stage seen, canopy cover, leaf colour, "
             "water stress, weeds, possible symptoms with likelihood) plus their observations (pests seen, irrigation, spray "
             "and fertiliser dates, notes), and the change since the previous photo. Use it to personalise advice.",
             DiaryArgs, ft.get_field_diary, needs="farm"),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def tool_schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in TOOLS]


@dataclass
class ToolRun:
    name: str
    ok: bool
    duration_ms: int
    evidence: Evidence | None = None
    output: ToolOutput | None = None
    error: dict[str, Any] | None = None

    def result_payload(self) -> dict[str, Any]:
        """What goes back to the model as the tool_result content."""
        return self.evidence.for_llm() if self.evidence else {"error": self.error}


class ToolExecutor:
    """Runs tools for one conversation; owns the evidence ledger and the farm's default place."""

    def __init__(self, ctx: ToolContext, ledger: EvidenceLedger, farm_lat: float | None = None,
                 farm_lon: float | None = None, farm: FarmProfile | None = None):
        self.ctx = ctx
        self.ledger = ledger
        self.farm = farm
        self._farm = (farm.lat, farm.lon) if farm is not None else (farm_lat, farm_lon)
        self._place: dict[str, Any] | None = None

    async def default_place(self) -> dict[str, Any] | None:
        if self._place is None and None not in self._farm:
            try:
                self._place, _ = await nominatim.reverse(self.ctx.dc, *self._farm)  # type: ignore[arg-type]
            except SourceError:
                return None
        return self._place

    async def run(self, name: str, raw_input: Any) -> ToolRun:
        started = time.perf_counter()

        def done(**kw: Any) -> ToolRun:
            return ToolRun(name=name, duration_ms=int((time.perf_counter() - started) * 1000), **kw)

        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            return done(ok=False, error={"message": f"Unknown tool '{name}'", "available": list(TOOLS_BY_NAME)})
        try:
            args = spec.input_model.model_validate(raw_input if isinstance(raw_input, dict) else {})
        except ValidationError as exc:
            problems = [f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()]
            return done(ok=False, error={"message": "Invalid tool input", "problems": problems,
                                         "hint": "Fix the listed fields and call the tool again."})
        try:
            if spec.needs == "place":
                coro = spec.func(self.ctx, args, await self.default_place())
            elif spec.needs == "farm":
                coro = spec.func(self.ctx, args, self.farm)
            else:
                coro = spec.func(self.ctx, args)
            output: ToolOutput = await asyncio.wait_for(coro, timeout=TOOL_TIMEOUT_S)
        except ToolError as exc:
            return done(ok=False, error={"message": exc.message, "hint": exc.hint, "retryable": exc.retryable})
        except SourceError as exc:
            return done(ok=False, error={"message": f"Live data source unavailable: {exc.message}",
                                         "retryable": exc.retryable})
        except asyncio.TimeoutError:
            return done(ok=False, error={"message": f"{name} timed out after {TOOL_TIMEOUT_S}s", "retryable": True})
        except Exception as exc:  # defensive: a bug in one tool must not crash the agent
            log.exception("Tool %s crashed", name)
            return done(ok=False, error={"message": f"Internal error in {name}: {type(exc).__name__}"})
        evidence = self.ledger.add(name, output.data, output.sources, output.notes)
        return done(ok=True, evidence=evidence, output=output)
