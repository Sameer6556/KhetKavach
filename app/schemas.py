"""Request/response models shared by the API, the agent and the sentinel monitor."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .agent.prompts import LANGUAGES
from .agronomy.crops import get_crop

IrrigationMethod = Literal["drip", "sprinkler", "furrow", "flood", "rainfed"]


class FarmProfile(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    crop: str = Field(min_length=2, max_length=40)
    sowing_date: date
    irrigation_method: IrrigationMethod = "flood"
    last_irrigation_date: date | None = None
    language: str = "en"
    place: str | None = Field(None, max_length=160)
    area_acres: float | None = Field(None, gt=0, le=10000, description="Field size in acres (optional)")

    @field_validator("crop")
    @classmethod
    def _known_crop(cls, v: str) -> str:
        try:
            return get_crop(v).key
        except KeyError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("sowing_date")
    @classmethod
    def _plausible_sowing(cls, v: date) -> date:
        today = date.today()
        if v > today + timedelta(days=120) or v < today - timedelta(days=500):
            raise ValueError("sowing date must be within the last 500 days or the next 120 days")
        return v

    @field_validator("last_irrigation_date")
    @classmethod
    def _past_irrigation(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("last irrigation date cannot be in the future")
        return v

    @field_validator("language")
    @classmethod
    def _known_language(cls, v: str) -> str:
        return v if v in LANGUAGES else "en"

    def key(self) -> tuple:
        """Identity of a chat session: any change starts a fresh conversation."""
        return (round(self.lat, 4), round(self.lon, 4), self.crop, self.sowing_date, self.irrigation_method,
                self.last_irrigation_date, self.language, self.area_acres)

    def farm_key(self) -> str:
        """Identity of the field for its diary: same place (~100 m), crop and sowing date."""
        return f"{self.lat:.3f}:{self.lon:.3f}:{self.crop}:{self.sowing_date.isoformat()}"


class ChatRequest(BaseModel):
    session_id: str | None = Field(None, max_length=64)
    message: str = Field(min_length=1, max_length=1500)
    farm: FarmProfile


class FarmCreate(FarmProfile):
    name: str = Field(min_length=1, max_length=60)
    telegram_chat_id: str | None = Field(None, max_length=32, pattern=r"^-?\d+$")
