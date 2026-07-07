"""Mock market-reference API.

Stands in for the instrument reference-data service that AgentCore Gateway
exposes as MCP tools in deployment. Backed by the generator's instrument
catalog so the correlator sees the same universe the events were drawn from.
"""

from __future__ import annotations

from typing import Any

from datagen.config import DEFAULT_INSTRUMENTS

_CATALOG = {spec.symbol: spec for spec in DEFAULT_INSTRUMENTS}


def get_instrument(symbol: str) -> dict[str, Any]:
    """Venue, tick size, and related listing for one instrument."""
    spec = _CATALOG.get(symbol)
    if spec is None:
        return {"symbol": symbol, "found": False}
    return {
        "symbol": spec.symbol,
        "found": True,
        "venue": spec.venue,
        "tick_size": spec.tick_size,
        "related": spec.related,
    }


def list_related(symbol: str) -> list[dict[str, Any]]:
    """Instruments economically linked to the given symbol (same underlier)."""
    spec = _CATALOG.get(symbol)
    if spec is None or spec.related is None:
        return []
    return [get_instrument(spec.related)]
