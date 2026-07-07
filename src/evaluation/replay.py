"""Offline replay of the Pattern Detector's verification rule.

The live case graph asks the Pattern Detector agent to recompute a hit's
statistics with the canonical detection functions and reject alerts that do
not survive the canonical thresholds. That rule is deterministic, so it can
be replayed here without a model: an alert is escalated when a canonical-
threshold hit of the same pattern family exists for the same instrument and
account with an overlapping window, and dismissed otherwise. Evaluation
numbers from this module measure the pipeline's decision rule, not model
prose quality.
"""

from __future__ import annotations

import pandas as pd

from detection.config import DetectionConfig
from detection.detectors import run_detection
from detection.matching import pattern_family


def replay_triage(
    alerts: pd.DataFrame,
    events: pd.DataFrame,
    tick_sizes: dict[str, float],
    config: DetectionConfig | None = None,
) -> dict[int, str]:
    """Map each alert row index to escalate or dismiss via canonical re-verification."""
    strict = run_detection(events, tick_sizes, config or DetectionConfig())
    out: dict[int, str] = {}
    for row in alerts.itertuples():
        fam = pattern_family(str(row.pattern))
        matches = strict[
            (strict["pattern"].map(pattern_family) == fam)
            & (strict["instrument"] == row.instrument)
            & (strict["account_id"] == row.account_id)
            & (strict["window_start"] < row.window_end)
            & (strict["window_end"] > row.window_start)
        ]
        out[int(row.Index)] = "escalate" if not matches.empty else "dismiss"
    return out
