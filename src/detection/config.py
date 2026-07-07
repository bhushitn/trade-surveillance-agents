"""Detection thresholds.

Values are calibrated against the synthetic generator's benign flow (see
docs/SYNTHETIC.md) and gated in CI by the evaluation harness. Each threshold
states what it separates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionConfig:
    window_s: float = 60.0
    hop_s: float = 30.0

    # Spoofing and layering. Benign flow cancels on a mean 40 s clock and its
    # order sizes have a log-normal median near 55, so a resting order of 1000+
    # shares cancelled within seconds of an opposite-side fill is the signature.
    # Reversion is measured as the most-reverted level within the horizon, so
    # benign quote noise produces excursions near -1 tick; -2 separates them
    # from injected displacements of 4 ticks and more.
    large_order_qty: int = 1000
    spoof_max_median_cancel_latency_s: float = 10.0
    min_impact_ticks: float = 2.0
    max_reversion_ticks: float = -2.0
    min_opposite_executions: int = 1
    layering_min_levels: int = 4
    reversion_horizon_s: float = 10.0

    # Wash trading. Benign counterparties are spread across all accounts, so a
    # pair trading repeatedly with each other at a flat price stands out.
    wash_min_pair_trades: int = 4
    wash_min_share: float = 0.4
    wash_max_price_range_ticks: float = 3.0
    wash_min_qty: int = 1000

    # Quote stuffing. Benign per-account message rates are single digits per
    # second and 45 percent of benign orders cancel; a burst where nearly every
    # new order is cancelled almost immediately is the signature.
    stuffing_min_burst_rate: int = 40
    stuffing_min_cancel_to_new_ratio: float = 0.9
    stuffing_max_median_latency_s: float = 0.2

    @classmethod
    def alerting(cls) -> DetectionConfig:
        """Loosened first-stage thresholds that over-alert on purpose.

        Production surveillance systems tune the first stage toward recall and
        rely on downstream triage for precision. This preset feeds the agent
        pipeline evaluation: the case graph re-verifies each alert with the
        canonical thresholds above.
        """
        return cls(
            spoof_max_median_cancel_latency_s=25.0,
            min_impact_ticks=1.0,
            max_reversion_ticks=-0.5,
            wash_min_pair_trades=3,
            wash_min_share=0.2,
            wash_min_qty=500,
            stuffing_min_burst_rate=20,
            stuffing_min_cancel_to_new_ratio=0.7,
            stuffing_max_median_latency_s=1.0,
        )
