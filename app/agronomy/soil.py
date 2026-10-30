"""Soil interpretation: USDA texture class, pH and organic-carbon classes, plant-available water.

* Texture: USDA NRCS soil texture triangle (12 classes).
* Organic carbon classes follow India's Soil Health Card scheme: low < 0.5%, medium 0.5-0.75%, high > 0.75%.
* Available water capacity = field capacity (-33 kPa) - wilting point (-1500 kPa), per metre of soil.
"""

from __future__ import annotations


def usda_texture(sand: float, silt: float, clay: float) -> str:
    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 2 * clay < 30:
        return "loamy sand"
    if (7 <= clay < 20 and sand > 52) or (clay < 7 and silt < 50):
        return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty clay loam"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 40 and silt >= 40:
        return "silty clay"
    return "clay"


def ph_class(ph: float) -> str:
    if ph < 5.5:
        return "strongly acidic"
    if ph < 6.5:
        return "slightly acidic"
    if ph <= 7.5:
        return "neutral"
    if ph <= 8.5:
        return "alkaline"
    return "strongly alkaline (sodicity risk)"


def organic_carbon_class(soc_g_per_kg: float) -> str:
    pct = soc_g_per_kg / 10
    if pct < 0.5:
        return "low"
    if pct <= 0.75:
        return "medium"
    return "high"


def saxton_rawls(sand_pct: float, clay_pct: float, organic_carbon_pct: float) -> tuple[float, float]:
    """Field capacity and wilting point (m3/m3) from texture and organic matter.

    Pedotransfer functions of Saxton & Rawls (2006), Soil Sci. Soc. Am. J. 70:1569-1578 (eqs. 1-2);
    organic matter = 1.724 x organic carbon. Used when measured water-retention layers are unavailable.
    """
    s, c = sand_pct / 100, clay_pct / 100
    om = min(max(1.724 * organic_carbon_pct, 0.0), 8.0)
    wp_t = -0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031
    wp = wp_t + (0.14 * wp_t - 0.02)
    fc_t = -0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299
    fc = fc_t + (1.283 * fc_t ** 2 - 0.374 * fc_t - 0.015)
    return round(fc, 3), round(wp, 3)
