"""ISRIC SoilGrids 2.0 REST API: 250 m global soil property maps (no key; fair use ~5 calls/min).

Docs: https://rest.isric.org/soilgrids/v2.0/docs
Values arrive as integers in "mapped units"; dividing by each layer's `d_factor` converts them to
conventional units (e.g. clay g/kg -> %, phh2o pH*10 -> pH, wv0033 0.1 v% -> v%).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..http import DataClient, Fetched, SourceError

URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
PROPERTIES = ["clay", "sand", "silt", "phh2o", "soc", "nitrogen", "cec", "bdod", "wv0033", "wv1500"]
DEPTHS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm"]
DEPTH_THICKNESS_CM = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15, "30-60cm": 30, "60-100cm": 40}


@dataclass
class SoilLayers:
    # values[property][depth_label] -> value in target units (None where SoilGrids has no data)
    values: dict[str, dict[str, float | None]]
    units: dict[str, str]
    fetched: Fetched

    def weighted(self, prop: str, top_cm: int, bottom_cm: int) -> float | None:
        """Thickness-weighted mean of a property between two depths (cm)."""
        total_w, acc = 0.0, 0.0
        depth_top = 0
        for label in DEPTHS:
            thick = DEPTH_THICKNESS_CM[label]
            lo, hi = depth_top, depth_top + thick
            depth_top = hi
            overlap = max(0, min(hi, bottom_cm) - max(lo, top_cm))
            val = self.values.get(prop, {}).get(label)
            if overlap and val is not None:
                acc += val * overlap
                total_w += overlap
        return round(acc / total_w, 3) if total_w else None


def parse(payload: dict[str, Any], fetched: Fetched) -> SoilLayers:
    layers = ((payload or {}).get("properties") or {}).get("layers")
    if not isinstance(layers, list) or not layers:
        raise SourceError("soilgrids", "unexpected response shape (no layers)", retryable=False)
    values: dict[str, dict[str, float | None]] = {}
    units: dict[str, str] = {}
    for layer in layers:
        name = layer.get("name")
        um = layer.get("unit_measure") or {}
        d_factor = um.get("d_factor") or 1
        units[name] = um.get("target_units") or um.get("mapped_units") or ""
        values[name] = {}
        for depth in layer.get("depths") or []:
            mean = (depth.get("values") or {}).get("mean")
            values[name][depth.get("label")] = None if mean is None else round(mean / d_factor, 3)
    if all(v is None for per in values.values() for v in per.values()):
        raise SourceError("soilgrids", "no soil data at this point (water body, urban area or outside coverage)",
                          retryable=False)
    return SoilLayers(values=values, units=units, fetched=fetched)


WATER_RETENTION = ("wv0033", "wv1500")


async def fetch_soil(dc: DataClient, lat: float, lon: float) -> SoilLayers:
    def build(props: list[str]) -> list[tuple[str, Any]]:
        params: list[tuple[str, Any]] = [("lon", round(lon, 4)), ("lat", round(lat, 4))]
        params += [("property", p) for p in props]
        params += [("depth", d) for d in DEPTHS]
        return params + [("value", "mean")]

    try:
        fetched = await dc.get("soilgrids", URL, build(PROPERTIES), ttl_s=90 * 24 * 3600)
    except SourceError as exc:
        if exc.status not in (400, 404, 422):
            raise
        # Self-heal: if the water-retention layers are rejected, fetch the core properties only;
        # field capacity / wilting point are then estimated with pedotransfer functions.
        core = [p for p in PROPERTIES if p not in WATER_RETENTION]
        fetched = await dc.get("soilgrids", URL, build(core), ttl_s=90 * 24 * 3600)
    return parse(fetched.data, fetched)
