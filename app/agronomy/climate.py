"""Climate normals and anomalies against the WMO 1991-2020 baseline (ERA5 reanalysis).

IMD seasonal/period rainfall departure categories: Large Excess >= +60%, Excess +20..+59%,
Normal -19..+19%, Deficient -20..-59%, Large Deficient -60..-99%, No Rain -100%.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

SMOOTH_DAYS = 7  # +/- days pooled around each calendar day for daily temperature normals
DRY_PERIOD_MM = 5.0  # below this normal, % departures are meaningless


class Climatology:
    """Index of a daily ERA5 series (1991-2020) for fast normal lookups."""

    def __init__(self, times: list[str], tmax: list, tmin: list, precip: list):
        self._tmax = {t: v for t, v in zip(times, tmax) if v is not None}
        self._tmin = {t: v for t, v in zip(times, tmin) if v is not None}
        self._precip = {t: v for t, v in zip(times, precip) if v is not None}
        years = sorted({int(t[:4]) for t in times}) if times else []
        self.years = years

    @staticmethod
    def _same_day(year: int, month: int, day: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:  # 29 Feb in a non-leap year
            return date(year, month, 28)

    def normal_temperature(self, target: date) -> tuple[float | None, float | None]:
        """Mean Tmax and Tmin for this calendar day (pooled +/- 7 days across all baseline years)."""
        hi, lo = [], []
        for y in self.years:
            centre = self._same_day(y, target.month, target.day)
            for k in range(-SMOOTH_DAYS, SMOOTH_DAYS + 1):
                key = (centre + timedelta(days=k)).isoformat()
                if key in self._tmax:
                    hi.append(self._tmax[key])
                if key in self._tmin:
                    lo.append(self._tmin[key])
        return (round(statistics.fmean(hi), 1) if hi else None, round(statistics.fmean(lo), 1) if lo else None)

    def normal_daily_rain(self, target: date) -> float | None:
        """Mean daily rainfall (mm/day) for this calendar day, pooled +/- 7 days across baseline years."""
        vals = []
        for y in self.years:
            centre = self._same_day(y, target.month, target.day)
            for k in range(-SMOOTH_DAYS, SMOOTH_DAYS + 1):
                key = (centre + timedelta(days=k)).isoformat()
                if key in self._precip:
                    vals.append(self._precip[key])
        return round(statistics.fmean(vals), 1) if vals else None

    def window_rain(self, start: date, n_days: int) -> dict[str, Any] | None:
        """Distribution of rainfall totals for the same n-day calendar window in each baseline year."""
        totals = []
        for y in self.years:
            d0 = self._same_day(y, start.month, start.day)
            keys = [(d0 + timedelta(days=k)).isoformat() for k in range(n_days)]
            if all(k in self._precip for k in keys):
                totals.append(sum(self._precip[k] for k in keys))
        if len(totals) < 10:
            return None
        totals.sort()
        q = statistics.quantiles(totals, n=10)
        return {"mean_mm": round(statistics.fmean(totals), 1), "p10_mm": round(q[0], 1),
                "median_mm": round(statistics.median(totals), 1), "p90_mm": round(q[-1], 1),
                "years": len(totals), "totals": totals}


def imd_category(actual_mm: float, normal_mm: float) -> tuple[str, float | None]:
    if normal_mm < DRY_PERIOD_MM:
        return ("climatologically dry period", None)
    dep = round(100 * (actual_mm - normal_mm) / normal_mm)
    if actual_mm <= 0.05:
        return ("no rain", -100.0)
    if dep >= 60:
        cat = "large excess"
    elif dep >= 20:
        cat = "excess"
    elif dep >= -19:
        cat = "normal"
    elif dep >= -59:
        cat = "deficient"
    else:
        cat = "large deficient"
    return (cat, float(dep))


def percentile_rank(value: float, sample: list[float]) -> int:
    if not sample:
        return 50
    below = sum(1 for s in sample if s < value)
    equal = sum(1 for s in sample if s == value)
    return int(round(100 * (below + 0.5 * equal) / len(sample)))


def rain_anomaly(clim: Climatology, start: date, n_days: int, actual_mm: float) -> dict[str, Any] | None:
    dist = clim.window_rain(start, n_days)
    if dist is None:
        return None
    category, departure = imd_category(actual_mm, dist["mean_mm"])
    return {
        "period": f"{start.isoformat()} to {(start + timedelta(days=n_days - 1)).isoformat()}",
        "days": n_days, "rain_mm": round(actual_mm, 1), "normal_mm": dist["mean_mm"],
        "departure_pct": departure, "imd_category": category,
        "percentile_vs_1991_2020": percentile_rank(actual_mm, dist["totals"]),
        "normal_range_p10_p90_mm": [dist["p10_mm"], dist["p90_mm"]],
    }
