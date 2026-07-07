"""Gateway authentication through AgentCore Identity.

In deployment the Context Agent's tools live behind AgentCore Gateway, which
requires an OAuth2 bearer token. AgentCore Identity holds the client
credentials and vends tokens; agent code never sees a client secret. Locally
there is no Gateway, so no token is issued and the tools bind to the mock
APIs in process.
"""

from __future__ import annotations

from services.settings import Settings


def gateway_token(settings: Settings) -> str | None:
    """Fetch a bearer token for the Gateway, or None when running locally."""
    if not settings.gateway_enabled or settings.identity_provider is None:
        return None
    from bedrock_agentcore.identity.auth import requires_access_token

    token_holder: dict[str, str] = {}

    @requires_access_token(
        provider_name=settings.identity_provider,
        scopes=["gateway:invoke"],
        auth_flow="M2M",
    )
    async def _fetch(*, access_token: str) -> None:
        token_holder["token"] = access_token

    import asyncio

    asyncio.get_event_loop().run_until_complete(_fetch())
    return token_holder.get("token")
