"""What the farmer reports each week, and what the AI is allowed to say about the photo."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Observations(BaseModel):
    """Quick taps in the weekly check-in. Everything is optional."""

    leaf_colour: Literal["green", "yellowing", "brown_spots", "mixed"] | None = None
    pests_seen: Literal["none", "few", "many"] | None = None
    irrigated_on: date | None = None
    sprayed_on: date | None = None
    fertilised_on: date | None = None
    note: str | None = Field(None, max_length=500)

    @field_validator("irrigated_on", "sprayed_on", "fertilised_on")
    @classmethod
    def _recent_past(cls, v: date | None) -> date | None:
        if v is not None and not (date.today() - timedelta(days=60) <= v <= date.today()):
            raise ValueError("date must be within the last 60 days")
        return v

    @field_validator("note")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None


class Symptom(BaseModel):
    name: str = Field(description="Short plain name, e.g. 'leaf spots', 'yellowing of older leaves', 'insect holes'")
    where: str = Field(description="Where it is seen, e.g. 'lower leaves', 'leaf tips', 'one corner of the field'")
    likelihood: Literal["low", "medium", "high"]


Stage = Literal["germination/establishment", "vegetative", "flowering", "fruiting/grain filling",
                "mature/harvest-ready", "unsure"]


class PhotoFindings(BaseModel):
    """Structured output the vision model must return. Visual estimates only, never a diagnosis."""

    photo_quality: Literal["good", "blurry", "too dark", "too far", "not a crop photo"]
    matches_declared_crop: Literal["yes", "no", "unsure"]
    observed_stage: Stage
    canopy_cover_pct: int = Field(description="Rough visual estimate (0-100) of the ground covered by crop leaves")
    leaf_colour: Literal["healthy green", "pale or yellowing", "brown or dry patches", "mixed", "unsure"]
    water_stress_signs: Literal["none", "mild", "severe", "unsure"]
    weed_pressure: Literal["none", "low", "moderate", "high", "unsure"]
    symptoms: list[Symptom] = Field(description="Visible symptoms, at most 5; empty if none")
    change_since_last_photo: str = Field(description="One sentence comparing with the previous photo, or 'no previous photo'")
    summary: str = Field(description="One or two short sentences for the farmer, in the requested language")
    next_checks: list[str] = Field(description="Up to 3 things to check in the field, in the requested language")

    def normalised(self) -> "PhotoFindings":
        """Clamp and trim model output to the documented limits."""
        return self.model_copy(update={
            "canopy_cover_pct": min(max(self.canopy_cover_pct, 0), 100),
            "symptoms": self.symptoms[:5],
            "next_checks": [c for c in self.next_checks if c.strip()][:3],
        })
