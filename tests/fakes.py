"""TEST-ONLY fakes. The application never imports this module.

* `FakeUpstream` is an httpx transport that answers like each live API (same JSON shapes as the
  published docs) with synthetic, deterministic values, so every connector, parser and tool can be
  exercised offline in CI.
* `ScriptedClaude` mimics the parts of the Anthropic SDK the agent uses (beta.messages.stream /
  create) so the agent loop can be tested without network or API spend.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

import httpx

IST = timezone(timedelta(hours=5, minutes=30))


def _today_ist() -> date:
    return datetime.now(IST).date()


# ======================================================================================= upstream APIs
@dataclass
class FakeUpstream:
    mandi_filter_style: str = "plain"  # "plain" -> honours filters[state]; "keyword" -> only filters[state.keyword]
    fail_hosts: set[str] = field(default_factory=set)
    cog_dir: Path | None = None  # where synthetic Sentinel-2 GeoTIFFs live (see make_cogs)
    calls: list[str] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        self.calls.append(url.netloc + url.path)
        if url.netloc in self.fail_hosts:
            return httpx.Response(503, text="upstream down")
        params = parse_qsl(url.query, keep_blank_values=True)
        q = dict(params)
        routes: dict[str, Callable[[], Any]] = {
            "api.open-meteo.com": lambda: forecast(q),
            "archive-api.open-meteo.com": lambda: archive(q),
            "flood-api.open-meteo.com": lambda: flood(q),
            "air-quality-api.open-meteo.com": lambda: air(q),
            "geocoding-api.open-meteo.com": lambda: geocoding(q),
            "rest.isric.org": lambda: soilgrids(params),
            "nominatim.openstreetmap.org": lambda: nominatim(q),
            "api.data.gov.in": lambda: mandi(q, self.mandi_filter_style),
            "www.gdacs.org": lambda: gdacs(q, url.path),
            "earth-search.aws.element84.com": lambda: stac(json.loads(request.content or b"{}"), self.cog_dir),
        }
        if url.netloc not in routes:
            return httpx.Response(404, text="unknown host")
        body = routes[url.netloc]()
        if isinstance(body, httpx.Response):
            return body
        if isinstance(body, str):
            return httpx.Response(200, text=body, headers={"content-type": "application/xml"})
        return httpx.Response(200, json=body)


def _days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def forecast(q: dict[str, str]) -> dict[str, Any]:
    past, fdays = int(q.get("past_days", 0)), int(q.get("forecast_days", 7))
    today = _today_ist()
    days = _days(today - timedelta(days=past), past + fdays)
    hourly_vars = q["hourly"].split(",")
    daily_vars = q["daily"].split(",")
    hours = [datetime(d.year, d.month, d.day, h) for d in days for h in range(24)]
    # Rain: a soaking 40 mm event 12 days ago, a dry spell, then a heavy-rain day 3 days ahead.
    rain_day = {today - timedelta(days=12): 40.0, today + timedelta(days=3): 72.0, today + timedelta(days=4): 18.0}
    humid_days = {today + timedelta(days=1), today + timedelta(days=2), today + timedelta(days=3)}
    all_hourly = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation", "precipitation_probability",
                  "wind_speed_10m", "wind_gusts_10m", "et0_fao_evapotranspiration", "weather_code", "is_day"]
    h: dict[str, list] = {v: [] for v in all_hourly}
    for t in hours:
        d = t.date()
        diurnal = math.sin(2 * math.pi * (t.hour - 9) / 24)
        temp = 24 + 8 * diurnal + (2.5 if d == today + timedelta(days=5) else 0)
        rh = 55 - 25 * diurnal + (30 if d in humid_days and (t.hour < 8 or t.hour > 19) else 0)
        rain = rain_day.get(d, 0.0) / 6 if 14 <= t.hour < 20 else 0.0
        vals = {
            "temperature_2m": round(temp, 1), "relative_humidity_2m": min(round(rh), 100),
            "dew_point_2m": round(temp - (100 - min(rh, 100)) / 5, 1), "precipitation": round(rain, 1),
            "precipitation_probability": 90 if rain_day.get(d) else 10,
            "wind_speed_10m": round(7 + 5 * max(diurnal, -0.6), 1), "wind_gusts_10m": round(12 + 8 * max(diurnal, 0), 1),
            "et0_fao_evapotranspiration": round(max(0.0, 0.55 * diurnal), 2), "weather_code": 63 if rain else 2,
            "is_day": 1 if 6 <= t.hour < 18 else 0,
        }
        for v in all_hourly:
            h[v].append(vals[v])
    daily: dict[str, list] = {v: [] for v in daily_vars}
    for i, d in enumerate(days):
        sl = slice(24 * i, 24 * i + 24)
        temps, rains = h["temperature_2m"][sl], h["precipitation"][sl]
        vals = {
            "weather_code": 65 if sum(rains) > 50 else 61 if sum(rains) else 2,
            "temperature_2m_max": max(temps), "temperature_2m_min": min(temps),
            "precipitation_sum": round(sum(rains), 1), "precipitation_probability_max": max(h["precipitation_probability"][sl]),
            "precipitation_hours": sum(1 for r in rains if r > 0), "et0_fao_evapotranspiration": round(sum(h["et0_fao_evapotranspiration"][sl]), 2),
            "wind_speed_10m_max": max(h["wind_speed_10m"][sl]), "wind_gusts_10m_max": max(h["wind_gusts_10m"][sl]),
            "shortwave_radiation_sum": 18.2, "uv_index_max": 7.1,
        }
        for v in daily_vars:
            daily[v].append(vals.get(v))
    now = datetime.now(IST)
    return {
        "latitude": float(q["latitude"]), "longitude": float(q["longitude"]), "generationtime_ms": 1.2,
        "utc_offset_seconds": 19800, "timezone": "Asia/Kolkata", "timezone_abbreviation": "GMT+5:30", "elevation": 584.0,
        "current_units": {"time": "iso8601"}, "current": {"time": now.strftime("%Y-%m-%dT%H:%M"), "interval": 900,
                                                         "temperature_2m": 27.4, "relative_humidity_2m": 61, "precipitation": 0.0,
                                                         "wind_speed_10m": 8.3, "weather_code": 2, "is_day": 1},
        "hourly_units": {v: "" for v in ["time", *hourly_vars]},
        "hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in hours], **{v: h[v] for v in hourly_vars}},
        "daily_units": {v: "" for v in ["time", *daily_vars]},
        "daily": {"time": [d.isoformat() for d in days], **daily},
    }


def archive(q: dict[str, str]) -> dict[str, Any]:
    start, end = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
    rng = random.Random(42)
    days = _days(start, (end - start).days + 1)
    tmax, tmin, rain = [], [], []
    for d in days:
        doy = d.timetuple().tm_yday
        season = math.sin(2 * math.pi * (doy - 80) / 365)
        tmax.append(round(31 + 4 * season + rng.uniform(-1.5, 1.5), 1))
        tmin.append(round(19 + 5 * season + rng.uniform(-1.5, 1.5), 1))
        monsoon = 152 <= doy <= 280
        rain.append(round(rng.expovariate(1 / 9) if monsoon and rng.random() < 0.6 else (rng.expovariate(1 / 4) if rng.random() < 0.08 else 0.0), 1))
    return {"latitude": float(q["latitude"]), "longitude": float(q["longitude"]), "utc_offset_seconds": 19800,
            "timezone": "Asia/Kolkata", "elevation": 584.0, "daily_units": {"time": "iso8601"},
            "daily": {"time": [d.isoformat() for d in days], "temperature_2m_max": tmax, "temperature_2m_min": tmin,
                      "precipitation_sum": rain}}


def flood(q: dict[str, str]) -> Any:
    if "start_date" in q:  # reanalysis history for one cell
        start, end = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
        rng = random.Random(7)
        days = _days(start, (end - start).days + 1)
        peaks = {y: rng.uniform(1400, 3200) for y in range(start.year, end.year + 1)}
        values = []
        for d in days:
            doy = d.timetuple().tm_yday
            pulse = math.exp(-((doy - 225) / 25) ** 2)
            values.append(round(150 + peaks[d.year] * pulse, 1))
        return {"latitude": float(q["latitude"]), "longitude": float(q["longitude"]), "daily_units": {"time": "iso8601"},
                "daily": {"time": [d.isoformat() for d in days], "river_discharge": values}}
    lats, lons = q["latitude"].split(","), q["longitude"].split(",")
    today = date.today()
    days = _days(today - timedelta(days=int(q.get("past_days", 0))), int(q.get("past_days", 0)) + int(q.get("forecast_days", 30)))
    out = []
    for k, (la, lo) in enumerate(zip(lats, lons)):
        scale = 1.0 if k == 3 else 0.02  # one neighbour carries the main river
        median = [round(scale * (600 + 1500 * math.exp(-((i - 12) / 4) ** 2)), 1) for i in range(len(days))]
        out.append({"latitude": float(la), "longitude": float(lo), "daily_units": {"time": "iso8601"},
                    "daily": {"time": [d.isoformat() for d in days], "river_discharge": median,
                              "river_discharge_median": median, "river_discharge_max": [round(v * 1.6, 1) for v in median],
                              "river_discharge_min": [round(v * 0.6, 1) for v in median],
                              "river_discharge_p25": [round(v * 0.85, 1) for v in median],
                              "river_discharge_p75": [round(v * 1.2, 1) for v in median]}})
    return out if len(out) > 1 else out[0]


def air(q: dict[str, str]) -> dict[str, Any]:
    now = datetime.now(IST).replace(minute=0, second=0, microsecond=0)
    hours = [now + timedelta(hours=i) for i in range(72)]
    return {"latitude": float(q["latitude"]), "longitude": float(q["longitude"]), "timezone": "Asia/Kolkata",
            "current": {"time": now.strftime("%Y-%m-%dT%H:%M"), "interval": 3600, "us_aqi": 158, "pm2_5": 71.3,
                        "pm10": 128.0, "ozone": 61.0, "nitrogen_dioxide": 22.4, "dust": 4.0},
            "hourly_units": {"time": "iso8601"},
            "hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in hours], "us_aqi": [150 + i % 24 for i in range(72)],
                       "pm2_5": [65 + (i % 24) / 2 for i in range(72)], "pm10": [120 + i % 24 for i in range(72)]}}


def geocoding(q: dict[str, str]) -> dict[str, Any]:
    if q["name"].lower().startswith("nashik"):
        return {"results": [{"id": 1261731, "name": "Nashik", "latitude": 19.99727, "longitude": 73.79096, "elevation": 584.0,
                             "country_code": "IN", "country": "India", "admin1": "Maharashtra", "admin2": "Nashik",
                             "timezone": "Asia/Kolkata", "population": 1289497}], "generationtime_ms": 0.6}
    return {"generationtime_ms": 0.4}


SOIL = {"clay": (450, 10, "g/kg", "%"), "sand": (250, 10, "g/kg", "%"), "silt": (300, 10, "g/kg", "%"),
        "phh2o": (79, 10, "pH*10", "-"), "soc": (85, 10, "dg/kg", "g/kg"), "nitrogen": (120, 100, "cg/kg", "g/kg"),
        "cec": (420, 10, "mmol(c)/kg", "cmol(c)/kg"), "bdod": (135, 100, "cg/cm³", "kg/dm³"),
        "wv0033": (380, 10, "10^-3 cm³/cm³", "10^-2 cm³/cm³"), "wv1500": (230, 10, "10^-3 cm³/cm³", "10^-2 cm³/cm³")}


def soilgrids(params: list[tuple[str, str]]) -> dict[str, Any]:
    props = [v for k, v in params if k == "property"]
    depths = [v for k, v in params if k == "depth"]
    q = dict(params)
    layers = []
    for p in props:
        value, d_factor, mapped, target = SOIL[p]
        layers.append({"name": p, "unit_measure": {"d_factor": d_factor, "mapped_units": mapped, "target_units": target,
                                                   "uncertainty_unit": ""},
                       "depths": [{"range": {"top_depth": int(lbl.split("-")[0]), "bottom_depth": int(lbl.split("-")[1][:-2]),
                                             "unit_depth": "cm"}, "label": lbl, "values": {"mean": value + 5 * i}}
                                  for i, lbl in enumerate(depths)]})
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(q["lon"]), float(q["lat"])]},
            "properties": {"layers": layers}, "query_time_s": 0.9}


def nominatim(q: dict[str, str]) -> dict[str, Any]:
    return {"place_id": 1, "lat": q["lat"], "lon": q["lon"], "display_name": "Pimpalgaon Baswant, Niphad, Nashik, Maharashtra, India",
            "address": {"village": "Pimpalgaon Baswant", "county": "Niphad", "state_district": "Nashik District",
                        "state": "Maharashtra", "ISO3166-2-lvl4": "IN-MH", "country": "India", "country_code": "in"}}


MANDI_ROWS = [("Nashik", "Lasalgaon", 1500, 2450, 2100), ("Nashik", "Pimpalgaon", 1600, 2500, 2250),
              ("Pune", "Pune", 1800, 2900, 2450), ("Mumbai", "Mumbai", 2000, 3100, 2600), ("Solapur", "Solapur", 1200, 2300, 1900)]


def mandi(q: dict[str, str], style: str) -> dict[str, Any]:
    key = ".keyword" if style == "keyword" else ""
    state, commodity = q.get(f"filters[state{key}]"), q.get(f"filters[commodity{key}]")
    if state is None:  # unrecognised filter syntax is silently ignored: unfiltered rows from another state
        return {"status": "ok", "total": 1, "count": 1, "records": [
            {"state": "Kerala", "district": "Ernakulam", "market": "Aluva", "commodity": "Banana", "variety": "Nendra",
             "grade": "FAQ", "arrival_date": _today_ist().strftime("%d/%m/%Y"), "min_price": "3000", "max_price": "3800", "modal_price": "3400"}]}
    rows = []
    if state == "Maharashtra" and commodity in (None, "Onion"):
        rows = [{"state": "Maharashtra", "district": d, "market": m, "commodity": "Onion", "variety": "Red", "grade": "FAQ",
                 "arrival_date": _today_ist().strftime("%d/%m/%Y"), "min_price": str(lo), "max_price": str(hi), "modal_price": str(mo)}
                for d, m, lo, hi, mo in MANDI_ROWS]
    limit = int(q.get("limit", 10))
    return {"status": "ok", "total": len(rows), "count": min(len(rows), limit), "records": rows[:limit],
            "field": [{"name": "State", "id": "state", "type": "keyword"}]}


def gdacs(q: dict[str, str], path: str) -> Any:
    if path.endswith("rss.xml"):
        return ('<?xml version="1.0"?><rss xmlns:gdacs="http://www.gdacs.org" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">'
                '<channel><item><title>Green flood alert in India</title><link>https://www.gdacs.org/report.aspx?eventid=1</link>'
                '<gdacs:eventtype>FL</gdacs:eventtype><gdacs:alertlevel>Green</gdacs:alertlevel><gdacs:country>India</gdacs:country>'
                '<geo:Point><geo:lat>21.1</geo:lat><geo:long>75.2</geo:long></geo:Point></item></channel></rss>')
    feats = [
        ("FL", "Flood in Maharashtra", "Orange", 20.9, 75.6),
        ("TC", "Tropical cyclone MONTHA", "Red", 15.5, 86.8),
        ("EQ", "Earthquake in Nepal", "Green", 28.2, 84.7),
    ]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "bbox": [lon - 1, lat - 1, lon + 1, lat + 1], "geometry": {"type": "Point", "coordinates": [lon, lat]},
         "properties": {"eventtype": et, "eventid": i, "name": name, "alertlevel": al, "country": "India",
                        "fromdate": f"{date.today().isoformat()}T00:00:00", "todate": f"{date.today().isoformat()}T00:00:00",
                        "severitydata": {"severitytext": "synthetic"}, "url": {"report": f"https://www.gdacs.org/report.aspx?eventid={i}"}}}
        for i, (et, name, al, lat, lon) in enumerate(feats)]}


def stac(body: dict[str, Any], cog_dir: Path | None) -> dict[str, Any]:
    if cog_dir is None:
        return {"type": "FeatureCollection", "features": []}
    start = date.fromisoformat(body["datetime"][:10])
    end = date.fromisoformat(body["datetime"].split("/")[1][:10])
    feats = []
    for i, (scene_date, ndvi_level) in enumerate(_scene_dates()):
        if not start <= scene_date <= end:
            continue
        tag = f"{scene_date.isoformat()}"
        feats.append({"type": "Feature", "id": f"S2A_43QCB_{tag}_0_L2A",
                      "properties": {"datetime": f"{tag}T05:35:00Z", "eo:cloud_cover": 5.0 + i},
                      "assets": {k: {"href": str(cog_dir / f"{tag}_{k}.tif"), "raster:bands": [{"scale": 0.0001, "offset": 0}]}
                                 for k in ("red", "nir", "scl")}})
    return {"type": "FeatureCollection", "features": feats}


def _scene_dates() -> list[tuple[date, float]]:
    today = date.today()
    return [(today - timedelta(days=d), lvl) for d, lvl in ((5, 0.62), (15, 0.55), (30, 0.41), (50, 0.28), (365, 0.58))]


def make_cogs(out_dir: Path, lat: float, lon: float) -> Path:
    """Write tiny UTM GeoTIFFs (red/nir/scl) around a point so NDVI reads can be tested offline."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import transform as warp

    out_dir.mkdir(parents=True, exist_ok=True)
    crs = "EPSG:32643"
    xs, ys = warp("EPSG:4326", crs, [lon], [lat])
    for scene_date, ndvi in _scene_dates():
        tag = scene_date.isoformat()
        red = np.full((64, 64), 900, dtype="uint16")
        nir = (red * (1 + ndvi) / (1 - ndvi)).astype("uint16")
        scl = np.full((32, 32), 4, dtype="uint8")
        for name, arr, res in (("red", red, 10), ("nir", nir, 10), ("scl", scl, 20)):
            transform = from_origin(xs[0] - 320, ys[0] + 320, res, res)
            with rasterio.open(out_dir / f"{tag}_{name}.tif", "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0],
                               count=1, dtype=arr.dtype, crs=crs, transform=transform) as dst:
                dst.write(arr, 1)
    return out_dir


