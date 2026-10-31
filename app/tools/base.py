"""Shared types for tools: inputs, outputs, errors and the per-request context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import Settings
from ..db import Database
from ..http import DataClient

RiskLevel = Literal["low", "moderate", "high", "severe", "watch", "info", "unknown"]
LEVEL_ORDER = {"unknown": 0, "info": 0, "low": 1, "watch": 2, "moderate": 2, "high": 3, "severe": 4}


class ToolError(Exception):
    """A tool could not answer. `hint` tells the agent how it might self-correct."""

    def __init__(self, message: str, hint: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.retryable = retryable


@dataclass
class ToolOutput:
    data: dict[str, Any]  # compact facts: what the LLM sees and what the verifier checks against
    sources: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    level: RiskLevel = "info"
    headline: str = ""
    chart: dict[str, Any] | None = None  # bulky series for the dashboard only (never sent to the LLM)


@dataclass
class ToolContext:
    dc: DataClient
    db: Database
    settings: Settings


# ----------------------------------------------------------------------------------- input models
class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90, description="Latitude in decimal degrees (e.g. 19.997)")
    lon: float = Field(ge=-180, le=180, description="Longitude in decimal degrees (e.g. 73.789)")


class OutlookArgs(LatLon):
    days: int = Field(7, ge=1, le=16, description="Number of forecast days (1-16)")


class CropArgs(LatLon):
    crop: str = Field(description="Crop key: rice, wheat, maize, cotton, sugarcane, soybean, groundnut, chickpea, "
                                  "mustard, pearl_millet, sorghum, potato, onion, tomato, chilli, banana")
    sowing_date: date = Field(description="Sowing or transplanting date, YYYY-MM-DD")


class IrrigationArgs(CropArgs):
    irrigation_method: Literal["drip", "sprinkler", "furrow", "flood", "rainfed"] = Field(
        "flood", description="How the field is irrigated")
    last_irrigation_date: date | None = Field(None, description="Date of the last irrigation if known, YYYY-MM-DD")
    area_acres: float | None = Field(None, gt=0, le=10000, description="Field size in acres, if known")


class DiaryArgs(BaseModel):
    weeks: int = Field(4, ge=1, le=12, description="How many recent weekly check-ins to return")


class SprayArgs(LatLon):
    hours: int = Field(72, ge=6, le=120, description="How many hours ahead to scan (6-120)")


class MandiArgs(BaseModel):
    commodity: str = Field(min_length=2, max_length=60,
                           description="Commodity or crop name, e.g. 'onion', 'wheat', 'Paddy(Dhan)(Common)'")
    state: str | None = Field(None, description="Indian state name, e.g. 'Maharashtra'. Defaults to the farm's state.")
    district: str | None = Field(None, description="District name to highlight local markets. Defaults to the farm's district.")


class AlertArgs(LatLon):
    radius_km: float = Field(500, ge=50, le=2000, description="Search radius in km")


class GeocodeArgs(BaseModel):
    place: str = Field(min_length=2, max_length=80, description="Village, town or district name")


def worst_level(*levels: str) -> RiskLevel:
    return max(levels, key=lambda lv: LEVEL_ORDER.get(lv, 0)) if levels else "unknown"  # type: ignore[return-value]
