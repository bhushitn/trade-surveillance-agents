"""Per-case orchestration graph.

Topology (deterministic, no LLM routing):

    pattern_detector --> context ----------\\
                     \\-> correlator --------> case_writer

The graph runs once per detection hit. The detector verifies the hit against
recomputed statistics; context and correlator run in parallel off its output;
the case writer joins both branches. The graph terminates at the case writer:
there is no node, tool, or edge that files anything. Its output lands in the
review queue where a human approves, edits, or dismisses it.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
from strands.multiagent import GraphBuilder
from strands.multiagent.graph import Graph

from agents.contracts import CaseMemo
from agents.definitions import build_agents
from agents.tools import build_case_tools
from detection.config import DetectionConfig
from schemas.detection import DetectionHit

NODE_ORDER = ["pattern_detector", "context", "correlator", "case_writer"]


def build_case_graph(
    model: Any,
    events: pd.DataFrame,
    tick_sizes: dict[str, float],
    config: DetectionConfig | None = None,
    session_manager: Any = None,
) -> Graph:
    """Wire the four agents into the case graph for one event session."""
    tools = build_case_tools(events, tick_sizes, config)
    agents = build_agents(model, tools)

    builder = GraphBuilder()
    for name in NODE_ORDER:
        builder.add_node(agents[name], name)
    builder.add_edge("pattern_detector", "context")
    builder.add_edge("pattern_detector", "correlator")
    builder.add_edge("context", "case_writer")
    builder.add_edge("correlator", "case_writer")
    builder.set_entry_point("pattern_detector")
    if session_manager is not None:
        builder.set_session_manager(session_manager)
    return builder.build()


def case_task(hit: DetectionHit, case_id: str) -> str:
    """The task string handed to the graph's entry node."""
    return (
        f"Case {case_id}. Investigate this detection hit and draft the case package.\n"
        f"{hit.model_dump_json(indent=2)}"
    )


def extract_memo(result: Any, case_id: str) -> CaseMemo:
    """Validate the case writer's final message into a CaseMemo.

    Raises pydantic.ValidationError or ValueError if the output does not
    conform; the caller decides whether to retry the node or park the case
    for manual triage. Invalid output never reaches the review queue as if
    it were a memo.
    """
    node = result.results["case_writer"]
    text = str(node.result)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"case_writer returned no JSON object for {case_id}")
    memo = CaseMemo(**json.loads(text[start : end + 1]))
    if memo.case_id != case_id:
        memo = memo.model_copy(update={"case_id": case_id})
    return memo
