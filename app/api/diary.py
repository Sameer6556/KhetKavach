"""Field diary endpoints: weekly check-in upload (photo + observations), history, photos and deletion.

Photos are served only by an unguessable per-entry token and are stored without metadata.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from ..diary.images import MAX_UPLOAD_BYTES, PhotoError
from ..diary.models import Observations
from ..diary.service import create_entry, diary_summary, public_entry
from ..schemas import FarmProfile
from .routes import RateLimiter, _client_ip

router = APIRouter(prefix="/api/diary")
diary_limiter = RateLimiter(limit=20, window_s=600)


def _validation_detail(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]


@router.post("", status_code=201)
async def create_check_in(request: Request, farm: str = Form(..., description="FarmProfile as JSON"),
                          observations: str = Form("{}", description="Observations as JSON"),
                          photo: UploadFile | None = File(None)) -> dict[str, Any]:
    if not diary_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "Too many check-ins from this address; please wait a few minutes.")
    try:
        profile = FarmProfile.model_validate_json(farm)
        obs = Observations.model_validate_json(observations)
    except ValidationError as exc:
        raise HTTPException(422, _validation_detail(exc)) from None
    raw = None
    if photo is not None:
        raw = await photo.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Photo is larger than 10 MB.")
    if not raw and not obs.model_dump(exclude_none=True):
        raise HTTPException(422, "Add a photo or at least one observation.")
    st = request.app.state
    try:
        return await create_entry(st.ctx.db, st.agent, profile, obs, raw or None)
    except PhotoError as exc:
        raise HTTPException(422, f"Photo not accepted: {exc}") from None


@router.get("")
async def list_check_ins(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                         crop: str = Query(min_length=2, max_length=40), sowing_date: date = Query(),
                         limit: int = Query(12, ge=1, le=52)) -> dict[str, Any]:
    try:
        profile = FarmProfile(lat=lat, lon=lon, crop=crop, sowing_date=sowing_date)
    except ValidationError as exc:
        raise HTTPException(422, _validation_detail(exc)) from None
    summary = diary_summary(request.app.state.ctx.db, profile, limit)
    return {"summary": {k: v for k, v in summary["data"].items() if k != "entries"},
            "entries": [public_entry(r) for r in summary["rows"]]}


@router.get("/photo/{token}")
async def photo(request: Request, token: str) -> Response:
    jpeg = request.app.state.ctx.db.diary_photo(token)
    if jpeg is None:
        raise HTTPException(404, "Photo not found")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/{token}")
async def delete_check_in(request: Request, token: str) -> dict[str, Any]:
    if not request.app.state.ctx.db.delete_diary_entry(token):
        raise HTTPException(404, "Check-in not found")
    return {"deleted": True}
