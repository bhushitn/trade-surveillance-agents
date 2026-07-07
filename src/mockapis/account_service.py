"""Mock account-master API.

Stands in for the compliance account system that AgentCore Gateway exposes as
MCP tools in deployment. Profiles are derived deterministically from the
account id hash so responses are stable across runs without leaking the
synthetic generator's ground truth.
"""

from __future__ import annotations

import hashlib
from typing import Any

_ACCOUNT_TYPES = ["retail", "proprietary", "institutional", "market_maker"]
_RISK_RATINGS = ["low", "medium", "high"]
_PATTERNS = ["spoofing", "layering", "wash_trading", "quote_stuffing", "marking_the_close"]
_DISPOSITIONS = ["dismissed", "escalated", "closed_no_action"]


def _digest(account_id: str, salt: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{salt}:{account_id}".encode()).digest()[:4], "big")


def get_account_profile(account_id: str) -> dict[str, Any]:
    """Account type, risk rating, and onboarding metadata for one account."""
    d = _digest(account_id, "profile")
    return {
        "account_id": account_id,
        "account_type": _ACCOUNT_TYPES[d % len(_ACCOUNT_TYPES)],
        "risk_rating": _RISK_RATINGS[(d >> 8) % len(_RISK_RATINGS)],
        "onboarded": f"20{18 + (d >> 16) % 8:02d}-{1 + (d >> 4) % 12:02d}-{1 + d % 28:02d}",
        "jurisdiction": "US",
    }


def get_alert_history(account_id: str) -> list[dict[str, Any]]:
    """Prior surveillance alerts for one account, oldest first."""
    d = _digest(account_id, "alerts")
    n = d % 4  # 0 to 3 prior alerts
    alerts = []
    for i in range(n):
        di = _digest(account_id, f"alert{i}")
        alerts.append(
            {
                "alert_id": f"AL-{di % 100000:05d}",
                "pattern": _PATTERNS[di % len(_PATTERNS)],
                "date": f"202{5 - i}-{1 + di % 12:02d}-{1 + (di >> 8) % 28:02d}",
                "disposition": _DISPOSITIONS[(di >> 16) % len(_DISPOSITIONS)],
            }
        )
    return alerts
