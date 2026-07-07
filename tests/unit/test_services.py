"""Local-fallback behavior of the service wrappers (no AWS access)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from services.identity import gateway_token
from services.memory import CaseMemory
from services.observability import case_span, configure_tracing
from services.settings import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        aws_region=None,
        memory_id=None,
        gateway_url=None,
        identity_provider=None,
        otlp_endpoint=None,
        local_state_dir=str(tmp_path),
    )


def test_settings_default_to_local(tmp_path: Path) -> None:
    s = make_settings(tmp_path)
    assert not s.memory_enabled and not s.gateway_enabled


def test_memory_local_branch_per_agent(tmp_path: Path) -> None:
    mem = CaseMemory(make_settings(tmp_path))
    mem.record("CASE-1", "pattern_detector", "assistant", "report a")
    mem.record("CASE-1", "case_writer", "assistant", "memo a")
    mem.record("CASE-1", "case_writer", "assistant", "memo b")
    assert len(mem.branch_history("CASE-1", "case_writer")) == 2
    assert len(mem.branch_history("CASE-1", "pattern_detector")) == 1
    assert mem.branch_history("CASE-2", "case_writer") == []


def test_memory_records_graph_result(tmp_path: Path) -> None:
    mem = CaseMemory(make_settings(tmp_path))
    result = SimpleNamespace(
        results={
            "context": SimpleNamespace(result="ctx"),
            "case_writer": SimpleNamespace(result="memo"),
        }
    )
    mem.record_graph_result("CASE-3", result)
    assert mem.branch_history("CASE-3", "context")[0]["text"] == "ctx"


def test_identity_returns_none_locally(tmp_path: Path) -> None:
    assert gateway_token(make_settings(tmp_path)) is None


def test_case_span_noop_locally(tmp_path: Path) -> None:
    configure_tracing(make_settings(tmp_path))
    with case_span("CASE-1", "spoofing", "ACCT-000") as span:
        assert span is not None
