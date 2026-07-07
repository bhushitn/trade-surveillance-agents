"""Alert-to-case pipeline behind the review API.

Detection runs with the loosened alerting thresholds; each alert becomes a
case. When a Bedrock model is configured the case graph drafts the memo. In
offline mode (the default here, since this repository ships without AWS
credentials) the memo is assembled by a deterministic template over the same
verified statistics, clearly labeled generated_by=offline-template, so the
review workflow is exercisable end to end without a model. Neither mode has
a code path that files anything.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from agents.contracts import CaseMemo, EvidenceCitation
from datagen.config import GeneratorConfig
from detection.config import DetectionConfig
from detection.detectors import hits_to_models, run_detection
from evaluation.replay import replay_triage
from mockapis import account_service
from schemas.detection import DetectionHit
from services.memory import CaseMemory
from services.observability import case_span, configure_tracing
from services.settings import Settings

_REG_REFS = {
    "spoofing": ["7 U.S.C. 6c(a)(5)(C)", "FINRA Rule 5210"],
    "layering": ["FINRA Rule 5210", "FINRA Regulatory Notice 15-09"],
    "wash_trading": ["Securities Exchange Act Section 9(a)(1)", "7 U.S.C. 6c(a)(1)"],
    "quote_stuffing": ["FINRA Rule 5210"],
}

ReviewStatus = Literal["pending", "approved", "dismissed"]


@dataclass
class Case:
    case_id: str
    hit: DetectionHit
    memo: CaseMemo
    generated_by: str
    status: ReviewStatus = "pending"
    reviewer_notes: str = ""
    review_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hit"] = self.hit.model_dump()
        d["memo"] = self.memo.model_dump()
        return d


def _offline_memo(case_id: str, hit: DetectionHit, verified: bool) -> CaseMemo:
    profile = account_service.get_account_profile(hit.account_id)
    alerts = account_service.get_alert_history(hit.account_id)
    same = [a for a in alerts if a["pattern"] == hit.pattern]
    evidence = [
        EvidenceCitation(claim=f"detector feature {k}", source=f"features.{k}", value=str(v))
        for k, v in hit.features.items()
    ]
    evidence.append(
        EvidenceCitation(
            claim="alert re-verified against canonical thresholds",
            source="evaluation.replay.replay_triage",
            value=str(verified),
        )
    )
    rec = "escalate" if verified else "dismiss"
    narrative = (
        f"The alerting stage flagged {hit.account_id} on {hit.instrument} ({hit.venue}) "
        f"for {hit.pattern} in window {hit.window_start:.0f}-{hit.window_end:.0f}s. "
        f"Canonical re-verification {'confirmed' if verified else 'did not confirm'} the "
        f"statistics. Account is {profile['account_type']} with {profile['risk_rating']} "
        f"risk rating and {len(alerts)} prior alerts ({len(same)} same-pattern)."
    )
    return CaseMemo(
        case_id=case_id,
        pattern=hit.pattern,
        account_id=hit.account_id,
        instrument=hit.instrument,
        window=f"{hit.window_start:.0f}-{hit.window_end:.0f}",
        headline=f"{hit.pattern} alert for {hit.account_id} on {hit.instrument}",
        narrative=narrative,
        evidence=evidence,
        regulatory_references=_REG_REFS.get(hit.pattern, []),
        recommendation=rec,
        confidence=hit.score if verified else round(hit.score * 0.3, 3),
    )


def build_cases(
    events: pd.DataFrame,
    tick_sizes: dict[str, float],
    settings: Settings,
    model: Any = None,
) -> list[Case]:
    """Run alerting detection and draft one case per alert."""
    configure_tracing(settings)
    memory = CaseMemory(settings)
    alerts = run_detection(events, tick_sizes, DetectionConfig.alerting())
    verdicts = replay_triage(alerts, events, tick_sizes)
    cases: list[Case] = []
    for i, hit in enumerate(hits_to_models(alerts)):
        case_id = f"CASE-{i:04d}"
        with case_span(case_id, hit.pattern, hit.account_id):
            if model is not None:
                from agents.graph import build_case_graph, case_task, extract_memo

                graph = build_case_graph(model, events, tick_sizes)
                result = graph(case_task(hit, case_id))
                memory.record_graph_result(case_id, result)
                memo = extract_memo(result, case_id)
                generated_by = "agent-graph"
            else:
                memo = _offline_memo(case_id, hit, verdicts[i] == "escalate")
                memory.record(case_id, "offline_pipeline", "assistant", memo.model_dump_json())
                generated_by = "offline-template"
        cases.append(Case(case_id=case_id, hit=hit, memo=memo, generated_by=generated_by))
    return cases


def default_session() -> tuple[pd.DataFrame, dict[str, float]]:
    """The frozen evaluation dataset, or the CI dataset if it is not present."""
    frozen = Path("eval/dataset/events.parquet")
    if frozen.exists():
        events = pd.read_parquet(frozen)
        config = GeneratorConfig.full()
    else:
        from datagen.generator import generate

        config = GeneratorConfig.ci()
        events, _ = generate(config)
    return events, {s.symbol: s.tick_size for s in config.instruments}


def persist(cases: list[Case], settings: Settings) -> None:
    path = Path(settings.local_state_dir) / "cases.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([c.to_dict() for c in cases], indent=1))
