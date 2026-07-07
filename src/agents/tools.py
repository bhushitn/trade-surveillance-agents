"""Strands tools backed by the detection functions and mock APIs.

The Pattern Detector and Correlator tools recompute statistics with the same
vectorized functions the detection layer uses; the model never computes a
number itself. Context tools call the mock APIs directly in local runs; in
deployment the same operations are served through AgentCore Gateway as MCP
tools and these local bindings are not registered (see agents/graph.py).

Tools close over the event DataFrame for the session under review, so each
case graph gets tools bound to its own data.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
from strands import tool

from detection.config import DetectionConfig
from detection.features import (
    message_burst_stats,
    mid_series,
    order_lifecycle,
    pair_execution_stats,
    price_impact_around_cancellations,
)
from mockapis import account_service, market_reference


def build_case_tools(
    events: pd.DataFrame,
    tick_sizes: dict[str, float],
    config: DetectionConfig | None = None,
) -> dict[str, list[Any]]:
    """Tools for one case graph, bound to the session's event stream."""
    cfg = config or DetectionConfig()

    @tool
    def recompute_window_features(
        instrument: str, account_id: str, window_start: float, window_end: float
    ) -> str:
        """Recompute detection statistics for one account and window from raw events.

        Returns order counts, large cancelled orders with price impact and
        reversion in ticks, and message burst statistics as JSON.
        """
        w = events[
            (events["instrument"] == instrument)
            & (events["ts"] >= window_start)
            & (events["ts"] < window_end)
        ]
        acct = w[w["account_id"] == account_id]
        orders = order_lifecycle(events[events["instrument"] == instrument])
        mids = mid_series(events)
        impacts = price_impact_around_cancellations(
            orders, mids, tick_sizes, cfg.large_order_qty, cfg.reversion_horizon_s
        )
        sel = impacts[
            (impacts["account_id"] == account_id)
            & (impacts["ts_cancel"] >= window_start)
            & (impacts["ts_cancel"] < window_end)
        ]
        bursts = message_burst_stats(acct, cfg.window_s, window_start % cfg.window_s)
        return json.dumps(
            {
                "window_event_count": int(len(w)),
                "account_event_count": int(len(acct)),
                "account_executions": int((acct["event_type"] == "execute").sum()),
                "large_cancelled_orders": sel[
                    ["order_id", "side", "price", "quantity", "cancel_latency_s",
                     "impact_ticks", "reversion_ticks"]
                ].round(3).to_dict("records"),
                "burst_stats": bursts.drop(columns=["instrument", "venue"], errors="ignore")
                .round(3)
                .to_dict("records"),
            }
        )

    @tool
    def get_account_profile(account_id: str) -> str:
        """Fetch account type, risk rating, and onboarding data from the account master."""
        return json.dumps(account_service.get_account_profile(account_id))

    @tool
    def get_alert_history(account_id: str) -> str:
        """Fetch prior surveillance alerts and their dispositions for an account."""
        return json.dumps(account_service.get_alert_history(account_id))

    @tool
    def get_related_instruments(symbol: str) -> str:
        """Look up instruments economically linked to a symbol (same underlier)."""
        return json.dumps(market_reference.list_related(symbol))

    @tool
    def get_account_activity(
        instrument: str, account_id: str, start_ts: float, end_ts: float
    ) -> str:
        """Summarize one account's raw activity on an instrument over a time range."""
        sel = events[
            (events["instrument"] == instrument)
            & (events["account_id"] == account_id)
            & (events["ts"] >= start_ts)
            & (events["ts"] < end_ts)
        ]
        orders = order_lifecycle(sel) if not sel.empty else pd.DataFrame()
        n_large_cancels = (
            int(
                (
                    orders["ts_cancel"].notna()
                    & orders["ts_exec"].isna()
                    & (orders["quantity"] >= cfg.large_order_qty)
                ).sum()
            )
            if not orders.empty
            else 0
        )
        return json.dumps(
            {
                "instrument": instrument,
                "n_events": int(len(sel)),
                "n_new": int((sel["event_type"] == "new").sum()) if not sel.empty else 0,
                "n_cancel": int((sel["event_type"] == "cancel").sum()) if not sel.empty else 0,
                "n_execute": int((sel["event_type"] == "execute").sum()) if not sel.empty else 0,
                "n_large_cancels": n_large_cancels,
            }
        )

    @tool
    def get_counterparty_concentration(
        instrument: str, account_id: str, window_start: float, window_end: float
    ) -> str:
        """Executed-volume shares between this account and each counterparty in a window."""
        pair = pair_execution_stats(
            events[events["ts"].between(window_start, window_end)],
            tick_sizes,
            cfg.window_s,
            window_start % cfg.window_s,
        )
        sel = pair[
            (pair["instrument"] == instrument)
            & ((pair["account_a"] == account_id) | (pair["account_b"] == account_id))
        ]
        return json.dumps(sel.round(3).to_dict("records"))

    return {
        "pattern_detector": [recompute_window_features],
        "context": [get_account_profile, get_alert_history],
        "correlator": [
            get_related_instruments,
            get_account_activity,
            get_counterparty_concentration,
        ],
        "case_writer": [],
    }
