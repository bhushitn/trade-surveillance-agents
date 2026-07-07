"""Context-agent tools served through AgentCore Gateway.

Gateway fronts the account-master and market-reference APIs (registered from
their OpenAPI specs, see deploy/gateway_targets.json) and exposes them to
agents as MCP tools over streamable HTTP with OAuth. When the Gateway is not
configured this module is unused and agents/tools.py binds the mock APIs in
process; the tool names and payloads match in both modes.
"""

from __future__ import annotations

from typing import Any

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

from services.identity import gateway_token
from services.settings import Settings


def gateway_tools(settings: Settings) -> tuple[MCPClient, list[Any]]:
    """Open an MCP session against the Gateway and list its tools.

    The returned client is a context manager the caller must keep open for
    the lifetime of the agents using the tools.
    """
    if settings.gateway_url is None:
        raise ValueError("AGENTCORE_GATEWAY_URL is not set; use the in-process mock APIs")
    token = gateway_token(settings)
    url = settings.gateway_url

    def transport() -> Any:
        return streamablehttp_client(
            url, headers={"Authorization": f"Bearer {token}"} if token else None
        )

    client = MCPClient(transport)
    client.start()
    return client, list(client.list_tools_sync())
