"""Tracing with a per-case correlation id.

Strands emits OpenTelemetry spans for every agent, model call, and tool call.
This module adds the pipeline-level span around each case run and stamps the
case id on it, so all traces for one case correlate in CloudWatch under
AgentCore Observability (the Runtime's ADOT collector exports automatically;
locally, spans go to the console only if OTEL_EXPORTER_OTLP_ENDPOINT or the
console flag is set, otherwise tracing is a no-op).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from services.settings import Settings

_CONFIGURED = False


def configure_tracing(settings: Settings) -> None:
    """Install an OTLP exporter when an endpoint is configured; otherwise leave
    the default no-op provider in place."""
    global _CONFIGURED
    if _CONFIGURED or settings.otlp_endpoint is None:
        _CONFIGURED = True
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": "trade-surveillance-agents"})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _CONFIGURED = True


@contextmanager
def case_span(case_id: str, pattern: str, account_id: str) -> Iterator[Any]:
    """Root span for one case; every child span inherits the correlation id."""
    tracer = trace.get_tracer("surveillance.pipeline")
    with tracer.start_as_current_span("case_run") as span:
        span.set_attribute("case.id", case_id)
        span.set_attribute("case.pattern", pattern)
        span.set_attribute("case.account_id", account_id)
        yield span