# ======================================================================================= Claude stand-in
@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    thinking: str = ""


@dataclass
class Usage:
    input_tokens: int = 1200
    output_tokens: int = 300
    cache_read_input_tokens: int = 800
    cache_creation_input_tokens: int = 0


@dataclass
class Message:
    content: list[Block]
    stop_reason: str
    model: str = "claude-opus-5-5"
    usage: Usage = field(default_factory=Usage)


@dataclass
class Event:
    type: str
    text: str = ""
    thinking: str = ""
    content_block: Any = None


class _Stream:
    def __init__(self, message: Message):
        self.message = message

    async def __aenter__(self) -> "_Stream":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self):
        return self._events()

    async def _events(self):
        for block in self.message.content:
            if block.type == "thinking":
                yield Event("thinking", thinking=block.thinking)
            elif block.type == "text":
                yield Event("text", text=block.text)
            elif block.type == "tool_use":
                yield Event("content_block_start", content_block=block)

    async def get_final_message(self) -> Message:
        return self.message


class ScriptedClaude:
    """Each call to `beta.messages.stream` returns script[i](messages) -> Message."""

    def __init__(self, script: list[Callable[[list[dict]], Message]]):
        self.script = script
        self.requests: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            def stream(self, **kwargs: Any) -> _Stream:
                outer.requests.append(kwargs)
                step = outer.script[min(len(outer.requests) - 1, len(outer.script) - 1)]
                return _Stream(step(kwargs["messages"]))

            async def create(self, **kwargs: Any) -> Message:
                outer.requests.append(kwargs)
                return outer.script[min(len(outer.requests) - 1, len(outer.script) - 1)](kwargs["messages"])

            async def parse(self, **kwargs: Any) -> Any:
                outer.requests.append(kwargs)
                return outer.script[min(len(outer.requests) - 1, len(outer.script) - 1)](kwargs["messages"])

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()


