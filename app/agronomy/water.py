"""Crop water requirement and irrigation scheduling (FAO-56 dual-step: Kc x ET0, root-zone water balance).

Reference: Allen, Pereira, Raes & Smith (1998), FAO Irrigation and Drainage Paper 56,
chapters 6 (Kc), 8 (water stress / soil water balance). Equation numbers below refer to FAO-56.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .crops import IRRIGATION_EFFICIENCY, Crop

LITRES_PER_MM_PER_ACRE = 4046.86  # 1 mm of water over 1 acre (4046.86 m^2)
SOAKING_RAIN_MM = 25.0  # a day this wet refills the root zone to field capacity
MIN_EFFECTIVE_RAIN_MM = 2.0  # light showers are mostly intercepted/evaporated
STAGE_LABELS = {
    "pre_sowing": "Not yet sown",
    "initial": "Initial (germination / establishment)",
    "development": "Crop development (vegetative growth)",
    "mid_season": "Mid-season (flowering / yield formation, most sensitive)",
    "late_season": "Late season (maturity / ripening)",
    "harvest_ready": "Season complete (harvest due or done)",
}

# FAO-56 Table 19 mid-range values for a loam, used only if SoilGrids is unreachable (flagged as assumed).
FALLBACK_FC, FALLBACK_WP = 0.29, 0.16


@dataclass
class StageInfo:
    stage: str
    label: str
    days_after_sowing: int
    kc: float
    root_depth_m: float
    progress_pct: int


def wind_10m_to_2m(u10_kmh: float) -> float:
    """FAO-56 eq. 47: convert wind speed at 10 m (km/h) to 2 m height (m/s)."""
    return (u10_kmh / 3.6) * 4.87 / math.log(67.8 * 10 - 5.42)


def adjust_kc(kc: float, u2_ms: float | None, rh_min: float | None, height_m: float) -> float:
    """FAO-56 eq. 62: climatic adjustment of Kc_mid / Kc_end for wind and humidity away from 2 m/s, 45%."""
    if u2_ms is None or rh_min is None:
        return kc
    u2 = min(max(u2_ms, 1.0), 6.0)
    rh = min(max(rh_min, 20.0), 80.0)
    h = min(max(height_m, 0.1), 10.0)
    return round(kc + (0.04 * (u2 - 2) - 0.004 * (rh - 45)) * (h / 3) ** 0.3, 3)


def crop_stage(crop: Crop, sowing: date, on: date, u2_ms: float | None = None, rh_min: float | None = None) -> StageInfo:
    """Growth stage, crop coefficient (FAO-56 eq. 66 linear interpolation) and rooting depth for a date."""
    das = (on - sowing).days
    l_ini, l_dev, l_mid, l_late = crop.stages
    kc_mid = adjust_kc(crop.kc_mid, u2_ms, rh_min, crop.height_m)
    kc_end = adjust_kc(crop.kc_end, u2_ms, rh_min, crop.height_m) if crop.kc_end >= 0.45 else crop.kc_end
    root_min = min(0.2, crop.root_max_m)

    if das < 0:
        stage, kc = "pre_sowing", 0.3
    elif das < l_ini:
        stage, kc = "initial", crop.kc_ini
    elif das < l_ini + l_dev:
        stage, kc = "development", crop.kc_ini + (das - l_ini) / l_dev * (kc_mid - crop.kc_ini)
    elif das < l_ini + l_dev + l_mid:
        stage, kc = "mid_season", kc_mid
    elif das <= crop.season_days:
        stage, kc = "late_season", kc_mid + (das - l_ini - l_dev - l_mid) / l_late * (kc_end - kc_mid)
    else:
        stage, kc = "harvest_ready", 0.3

    grow = min(max(das, 0) / (l_ini + l_dev), 1.0)
    root = root_min + (crop.root_max_m - root_min) * grow
    progress = int(round(100 * min(max(das, 0), crop.season_days) / crop.season_days))
    return StageInfo(stage, STAGE_LABELS[stage], das, round(kc, 3), round(root, 2), progress)


@dataclass
class DayBalance:
    date: str
    forecast: bool
    rain_mm: float
    et0_mm: float
    kc: float
    etc_mm: float
    depletion_mm: float
    taw_mm: float
    raw_mm: float
    depletion_pct: int
    action: str = ""  # "irrigate" | "hold_for_rain" | ""
    irrigation_net_mm: float = 0.0


@dataclass
class IrrigationPlan:
    method: str
    efficiency: float | None
    initialised_from: str
    soil_assumed: bool
    days: list[DayBalance] = field(default_factory=list)
    today: DayBalance | None = None
    next_irrigation: dict | None = None
    deferrals: list[dict] = field(default_factory=list)


def run_water_balance(
    crop: Crop,
    sowing: date,
    dates: list[str],
    rain: list[float | None],
    et0: list[float | None],
    today_index: int,
    theta_fc: float | None,
    theta_wp: float | None,
    method: str = "flood",
    last_irrigation: date | None = None,
    u2_ms: float | None = None,
    rh_min: float | None = None,
) -> IrrigationPlan:
    """Daily root-zone depletion from the last known refill to the end of the forecast.

    Depletion (Dr) is reset to 0 at the end of the most recent soaking rain (>= 25 mm) or the
    farmer's last irrigation, then advanced with FAO-56 eq. 85: Dr_i = Dr_{i-1} - Pe_i + ETc_i - I_i,
    where ETc is reduced by the water-stress coefficient Ks (eq. 84) once Dr exceeds RAW.
    For future days an irrigation is scheduled when Dr > RAW, unless forecast rain in the next two
    days would cover at least half the deficit (then the advice is to hold and save water).
    """
    soil_assumed = theta_fc is None or theta_wp is None or theta_fc <= theta_wp
    fc = FALLBACK_FC if soil_assumed else float(theta_fc)
    wp = FALLBACK_WP if soil_assumed else float(theta_wp)
    eff = IRRIGATION_EFFICIENCY.get(method)

    clean_rain = [float(r or 0.0) for r in rain]
    clean_et0 = [float(e or 0.0) for e in et0]

    start, depletion_frac, init_note = 0, 0.5, "assumed 50% depletion 30 days ago (no soaking rain since)"
    for i in range(min(today_index, len(dates) - 1), -1, -1):
        if clean_rain[i] >= SOAKING_RAIN_MM:
            start, depletion_frac = i, 0.0
            init_note = f"soaking rain of {clean_rain[i]:.0f} mm on {dates[i]}"
            break
    if last_irrigation is not None and last_irrigation.isoformat() in dates:
        idx = dates.index(last_irrigation.isoformat())
        if idx <= today_index and (depletion_frac > 0 or idx > start):
            start, depletion_frac = idx, 0.0
            init_note = f"farmer's last irrigation on {dates[idx]}"

    plan = IrrigationPlan(method=method, efficiency=eff, initialised_from=init_note, soil_assumed=soil_assumed)
    prev_taw = None
    depletion = 0.0
    for i in range(start, len(dates)):
        day = date.fromisoformat(dates[i])
        st = crop_stage(crop, sowing, day, u2_ms, rh_min)
        taw = 1000 * (fc - wp) * st.root_depth_m
        if prev_taw is None:
            depletion = depletion_frac * taw
        elif prev_taw > 0:
            depletion *= taw / prev_taw  # keep the depleted fraction when the root zone deepens
        prev_taw = taw

        if i == start and depletion_frac == 0.0:
            # The refill day itself ends at field capacity.
            plan.days.append(_day(dates[i], i > today_index, clean_rain[i], clean_et0[i], st.kc, 0.0, 0.0, taw, crop.p))
            if i == today_index:
                plan.today = plan.days[-1]
            continue

        p_adj = min(max(crop.p + 0.04 * (5 - st.kc * clean_et0[i]), 0.1), 0.8)
        raw = p_adj * taw
        ks = 1.0 if depletion <= raw else max((taw - depletion) / ((1 - p_adj) * taw), 0.0)
        etc = ks * st.kc * clean_et0[i]
        pe = clean_rain[i] if clean_rain[i] >= MIN_EFFECTIVE_RAIN_MM else 0.0
        depletion = min(max(depletion - pe + etc, 0.0), taw)
        record = _day(dates[i], i > today_index, clean_rain[i], clean_et0[i], st.kc, etc, depletion, taw, p_adj)

        if i > today_index and method != "rainfed" and st.stage not in ("pre_sowing", "harvest_ready") and depletion > raw:
            rain_next = sum(r for r in clean_rain[i + 1:i + 3] if r >= MIN_EFFECTIVE_RAIN_MM)
            if rain_next >= 0.5 * depletion and depletion < 0.9 * taw:
                record.action = "hold_for_rain"
                plan.deferrals.append({"date": dates[i], "deficit_mm": round(depletion, 1),
                                       "rain_next_2_days_mm": round(rain_next, 1)})
            else:
                record.action = "irrigate"
                record.irrigation_net_mm = round(depletion, 1)
                depletion = 0.0  # assume the advice is followed when projecting later days
        plan.days.append(record)
        if i == today_index:
            plan.today = record

    upcoming = [d for d in plan.days if d.forecast and d.action == "irrigate"]
    if upcoming:
        first = upcoming[0]
        gross = first.irrigation_net_mm / eff if eff else None
        plan.next_irrigation = {
            "date": first.date, "net_mm": first.irrigation_net_mm,
            "gross_mm": round(gross, 1) if gross else None,
            "litres_per_acre": round(gross * LITRES_PER_MM_PER_ACRE) if gross else None,
        }
    return plan


def _day(day: str, forecast: bool, rain: float, et0: float, kc: float, etc: float, depletion: float,
         taw: float, p: float) -> DayBalance:
    return DayBalance(
        date=day, forecast=forecast, rain_mm=round(rain, 1), et0_mm=round(et0, 2), kc=kc, etc_mm=round(etc, 2),
        depletion_mm=round(depletion, 1), taw_mm=round(taw, 1), raw_mm=round(p * taw, 1),
        depletion_pct=int(round(100 * depletion / taw)) if taw else 0,
    )
