"""Pydantic contracts passed between graph nodes.

Each agent's final message must validate against its contract; the backend
rejects and retries a node whose output does not parse. Free-text handoffs
between agents are not allowed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.detection import DetectionHit


class DetectionReport(BaseModel):
    """Pattern Detector output: the hit under investigation plus verification."""

    hit: DetectionHit
    verified: bool = Field(description="Recomputed features match the hit payload")
    verification_notes: str
    window_event_count: int
    account_event_count: int


class PriorAlert(BaseModel):
    alert_id: str
    pattern: str
    date: str
    disposition: str


class AccountContext(BaseModel):
    """Context Agent output: who the account is and their history."""

    account_id: str
    account_type: str
    risk_rating: str
    prior_alerts: list[PriorAlert]
    is_recidivist: bool = Field(description="Two or more prior alerts with the same pattern")
    context_summary: str


class RelatedActivity(BaseModel):
    instrument: str
    venue: str
    n_events: int
    n_large_cancels: int
    overlaps_window: bool


class CorrelationFindings(BaseModel):
    """Cross-Market Correlator output: same-account activity on related markets."""

    account_id: str
    related_instrument: str | None
    related_activity: list[RelatedActivity]
    coordinated_accounts: list[str] = Field(
        description="Counterparties whose executed volume with this account is concentrated"
    )
    correlation_summary: str


class EvidenceCitation(BaseModel):
    claim: str
    source: str = Field(description="Feature name, order id, or upstream agent field cited")
    value: str


class CaseMemo(BaseModel):
    """Case Writer output: the draft memo a human reviewer sees.

    recommendation is advisory text for the reviewer. No value of any field
    causes a filing; the reviewer acts through the review API or not at all.
    """

    case_id: str
    pattern: str
    account_id: str
    instrument: str
    window: str
    headline: str
    narrative: str
    evidence: list[EvidenceCitation]
    regulatory_references: list[str]
    recommendation: str = Field(description="One of: escalate, monitor, dismiss")
    confidence: float = Field(ge=0.0, le=1.0)
