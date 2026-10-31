"""Field diary workflow: store a weekly check-in, analyse its photo with the active AI, summarise the history."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import date, timedelta
from typing import Any

from ..agent.prompts import vision_prompt
from ..agronomy.crops import get_crop
from ..agronomy.water import crop_stage
from ..db import Database
from ..schemas import FarmProfile
from .images import prepare_photo
from .models import Observations

log = logging.getLogger(__name__)

PHOTO_ANALYSIS_TIMEOUT_S = 90
CHECK_IN_INTERVAL_DAYS = 7
FINDING_FIELDS = ("photo_quality", "matches_declared_crop", "observed_stage", "canopy_cover_pct", "leaf_colour",
                  "water_stress_signs", "weed_pressure", "symptoms", "change_since_last_photo", "summary")


def public_entry(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row[k] for k in ("id", "token", "created_at", "observations", "analysis", "analysis_status", "has_photo")}
    out["photo_url"] = f"/api/diary/photo/{row['token']}" if row["has_photo"] else None
    return out


async def create_entry(db: Database, agent: Any, farm: FarmProfile, observations: Observations,
                       raw_photo: bytes | None) -> dict[str, Any]:
    """Save one check-in. The photo is resized and stripped of metadata, then analysed if an AI is configured.

    Raises `PhotoError` for an unusable upload. A failed or unavailable analysis never blocks saving:
    the farmer's own observations are still recorded and used.
    """
    key = farm.farm_key()
    obs = observations.model_dump(mode="json", exclude_none=True)
    jpeg = width = height = None
    if raw_photo:
        jpeg, width, height = prepare_photo(raw_photo)

    analysis, status = None, "no_photo"
    if jpeg is not None:
        status = "unavailable"
        if agent.enabled:
            previous = db.latest_diary_photo(key)
            crop = get_crop(farm.crop)
            prompt = vision_prompt(crop.name, farm.sowing_date.isoformat(),
                                   crop_stage(crop, farm.sowing_date, date.today()).label, farm.language, obs,
                                   previous[1] if previous else None)
            photos = ([previous[0]] if previous else []) + [jpeg]
            try:
                findings = await asyncio.wait_for(agent.analyze_photo(photos, prompt), timeout=PHOTO_ANALYSIS_TIMEOUT_S)
            except asyncio.TimeoutError:
                findings = None
            except Exception:  # analysis is an extra; never lose the farmer's check-in because of it
                log.exception("Photo analysis crashed")
                findings = None
            analysis = findings.normalised().model_dump() if findings else None
            status = "ok" if analysis else "failed"

    row = db.add_diary_entry({"farm_key": key, "token": secrets.token_urlsafe(16), "observations": obs,
                              "analysis": analysis, "analysis_status": status, "photo": jpeg,
                              "photo_width": width, "photo_height": height})
    return public_entry(row)


def compare(latest: dict[str, Any] | None, previous: dict[str, Any] | None) -> dict[str, Any] | None:
    """Week-over-week change between two photo analyses."""
    if not latest or not previous:
        return None
    out: dict[str, Any] = {}
    lc, pc = latest.get("canopy_cover_pct"), previous.get("canopy_cover_pct")
    if isinstance(lc, int) and isinstance(pc, int):
        out["canopy_cover_change_pct_points"] = lc - pc

    def names(findings: dict[str, Any]) -> set[str]:
        return {s["name"].strip().lower() for s in findings.get("symptoms", []) if s.get("name")}

    new, gone = names(latest) - names(previous), names(previous) - names(latest)
    if new:
        out["new_symptoms"] = sorted(new)
    if gone:
        out["symptoms_no_longer_seen"] = sorted(gone)
    if latest.get("observed_stage") != previous.get("observed_stage"):
        out["stage_change"] = f"{previous.get('observed_stage')} -> {latest.get('observed_stage')}"
    return out


def diary_summary(db: Database, farm: FarmProfile, limit: int = 4) -> dict[str, Any]:
    """Recent check-ins in the compact, evidence-ready form the agent and dashboard use."""
    rows = db.list_diary(farm.farm_key(), limit)
    crop = get_crop(farm.crop)
    entries = []
    for r in rows:
        entries.append({
            "date": r["created_at"][:10],
            "farmer_observations": r["observations"],
            "photo": {"ok": "analysed", "unavailable": "not analysed (no AI key)", "failed": "analysis failed",
                      "no_photo": "no photo"}.get(r["analysis_status"], r["analysis_status"]),
            "photo_findings": {k: r["analysis"][k] for k in FINDING_FIELDS if k in r["analysis"]} if r["analysis"] else None,
        })
    data: dict[str, Any] = {
        "entries_count": len(rows),
        "expected_stage_from_sowing_date": crop_stage(crop, farm.sowing_date, date.today()).label,
        "entries": entries,
    }
    if rows:
        last = date.fromisoformat(rows[0]["created_at"][:10])
        data["days_since_last_check_in"] = (date.today() - last).days
        data["next_check_in_due"] = (last + timedelta(days=CHECK_IN_INTERVAL_DAYS)).isoformat()
        irrigations = [r["observations"].get("irrigated_on") for r in rows if r["observations"].get("irrigated_on")]
        if irrigations:
            data["last_irrigation_reported"] = max(irrigations)
        analysed = [r["analysis"] for r in rows if r["analysis"]]
        change = compare(analysed[0], analysed[1]) if len(analysed) >= 2 else None
        if change:
            data["latest_vs_previous_photo"] = change
    return {"data": data, "rows": rows}