def last_tool_results(messages: list[dict]) -> dict[str, dict]:
    """Map tool_use_id -> parsed tool_result payload from the latest user turn."""
    content = messages[-1]["content"]
    return {r["tool_use_id"]: json.loads(r["content"]) for r in content if isinstance(r, dict) and r.get("type") == "tool_result"}


# ======================================================================================= Gemini stand-in
class ScriptedGemini:
    """Mimics `client.aio.models.generate_content`: call i returns script[i](model, contents, config).

    Responses are real `google.genai.types` objects, so the tests also check our use of the SDK types.
    """

    def __init__(self, script: list[Callable[..., Any]]):
        self.script = script
        self.requests: list[dict[str, Any]] = []
        outer = self

        class _Models:
            async def generate_content(self, *, model: str, contents: Any, config: Any = None) -> Any:
                outer.requests.append({"model": model, "contents": list(contents) if isinstance(contents, list) else contents,
                                       "config": config})
                step = outer.script[min(len(outer.requests) - 1, len(outer.script) - 1)]
                return step(model, contents, config)

        class _Aio:
            models = _Models()

        self.aio = _Aio()


def gemini_response(*parts: Any, finish: str = "STOP", model_version: str = "gemini-test") -> Any:
    from google.genai import types

    candidate = types.Candidate(content=types.Content(role="model", parts=list(parts)) if parts else None, finish_reason=finish)
    return types.GenerateContentResponse(
        candidates=[candidate], model_version=model_version,
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=1200, candidates_token_count=250,
                                                                  thoughts_token_count=120, cached_content_token_count=0))


def last_function_responses(contents: list[Any]) -> dict[str, dict]:
    """Map tool name -> function response payload from the latest user turn."""
    return {p.function_response.name: p.function_response.response for p in contents[-1].parts if p.function_response}
