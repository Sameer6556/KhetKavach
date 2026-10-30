"""Crop knowledge base: published agronomic constants (reference science, not observations).

Sources
-------
* Kc_ini/mid/end, maximum crop height: FAO Irrigation & Drainage Paper 56 (Allen et al., 1998), Table 12.
* Stage lengths (days): FAO-56 Table 11 (tropical / South-Asian entries where available); entries
  marked `adapted` are shortened to typical Indian season lengths.
* Maximum rooting depth and depletion fraction p: FAO-56 Table 22 (mid-range values).
* Heat / cold stress thresholds: critical temperatures for reproductive-stage damage commonly used
  in ICAR and state agricultural university advisories (e.g. wheat terminal heat >32 C, rice spikelet
  sterility >35 C at anthesis, tomato fruit-set failure >32 C, frost damage near 2 C).
* Disease windows: weather conditions that favour infection (temperature range + leaf-wetness hours,
  proxied by RH >= 90%), used by agromet advisory services. They indicate favourable weather, not
  confirmed disease.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiseaseWindow:
    name: str
    t_min: float
    t_max: float
    wet_hours: int  # hours/day with RH >= 90% inside [t_min, t_max] that make a day "favourable"


@dataclass(frozen=True)
class Crop:
    key: str
    name: str
    hindi: str
    kc_ini: float
    kc_mid: float
    kc_end: float
    stages: tuple[int, int, int, int]  # initial, development, mid-season, late-season (days)
    height_m: float
    root_max_m: float
    p: float  # fraction of available water that can be depleted before stress (FAO-56 Table 22)
    heat_c: float  # Tmax above which reproductive-stage heat stress is expected
    cold_c: float  # Tmin below which chilling/frost injury is expected
    season: str
    diseases: tuple[DiseaseWindow, ...] = field(default_factory=tuple)
    late_blight_model: bool = False  # Hutton criteria apply (potato/tomato)
    ponded: bool = False  # paddy: kept flooded, managed with alternate wetting & drying

    @property
    def season_days(self) -> int:
        return sum(self.stages)


def _d(name: str, t_min: float, t_max: float, wet_hours: int) -> DiseaseWindow:
    return DiseaseWindow(name, t_min, t_max, wet_hours)


CROPS: dict[str, Crop] = {c.key: c for c in [
    Crop("rice", "Rice (paddy)", "धान", 1.05, 1.20, 0.75, (30, 30, 60, 30), 1.0, 0.6, 0.20, 35, 15, "Kharif",
         (_d("Rice blast", 20, 28, 10), _d("Sheath blight", 25, 32, 8)), ponded=True),
    Crop("wheat", "Wheat", "गेहूं", 0.30, 1.15, 0.40, (15, 25, 50, 30), 1.0, 1.2, 0.55, 32, 2, "Rabi",
         (_d("Yellow (stripe) rust", 8, 16, 6), _d("Brown (leaf) rust", 15, 25, 6))),
    Crop("maize", "Maize", "मक्का", 0.30, 1.20, 0.60, (20, 35, 40, 30), 2.0, 1.2, 0.55, 35, 8, "Kharif/Rabi",
         (_d("Turcicum leaf blight", 18, 27, 6),)),
    Crop("cotton", "Cotton", "कपास", 0.35, 1.18, 0.60, (30, 50, 55, 45), 1.3, 1.3, 0.65, 38, 12, "Kharif",
         (_d("Boll rot / Alternaria leaf spot", 25, 32, 8),)),
    Crop("sugarcane", "Sugarcane", "गन्ना", 0.40, 1.25, 0.75, (30, 50, 180, 60), 3.0, 1.5, 0.65, 38, 10, "Annual",
         (_d("Red rot", 25, 32, 10),)),
    Crop("soybean", "Soybean", "सोयाबीन", 0.40, 1.15, 0.50, (20, 25, 45, 20), 0.7, 1.0, 0.50, 35, 10, "Kharif",
         (_d("Soybean rust", 15, 28, 6),)),
    Crop("groundnut", "Groundnut", "मूंगफली", 0.40, 1.15, 0.60, (25, 35, 45, 25), 0.4, 0.8, 0.50, 35, 12, "Kharif/Rabi",
         (_d("Tikka leaf spot", 25, 30, 8),)),
    Crop("chickpea", "Chickpea (gram)", "चना", 0.40, 1.00, 0.35, (20, 30, 45, 25), 0.5, 0.8, 0.50, 32, 3, "Rabi",
         (_d("Ascochyta blight", 15, 25, 8),)),
    Crop("mustard", "Mustard", "सरसों", 0.35, 1.07, 0.35, (20, 30, 50, 30), 1.0, 1.2, 0.60, 30, 3, "Rabi",
         (_d("Alternaria blight", 18, 27, 8), _d("White rust", 10, 20, 6))),
    Crop("pearl_millet", "Pearl millet (bajra)", "बाजरा", 0.30, 1.00, 0.30, (15, 25, 40, 25), 1.5, 1.5, 0.55, 42, 12, "Kharif",
         (_d("Downy mildew", 20, 30, 8),)),
    Crop("sorghum", "Sorghum (jowar)", "ज्वार", 0.30, 1.05, 0.55, (20, 35, 40, 30), 1.5, 1.5, 0.55, 38, 10, "Kharif/Rabi",
         (_d("Grain mould", 25, 32, 8),)),
    Crop("potato", "Potato", "आलू", 0.50, 1.15, 0.75, (25, 30, 45, 20), 0.6, 0.5, 0.35, 30, 2, "Rabi",
         (_d("Early blight", 24, 29, 8),), late_blight_model=True),
    Crop("onion", "Onion", "प्याज", 0.70, 1.05, 0.75, (15, 25, 70, 40), 0.4, 0.45, 0.30, 35, 5, "Rabi/Kharif",
         (_d("Purple blotch", 21, 30, 10), _d("Stemphylium blight", 18, 25, 8))),
    Crop("tomato", "Tomato", "टमाटर", 0.60, 1.15, 0.80, (30, 40, 40, 25), 0.6, 1.0, 0.40, 32, 10, "All seasons",
         (_d("Early blight", 24, 29, 8),), late_blight_model=True),
    Crop("chilli", "Chilli", "मिर्च", 0.60, 1.05, 0.90, (25, 35, 40, 20), 0.7, 0.8, 0.30, 34, 10, "Kharif/Rabi",
         (_d("Anthracnose (fruit rot)", 25, 30, 8),)),
    Crop("banana", "Banana", "केला", 0.50, 1.10, 1.00, (120, 90, 120, 60), 3.0, 0.7, 0.35, 38, 12, "Annual",
         (_d("Sigatoka leaf spot", 25, 30, 8),)),
]}

IRRIGATION_EFFICIENCY = {"drip": 0.90, "sprinkler": 0.75, "furrow": 0.60, "flood": 0.50}
IRRIGATION_METHODS = (*IRRIGATION_EFFICIENCY.keys(), "rainfed")


def get_crop(key: str) -> Crop:
    norm = key.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"paddy": "rice", "dhan": "rice", "gehun": "wheat", "makka": "maize", "kapas": "cotton",
               "soya": "soybean", "soyabean": "soybean", "gram": "chickpea", "chana": "chickpea",
               "sarson": "mustard", "bajra": "pearl_millet", "millet": "pearl_millet", "jowar": "sorghum",
               "aloo": "potato", "pyaz": "onion", "tamatar": "tomato", "mirch": "chilli", "chili": "chilli",
               "kela": "banana", "ganna": "sugarcane", "peanut": "groundnut", "moongphali": "groundnut"}
    norm = aliases.get(norm, norm)
    if norm not in CROPS:
        raise KeyError(f"Unsupported crop '{key}'. Supported: {', '.join(sorted(CROPS))}")
    return CROPS[norm]
