"""Tool implementations: each combines live data connectors with the agronomy science layer.

Every tool returns a `ToolOutput` whose `data` holds compact, unit-labelled facts. The agent sees
exactly this data (wrapped as evidence E#), the verifier checks the agent's figures against it,
and the dashboard renders it as risk cards.
"""

from __future__ import annotations

import asyncio
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..agronomy import climate, hydrology, soil, spray, stress
from ..agronomy.crops import CROPS, IRRIGATION_EFFICIENCY, get_crop
from ..agronomy.water import LITRES_PER_MM_PER_ACRE, crop_stage, run_water_balance, wind_10m_to_2m
from ..diary.service import diary_summary
from ..http import SourceError
from ..sources import gdacs, mandi, nominatim, open_meteo, sentinel2, soilgrids
from .base import (AlertArgs, CropArgs, DiaryArgs, GeocodeArgs, IrrigationArgs, LatLon, MandiArgs, OutlookArgs,
                   SprayArgs, ToolContext, ToolError, ToolOutput, worst_level)

WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "moderate rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}
HILLY_ELEVATION_M = 1000  # IMD applies hill criteria to hill stations; elevation is used as the proxy


def _r(x: float | None, n: int = 1) -> float | None:
    return None if x is None else round(float(x), n)


def _i(x: float | None) -> int | None:
    return None if x is None else int(round(float(x)))


