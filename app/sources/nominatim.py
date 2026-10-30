"""OpenStreetMap Nominatim reverse geocoding (village / district / state for a coordinate).

Usage policy (https://operations.osmfoundation.org/policies/nominatim/): max 1 request/second,
identifying User-Agent, and caching of results. All three are enforced by `DataClient`.
"""

from __future__ import annotations

from typing import Any

from ..http import DataClient, Fetched, SourceError

URL = "https://nominatim.openstreetmap.org/reverse"


def _clean_district(raw: str | None) -> str | None:
    if not raw:
        return None
    for suffix in (" District", " district", " Division", " Tahsil", " Tehsil", " Taluka", " taluka"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)].strip()
    return raw.strip()


def parse(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("error"):
        raise SourceError("nominatim", str((payload or {}).get("error") or "no address found"), retryable=False)
    addr = payload.get("address") or {}
    locality = next((addr[k] for k in ("village", "hamlet", "town", "suburb", "city", "municipality") if addr.get(k)), None)
    district = _clean_district(addr.get("state_district") or addr.get("district") or addr.get("county") or addr.get("city"))
    return {
        "locality": locality,
        "subdistrict": _clean_district(addr.get("county") or addr.get("subdistrict")),
        "district": district,
        "state": addr.get("state") or addr.get("territory"),
        "country": addr.get("country"),
        "country_code": (addr.get("country_code") or "").upper() or None,
        "display_name": payload.get("display_name"),
    }


async def reverse(dc: DataClient, lat: float, lon: float) -> tuple[dict[str, Any], Fetched]:
    params = {"lat": round(lat, 4), "lon": round(lon, 4), "format": "jsonv2", "zoom": 12,
              "addressdetails": 1, "accept-language": "en"}
    fetched = await dc.get("nominatim", URL, params, ttl_s=30 * 24 * 3600)
    return parse(fetched.data), fetched
