"""Agmarknet daily mandi (wholesale market) prices via the Government of India Open Data Platform.

Dataset: "Current Daily Price of Various Commodities from Various Markets (Mandi)", Directorate of
Marketing & Inspection, published on data.gov.in (resource 9ef84268-d588-465a-a308-a864a43d0070).
Prices are in Rs per quintal. The API needs a free key (https://data.gov.in -> My Account -> API key).

The connector is self-healing: data.gov.in has used both `filters[field]` and `filters[field.keyword]`
conventions, Agmarknet commodity names are idiosyncratic ("Paddy(Dhan)(Common)"), and field ids can
change case. We try both filter styles, map crop names to known Agmarknet aliases, always re-filter
client-side, and fall back to a state-wide query + fuzzy match when an exact name finds nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..http import DataClient, Fetched, SourceError

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"
FILTER_STYLES = ("{f}", "{f}.keyword")

# Agmarknet commodity names (exact strings used by the dataset) for the crops KhetKavach models.
COMMODITY_ALIASES: dict[str, list[str]] = {
    "rice": ["Paddy(Dhan)(Common)", "Rice", "Paddy(Dhan)(Basmati)"],
    "wheat": ["Wheat"],
    "maize": ["Maize"],
    "cotton": ["Cotton", "Kapas"],
    "sugarcane": ["Sugarcane", "Gur(Jaggery)"],
    "soybean": ["Soyabean"],
    "groundnut": ["Groundnut", "Groundnut pods (raw)"],
    "chickpea": ["Bengal Gram(Gram)(Whole)", "Kabuli Chana(Chickpeas-White)"],
    "mustard": ["Mustard"],
    "pearl_millet": ["Bajra(Pearl Millet/Cumbu)"],
    "sorghum": ["Jowar(Sorghum)"],
    "potato": ["Potato"],
    "onion": ["Onion"],
    "tomato": ["Tomato"],
    "chilli": ["Green Chilli", "Dry Chillies"],
    "banana": ["Banana", "Banana - Green"],
}
# Keywords for fuzzy matching (English + common Hindi transliterations).
COMMODITY_KEYWORDS: dict[str, list[str]] = {
    "rice": ["paddy", "rice", "dhan"], "wheat": ["wheat", "gehun"], "maize": ["maize", "makka"],
    "cotton": ["cotton", "kapas"], "sugarcane": ["sugarcane", "gur"], "soybean": ["soyabean", "soybean"],
    "groundnut": ["groundnut", "moongphali"], "chickpea": ["bengal gram", "chana", "gram"],
    "mustard": ["mustard", "sarson"], "pearl_millet": ["bajra", "pearl millet"], "sorghum": ["jowar", "sorghum"],
    "potato": ["potato", "aloo"], "onion": ["onion", "pyaz"], "tomato": ["tomato", "tamatar"],
    "chilli": ["chilli", "chillies", "mirch"], "banana": ["banana", "kela"],
}
CANONICAL_FIELDS = ("state", "district", "market", "commodity", "variety", "grade",
                    "arrival_date", "min_price", "max_price", "modal_price")


@dataclass
class MandiResult:
    records: list[dict[str, Any]]
    fetched: Fetched | None
    commodity_query: str
    matched_names: list[str] = field(default_factory=list)
    available_commodities: list[str] = field(default_factory=list)


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.replace("_x0020_", "_").lower()).strip("_")


def _num(value: Any) -> float | None:
    if value in (None, "", "NA", "NR"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _iso_date(raw: Any) -> str:
    text = str(raw or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return text


def normalize_records(raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for raw in raw_records or []:
        rec = {_norm_key(k): v for k, v in raw.items()}
        if not rec.get("commodity") or not rec.get("market"):
            continue
        out.append({
            "state": str(rec.get("state") or "").strip(),
            "district": str(rec.get("district") or "").strip(),
            "market": str(rec.get("market") or "").strip(),
            "commodity": str(rec.get("commodity") or "").strip(),
            "variety": str(rec.get("variety") or "").strip() or "-",
            "grade": str(rec.get("grade") or "").strip() or "-",
            "arrival_date": _iso_date(rec.get("arrival_date")),
            "min_price": _num(rec.get("min_price")),
            "max_price": _num(rec.get("max_price")),
            "modal_price": _num(rec.get("modal_price")),
        })
    return out


def candidate_names(commodity: str) -> tuple[list[str], list[str]]:
    """Exact Agmarknet names to try and fuzzy keywords for a crop key or free-text commodity."""
    key = commodity.strip().lower().replace(" ", "_")
    if key in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[key], COMMODITY_KEYWORDS[key]
    for crop, words in COMMODITY_KEYWORDS.items():
        if any(w == commodity.strip().lower() for w in words):
            return COMMODITY_ALIASES[crop], words
    return [commodity.strip().title()], [commodity.strip().lower()]


def matches_commodity(name: str, exact: list[str], keywords: list[str]) -> bool:
    low = name.lower()
    return name in exact or any(re.search(rf"\b{re.escape(k)}\b", low) for k in keywords)


def same_place(a: str, b: str) -> bool:
    return re.sub(r"[^a-z]", "", a.lower()) == re.sub(r"[^a-z]", "", b.lower())


def _honours(record: dict[str, Any], filters: dict[str, str]) -> bool:
    for key, wanted in filters.items():
        got = str(record.get(key, ""))
        if key in ("state", "district", "market"):
            if not same_place(got, wanted):
                return False
        elif got.strip().lower() != wanted.strip().lower():
            return False
    return True


async def _query(dc: DataClient, api_key: str, filters: dict[str, str], limit: int) -> tuple[list[dict], Fetched | None]:
    """Run one filtered query, trying each filter convention until the API demonstrably honours it.

    A filter syntax the API does not understand is silently ignored (unfiltered rows come back), so
    every row is re-checked against the filters client-side before it is accepted.
    """
    last: Fetched | None = None
    for style in FILTER_STYLES:
        params: dict[str, Any] = {"api-key": api_key, "format": "json", "limit": limit, "offset": 0}
        params.update({f"filters[{style.format(f=k)}]": v for k, v in filters.items()})
        fetched = await dc.get("data-gov-in", URL, params, ttl_s=2 * 3600)
        last = fetched
        payload = fetched.data if isinstance(fetched.data, dict) else {}
        if str(payload.get("status", "ok")).lower() == "error":
            continue
        records = [r for r in normalize_records(payload.get("records") or []) if _honours(r, filters)]
        if records:
            return records, fetched
    return [], last


async def fetch_prices(dc: DataClient, api_key: str, state: str, commodity: str,
                       sample_key: bool = False) -> MandiResult:
    if not state:
        raise SourceError("data-gov-in", "state is required to look up mandi prices", retryable=False)
    exact, keywords = candidate_names(commodity)
    limit = 10 if sample_key else 1000

    for name in exact:
        records, fetched = await _query(dc, api_key, {"state": state, "commodity": name}, limit)
        records = [r for r in records if matches_commodity(r["commodity"], exact, keywords)]
        if records:
            return MandiResult(records, fetched, commodity, matched_names=sorted({r["commodity"] for r in records}))

    # No exact hit: pull the state's full board for today and fuzzy-match (needs a personal key).
    records, fetched = await _query(dc, api_key, {"state": state}, 10 if sample_key else 2000)
    available = sorted({r["commodity"] for r in records})
    hits = [r for r in records if matches_commodity(r["commodity"], exact, keywords)]
    return MandiResult(hits, fetched, commodity, matched_names=sorted({r["commodity"] for r in hits}),
                       available_commodities=available)
