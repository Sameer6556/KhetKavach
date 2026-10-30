"""Weather-driven crop and farm-worker stress: heat/cold, IMD heavy-rain and heat/cold-wave criteria,
late-blight (Hutton criteria) and crop-specific disease-favourable weather.

References
----------
* IMD daily rainfall categories: heavy 64.5-115.5 mm, very heavy 115.6-204.4 mm, extremely heavy >= 204.5 mm.
* IMD heat wave (plains): Tmax >= 40 C and departure >= 4.5 C (severe >= 6.5 C), or Tmax >= 45 C (severe >= 47 C);
  hills: Tmax >= 30 C. Cold wave (plains): Tmin <= 10 C and departure <= -4.5 C (severe <= -6.5 C), or Tmin <= 4 C
  (severe <= 2 C).
* Hutton criteria (AHDB, 2017): two consecutive days with Tmin >= 10 C and >= 6 h of RH >= 90% -> late blight risk.
* Humid-heat for outdoor work: wet-bulb >= 28 C caution, >= 31 C danger (approaching physiological limits).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from .crops import Crop
from .spray import wet_bulb_stull


def imd_rain_category(mm: float | None) -> str | None:
    if mm is None:
        return None
    if mm >= 204.5:
        return "extremely heavy"
    if mm >= 115.6:
        return "very heavy"
    if mm >= 64.5:
        return "heavy"
    return None


def imd_heat_wave(tmax: float, normal_tmax: float | None, hilly: bool) -> str | None:
    departure = None if normal_tmax is None else tmax - normal_tmax
    if hilly:
        if tmax >= 30 and departure is not None and departure >= 6.5:
            return "severe heat wave"
        if tmax >= 30 and departure is not None and departure >= 4.5:
            return "heat wave"
        return None
    if tmax >= 47:
        return "severe heat wave"
    if tmax >= 45:
        return "heat wave"
    if tmax >= 40 and departure is not None:
        if departure >= 6.5:
            return "severe heat wave"
        if departure >= 4.5:
            return "heat wave"
    return None


def imd_cold_wave(tmin: float, normal_tmin: float | None, hilly: bool) -> str | None:
    departure = None if normal_tmin is None else tmin - normal_tmin
    if not hilly:
        if tmin <= 2:
            return "severe cold wave"
        if tmin <= 4:
            return "cold wave"
    limit = 0 if hilly else 10
    if tmin <= limit and departure is not None:
        if departure <= -6.5:
            return "severe cold wave"
        if departure <= -4.5:
            return "cold wave"
    return None


def group_hours_by_day(times: list[str], *cols: list) -> "OrderedDict[str, list[tuple]]":
    days: OrderedDict[str, list[tuple]] = OrderedDict()
    for i, t in enumerate(times):
        days.setdefault(t[:10], []).append(tuple(c[i] for c in cols))
    return days


def hutton_periods(times: list[str], temp: list, rh: list) -> list[dict[str, Any]]:
    """Dates that complete a Hutton period (late-blight infection risk for potato/tomato)."""
    day_flags = []
    for day, rows in group_hours_by_day(times, temp, rh).items():
        valid = [(t, h) for t, h in rows if t is not None and h is not None]
        if len(valid) < 18:  # need (almost) a full day of data
            continue
        tmin = min(t for t, _ in valid)
        humid_hours = sum(1 for _, h in valid if h >= 90)
        day_flags.append((day, tmin >= 10 and humid_hours >= 6, round(tmin, 1), humid_hours))
    periods = []
    for (d1, ok1, _, h1), (d2, ok2, tmin2, h2) in zip(day_flags, day_flags[1:]):
        if ok1 and ok2:
            periods.append({"date": d2, "tmin_c": tmin2, "humid_hours": h2, "previous_day_humid_hours": h1})
    return periods


def disease_pressure(crop: Crop, times: list[str], temp: list, rh: list) -> list[dict[str, Any]]:
    """For each crop disease, the days whose weather favours infection (leaf-wetness proxy: RH >= 90%)."""
    by_day = group_hours_by_day(times, temp, rh)
    results = []
    for window in crop.diseases:
        favourable = []
        for day, rows in by_day.items():
            wet = sum(1 for t, h in rows if t is not None and h is not None and h >= 90 and window.t_min <= t <= window.t_max)
            if wet >= window.wet_hours:
                favourable.append({"date": day, "wet_hours": wet})
        level = "high" if len(favourable) >= 3 else "moderate" if favourable else "low"
        results.append({
            "disease": window.name, "risk": level, "favourable_days": favourable,
            "rule": f"{window.wet_hours}+ h/day at RH>=90% with {window.t_min:g}-{window.t_max:g} C",
        })
    return results


def daily_wet_bulb_max(times: list[str], temp: list, rh: list) -> list[dict[str, Any]]:
    out = []
    for day, rows in group_hours_by_day(times, temp, rh).items():
        vals = [wet_bulb_stull(t, h) for t, h in rows if t is not None and h is not None]
        if vals:
            peak = round(max(vals), 1)
            level = "danger" if peak >= 31 else "caution" if peak >= 28 else None
            out.append({"date": day, "wet_bulb_max_c": peak, "level": level})
    return out
