"""River flood thresholds from GloFAS reanalysis, mirroring GloFAS/EFAS alert classes.

Return levels for 2-, 5- and 20-year floods are estimated by fitting a Gumbel (EV1) distribution to
annual maximum daily discharge with the method of moments (Chow, Maidment & Mays, Applied Hydrology):
    beta = sqrt(6) * s / pi,   u = mean - 0.5772 * beta,   Q_T = u - beta * ln(-ln(1 - 1/T)).
"""

from __future__ import annotations

import math
import statistics
from typing import Any

EULER_GAMMA = 0.5772156649


def annual_maxima(times: list[str], discharge: list, min_days: int = 300) -> list[float]:
    by_year: dict[str, list[float]] = {}
    for t, q in zip(times, discharge):
        if q is not None:
            by_year.setdefault(t[:4], []).append(float(q))
    return [max(vals) for _, vals in sorted(by_year.items()) if len(vals) >= min_days]


def gumbel_return_levels(maxima: list[float], periods: tuple[int, ...] = (2, 5, 20)) -> dict[int, float] | None:
    if len(maxima) < 10:
        return None
    mean = statistics.fmean(maxima)
    sd = statistics.stdev(maxima)
    beta = math.sqrt(6) * sd / math.pi
    u = mean - EULER_GAMMA * beta
    return {t: round(u - beta * math.log(-math.log(1 - 1 / t)), 2) for t in periods}


def classify_flood(peak_median: float | None, peak_max: float | None, levels: dict[int, float] | None) -> dict[str, Any]:
    """Alert level for the forecast peak: ensemble median vs return levels, ensemble max for early watch."""
    if peak_median is None or not levels:
        return {"level": "unknown", "reason": "insufficient discharge data"}
    if peak_median >= levels[20]:
        return {"level": "severe", "reason": "ensemble-median peak exceeds the 20-year flood level"}
    if peak_median >= levels[5]:
        return {"level": "high", "reason": "ensemble-median peak exceeds the 5-year flood level"}
    if peak_median >= levels[2]:
        return {"level": "moderate", "reason": "ensemble-median peak exceeds the 2-year flood level"}
    if peak_max is not None and peak_max >= levels[2]:
        return {"level": "watch", "reason": "some ensemble members exceed the 2-year flood level"}
    return {"level": "low", "reason": "forecast discharge stays below the 2-year flood level"}
