"""Open-Meteo APIs (no key required): forecast, ERA5 archive, GloFAS flood, air quality, geocoding.

Docs: https://open-meteo.com/en/docs (forecast), /en/docs/historical-weather-api,
/en/docs/flood-api, /en/docs/air-quality-api, /en/docs/geocoding-api.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..http import DataClient, Fetched, SourceError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Only variables the analyses use: one unknown name would make Open-Meteo reject the whole request.
HOURLY_VARS = [
    "temperature_2m", "relative_humidity_2m", "precipitation", "precipitation_probability",
    "wind_speed_10m", "wind_gusts_10m", "weather_code", "is_day",
]
DAILY_VARS = [
    "weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
    "precipitation_probability_max", "et0_fao_evapotranspiration", "wind_speed_10m_max", "wind_gusts_10m_max",
]
CURRENT_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m", "weather_code", "is_day"]

# WMO 1991-2020 climate normal period, computed from ERA5 reanalysis.
NORMAL_START, NORMAL_END = "1991-01-01", "2020-12-31"
FLOOD_HISTORY_START = "1984-01-01"


@dataclass
class Series:
    """Columnar time series as returned by Open-Meteo: one time axis and one list per variable."""

    times: list[str]
    values: dict[str, list[float | None]]
    units: dict[str, str] = field(default_factory=dict)

    def col(self, name: str) -> list[float | None]:
        return self.values.get(name) or [None] * len(self.times)

    def __len__(self) -> int:
        return len(self.times)


@dataclass
class Forecast:
    lat: float
    lon: float
    elevation_m: float | None
    timezone: str
    utc_offset_s: int
    daily: Series
    hourly: Series
    current: dict[str, Any]
    fetched: Fetched

    @property
    def local_now(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=self.utc_offset_s)

    @property
    def today_index(self) -> int:
        """Index of the local 'today' in the daily axis (past days come first)."""
        today = self.local_now.date().isoformat()
        if today in self.daily.times:
            return self.daily.times.index(today)
        # Fall back to the first day that is not in the past.
        for i, t in enumerate(self.daily.times):
            if t >= today:
                return i
        return len(self.daily.times) - 1

    @property
    def now_hour_index(self) -> int:
        stamp = self.local_now.strftime("%Y-%m-%dT%H:00")
        for i, t in enumerate(self.hourly.times):
            if t >= stamp:
                return i
        return max(len(self.hourly.times) - 1, 0)


def _series(payload: dict[str, Any], key: str) -> Series:
    block = payload.get(key) or {}
    times = block.get("time") or []
    values = {k: v for k, v in block.items() if k != "time" and isinstance(v, list)}
    return Series(times=list(times), values=values, units=dict(payload.get(f"{key}_units") or {}))


def _require(payload: Any, source: str, *keys: str) -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get("error"):
        raise SourceError(source, str(payload.get("reason") or "upstream reported an error"), retryable=False)
    if not isinstance(payload, dict) or any(k not in payload for k in keys):
        raise SourceError(source, f"unexpected response shape (missing {', '.join(keys)})", retryable=False)
    return payload


async def fetch_forecast(dc: DataClient, lat: float, lon: float, past_days: int = 30,
                         forecast_days: int = 16) -> Forecast:
    params = {
        "latitude": round(lat, 3), "longitude": round(lon, 3),
        "hourly": ",".join(HOURLY_VARS), "daily": ",".join(DAILY_VARS), "current": ",".join(CURRENT_VARS),
        "timezone": "auto", "past_days": past_days, "forecast_days": forecast_days,
    }
    fetched = await dc.get("open-meteo-forecast", FORECAST_URL, params, ttl_s=30 * 60)
    payload = _require(fetched.data, "open-meteo-forecast", "daily", "hourly")
    return Forecast(
        lat=payload.get("latitude", lat), lon=payload.get("longitude", lon),
        elevation_m=payload.get("elevation"), timezone=payload.get("timezone", "UTC"),
        utc_offset_s=int(payload.get("utc_offset_seconds") or 0),
        daily=_series(payload, "daily"), hourly=_series(payload, "hourly"),
        current=dict(payload.get("current") or {}), fetched=fetched,
    )


async def fetch_climatology(dc: DataClient, lat: float, lon: float) -> tuple[Series, Fetched]:
    """Daily ERA5 reanalysis for the 1991-2020 normal period (cached for 30 days; the past does not change)."""
    params = {
        "latitude": round(lat, 1), "longitude": round(lon, 1),
        "start_date": NORMAL_START, "end_date": NORMAL_END,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum", "timezone": "auto",
    }
    fetched = await dc.get("open-meteo-archive", ARCHIVE_URL, params, ttl_s=30 * 24 * 3600)
    payload = _require(fetched.data, "open-meteo-archive", "daily")
    return _series(payload, "daily"), fetched


# ----------------------------------------------------------------------------------------- flood
FLOOD_FORECAST_VARS = ["river_discharge", "river_discharge_median", "river_discharge_max",
                       "river_discharge_min", "river_discharge_p25", "river_discharge_p75"]
NEIGHBOUR_OFFSETS = [(0.0, 0.0), (0.05, 0.0), (-0.05, 0.0), (0.0, 0.05), (0.0, -0.05),
                     (0.05, 0.05), (0.05, -0.05), (-0.05, 0.05), (-0.05, -0.05)]


@dataclass
class FloodCell:
    lat: float
    lon: float
    daily: Series


async def fetch_flood_forecast(dc: DataClient, lat: float, lon: float) -> tuple[FloodCell, Fetched, int]:
    """GloFAS ensemble forecast for the largest river cell within ~5 km.

    GloFAS runs on a ~5 km grid and only river cells carry meaningful discharge, so we query the
    farm's cell plus its 8 neighbours in one multi-location request and keep the cell with the
    highest mean discharge: the main river that could flood the area. Returns (cell, provenance,
    number of cells compared).
    """
    lats = [round(lat + dy, 3) for dy, _ in NEIGHBOUR_OFFSETS]
    lons = [round(lon + dx, 3) for _, dx in NEIGHBOUR_OFFSETS]
    params = {
        "latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons)),
        "daily": ",".join(FLOOD_FORECAST_VARS), "past_days": 7, "forecast_days": 30,
    }
    try:
        fetched = await dc.get("open-meteo-flood", FLOOD_URL, params, ttl_s=3 * 3600)
    except SourceError as exc:
        if exc.status != 400:
            raise
        # Self-heal: if multi-location queries are rejected, fall back to the farm's own cell.
        params.update(latitude=round(lat, 3), longitude=round(lon, 3))
        fetched = await dc.get("open-meteo-flood", FLOOD_URL, params, ttl_s=3 * 3600)
    payloads = fetched.data if isinstance(fetched.data, list) else [fetched.data]
    cells = []
    for p in payloads:
        if isinstance(p, dict) and "daily" in p:
            cells.append(FloodCell(p.get("latitude", lat), p.get("longitude", lon), _series(p, "daily")))
    if not cells:
        raise SourceError("open-meteo-flood", "no river discharge returned for this area", retryable=False)

    def mean_q(cell: FloodCell) -> float:
        vals = [v for v in cell.daily.col("river_discharge") if v is not None]
        return sum(vals) / len(vals) if vals else -1.0

    return max(cells, key=mean_q), fetched, len(cells)


async def fetch_flood_history(dc: DataClient, cell_lat: float, cell_lon: float) -> tuple[Series, Fetched]:
    """Daily GloFAS reanalysis discharge since 1984 for one cell (for return-period thresholds)."""
    end = (date.today().replace(day=1) - timedelta(days=1)).isoformat()
    params = {
        "latitude": cell_lat, "longitude": cell_lon, "daily": "river_discharge",
        "start_date": FLOOD_HISTORY_START, "end_date": end,
    }
    fetched = await dc.get("open-meteo-flood", FLOOD_URL, params, ttl_s=30 * 24 * 3600)
    payload = fetched.data[0] if isinstance(fetched.data, list) and fetched.data else fetched.data
    payload = _require(payload, "open-meteo-flood", "daily")
    return _series(payload, "daily"), fetched


# ----------------------------------------------------------------------------------------- air quality
async def fetch_air_quality(dc: DataClient, lat: float, lon: float) -> tuple[dict[str, Any], Series, Fetched]:
    params = {
        "latitude": round(lat, 3), "longitude": round(lon, 3),
        "current": "us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide,dust",
        "hourly": "us_aqi,pm2_5,pm10", "forecast_days": 3, "timezone": "auto",
    }
    fetched = await dc.get("open-meteo-air", AIR_URL, params, ttl_s=60 * 60)
    payload = _require(fetched.data, "open-meteo-air", "hourly")
    return dict(payload.get("current") or {}), _series(payload, "hourly"), fetched


# ----------------------------------------------------------------------------------------- geocoding
async def geocode(dc: DataClient, query: str, country_code: str | None = "IN", count: int = 8) -> tuple[list[dict], Fetched]:
    params: dict[str, Any] = {"name": query.strip(), "count": count, "language": "en", "format": "json"}
    if country_code:
        params["countryCode"] = country_code
    fetched = await dc.get("open-meteo-geocoding", GEOCODE_URL, params, ttl_s=7 * 24 * 3600)
    results = []
    for r in (fetched.data or {}).get("results") or []:
        if "latitude" not in r or "longitude" not in r:
            continue
        results.append({
            "name": r.get("name"), "lat": r["latitude"], "lon": r["longitude"],
            "district": r.get("admin2"), "state": r.get("admin1"), "country": r.get("country"),
            "country_code": r.get("country_code"), "elevation_m": r.get("elevation"),
            "population": r.get("population"),
        })
    return results, fetched
