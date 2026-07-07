"""Exact-value tests for the detection feature functions on hand-built frames."""

from __future__ import annotations

import pandas as pd
import pytest

from detection.features import (
    message_burst_stats,
    mid_series,
    order_lifecycle,
    order_to_trade_ratio,
    pair_execution_stats,
    price_impact_around_cancellations,
    window_start,
)


def make_events(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "event_id": 0,
        "ts": 0.0,
        "event_type": "new",
        "order_id": "X-1",
        "account_id": "A",
        "instrument": "TST",
        "venue": "XTST",
        "side": "buy",
        "price": 10.0,
        "quantity": 100,
        "counterparty_id": None,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_order_lifecycle_latencies() -> None:
    events = make_events(
        [
            {"order_id": "A-1", "ts": 0.0, "event_type": "new"},
            {"order_id": "A-1", "ts": 10.0, "event_type": "cancel"},
            {"order_id": "A-2", "ts": 1.0, "event_type": "new"},
            {"order_id": "A-2", "ts": 3.0, "event_type": "execute"},
        ]
    )
    orders = order_lifecycle(events).set_index("order_id")
    assert orders.loc["A-1", "cancel_latency_s"] == 10.0
    assert pd.isna(orders.loc["A-1", "ts_exec"])
    assert orders.loc["A-2", "ts_exec"] == 3.0
    assert pd.isna(orders.loc["A-2", "cancel_latency_s"])


def test_order_to_trade_ratio() -> None:
    events = make_events(
        [{"event_type": "new"}] * 4 + [{"event_type": "execute"}] * 2
    )
    assert order_to_trade_ratio(events) == 2.0
    assert order_to_trade_ratio(make_events([{"event_type": "new"}] * 3)) == 3.0


def test_window_start_assignment() -> None:
    ts = pd.Series([0.0, 29.9, 30.0, 61.0])
    assert window_start(ts, 60.0, 0.0).tolist() == [0.0, 0.0, 0.0, 60.0]
    assert window_start(ts, 60.0, 30.0).tolist() == [30.0, 30.0, 30.0, 30.0]


def quote_background(mid_at: dict[range, float] | None = None, seconds: int = 31) -> list[dict]:
    """One buy one tick below and one sell one tick above the target mid, each second."""
    rows = []
    for i in range(seconds):
        p = 10.00
        if mid_at:
            for rng, price in mid_at.items():
                if i in rng:
                    p = price
        rows.append(
            {"order_id": f"BUY-{i}", "ts": float(i), "side": "buy", "price": round(p - 0.01, 2)}
        )
        rows.append(
            {"order_id": f"SELL-{i}", "ts": float(i), "side": "sell", "price": round(p + 0.01, 2)}
        )
    return rows


def test_mid_series_averages_best_bid_and_ask() -> None:
    events = make_events(quote_background(seconds=10))
    mid = mid_series(events)["TST"]
    assert (mid[:10] == 10.0).all()


def test_price_impact_and_reversion_signs() -> None:
    # Quoted mid: 10.00 for seconds 0..5, 10.05 for 6..14, 10.00 for 15..30.
    rows = quote_background({range(6, 15): 10.05})
    # Large buy order rests from t=5.5 to t=14.5 and never executes.
    rows.append(
        {"order_id": "BIG", "ts": 5.5, "price": 10.00, "quantity": 2000, "side": "buy"}
    )
    rows.append({"order_id": "BIG", "ts": 14.5, "event_type": "cancel", "quantity": 0})
    events = make_events(rows)
    orders = order_lifecycle(events)
    mids = mid_series(events)
    imp = price_impact_around_cancellations(
        orders, mids, {"TST": 0.01}, large_qty=1000, reversion_horizon_s=10.0
    )
    assert len(imp) == 1
    row = imp.iloc[0]
    # Trailing 3s median at cancel (sec 14) is 10.05; baseline is the second
    # before placement (sec 4), where the median is 10.00.
    assert row["impact_ticks"] == pytest.approx(5.0)
    # Within ten seconds of the cancel the lowest median level is back at 10.00.
    assert row["reversion_ticks"] == pytest.approx(-5.0)


def test_price_impact_sell_side_sign_flips() -> None:
    rows = quote_background({range(6, 15): 9.95})
    rows.append(
        {"order_id": "BIG", "ts": 5.5, "price": 10.00, "quantity": 2000, "side": "sell"}
    )
    rows.append(
        {"order_id": "BIG", "ts": 14.5, "event_type": "cancel", "quantity": 0, "side": "sell"}
    )
    events = make_events(rows)
    imp = price_impact_around_cancellations(
        order_lifecycle(events), mid_series(events), {"TST": 0.01}, 1000, 10.0
    )
    # A sell-side order that saw the price fall while resting has positive impact.
    assert imp.iloc[0]["impact_ticks"] == pytest.approx(5.0)
    assert imp.iloc[0]["reversion_ticks"] == pytest.approx(-5.0)


def test_pair_execution_stats() -> None:
    rows = []
    for i in range(6):
        rows.append(
            {
                "order_id": f"W-{i}",
                "ts": float(i * 5),
                "event_type": "execute",
                "account_id": "A" if i % 2 == 0 else "B",
                "counterparty_id": "B" if i % 2 == 0 else "A",
                "price": 10.00,
                "quantity": 500,
            }
        )
    # One unrelated execution so shares are below 1 for account C.
    rows.append(
        {
            "order_id": "O-1",
            "ts": 8.0,
            "event_type": "execute",
            "account_id": "A",
            "counterparty_id": "C",
            "price": 10.01,
            "quantity": 100,
        }
    )
    events = make_events(rows)
    pair = pair_execution_stats(events, {"TST": 0.01}, window_s=60.0, offset=0.0)
    ab = pair[(pair.account_a == "A") & (pair.account_b == "B")].iloc[0]
    assert ab["n_trades"] == 6
    assert ab["qty"] == 3000
    assert ab["price_range_ticks"] == 0.0
    assert ab["share_a"] == pytest.approx(3000 / 3100)  # A also traded 100 with C
    assert ab["share_b"] == pytest.approx(1.0)


def test_message_burst_stats() -> None:
    rows = []
    for i in range(50):
        ts = 10.0 + i * 0.01
        rows.append({"order_id": f"S-{i}", "ts": ts, "event_type": "new"})
        rows.append({"order_id": f"S-{i}", "ts": ts + 0.004, "event_type": "cancel"})
    events = make_events(rows)
    stats = message_burst_stats(events, window_s=60.0, offset=0.0)
    row = stats.iloc[0]
    assert row["n_msgs"] == 100
    assert row["max_msgs_1s"] == 100  # the whole burst lands in second 10
    assert row["cancel_to_new_ratio"] == pytest.approx(1.0)