def nice_date(iso: str) -> str:
    """'2026-11-02' -> 'Mon 2 Nov' for human-facing headlines (data keeps ISO dates)."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _sum(values: list) -> float:
    return round(sum(v for v in values if v is not None), 1)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _wrap(source: SourceError) -> ToolError:
    return ToolError(f"Live data source unavailable: {source.message}", retryable=source.retryable,
                     hint="Tell the user this data is temporarily unavailable; do not estimate it.")


async def _forecast(ctx: ToolContext, lat: float, lon: float) -> open_meteo.Forecast:
    try:
        return await open_meteo.fetch_forecast(ctx.dc, lat, lon)
    except SourceError as exc:
        raise _wrap(exc) from None


async def _climatology(ctx: ToolContext, lat: float, lon: float) -> tuple[climate.Climatology | None, dict | None, str | None]:
    try:
        series, fetched = await open_meteo.fetch_climatology(ctx.dc, lat, lon)
    except SourceError as exc:
        return None, None, f"Climate normals unavailable ({exc.message})"
    clim = climate.Climatology(series.times, series.col("temperature_2m_max"), series.col("temperature_2m_min"),
                               series.col("precipitation_sum"))
    return clim, fetched.provenance("ERA5 reanalysis via Open-Meteo Historical API (1991-2020 normals)"), None


def _forecast_source(fc: open_meteo.Forecast) -> dict[str, Any]:
    return fc.fetched.provenance("Open-Meteo Forecast API (ECMWF IFS / NCEP GFS / DWD ICON best-match)")


def _daily_rh_min_and_wind(fc: open_meteo.Forecast, days: int = 7) -> tuple[float | None, float | None]:
    """Mean daily minimum RH (%) and mean 2 m wind (m/s) over the coming days, for FAO-56 Kc adjustment."""
    start = fc.now_hour_index
    stop = min(start + 24 * days, len(fc.hourly))
    rh, wind = fc.hourly.col("relative_humidity_2m")[start:stop], fc.hourly.col("wind_speed_10m")[start:stop]
    mins = []
    for k in range(0, len(rh), 24):
        chunk = [v for v in rh[k:k + 24] if v is not None]
        if chunk:
            mins.append(min(chunk))
    winds = [w for w in wind if w is not None]
    return (statistics.fmean(mins) if mins else None,
            wind_10m_to_2m(statistics.fmean(winds)) if winds else None)


# ===================================================================================== location
async def get_location_context(ctx: ToolContext, args: LatLon) -> ToolOutput:
    place_res, fc_res = await asyncio.gather(nominatim.reverse(ctx.dc, args.lat, args.lon),
                                             _forecast(ctx, args.lat, args.lon), return_exceptions=True)
    data: dict[str, Any] = {"lat": round(args.lat, 4), "lon": round(args.lon, 4)}
    sources, notes = [], []
    if isinstance(place_res, tuple):
        place, fetched = place_res
        data.update({k: place[k] for k in ("locality", "subdistrict", "district", "state", "country") if place.get(k)})
        sources.append(fetched.provenance("OpenStreetMap Nominatim reverse geocoding"))
    else:
        notes.append("Place name lookup unavailable right now.")
    if isinstance(fc_res, open_meteo.Forecast):
        data.update({"elevation_m": _r(fc_res.elevation_m, 0), "timezone": fc_res.timezone,
                     "local_time": fc_res.local_now.strftime("%Y-%m-%d %H:%M")})
        cur = fc_res.current
        if cur:
            data["now"] = {"temperature_c": _r(cur.get("temperature_2m")), "humidity_pct": _r(cur.get("relative_humidity_2m"), 0),
                           "wind_kmh": _r(cur.get("wind_speed_10m")), "weather": WMO_CODES.get(cur.get("weather_code"), None)}
        sources.append(_forecast_source(fc_res))
    if not sources:
        raise ToolError("Location services are unreachable right now.", retryable=True)
    name = ", ".join(x for x in (data.get("locality"), data.get("district"), data.get("state")) if x)
    return ToolOutput(data, sources, notes, "info", name or f"{args.lat:.3f}, {args.lon:.3f}")


async def geocode_place(ctx: ToolContext, args: GeocodeArgs) -> ToolOutput:
    try:
        results, fetched = await open_meteo.geocode(ctx.dc, args.place)
        if not results:
            results, fetched = await open_meteo.geocode(ctx.dc, args.place, country_code=None)
    except SourceError as exc:
        raise _wrap(exc) from None
    if not results:
        raise ToolError(f"No place called '{args.place}' was found.",
                        hint="Ask the user for the nearest town or district, or check the spelling.")
    return ToolOutput({"query": args.place, "matches": results[:5]},
                      [fetched.provenance("Open-Meteo Geocoding API (GeoNames)")], level="info",
                      headline=f"{len(results)} match(es)")


# ===================================================================================== weather
async def get_weather_outlook(ctx: ToolContext, args: OutlookArgs) -> ToolOutput:
    fc, (clim, clim_src, clim_note) = await asyncio.gather(_forecast(ctx, args.lat, args.lon),
                                                           _climatology(ctx, args.lat, args.lon))
    d, t0 = fc.daily, fc.today_index
    hilly = (fc.elevation_m or 0) >= HILLY_ELEVATION_M
    rows, hazards, chart_rows = [], [], []
    for i in range(t0, len(d)):
        day = date.fromisoformat(d.times[i])
        tmax, tmin, rain = d.col("temperature_2m_max")[i], d.col("temperature_2m_min")[i], d.col("precipitation_sum")[i]
        ntx, ntn = clim.normal_temperature(day) if clim else (None, None)
        chart_rows.append({"date": d.times[i], "tmax": tmax, "tmin": tmin, "rain": rain, "normal_tmax": ntx,
                           "normal_tmin": ntn, "normal_rain": clim.normal_daily_rain(day) if clim else None})
        if i >= t0 + args.days:
            continue
        row = {"date": d.times[i], "day": day.strftime("%a"), "weather": WMO_CODES.get(d.col("weather_code")[i]),
               "tmax_c": _r(tmax), "tmin_c": _r(tmin), "rain_mm": _r(rain),
               "rain_chance_pct": d.col("precipitation_probability_max")[i],
               "wind_max_kmh": _r(d.col("wind_speed_10m_max")[i]), "gust_max_kmh": _r(d.col("wind_gusts_10m_max")[i]),
               "et0_mm": _r(d.col("et0_fao_evapotranspiration")[i], 1)}
        if ntx is not None:
            row["normal_tmax_c"], row["normal_tmin_c"] = ntx, ntn
        rain_cat = stress.imd_rain_category(rain)
        for label in filter(None, [
            f"{rain_cat} rain" if rain_cat else None,
            stress.imd_heat_wave(tmax, ntx, hilly) if tmax is not None else None,
            stress.imd_cold_wave(tmin, ntn, hilly) if tmin is not None else None,
        ]):
            hazards.append({"date": d.times[i], "hazard": label})
        rows.append(row)
    if not rows:
        raise ToolError("Forecast returned no future days.", retryable=True)

    rain_total = _sum([r["rain_mm"] for r in rows])
    summary = {
        "days": len(rows), "rain_total_mm": rain_total,
        "rainy_days": sum(1 for r in rows if (r["rain_mm"] or 0) >= 2.5),
        "max_tmax_c": max((r["tmax_c"] for r in rows if r["tmax_c"] is not None), default=None),
        "min_tmin_c": min((r["tmin_c"] for r in rows if r["tmin_c"] is not None), default=None),
    }
    severe = any(h["hazard"].startswith(("severe", "extremely")) for h in hazards)
    level = "severe" if severe else "high" if hazards else "moderate" if rain_total >= 50 else "low"
    headline = f"{rain_total} mm rain over {summary['rainy_days']} rainy day(s) in the next {len(rows)} days"
    if hazards:
        headline = f"{hazards[0]['hazard'].capitalize()} on {nice_date(hazards[0]['date'])}; " + headline
    notes = ["Rainy day = at least 2.5 mm (IMD definition)."]
    if clim_note:
        notes.append(clim_note)
    if hilly:
        notes.append("Elevation above 1000 m: IMD hill-station heat-wave criteria applied.")
    sources = [_forecast_source(fc)] + ([clim_src] if clim_src else [])
    return ToolOutput({"summary": summary, "hazards": hazards, "daily": rows}, sources, notes, level, headline,
                      chart={"daily": chart_rows})


async def get_climate_anomaly(ctx: ToolContext, args: LatLon) -> ToolOutput:
    fc, (clim, clim_src, clim_note) = await asyncio.gather(_forecast(ctx, args.lat, args.lon),
                                                           _climatology(ctx, args.lat, args.lon))
    if clim is None:
        raise ToolError(clim_note or "Climate normals unavailable.", retryable=True)
    d, t0 = fc.daily, fc.today_index
    rain = d.col("precipitation_sum")
    today = date.fromisoformat(d.times[t0])
    out: dict[str, Any] = {}
    past_days = min(30, t0)
    if past_days >= 7:
        out["last_30_days"] = climate.rain_anomaly(clim, today - timedelta(days=past_days), past_days,
                                                   _sum(rain[t0 - past_days:t0]))
    out["next_7_days"] = climate.rain_anomaly(clim, today, min(7, len(d) - t0), _sum(rain[t0:t0 + 7]))
    out["next_16_days"] = climate.rain_anomaly(clim, today, len(d) - t0, _sum(rain[t0:]))

    def temp_departure(lo: int, hi: int) -> dict[str, Any] | None:
        pairs = []
        for i in range(max(lo, 0), min(hi, len(d))):
            tmax = d.col("temperature_2m_max")[i]
            ntx, _ = clim.normal_temperature(date.fromisoformat(d.times[i]))
            if tmax is not None and ntx is not None:
                pairs.append((tmax, ntx))
        if not pairs:
            return None
        mean_t, mean_n = statistics.fmean(p[0] for p in pairs), statistics.fmean(p[1] for p in pairs)
        return {"mean_tmax_c": round(mean_t, 1), "normal_tmax_c": round(mean_n, 1),
                "departure_c": round(mean_t - mean_n, 1)}

    out["tmax_last_30_days"] = temp_departure(t0 - past_days, t0)
    out["tmax_next_7_days"] = temp_departure(t0, t0 + 7)

    level = "low"
    past = out.get("last_30_days") or {}
    nxt = out.get("next_7_days") or {}
    if past.get("imd_category") in ("large deficient", "no rain") and (past.get("normal_mm") or 0) >= 50:
        level = "high"
    elif nxt.get("imd_category") == "large excess" and (nxt.get("rain_mm") or 0) >= 64.5:
        level = "high"
    elif past.get("imd_category") in ("deficient", "large deficient") or nxt.get("imd_category") in ("excess", "large excess"):
        level = "moderate"
    t7 = out.get("tmax_next_7_days") or {}
    if (t7.get("departure_c") or 0) >= 4.5:
        level = worst_level(level, "high")

    parts = []
    if past:
        dep = past.get("departure_pct")
        parts.append(f"Last {past['days']} days: {past['rain_mm']} mm vs normal {past['normal_mm']} mm"
                     + (f" ({dep:+.0f}%, {past['imd_category']})" if dep is not None else f" ({past['imd_category']})"))
    if nxt:
        parts.append(f"next 7 days: {nxt['rain_mm']} mm vs normal {nxt['normal_mm']} mm")
    notes = ["Normal = 1991-2020 WMO baseline from ERA5; categories follow IMD rainfall departure classes."]
    return ToolOutput(out, [_forecast_source(fc), clim_src], notes, level, "; ".join(parts))  # type: ignore[list-item]


# ===================================================================================== soil
async def get_soil_profile(ctx: ToolContext, args: LatLon) -> ToolOutput:
    try:
        layers = await soilgrids.fetch_soil(ctx.dc, args.lat, args.lon)
    except SourceError as exc:
        raise _wrap(exc) from None
    return soil_profile_output(layers)


def soil_profile_output(layers: soilgrids.SoilLayers) -> ToolOutput:
    w = layers.weighted
    sand, silt, clay = w("sand", 0, 30), w("silt", 0, 30), w("clay", 0, 30)
    data: dict[str, Any] = {"depth_analysed_cm": "0-30 (topsoil), 0-60 (root zone water)"}
    notes: list[str] = []
    if None not in (sand, silt, clay) and sand + silt + clay > 0:
        total = sand + silt + clay
        sand, silt, clay = (round(100 * v / total, 1) for v in (sand, silt, clay))
        data.update({"sand_pct": sand, "silt_pct": silt, "clay_pct": clay,
                     "texture_class": soil.usda_texture(sand, silt, clay)})
    ph, soc = w("phh2o", 0, 30), w("soc", 0, 30)
    if ph is not None:
        data["ph"], data["ph_class"] = round(ph, 1), soil.ph_class(ph)
    if soc is not None:
        data["organic_carbon_g_per_kg"] = round(soc, 1)
        data["organic_carbon_pct"] = round(soc / 10, 2)
        data["organic_carbon_class"] = soil.organic_carbon_class(soc)
    for prop, key, nd in (("nitrogen", "total_nitrogen_g_per_kg", 2), ("cec", "cec_cmol_per_kg", 1),
                          ("bdod", "bulk_density_g_per_cm3", 2)):
        val = w(prop, 0, 30)
        if val is not None:
            data[key] = round(val, nd)

    fc, wp = w("wv0033", 0, 60), w("wv1500", 0, 60)
    if fc is not None and wp is not None and fc > wp:
        data["field_capacity_vol_pct"], data["wilting_point_vol_pct"] = round(fc, 1), round(wp, 1)
        data["water_retention_method"] = "SoilGrids water-retention maps (33 kPa / 1500 kPa)"
    elif "clay_pct" in data and soc is not None:
        fc_m3, wp_m3 = soil.saxton_rawls(data["sand_pct"], data["clay_pct"], soc / 10)
        data["field_capacity_vol_pct"], data["wilting_point_vol_pct"] = round(100 * fc_m3, 1), round(100 * wp_m3, 1)
        data["water_retention_method"] = "Saxton & Rawls (2006) pedotransfer from SoilGrids texture + carbon"
    if "field_capacity_vol_pct" in data:
        data["available_water_mm_per_m"] = round(10 * (data["field_capacity_vol_pct"] - data["wilting_point_vol_pct"]))

    texture = data.get("texture_class", "")
    if "clay" in texture:
        notes.append("Clayey soil: holds water well but drains slowly; avoid over-irrigation and watch for waterlogging.")
    elif "sand" in texture:
        notes.append("Sandy soil: low water holding; prefer lighter, more frequent irrigation.")
    if data.get("ph", 7) > 8.0:
        notes.append("Alkaline pH: zinc and iron deficiencies are more likely; confirm with a Soil Health Card test.")
    elif data.get("ph", 7) < 5.5:
        notes.append("Acidic pH: liming may be needed; confirm with a Soil Health Card test.")
    if data.get("organic_carbon_class") == "low":
        notes.append("Low organic carbon: compost/FYM and crop residue retention improve fertility and water holding.")
    notes.append("SoilGrids are 250 m modelled estimates, not a lab test of this exact field.")
    headline = f"{texture.capitalize() or 'Soil'}" + (f", pH {data['ph']}" if "ph" in data else "") + \
        (f", organic carbon {data['organic_carbon_class']}" if "organic_carbon_class" in data else "")
    return ToolOutput(data, [layers.fetched.provenance("ISRIC SoilGrids 2.0 (250 m)")], notes, "info", headline)


# ===================================================================================== irrigation
async def get_irrigation_plan(ctx: ToolContext, args: IrrigationArgs) -> ToolOutput:
    try:
        crop = get_crop(args.crop)
    except KeyError as exc:
        raise ToolError(str(exc), hint=f"Use one of: {', '.join(CROPS)}") from None
    fc_res, soil_res = await asyncio.gather(_forecast(ctx, args.lat, args.lon),
                                            soilgrids.fetch_soil(ctx.dc, args.lat, args.lon), return_exceptions=True)
    if isinstance(fc_res, BaseException):
        raise fc_res
    fc: open_meteo.Forecast = fc_res
    sources, notes = [_forecast_source(fc)], []
    theta_fc = theta_wp = None
    if isinstance(soil_res, soilgrids.SoilLayers):
        profile = soil_profile_output(soil_res)
        if "field_capacity_vol_pct" in profile.data:
            theta_fc = profile.data["field_capacity_vol_pct"] / 100
            theta_wp = profile.data["wilting_point_vol_pct"] / 100
        sources.append(soil_res.fetched.provenance("ISRIC SoilGrids 2.0 (250 m)"))

    rh_min, u2 = _daily_rh_min_and_wind(fc)
    plan = run_water_balance(crop, args.sowing_date, fc.daily.times, fc.daily.col("precipitation_sum"),
                             fc.daily.col("et0_fao_evapotranspiration"), fc.today_index, theta_fc, theta_wp,
                             args.irrigation_method, args.last_irrigation_date, u2, rh_min)
    if plan.soil_assumed:
        notes.append("Soil water-holding data unavailable: assumed a medium loam (FAO-56 Table 19); lower confidence.")
    today = plan.today
    if today is None:
        raise ToolError("Not enough forecast data to run the water balance.", retryable=True)
    st = crop_stage(crop, args.sowing_date, date.fromisoformat(today.date), u2, rh_min)

    upcoming = [x for x in plan.days if x.forecast][:7]
    if today.depletion_mm > today.raw_mm:
        status = "crop is under water stress now"
    elif today.depletion_mm > 0.5 * today.raw_mm:
        status = "adequate, but drying"
    else:
        status = "comfortable"
    data: dict[str, Any] = {
        "crop": crop.name, "sowing_date": args.sowing_date.isoformat(), "stage": st.label,
        "days_after_sowing": st.days_after_sowing, "season_progress_pct": st.progress_pct,
        "crop_coefficient_kc": st.kc, "root_depth_m": st.root_depth_m,
        "soil_water_today": {
            "total_available_water_mm": today.taw_mm, "readily_available_water_mm": today.raw_mm,
            "depletion_mm": today.depletion_mm, "depletion_pct": today.depletion_pct, "status": status,
        },
        "balance_started_from": plan.initialised_from,
        "next_7_days": {"rain_mm": _sum([x.rain_mm for x in upcoming]),
                        "crop_water_use_mm": _sum([x.etc_mm for x in upcoming])},
        "irrigation_method": args.irrigation_method,
        "next_irrigation": plan.next_irrigation,
        "rain_deferrals": plan.deferrals[:3],
        "daily": [{"date": x.date, "rain_mm": x.rain_mm, "crop_water_use_mm": x.etc_mm,
                   "depletion_pct": x.depletion_pct, "action": x.action or "-"} for x in upcoming],
    }
    if args.irrigation_method in IRRIGATION_EFFICIENCY:
        data["application_efficiency_pct"] = int(100 * IRRIGATION_EFFICIENCY[args.irrigation_method])
    if plan.deferrals and IRRIGATION_EFFICIENCY.get(args.irrigation_method):
        saved = plan.deferrals[0]["deficit_mm"] / IRRIGATION_EFFICIENCY[args.irrigation_method]
        data["water_saved_by_waiting_for_rain_litres_per_acre"] = round(saved * LITRES_PER_MM_PER_ACRE, -2)
    if args.area_acres:
        data["field_size_acres"] = args.area_acres
        if plan.next_irrigation and plan.next_irrigation.get("litres_per_acre"):
            plan.next_irrigation["litres_for_field"] = round(plan.next_irrigation["litres_per_acre"] * args.area_acres, -2)
        if "water_saved_by_waiting_for_rain_litres_per_acre" in data:
            data["water_saved_for_field_litres"] = round(
                data["water_saved_by_waiting_for_rain_litres_per_acre"] * args.area_acres, -2)
    if crop.ponded:
        notes.append("Paddy: follow alternate wetting and drying (re-flood when water in a field tube falls 15 cm "
                     "below the surface); keep 5 cm standing water from flowering to grain filling.")
    if args.irrigation_method == "rainfed":
        notes.append("Rainfed field: no irrigation scheduled; use mulching and interculture to conserve moisture.")

    nxt = plan.next_irrigation
    in_days = (date.fromisoformat(nxt["date"]) - date.fromisoformat(today.date)).days if nxt else None
    hold_first = bool(plan.deferrals) and (nxt is None or plan.deferrals[0]["date"] < nxt["date"])
    stressed = today.depletion_mm > today.raw_mm
    if stressed:
        level = "moderate" if hold_first else "high"  # stressed, but rain is about to refill the soil
    elif in_days is not None and in_days <= 3:
        level = "moderate"
    else:
        level = "low"
    parts = []
    if hold_first:
        d0 = plan.deferrals[0]
        parts.append(f"Hold irrigation: {d0['rain_next_2_days_mm']} mm rain expected after {nice_date(d0['date'])}")
    if nxt:
        gross = f" ({nxt['gross_mm']} mm applied by {args.irrigation_method})" if nxt.get("gross_mm") else ""
        parts.append(f"{'next irrigation' if hold_first else 'Irrigate'} on {nice_date(nxt['date'])}: {nxt['net_mm']} mm net{gross}")
    if not parts:
        parts.append(f"No irrigation needed in the forecast window; root zone {today.depletion_pct}% depleted")
    headline = "; ".join(parts)
    chart = {"days": [{"date": x.date, "depletion_pct": x.depletion_pct,
                       "raw_pct": int(round(100 * x.raw_mm / x.taw_mm)) if x.taw_mm else None,
                       "rain_mm": x.rain_mm, "forecast": x.forecast, "action": x.action} for x in plan.days]}
    return ToolOutput(data, sources, notes, level, headline, chart)


# ===================================================================================== spraying
async def get_spray_windows(ctx: ToolContext, args: SprayArgs) -> ToolOutput:
    fc = await _forecast(ctx, args.lat, args.lon)
    h = fc.hourly
    res = spray.spray_windows(h.times, h.col("temperature_2m"), h.col("relative_humidity_2m"), h.col("wind_speed_10m"),
                              h.col("wind_gusts_10m"), h.col("precipitation"), h.col("precipitation_probability"),
                              h.col("is_day"), fc.now_hour_index, args.hours)
    windows = res["windows"]
    data = {"hours_scanned": args.hours, "good_windows": windows, "good_hours": res["good_hours"],
            "marginal_daylight_hours": res["marginal_hours"], "main_blockers": res["main_blockers"],
            "rules": "Delta T 2-8 C, wind 3-15 km/h, no rain within 3 h, temperature <= 30 C, daylight"}
    if windows:
        best = windows[0]
        headline = f"Next good window {nice_date(best['start'])} {best['start'][11:16]}-{best['end'][11:16]} ({best['hours']} h)"
        level = "low"
    else:
        headline = "No good spray window in the scan period"
        level = "moderate"
    notes = ["Always follow the product label for dose, re-entry and pre-harvest intervals."]
    return ToolOutput(data, [_forecast_source(fc)], notes, level, headline, chart={"hourly": res["hourly"]})


# ===================================================================================== crop stress
async def get_crop_risk(ctx: ToolContext, args: CropArgs) -> ToolOutput:
    try:
        crop = get_crop(args.crop)
    except KeyError as exc:
        raise ToolError(str(exc), hint=f"Use one of: {', '.join(CROPS)}") from None
    fc, (clim, clim_src, _) = await asyncio.gather(_forecast(ctx, args.lat, args.lon),
                                                   _climatology(ctx, args.lat, args.lon))
    d, t0 = fc.daily, fc.today_index
    today = date.fromisoformat(d.times[t0])
    st_now = crop_stage(crop, args.sowing_date, today)
    hilly = (fc.elevation_m or 0) >= HILLY_ELEVATION_M

    heat, cold, rain_events = [], [], []
    for i in range(t0, min(t0 + 7, len(d))):
        day = date.fromisoformat(d.times[i])
        stage = crop_stage(crop, args.sowing_date, day).stage
        if stage in ("pre_sowing", "harvest_ready"):
            continue
        tmax, tmin, rain = d.col("temperature_2m_max")[i], d.col("temperature_2m_min")[i], d.col("precipitation_sum")[i]
        ntx, ntn = clim.normal_temperature(day) if clim else (None, None)
        if tmax is not None and tmax >= crop.heat_c:
            heat.append({"date": d.times[i], "tmax_c": _r(tmax), "crop_threshold_c": crop.heat_c,
                         "critical_stage": stage == "mid_season",
                         "imd": stress.imd_heat_wave(tmax, ntx, hilly)})
        if tmin is not None and tmin <= crop.cold_c:
            cold.append({"date": d.times[i], "tmin_c": _r(tmin), "crop_threshold_c": crop.cold_c,
                         "frost_risk": tmin <= 2, "imd": stress.imd_cold_wave(tmin, ntn, hilly)})
        cat = stress.imd_rain_category(rain)
        if cat:
            rain_events.append({"date": d.times[i], "rain_mm": _r(rain), "imd_category": cat})

    rain = d.col("precipitation_sum")
    wettest_3d = max((_sum(rain[i:i + 3]) for i in range(t0, t0 + 5)), default=0.0)
    h = fc.hourly
    start, stop = fc.now_hour_index, min(fc.now_hour_index + 24 * 7, len(h))
    times, temp, rh = h.times[start:stop], h.col("temperature_2m")[start:stop], h.col("relative_humidity_2m")[start:stop]
    diseases = stress.disease_pressure(crop, times, temp, rh) if st_now.stage not in ("pre_sowing", "harvest_ready") else []
    late_blight = stress.hutton_periods(times, temp, rh) if crop.late_blight_model else []
    worker = [x for x in stress.daily_wet_bulb_max(times, temp, rh) if x["level"]]

    levels = ["low"]
    if heat:
        levels.append("high" if any(x["critical_stage"] for x in heat) else "moderate")
    if any(x["imd"] and x["imd"].startswith("severe") for x in heat + cold):
        levels.append("severe")
    if cold:
        levels.append("high" if any(x["frost_risk"] for x in cold) else "moderate")
    if late_blight:
        levels.append("high")
    levels += [x["risk"] for x in diseases]
    if rain_events:
        levels.append("severe" if any(e["imd_category"] != "heavy" for e in rain_events) else "high")
    if wettest_3d >= 100 and not crop.ponded:
        levels.append("high")
    if worker:
        levels.append("high" if any(x["level"] == "danger" for x in worker) else "moderate")
    level = worst_level(*levels)

    data: dict[str, Any] = {
        "crop": crop.name, "stage_now": st_now.label, "days_after_sowing": st_now.days_after_sowing,
        "thresholds": {"heat_stress_tmax_c": crop.heat_c, "cold_stress_tmin_c": crop.cold_c},
        "heat_stress_days": heat, "cold_stress_days": cold, "heavy_rain_days": rain_events,
        "wettest_3_day_rain_mm": wettest_3d, "disease_weather": diseases, "worker_humid_heat": worker,
    }
    if crop.late_blight_model:
        data["late_blight_hutton_periods"] = late_blight
    issues = []
    if heat:
        issues.append(f"heat stress on {len(heat)} day(s) (Tmax >= {crop.heat_c:g} C)")
    if cold:
        issues.append(f"cold stress on {len(cold)} day(s)")
    if late_blight:
        issues.append(f"late blight weather (Hutton) on {nice_date(late_blight[0]['date'])}")
    issues += [f"{x['disease']} weather {x['risk']}" for x in diseases if x["risk"] != "low"]
    if rain_events:
        issues.append(f"{rain_events[0]['imd_category']} rain on {nice_date(rain_events[0]['date'])}")
    if worker:
        issues.append("humid heat for field work")
    notes = ["Disease entries mean the weather favours infection; scout the field before any spray.",
             "Thresholds are crop-level references; varieties differ."]
    sources = [_forecast_source(fc)] + ([clim_src] if clim_src else [])
    summary = "; ".join(issues[:3])
    return ToolOutput(data, sources, notes, level, summary[:1].upper() + summary[1:] if summary else "No major weather stress in 7 days")


# ===================================================================================== flood
async def get_flood_risk(ctx: ToolContext, args: LatLon) -> ToolOutput:
    try:
        cell, fetched, n_cells = await open_meteo.fetch_flood_forecast(ctx.dc, args.lat, args.lon)
        history, hist_fetched = await open_meteo.fetch_flood_history(ctx.dc, cell.lat, cell.lon)
    except SourceError as exc:
        raise _wrap(exc) from None
    s = cell.daily
    today = date.today().isoformat()
    t0 = next((i for i, t in enumerate(s.times) if t >= today), 0)
    median = s.col("river_discharge_median") if "river_discharge_median" in s.values else s.col("river_discharge")
    q_max, q_now = s.col("river_discharge_max"), s.col("river_discharge")
    future = [(s.times[i], median[i], q_max[i]) for i in range(t0, len(s)) if median[i] is not None]
    if not future:
        raise ToolError("No river discharge forecast for this area.", retryable=False)
    peak_day, peak_median, _ = max(future, key=lambda x: x[1])
    peak_max = max((x[2] for x in future if x[2] is not None), default=None)
    maxima = hydrology.annual_maxima(history.times, history.col("river_discharge"))
    levels = hydrology.gumbel_return_levels(maxima)
    verdict = hydrology.classify_flood(peak_median, peak_max, levels)
    distance = haversine_km(args.lat, args.lon, cell.lat, cell.lon)

    hist_vals = [v for v in history.col("river_discharge") if v is not None]
    notes = ["GloFAS river discharge on a ~5 km grid: indicates flooding of the nearby main river channel, "
             "not field-level waterlogging (see rainfall analysis for that)."]
    if hist_vals and statistics.fmean(hist_vals) < 5:
        verdict = {"level": "low", "reason": "no large river within ~5 km (long-term mean discharge below 5 m3/s)"}
    data = {
        "river_cell": {"lat": round(cell.lat, 3), "lon": round(cell.lon, 3), "distance_km": round(distance, 1),
                       "cells_compared": n_cells},
        "discharge_today_m3s": _i(q_now[t0] if t0 < len(q_now) else None),
        "forecast_peak": {"date": peak_day, "median_m3s": _i(peak_median), "ensemble_max_m3s": _i(peak_max)},
        "return_levels_m3s": {f"{k}_year": _i(v) for k, v in (levels or {}).items()},
        "years_of_record": len(maxima), "alert_level": verdict["level"], "reason": verdict["reason"],
    }
    chart = {"days": [{"date": s.times[i], "median": median[i], "p25": s.col("river_discharge_p25")[i],
                       "p75": s.col("river_discharge_p75")[i], "max": q_max[i]} for i in range(len(s))],
             "return_levels": data["return_levels_m3s"]}
    sources = [fetched.provenance("GloFAS v4 river discharge (Copernicus EMS) via Open-Meteo Flood API"),
               hist_fetched.provenance("GloFAS reanalysis 1984-present via Open-Meteo Flood API")]
    headline = f"{verdict['level'].capitalize()}: peak {data['forecast_peak']['median_m3s']} m³/s on {nice_date(peak_day)}"
    return ToolOutput(data, sources, notes, verdict["level"], headline, chart)


# ===================================================================================== mandi prices
async def get_mandi_prices(ctx: ToolContext, args: MandiArgs, default_place: dict[str, Any] | None = None) -> ToolOutput:
    state = args.state or (default_place or {}).get("state")
    district = args.district or (default_place or {}).get("district")
    if not state:
        raise ToolError("State is unknown for this farm.", hint="Pass the Indian state name explicitly.")
    try:
        res = await mandi.fetch_prices(ctx.dc, ctx.settings.data_gov_in_api_key, state, args.commodity,
                                       sample_key=ctx.settings.using_sample_mandi_key)
    except SourceError as exc:
        raise _wrap(exc) from None
    if not res.records:
        hint = "Try another commodity spelling"
        if res.available_commodities:
            hint += f"; commodities reported in {state} today: {', '.join(res.available_commodities[:25])}"
        raise ToolError(f"No mandi price records for '{args.commodity}' in {state} in today's Agmarknet feed.", hint=hint)
    ctx.db.upsert_mandi_prices(res.records)

    by_market: dict[str, list[dict]] = {}
    for r in res.records:
        if r["modal_price"] is not None:
            by_market.setdefault(r["market"], []).append(r)
    markets = []
    for name, recs in by_market.items():
        modal = statistics.median(r["modal_price"] for r in recs)
        markets.append({"market": name, "district": recs[0]["district"], "commodity": recs[0]["commodity"],
                        "modal_price": round(modal),
                        "min_price": min((r["min_price"] for r in recs if r["min_price"] is not None), default=None),
                        "max_price": max((r["max_price"] for r in recs if r["max_price"] is not None), default=None),
                        "varieties": sorted({r["variety"] for r in recs})[:4], "arrival_date": recs[0]["arrival_date"]})
    markets.sort(key=lambda m: -m["modal_price"])
    local = [m for m in markets if district and mandi.same_place(m["district"], district)]
    modals = [m["modal_price"] for m in markets]
    data: dict[str, Any] = {
        "commodity": ", ".join(res.matched_names), "state": state, "unit": "Rs per quintal",
        "latest_arrival_date": max(r["arrival_date"] for r in res.records),
        "markets_reporting": len(markets),
        "state_modal_median": round(statistics.median(modals)) if modals else None,
        "best_markets": markets[:6],
        "district_markets": local[:4],
    }
    if local and markets:
        gap = markets[0]["modal_price"] - local[0]["modal_price"]
        data["best_vs_local_gap"] = {"amount": gap, "pct": round(100 * gap / local[0]["modal_price"]) if local[0]["modal_price"] else None,
                                     "best_market": markets[0]["market"], "local_market": local[0]["market"]}
        history = ctx.db.mandi_history(local[0]["commodity"], local[0]["market"])
        if len(history) >= 2:
            data["local_price_history"] = history[:10]
    notes = ["Agmarknet prices are reported by APMC markets; transport cost to a distant market is not included."]
    if ctx.settings.using_sample_mandi_key:
        notes.append("Using data.gov.in's public sample key (max 10 records): coverage is partial.")
    top = markets[0]
    headline = f"Best: Rs {top['modal_price']}/q at {top['market']}"
    if local:
        headline = f"Local: Rs {local[0]['modal_price']}/q at {local[0]['market']}; " + headline
    return ToolOutput(data, [res.fetched.provenance("Agmarknet daily mandi prices via data.gov.in (OGD India)")]
                      if res.fetched else [], notes, "info", headline)


# ===================================================================================== disasters
async def get_disaster_alerts(ctx: ToolContext, args: AlertArgs) -> ToolOutput:
    try:
        events, fetched = await gdacs.fetch_events(ctx.dc)
    except SourceError as exc:
        raise _wrap(exc) from None
    nearby, regional = [], []
    for e in events:
        dist = round(haversine_km(args.lat, args.lon, e["lat"], e["lon"]))
        if dist <= 2000:
            regional.append({**e, "distance_km": dist})  # map layer only
        if dist <= args.radius_km:
            nearby.append({**{k: e[k] for k in ("type_name", "name", "alert_level", "country", "from", "to", "severity", "url")},
                           "distance_km": dist})
    nearby.sort(key=lambda e: e["distance_km"])
    alerts = {e.get("alert_level") for e in nearby}
    level = "severe" if "Red" in alerts else "high" if "Orange" in alerts else "moderate" if nearby else "low"
    data = {"radius_km": args.radius_km, "nearby_events": nearby[:8], "nearby_count": len(nearby),
            "global_orange_or_red_events": sum(1 for e in events if e.get("alert_level") in ("Orange", "Red"))}
    headline = (f"{nearby[0]['type_name']} ({nearby[0]['alert_level']}) {nearby[0]['distance_km']} km away"
                if nearby else f"No GDACS events within {args.radius_km:.0f} km")
    return ToolOutput(data, [fetched.provenance("GDACS (UN OCHA / EC JRC) disaster alerts")], [], level, headline,
                      chart={"events": regional})


# ===================================================================================== air quality
AQI_BANDS = [(50, "good"), (100, "moderate"), (150, "unhealthy for sensitive groups"), (200, "unhealthy"),
             (300, "very unhealthy"), (10_000, "hazardous")]


def aqi_category(aqi: float | None) -> str | None:
    if aqi is None:
        return None
    return next(label for limit, label in AQI_BANDS if aqi <= limit)


async def get_air_quality(ctx: ToolContext, args: LatLon) -> ToolOutput:
    try:
        current, hourly, fetched = await open_meteo.fetch_air_quality(ctx.dc, args.lat, args.lon)
    except SourceError as exc:
        raise _wrap(exc) from None
    aqi_series = [v for v in hourly.col("us_aqi") if v is not None]
    pm25_series = [v for v in hourly.col("pm2_5") if v is not None]
    aqi_now = current.get("us_aqi") if current.get("us_aqi") is not None else (aqi_series[0] if aqi_series else None)
    data = {
        "us_aqi_now": _i(aqi_now), "category_now": aqi_category(aqi_now),
        "pm2_5_ugm3": _r(current.get("pm2_5")), "pm10_ugm3": _r(current.get("pm10")),
        "ozone_ugm3": _r(current.get("ozone")), "no2_ugm3": _r(current.get("nitrogen_dioxide")),
        "max_us_aqi_next_72h": _i(max(aqi_series)) if aqi_series else None,
        "max_pm2_5_next_72h_ugm3": _r(max(pm25_series)) if pm25_series else None,
    }
    worst = data["max_us_aqi_next_72h"] or aqi_now or 0
    level = "high" if worst > 200 else "moderate" if worst > 100 else "low"
    notes = ["CAMS atmospheric-composition forecast (model), not a ground monitor."]
    if (data["pm2_5_ugm3"] or 0) > 60 and date.today().month in (10, 11):
        notes.append("Post-harvest season: crop-residue burning worsens PM2.5; in-situ residue management "
                     "(e.g. bio-decomposer, Happy Seeder) avoids burning.")
    return ToolOutput(data, [fetched.provenance("Copernicus CAMS air quality via Open-Meteo Air Quality API")], notes,
                      level, f"AQI {data['us_aqi_now']} ({data['category_now']})")


# ===================================================================================== field diary
async def get_field_diary(ctx: ToolContext, args: DiaryArgs, farm: Any | None) -> ToolOutput:
    if farm is None:
        raise ToolError("The field diary needs this conversation's farm profile.")
    summary = diary_summary(ctx.db, farm, args.weeks)
    data, rows = summary["data"], summary["rows"]
    notes = ["Photo findings are AI visual estimates from the farmer's own photos, not a diagnosis: confirm in the field."]
    if not rows:
        notes.append("No check-ins yet: suggest the farmer adds a weekly crop photo in the Field diary tab.")
        headline, level = "No check-ins yet: add this week's crop photo", "info"
    else:
        latest = rows[0]
        findings = latest["analysis"] or {}
        likely = [s for s in findings.get("symptoms", []) if s.get("likelihood") == "high"]
        level = "moderate" if likely else "info"
        parts = [f"Last check-in {nice_date(latest['created_at'])}"]
        if findings.get("observed_stage") and findings["observed_stage"] != "unsure":
            parts.append(f"{findings['observed_stage']}, about {findings.get('canopy_cover_pct')}% canopy")
        if likely:
            parts.append(f"possible {likely[0]['name']}")
        if data.get("days_since_last_check_in", 0) >= 7:
            parts.append("new photo due")
        headline = "; ".join(parts)
    source = {"name": "Farmer's field diary (photos and observations)", "url": "/api/diary",
              "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "cached": False, "stale": False}
    chart = {"entries": [{"date": r["created_at"][:10], "photo_url": f"/api/diary/photo/{r['token']}" if r["has_photo"] else None,
                          "status": r["analysis_status"], "summary": (r["analysis"] or {}).get("summary"),
                          "stage": (r["analysis"] or {}).get("observed_stage"),
                          "canopy": (r["analysis"] or {}).get("canopy_cover_pct"),
                          "symptoms": (r["analysis"] or {}).get("symptoms", []), "observations": r["observations"]}
                         for r in rows]}
    return ToolOutput(data, [source], notes, level, headline, chart)


# ===================================================================================== satellite
def ndvi_class(v: float) -> str:
    if v < 0.2:
        return "bare soil or very sparse cover"
    if v < 0.4:
        return "low vegetation / early growth"
    if v < 0.6:
        return "moderate canopy"
    return "dense, vigorous canopy"


async def get_satellite_crop_health(ctx: ToolContext, args: LatLon) -> ToolOutput:
    if not ctx.settings.satellite_enabled:
        raise ToolError("Satellite analysis is disabled on this server.")
    try:
        res = await asyncio.wait_for(sentinel2.ndvi_series(ctx.dc, args.lat, args.lon), timeout=75)
    except SourceError as exc:
        raise _wrap(exc) from None
    except asyncio.TimeoutError:
        raise ToolError("Satellite imagery took too long to read.", retryable=True,
                        hint="Answer without satellite data and mention it timed out.") from None
    recent = res["recent"]
    if not recent:
        raise ToolError(f"No cloud-free Sentinel-2 view of this farm in the last 120 days "
                        f"({res['scenes_considered']} scenes checked).", hint="Likely persistent cloud cover.")
    latest = recent[-1]
    data: dict[str, Any] = {"latest": {"date": latest["date"], "ndvi": latest["ndvi_mean"],
                                       "class": ndvi_class(latest["ndvi_mean"])},
                            "observations": [{"date": r["date"], "ndvi": r["ndvi_mean"]} for r in recent],
                            "window": "150 m x 150 m around the farm point, clouds masked"}
    level = "low"
    if len(recent) >= 2:
        change = round(latest["ndvi_mean"] - recent[-2]["ndvi_mean"], 2)
        data["change_since_previous"] = change
        if change <= -0.1:
            level = "moderate"
    if res["last_year"]:
        ly = res["last_year"]
        data["same_period_last_year"] = {"date": ly["date"], "ndvi": ly["ndvi_mean"],
                                         "difference": round(latest["ndvi_mean"] - ly["ndvi_mean"], 2)}
    notes = ["A sharp NDVI drop can mean harvest, water stress, pest damage or flooding: verify on the ground.",
             "Sentinel-2 revisits every ~5 days; cloudy scenes are skipped."]
    return ToolOutput(data, [res["fetched"].provenance("Copernicus Sentinel-2 L2A via Earth Search STAC (AWS Open Data)")],
                      notes, level, f"NDVI {latest['ndvi_mean']} on {nice_date(latest['date'])} ({ndvi_class(latest['ndvi_mean'])})",
                      chart={"observations": recent, "last_year": res["last_year"]})
