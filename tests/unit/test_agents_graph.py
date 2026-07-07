"""Structural tests for the case graph: topology, tool outputs, contract parsing.

No test here invokes a model. The graph's shape and the tools' numeric outputs
are deterministic and testable offline; model-dependent behavior is exercised
by the evaluation harness against recorded runs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.contracts import CaseMemo
from agents.graph import NODE_ORDER, build_case_graph, case_task, extract_memo
from agents.tools import build_case_tools
from datagen.config import GeneratorConfig
from datagen.generator import generate
from detection.detectors import hits_to_models, run_detection


@pytest.fixture(scope="module")
def session() -> tuple:
    config = GeneratorConfig.ci()
    events, _ = generate(config)
    ticks = {s.symbol: s.tick_size for s in config.instruments}
    return events, ticks


def test_graph_topology(session: tuple) -> None:
    events, ticks = session
    graph = build_case_graph(model=None, events=events, tick_sizes=ticks)
    assert set(graph.nodes) == set(NODE_ORDER)
    edges = {(e.from_node.node_id, e.to_node.node_id) for e in graph.edges}
    assert edges == {
        ("pattern_detector", "context"),
        ("pattern_detector", "correlator"),
        ("context", "case_writer"),
        ("correlator", "case_writer"),
    }
    assert [n.node_id for n in graph.entry_points] == ["pattern_detector"]


def test_case_writer_has_no_tools(session: tuple) -> None:
    events, ticks = session
    tools = build_case_tools(events, ticks)
    assert tools["case_writer"] == []


def test_recompute_tool_matches_hit_features(session: tuple) -> None:
    events, ticks = session
    hits = hits_to_models(run_detection(events, ticks))
    hit = next(h for h in hits if h.pattern in ("spoofing", "layering"))
    tools = build_case_tools(events, ticks)
    recompute = tools["pattern_detector"][0]
    payload = json.loads(
        recompute(hit.instrument, hit.account_id, hit.window_start, hit.window_end)
    )
    assert payload["window_event_count"] > 0
    assert len(payload["large_cancelled_orders"]) == hit.features["n_large_cancels"]
    latencies = sorted(o["cancel_latency_s"] for o in payload["large_cancelled_orders"])
    mid = len(latencies) // 2
    median = (
        latencies[mid] if len(latencies) % 2 else (latencies[mid - 1] + latencies[mid]) / 2
    )
    assert median == pytest.approx(hit.features["median_cancel_latency_s"], abs=0.01)


def test_correlator_tools_return_json(session: tuple) -> None:
    events, ticks = session
    tools = build_case_tools(events, ticks)
    related, activity, concentration = tools["correlator"]
    assert json.loads(related("ALPH"))[0]["symbol"] == "ALPH-F"
    assert json.loads(related("CRUX")) == []
    act = json.loads(activity("ALPH", "ACCT-000", 0.0, 600.0))
    assert act["n_events"] >= 0
    assert isinstance(json.loads(concentration("ALPH", "ACCT-000", 0.0, 600.0)), list)


def test_case_task_embeds_hit(session: tuple) -> None:
    events, ticks = session
    hit = hits_to_models(run_detection(events, ticks))[0]
    task = case_task(hit, "CASE-0001")
    assert "CASE-0001" in task and hit.account_id in task


def test_extract_memo_parses_and_pins_case_id() -> None:
    memo = CaseMemo(
        case_id="WRONG",
        pattern="spoofing",
        account_id="ACCT-001",
        instrument="ALPH",
        window="60.0-120.0",
        headline="h",
        narrative="n",
        evidence=[],
        regulatory_references=[],
        recommendation="escalate",
        confidence=0.8,
    )
    result = SimpleNamespace(
        results={"case_writer": SimpleNamespace(result=f"text {memo.model_dump_json()} tail")}
    )
    out = extract_memo(result, "CASE-0042")
    assert out.case_id == "CASE-0042"
    with pytest.raises(ValueError):
        extract_memo(
            SimpleNamespace(results={"case_writer": SimpleNamespace(result="no json")}),
            "CASE-0042",
        )
