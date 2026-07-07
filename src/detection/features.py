"""Vectorized feature functions over the event stream.

Every function here is deterministic and unit tested against hand-built
frames and the generator's ground truth. The detectors in detectors.py
apply thresholds to these outputs; no statistic is computed anywhere else.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def order_lifecycle(events: pd.DataFrame) -> pd.DataFrame:
    """One row per order: placement fields plus first cancel and execute timestamps."""
    news = (
        events.loc[
            events["event_type"] == "new",
            ["order_id", "ts", "account_id", "instrument", "venue", "side", "price", "quantity"],
        ]
        .rename(columns={"ts": "ts_new"})
        .set_index("order_id")
    )
    ts_cancel = events.loc[events["event_type"] == "cancel"].groupby("order_id")["ts"].min()
    ts_exec = events.loc[events["event_type"] == "execute"].groupby("order_id")["ts"].min()
    out = news.assign(
        ts_cancel=ts_cancel.reindex(news.index),
        ts_exec=ts_exec.reindex(news.index),
    )
    out["cancel_latency_s"] = out["ts_cancel"] - out["ts_new"]
    return out.reset_index()


def order_to_trade_ratio(events: pd.DataFrame) -> float:
    """New orders per execution. High values mean quoting without trading."""
    n_new = int((events["event_type"] == "new").sum())
    n_exec = int((events["event_type"] == "execute").sum())
    return n_new / max(n_exec, 1)


def mid_series(events: pd.DataFrame, smooth_s: int = 3) -> dict[str, np.ndarray]:
    """Per-instrument, per-second quote-derived mid proxy.

    Best bid (highest buy quote) and best ask (lowest sell quote) per second,
    forward filled, averaged, then a trailing rolling median over smooth_s
    seconds. Quote based rather than trade based so it tracks placement-time
    prices without execution lag, and side aware so a second with quotes on
    only one side does not drag the estimate toward that side's offsets.
    """
    horizon = int(events["ts"].max()) + 31
    news = events.loc[events["event_type"] == "new"]
    out: dict[str, np.ndarray] = {}
    for sym, grp in news.groupby("instrument"):
        sec = grp["ts"].astype(int)
        buys = grp.loc[grp["side"] == "buy"]
        sells = grp.loc[grp["side"] == "sell"]
        bb = (
            buys.groupby(sec.loc[buys.index])["price"]
            .max()
            .reindex(range(horizon))
            .ffill()
            .bfill()
        )
        ba = (
            sells.groupby(sec.loc[sells.index])["price"]
            .min()
            .reindex(range(horizon))
            .ffill()
            .bfill()
        )
        mid = pd.concat([bb, ba], axis=1).mean(axis=1)
        out[str(sym)] = mid.rolling(smooth_s, min_periods=1).median().to_numpy()
    return out


def price_impact_around_cancellations(
    orders: pd.DataFrame,
    mids: dict[str, np.ndarray],
    tick_sizes: dict[str, float],
    large_qty: int,
    reversion_horizon_s: float,
    baseline_lookback_s: int = 10,
) -> pd.DataFrame:
    """Signed mid move while each large cancelled order rested, and after its cancel.

    impact_ticks: mid move from a pre-placement baseline to the cancel, positive
    toward the order's side (a buy order with positive impact saw the price rise
    while it rested). The baseline is the side-adverse extremum of the mid over
    the baseline_lookback_s seconds ending just before placement (lowest recent
    level for a buy, highest for a sell): in repeated spoof cycles the mid has
    often not fully reverted from the previous cycle when the next order
    arrives, so a point baseline understates the order's contribution.
    reversion_ticks: mid move from the cancel to the most-reverted level reached
    within reversion_horizon_s after it, same sign convention (negative means
    the move reversed). An extremum rather than a point read because quote
    updates are sparse enough that the post-cancel trough lands at a variable
    lag, and a repeated cycle can re-displace the mid before the horizon ends.
    Only orders with quantity >= large_qty that were cancelled without
    executing are scored.
    """
    sel = orders.loc[
        orders["ts_cancel"].notna() & orders["ts_exec"].isna() & (orders["quantity"] >= large_qty)
    ].copy()
    if sel.empty:
        return sel.assign(
            impact_ticks=pd.Series(dtype=float), reversion_ticks=pd.Series(dtype=float)
        )

    parts = []
    for sym, grp in sel.groupby("instrument"):
        m = mids[str(sym)]
        tick = tick_sizes[str(sym)]
        last = len(m) - 1
        s = pd.Series(m)
        pre_min = s.rolling(baseline_lookback_s, min_periods=1).min().to_numpy()
        pre_max = s.rolling(baseline_lookback_s, min_periods=1).max().to_numpy()
        h = max(int(reversion_horizon_s), 1)
        post_min = s.rolling(h, min_periods=1).min().to_numpy()
        post_max = s.rolling(h, min_periods=1).max().to_numpy()
        i_new = (grp["ts_new"].astype(int) - 1).clip(0, last).to_numpy()
        i_cxl = grp["ts_cancel"].astype(int).clip(0, last).to_numpy()
        i_rev = (grp["ts_cancel"] + reversion_horizon_s).astype(int).clip(0, last).to_numpy()
        is_buy = (grp["side"] == "buy").to_numpy()
        sign = np.where(is_buy, 1.0, -1.0)
        baseline = np.where(is_buy, pre_min[i_new], pre_max[i_new])
        reverted = np.where(is_buy, post_min[i_rev], post_max[i_rev])
        # Rounded so a move of exactly N ticks compares cleanly against integer
        # thresholds instead of landing at N - 1e-12.
        grp = grp.assign(
            impact_ticks=np.round(sign * (m[i_cxl] - baseline) / tick, 6),
            reversion_ticks=np.round(sign * (reverted - m[i_cxl]) / tick, 6),
        )
        parts.append(grp)
    return pd.concat(parts, ignore_index=True)


def window_start(ts: pd.Series, window_s: float, offset: float) -> pd.Series:
    """Tumbling window assignment; timestamps before offset land in the first window."""
    return (np.maximum(ts - offset, 0.0) // window_s) * window_s + offset


def pair_execution_stats(
    events: pd.DataFrame,
    tick_sizes: dict[str, float],
    window_s: float,
    offset: float,
) -> pd.DataFrame:
    """Per (instrument, window, counterparty pair): trade count, quantity, price range, shares.

    share_a and share_b are the pair's executed quantity as a fraction of each
    account's total executed quantity (either side of the tape) in the window.
    """
    ex = events.loc[
        (events["event_type"] == "execute") & events["counterparty_id"].notna()
    ].copy()
    if ex.empty:
        return pd.DataFrame(
            columns=[
                "instrument", "venue", "window_start", "account_a", "account_b",
                "n_trades", "qty", "price_range_ticks", "share_a", "share_b",
            ]
        )
    ex["window_start"] = window_start(ex["ts"], window_s, offset)
    a = ex["account_id"].to_numpy()
    b = ex["counterparty_id"].to_numpy()
    ex["account_a"] = np.where(a <= b, a, b)
    ex["account_b"] = np.where(a <= b, b, a)

    pair = (
        ex.groupby(["instrument", "venue", "window_start", "account_a", "account_b"])
        .agg(
            n_trades=("order_id", "count"),
            qty=("quantity", "sum"),
            pmin=("price", "min"),
            pmax=("price", "max"),
        )
        .reset_index()
    )
    ticks = pair["instrument"].map(tick_sizes)
    pair["price_range_ticks"] = ((pair["pmax"] - pair["pmin"]) / ticks).round(6)

    vol = pd.concat(
        [
            ex[["instrument", "window_start", "account_id", "quantity"]].rename(
                columns={"account_id": "account"}
            ),
            ex[["instrument", "window_start", "counterparty_id", "quantity"]].rename(
                columns={"counterparty_id": "account"}
            ),
        ]
    )
    acct_vol = vol.groupby(["instrument", "window_start", "account"])["quantity"].sum()

    idx_a = pd.MultiIndex.from_frame(pair[["instrument", "window_start", "account_a"]])
    idx_b = pd.MultiIndex.from_frame(pair[["instrument", "window_start", "account_b"]])
    pair["share_a"] = pair["qty"].to_numpy() / acct_vol.reindex(idx_a).to_numpy()
    pair["share_b"] = pair["qty"].to_numpy() / acct_vol.reindex(idx_b).to_numpy()
    return pair.drop(columns=["pmin", "pmax"])


def message_burst_stats(
    events: pd.DataFrame, window_s: float, offset: float
) -> pd.DataFrame:
    """Per (instrument, account, window): message count, peak 1-second rate, cancel ratio.

    cancel_to_new_ratio is cancels per new order in the window. Benign flow
    sits near 0.45 (the generator's cancel fraction); a stuffing burst where
    every order is cancelled sits near 1.0.
    """
    msg = events.loc[events["event_type"].isin(["new", "cancel"])].copy()
    if msg.empty:
        return pd.DataFrame(
            columns=[
                "instrument", "venue", "account_id", "window_start",
                "n_msgs", "max_msgs_1s", "cancel_to_new_ratio",
            ]
        )
    msg["sec"] = msg["ts"].astype(int)
    msg["window_start"] = window_start(msg["ts"], window_s, offset)
    msg["is_cancel"] = msg["event_type"] == "cancel"
    per_sec = (
        msg.groupby(["instrument", "venue", "account_id", "window_start", "sec"])
        .size()
        .rename("n")
        .reset_index()
    )
    peaks = (
        per_sec.groupby(["instrument", "venue", "account_id", "window_start"])["n"]
        .max()
        .rename("max_msgs_1s")
    )
    totals = msg.groupby(["instrument", "venue", "account_id", "window_start"]).agg(
        n_msgs=("event_type", "count"),
        n_new=("is_cancel", lambda s: int((~s).sum())),
        n_cancel=("is_cancel", "sum"),
    )
    totals["cancel_to_new_ratio"] = totals["n_cancel"] / totals["n_new"].clip(lower=1)
    return totals.drop(columns=["n_new", "n_cancel"]).join(peaks).reset_index()
