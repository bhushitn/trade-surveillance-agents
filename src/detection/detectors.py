"""Threshold detectors composed from the feature functions.

run_detection() is the single entry point the rest of the system uses. It
returns a DataFrame of hits (one row per pattern, account, instrument, window)
with a features column recording the statistics that fired. Windows are
evaluated twice, at offset 0 and at half a window, so episodes straddling a
boundary are not missed; matching and evaluation deduplicate by episode.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from detection.config import DetectionConfig
from detection.features import (
    message_burst_stats,
    mid_series,
    order_lifecycle,
    pair_execution_stats,
    price_impact_around_cancellations,
    window_start,
)
from schemas.detection import DetectionHit
from schemas.events import PatternType

HIT_COLUMNS = [
    "pattern",
    "instrument",
    "venue",
    "account_id",
    "window_start",
    "window_end",
    "score",
    "features",
]


def run_detection(
    events: pd.DataFrame,
    tick_sizes: dict[str, float],
    config: DetectionConfig | None = None,
) -> pd.DataFrame:
    """Run all detectors over the event stream."""
    cfg = config or DetectionConfig()
    orders = order_lifecycle(events)
    mids = mid_series(events)
    impacts = price_impact_around_cancellations(
        orders, mids, tick_sizes, cfg.large_order_qty, cfg.reversion_horizon_s
    )

    hits: list[dict[str, Any]] = []
    for offset in (0.0, cfg.hop_s):
        hits.extend(_spoof_layer_hits(events, orders, impacts, offset, cfg))
        hits.extend(_wash_hits(events, tick_sizes, offset, cfg))
        hits.extend(_stuffing_hits(events, orders, offset, cfg))

    if not hits:
        return pd.DataFrame(columns=HIT_COLUMNS)
    return (
        pd.DataFrame(hits)[HIT_COLUMNS]
        .sort_values(["window_start", "instrument", "account_id"])
        .reset_index(drop=True)
    )


def hits_to_models(hits: pd.DataFrame) -> list[DetectionHit]:
    """Validate hit rows into the schema the agents consume."""
    return [DetectionHit(**row) for row in hits.to_dict("records")]


def _score(margins: list[float]) -> float:
    """Average threshold margin, each capped at 2x, mapped to [0, 1]."""
    capped = [min(max(m, 0.0), 2.0) for m in margins]
    return round(sum(capped) / (2 * len(capped)), 3)


def _spoof_layer_hits(
    events: pd.DataFrame,
    orders: pd.DataFrame,
    impacts: pd.DataFrame,
    offset: float,
    cfg: DetectionConfig,
) -> list[dict[str, Any]]:
    if impacts.empty:
        return []
    imp = impacts.copy()
    imp["window_start"] = window_start(imp["ts_cancel"], cfg.window_s, offset)

    execs = events.loc[events["event_type"] == "execute"].copy()
    execs["window_start"] = window_start(execs["ts"], cfg.window_s, offset)
    exec_counts = execs.groupby(
        ["instrument", "account_id", "window_start", "side"]
    ).size()

    ow = orders.copy()
    ow["window_start"] = window_start(ow["ts_new"], cfg.window_s, offset)
    new_counts = ow.groupby(["instrument", "account_id", "window_start"]).size()
    exec_totals = (
        ow.loc[ow["ts_exec"].notna()]
        .groupby(["instrument", "account_id", "window_start"])
        .size()
    )

    hits: list[dict[str, Any]] = []
    grouped = imp.groupby(["instrument", "venue", "account_id", "window_start"])
    for (instrument, venue, account, w), grp in grouped:
        med_latency = float(grp["cancel_latency_s"].median())
        med_impact = float(grp["impact_ticks"].median())
        med_reversion = float(grp["reversion_ticks"].median())
        n_levels = int(grp["price"].nunique())
        side = str(grp["side"].mode().iloc[0])
        opp_side = "sell" if side == "buy" else "buy"
        n_opp_exec = int(exec_counts.get((instrument, account, w, opp_side), 0))
        n_new = int(new_counts.get((instrument, account, w), 0))
        n_exec = int(exec_totals.get((instrument, account, w), 0))
        otr = n_new / max(n_exec, 1)

        fired = (
            med_latency <= cfg.spoof_max_median_cancel_latency_s
            and med_impact >= cfg.min_impact_ticks
            and med_reversion <= cfg.max_reversion_ticks
            and n_opp_exec >= cfg.min_opposite_executions
        )
        if not fired:
            continue
        pattern = (
            PatternType.LAYERING if n_levels >= cfg.layering_min_levels else PatternType.SPOOFING
        )
        score = _score(
            [
                cfg.spoof_max_median_cancel_latency_s / max(med_latency, 1e-6),
                med_impact / cfg.min_impact_ticks,
                med_reversion / cfg.max_reversion_ticks,
                len(grp) / 1.0,
            ]
        )
        hits.append(
            {
                "pattern": pattern.value,
                "instrument": instrument,
                "venue": venue,
                "account_id": account,
                "window_start": float(w),
                "window_end": float(w) + cfg.window_s,
                "score": score,
                "features": {
                    "n_large_cancels": len(grp),
                    "median_cancel_latency_s": round(med_latency, 3),
                    "median_impact_ticks": round(med_impact, 2),
                    "median_reversion_ticks": round(med_reversion, 2),
                    "n_price_levels": n_levels,
                    "order_to_trade_ratio": round(otr, 2),
                    "opposite_side_executions": n_opp_exec,
                    "side": side,
                },
            }
        )
    return hits


def _wash_hits(
    events: pd.DataFrame,
    tick_sizes: dict[str, float],
    offset: float,
    cfg: DetectionConfig,
) -> list[dict[str, Any]]:
    pair = pair_execution_stats(events, tick_sizes, cfg.window_s, offset)
    if pair.empty:
        return []
    fired = pair.loc[
        (pair["n_trades"] >= cfg.wash_min_pair_trades)
        & (pair["qty"] >= cfg.wash_min_qty)
        & (pair["price_range_ticks"] <= cfg.wash_max_price_range_ticks)
        & (pair[["share_a", "share_b"]].min(axis=1) >= cfg.wash_min_share)
    ]
    hits: list[dict[str, Any]] = []
    for row in fired.itertuples():
        score = _score(
            [
                row.n_trades / cfg.wash_min_pair_trades,
                min(row.share_a, row.share_b) / cfg.wash_min_share,
                row.qty / cfg.wash_min_qty,
            ]
        )
        features = {
            "n_pair_trades": int(row.n_trades),
            "matched_qty": int(row.qty),
            "price_range_ticks": round(float(row.price_range_ticks), 2),
            "share_a": round(float(row.share_a), 3),
            "share_b": round(float(row.share_b), 3),
        }
        for account, counterparty in (
            (row.account_a, row.account_b),
            (row.account_b, row.account_a),
        ):
            hits.append(
                {
                    "pattern": PatternType.WASH_TRADING.value,
                    "instrument": row.instrument,
                    "venue": row.venue,
                    "account_id": account,
                    "window_start": float(row.window_start),
                    "window_end": float(row.window_start) + cfg.window_s,
                    "score": score,
                    "features": {**features, "counterparty": counterparty},
                }
            )
    return hits


def _stuffing_hits(
    events: pd.DataFrame,
    orders: pd.DataFrame,
    offset: float,
    cfg: DetectionConfig,
) -> list[dict[str, Any]]:
    bursts = message_burst_stats(events, cfg.window_s, offset)
    if bursts.empty:
        return []
    ow = orders.copy()
    ow["window_start"] = window_start(ow["ts_new"], cfg.window_s, offset)
    med_latency = (
        ow.loc[ow["cancel_latency_s"].notna()]
        .groupby(["instrument", "account_id", "window_start"])["cancel_latency_s"]
        .median()
    )

    fired = bursts.loc[
        (bursts["max_msgs_1s"] >= cfg.stuffing_min_burst_rate)
        & (bursts["cancel_to_new_ratio"] >= cfg.stuffing_min_cancel_to_new_ratio)
    ]
    hits: list[dict[str, Any]] = []
    for row in fired.itertuples():
        latency = float(
            med_latency.get((row.instrument, row.account_id, row.window_start), float("inf"))
        )
        if latency > cfg.stuffing_max_median_latency_s:
            continue
        score = _score(
            [
                row.max_msgs_1s / cfg.stuffing_min_burst_rate,
                float(row.cancel_to_new_ratio) / cfg.stuffing_min_cancel_to_new_ratio,
                cfg.stuffing_max_median_latency_s / max(latency, 1e-6),
            ]
        )
        hits.append(
            {
                "pattern": PatternType.QUOTE_STUFFING.value,
                "instrument": row.instrument,
                "venue": row.venue,
                "account_id": row.account_id,
                "window_start": float(row.window_start),
                "window_end": float(row.window_start) + cfg.window_s,
                "score": score,
                "features": {
                    "n_messages": int(row.n_msgs),
                    "max_msgs_per_second": int(row.max_msgs_1s),
                    "cancel_to_new_ratio": round(float(row.cancel_to_new_ratio), 3),
                    "median_cancel_latency_s": round(latency, 4),
                },
            }
        )
    return hits
