"""Detection output schema: the contract between the detection layer and the agents."""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.events import PatternType


class DetectionHit(BaseModel):
    """One threshold breach for one account on one instrument in one time window.

    Produced by deterministic code in src/detection. Agents consume these; they
    never compute the underlying statistics themselves.
    """

    pattern: PatternType
    instrument: str
    venue: str
    account_id: str
    window_start: float
    window_end: float
    score: float = Field(ge=0.0, le=1.0, description="Average threshold margin, capped at 2x")
    features: dict[str, float | int | str] = Field(
        description="The statistics that fired, named, with their values"
    )
