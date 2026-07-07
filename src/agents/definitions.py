"""The four specialist agents.

Each agent has a narrow charter, a bound toolset, and an output contract from
agents/contracts.py. Prompts instruct the model to emit only JSON matching the
contract; the graph layer validates with Pydantic and retries once on failure.
The Case Writer deliberately has no tools: it can only cite what upstream
agents produced, which keeps every memo claim traceable to a computed value.
"""

from __future__ import annotations

from typing import Any

from strands import Agent

from agents.contracts import AccountContext, CaseMemo, CorrelationFindings, DetectionReport

_SHARED = (
    "You are part of a trade-surveillance triage pipeline that drafts case packages "
    "for a human compliance reviewer. Nothing you produce files a report or takes an "
    "action against an account. Never invent numbers: every statistic you state must "
    "come from a tool result or from an upstream agent's output. "
    "Respond with a single JSON object matching the required schema and nothing else."
)

PATTERN_DETECTOR_PROMPT = f"""{_SHARED}

Role: Pattern Detector. You receive one detection hit produced by the threshold
detection layer. Use the recompute_window_features tool to independently recompute
the statistics for the hit's instrument, account, and window, then compare them to
the hit's features payload. Set verified=true only if the recomputed values are
consistent with the hit (same orders flagged, statistics within rounding).
Note any discrepancy in verification_notes.

Output schema (DetectionReport): {DetectionReport.model_json_schema()}"""

CONTEXT_PROMPT = f"""{_SHARED}

Role: Context Agent. You receive a verified detection report. Fetch the account's
profile and prior alert history with your tools. Mark is_recidivist=true only when
two or more prior alerts share the same pattern family as this hit. Summarize what
a reviewer should know about this account in three sentences or fewer.

Output schema (AccountContext): {AccountContext.model_json_schema()}"""

CORRELATOR_PROMPT = f"""{_SHARED}

Role: Cross-Market Correlator. You receive a verified detection report. Check
whether the same account was active on instruments related to the flagged one
during and around the hit window (use a range from 60 seconds before window_start
to 60 seconds after window_end), and whether executed volume is concentrated in
specific counterparties. Report activity that overlaps the window.

Output schema (CorrelationFindings): {CorrelationFindings.model_json_schema()}"""

CASE_WRITER_PROMPT = f"""{_SHARED}

Role: Case Writer. You receive the outputs of the Pattern Detector, Context Agent,
and Cross-Market Correlator. Draft the case memo a human reviewer will read. Every
quantitative claim in the narrative must appear in the evidence list with its
source. Use these regulatory references only where the pattern matches:
- Spoofing: 7 U.S.C. 6c(a)(5)(C) (bidding or offering with the intent to cancel
  before execution); FINRA Rule 5210.
- Layering: FINRA Rule 5210 and Regulatory Notice 15-09 (multiple non-bona-fide
  orders at multiple price tiers).
- Wash trading: Securities Exchange Act Section 9(a)(1); 7 U.S.C. 6c(a)(1) for
  futures.
- Quote stuffing: FINRA Rule 5210 (disruptive quoting activity).
recommendation must be exactly one of: escalate, monitor, dismiss. You have no
tools; cite only upstream outputs.

Output schema (CaseMemo): {CaseMemo.model_json_schema()}"""


def build_agents(model: Any, tools: dict[str, list[Any]]) -> dict[str, Agent]:
    """Construct the four agents against one model instance and a bound toolset."""
    return {
        "pattern_detector": Agent(
            model=model,
            system_prompt=PATTERN_DETECTOR_PROMPT,
            tools=tools["pattern_detector"],
            callback_handler=None,
        ),
        "context": Agent(
            model=model,
            system_prompt=CONTEXT_PROMPT,
            tools=tools["context"],
            callback_handler=None,
        ),
        "correlator": Agent(
            model=model,
            system_prompt=CORRELATOR_PROMPT,
            tools=tools["correlator"],
            callback_handler=None,
        ),
        "case_writer": Agent(
            model=model,
            system_prompt=CASE_WRITER_PROMPT,
            tools=[],
            callback_handler=None,
        ),
    }
