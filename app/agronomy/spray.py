"""Pesticide / foliar-spray timing from hourly weather.

Rules follow widely used spray-drift guidance (e.g. GRDC "Delta T" fact sheet; state agricultural
university advisories): Delta T 2-8 C ideal, wind 3-15 km/h, no rain for ~3 hours after spraying,
avoid heat above 30-35 C, spray only in daylight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def wet_bulb_stull(t_c: float, rh_pct: float) -> float:
    """Wet-bulb temperature (C) from air temperature and RH (Stull 2011, J. Appl. Meteor. Climatol.).

    Valid for RH 5-99% and -20..50 C; accurate to about +/-1 C.
    """
    rh = min(max(rh_pct, 5.0), 99.0)
    return (t_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
            + math.atan(t_c + rh) - math.atan(rh - 1.676331)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def delta_t(t_c: float, rh_pct: float) -> float:
    return t_c - wet_bulb_stull(t_c, rh_pct)


@dataclass
class HourCheck:
    time: str
    status: str  # good | marginal | unsuitable
    delta_t: float
    wind_kmh: float
    temp_c: float
    reasons: list[str] = field(default_factory=list)


def assess_hour(time: str, temp: float, rh: float, wind: float, gust: float | None, rain_next3_mm: float,
                rain_prob_next3: float | None, is_day: bool) -> HourCheck:
    dt = round(delta_t(temp, rh), 1)
    bad: list[str] = []
    meh: list[str] = []
    if not is_day:
        bad.append("night")
    if rain_next3_mm >= 0.2 or (rain_prob_next3 is not None and rain_prob_next3 >= 50):
        bad.append("rain expected within 3 h (wash-off)")
    if wind > 20 or (gust is not None and gust > 30):
        bad.append(f"too windy ({wind:.0f} km/h, drift)")
    elif wind > 15:
        meh.append(f"breezy ({wind:.0f} km/h)")
    elif wind < 3:
        meh.append("calm air (inversion / drift risk)")
    if dt > 10:
        bad.append(f"Delta T {dt} C (droplets evaporate)")
    elif dt > 8:
        meh.append(f"Delta T {dt} C")
    elif dt < 2:
        meh.append(f"Delta T {dt} C (very humid, slow drying)")
    if temp > 35:
        bad.append(f"hot ({temp:.0f} C)")
    elif temp > 30:
        meh.append(f"warm ({temp:.0f} C)")
    status = "unsuitable" if bad else ("marginal" if meh else "good")
    return HourCheck(time, status, dt, round(wind, 1), round(temp, 1), bad or meh)


def spray_windows(times: list[str], temp: list, rh: list, wind: list, gust: list, precip: list,
                  precip_prob: list, is_day: list, start: int, hours: int = 72) -> dict:
    """Score each of the next `hours` hours and group consecutive good hours into windows."""
    checks: list[HourCheck] = []
    end = min(start + hours, len(times))
    for i in range(start, end):
        if temp[i] is None or rh[i] is None or wind[i] is None:
            continue
        nxt = slice(i, min(i + 4, len(times)))
        rain3 = sum(p or 0.0 for p in precip[nxt])
        probs = [p for p in precip_prob[nxt] if p is not None]
        checks.append(assess_hour(times[i], temp[i], rh[i], wind[i], gust[i] if gust else None, rain3,
                                  max(probs) if probs else None, bool(is_day[i]) if is_day[i] is not None else True))

    windows, run = [], []
    for c in checks + [None]:
        if c is not None and c.status == "good":
            run.append(c)
            continue
        if len(run) >= 2:
            windows.append({
                "start": run[0].time, "end": run[-1].time, "hours": len(run),
                "avg_wind_kmh": round(sum(h.wind_kmh for h in run) / len(run), 1),
                "avg_delta_t_c": round(sum(h.delta_t for h in run) / len(run), 1),
                "max_temp_c": max(h.temp_c for h in run),
            })
        run = []
    daylight = [c for c in checks if "night" not in c.reasons]
    blockers: dict[str, int] = {}
    for c in daylight:
        if c.status == "unsuitable":
            key = c.reasons[0].split(" (")[0]
            blockers[key] = blockers.get(key, 0) + 1
    return {
        "windows": windows[:8],
        "daylight_hours_checked": len(daylight),
        "good_hours": sum(c.status == "good" for c in checks),
        "marginal_hours": sum(c.status == "marginal" for c in daylight),
        "main_blockers": dict(sorted(blockers.items(), key=lambda kv: -kv[1])[:3]),
        "hourly": [{"time": c.time, "status": c.status, "delta_t": c.delta_t, "wind_kmh": c.wind_kmh} for c in checks],
    }
