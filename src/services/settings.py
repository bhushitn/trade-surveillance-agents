"""Environment-driven configuration for the AgentCore services.

Every value defaults to unset, and each service degrades to a documented
local fallback when its settings are missing, so the whole system runs
offline. Populate .env from .env.example to connect a real AWS account;
docs/DEPLOYMENT.md walks through creating each resource.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


@dataclass(frozen=True)
class Settings:
    aws_region: str | None = field(default_factory=lambda: _env("AWS_REGION"))
    bedrock_model_id: str = field(
        default_factory=lambda: _env("BEDROCK_MODEL_ID") or "claude-sonnet-4-6"
    )
    # AgentCore Memory: one memory store, branch-per-agent within each case session.
    memory_id: str | None = field(default_factory=lambda: _env("AGENTCORE_MEMORY_ID"))
    # AgentCore Gateway: MCP endpoint that fronts the account and market APIs.
    gateway_url: str | None = field(default_factory=lambda: _env("AGENTCORE_GATEWAY_URL"))
    # AgentCore Identity: OAuth2 client-credentials provider for Gateway auth.
    identity_provider: str | None = field(
        default_factory=lambda: _env("AGENTCORE_IDENTITY_PROVIDER")
    )
    # OTLP endpoint; in AgentCore Runtime the ADOT sidecar sets this automatically.
    otlp_endpoint: str | None = field(
        default_factory=lambda: _env("OTEL_EXPORTER_OTLP_ENDPOINT")
    )
    local_state_dir: str = field(
        default_factory=lambda: _env("LOCAL_STATE_DIR") or ".local_state"
    )

    @property
    def memory_enabled(self) -> bool:
        return self.memory_id is not None and self.aws_region is not None

    @property
    def gateway_enabled(self) -> bool:
        return self.gateway_url is not None
