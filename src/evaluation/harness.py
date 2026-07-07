"""Detection and triage metrics with confidence intervals.

All metrics are computed against the synthetic generator's ground-truth
episodes. Wilson score intervals are used throughout because episode counts
are small; a normal approximation would produce intervals outside [0, 1].
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from detection.features import window_start
from detection.matching import label_hits
from schemas.events import GroundTruthEpisode


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass(frozen=True)
class Proportion:
    """A metric with its numerator, denominator, and 95 percent interval."""

    value: float
    k: int
    n: int
    ci_low: float
    ci_high: float

    @classmethod
    def of(cls, k: int, n: int) -> Proportion:
        lo, hi = wilson_ci(k, n)
        return cls(value=k / n if n else 0.0, k=k, n=n, ci_low=lo, ci_high=hi)

    def __str__(self) -> str:
        return f"{self.value:.3f} ({self.k}/{self.n}, 95% CI {self.ci_low:.3f}-{self.ci_high:.3f})"


def benign_account_windows(
    events: pd.DataFrame,
    episodes: list[GroundTruthEpisode],
    window_s: float = 60.0,
) -> int:
    """Number of active (account, instrument, window) units with no episode overlap.

    This is the denominator for the false-positive rate: each unit is one
    opportunity for the detector to raise a spurious hit.
    """
    df = events[["account_id", "instrument", "ts"]].copy()
    df["w"] = window_start(df["ts"], window_s, 0.0)
    units = df.drop_duplicates(["account_id", "instrument", "w"])
    flagged = set()
    for ep in episodes:
        w0 = math.floor(ep.start_ts / window_s) * window_s
        w1 = math.floor(ep.end_ts / window_s) * window_s
        for acct in ep.account_ids:
            w = w0
            while w <= w1:
                flagged.add((acct, ep.instrument, w))
                w += window_s
    mask = [
        (r.account_id, r.instrument, r.w) not in flagged
        for r in units.itertuples()
    ]
    return int(sum(mask))


def detection_metrics(
    hits: pd.DataFrame,
    episodes: list[GroundTruthEpisode],
    events: pd.DataFrame,
) -> dict[str, object]:
    """Recall, precision, and false-positive rate for a detection run."""
    ep, labeled = label_hits(hits, episodes)
    n_benign = benign_account_windows(events, episodes)
    n_fp = int(labeled["matched_episode_id"].isna().sum()) if not labeled.empty else 0
    by_pattern = {
        str(p): Proportion.of(int(g["matched"].sum()), len(g))
        for p, g in ep.groupby("pattern")
    }
    return {
        "family_recall": Proportion.of(int(ep["matched"].sum()), len(ep)),
        "exact_recall": Proportion.of(int(ep["matched_exact"].sum()), len(ep)),
        "hit_precision": Proportion.of(len(labeled) - n_fp, len(labeled)),
        "false_positive_rate": Proportion.of(n_fp, n_benign),
        "recall_by_pattern": by_pattern,
        "n_hits": len(labeled),
        "n_false_positive_hits": n_fp,
    }


def triage_metrics(
    labeled_hits: pd.DataFrame,
    episodes: list[GroundTruthEpisode],
    recommendations: dict[int, str],
    events: pd.DataFrame,
) -> dict[str, object]:
    """Metrics for a triage policy applied on top of labeled hits.

    recommendations maps hit row index to escalate, monitor, or dismiss.
    Escalations are what a reviewer is asked to act on, so precision and
    recall are computed over escalated hits only.
    """
    esc = labeled_hits.loc[[i for i, r in recommendations.items() if r == "escalate"]]
    matched_ids = set(esc["matched_episode_id"].dropna())
    n_fp_esc = int(esc["matched_episode_id"].isna().sum())
    ep_df, _ = label_hits(labeled_hits[[c for c in labeled_hits.columns
                                        if c not in ("matched_episode_id", "pattern_exact")]],
                          episodes)
    families_recalled = sum(
        1 for e in episodes if e.episode_id in matched_ids
    )
    n_benign = benign_account_windows(events, episodes)
    return {
        "episode_recall_after_triage": Proportion.of(families_recalled, len(episodes)),
        "escalation_precision": Proportion.of(len(esc) - n_fp_esc, len(esc)),
        "false_positive_rate_after_triage": Proportion.of(n_fp_esc, n_benign),
        "n_escalated": len(esc),
        "n_alerts_in": len(labeled_hits),
        "detector_family_recall": Proportion.of(int(ep_df["matched"].sum()), len(ep_df)),
    }
