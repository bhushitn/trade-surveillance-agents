"""Event and ground-truth schemas for the synthetic order stream.

The generator emits events as DataFrame rows for speed. These models are the
schema of record: tests validate sampled rows against them, and downstream
agents receive objects built from them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class EventType(StrEnum):
    NEW = "new"
    CANCEL = "cancel"
    EXECUTE = "execute"


class PatternType(StrEnum):
    SPOOFING = "spoofing"
    LAYERING = "layering"
    WASH_TRADING = "wash_trading"
    QUOTE_STUFFING = "quote_stuffing"


EVENT_COLUMNS = [
    "event_id",
    "ts",
    "event_type",
    "order_id",
    "account_id",
    "instrument",
    "venue",
    "side",
    "price",
    "quantity",
    "counterparty_id",
]


class OrderEvent(BaseModel):
    """One order lifecycle event on the simulated tape."""

    event_id: int
    ts: float = Field(description="Seconds since session open")
    event_type: EventType
    order_id: str
    account_id: str
    instrument: str
    venue: str
    side: Side
    price: float
    quantity: int
    counterparty_id: str | None = Field(
        default=None, description="Passive-side account, set on EXECUTE events only"
    )


class GroundTruthEpisode(BaseModel):
    """One injected manipulation episode: the answer key for evaluation."""

    episode_id: str
    pattern: PatternType
    account_ids: list[str]
    instrument: str
    venue: str
    start_ts: float
    end_ts: float
    order_ids: list[str]
    coordinated_with: str | None = Field(
        default=None, description="Episode id of a linked episode on a related instrument"
    )
