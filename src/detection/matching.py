"""Attribute detection hits to ground-truth episodes.

Used by the unit tests and the evaluation harness. A hit matches an episode
when the instrument matches, the hit account is one of the episode's accounts,
the windows overlap in time, and the patterns agree. Spoofing and layering are
matched as a family by default (a window at a cycle boundary can see fewer
price tiers than the episode placed overall); pattern-exact agreement is
reported separately so the eval can score both.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from schemas.events import GroundTruthEpisode, PatternType

_FAMILY = {
    PatternType.SPOOFING.value: "spoof_layer",
    PatternType.LAYERING.value: "spoof_layer",
    PatternType.WASH_TRADING.value: "wash_trading",
    PatternType.QUOTE_STUFFING.value: "quote_stuffing",
}


def pattern_family(pattern: str) -> str:
    """Collapse spoofing and layering into one family; other patterns map to themselves."""
    return _FAMILY[pattern]


def label_hits(
    hits: pd.DataFrame, episodes: Iterable[GroundTruthEpisode]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (episode table with matched flags, hit table with attribution).

    Episode columns: episode_id, pattern, matched, matched_exact, n_hits.
    Hit table gains matched_episode_id (None means false positive) and
    pattern_exact.
    """
    hits = hits.copy()
    hits["matched_episode_id"] = None
    hits["pattern_exact"] = False

    episode_rows = []
    for ep in episodes:
        mask = (
            (hits["instrument"] == ep.instrument)
            & hits["account_id"].isin(ep.account_ids)
            & (hits["window_start"] < ep.end_ts)
            & (hits["window_end"] > ep.start_ts)
            & (hits["pattern"].map(_FAMILY) == _FAMILY[ep.pattern.value])
        )
        exact = mask & (hits["pattern"] == ep.pattern.value)
        unclaimed = mask & hits["matched_episode_id"].isna()
        hits.loc[unclaimed, "matched_episode_id"] = ep.episode_id
        hits.loc[exact, "pattern_exact"] = True
        episode_rows.append(
            {
                "episode_id": ep.episode_id,
                "pattern": ep.pattern.value,
                "matched": bool(mask.any()),
                "matched_exact": bool(exact.any()),
                "n_hits": int(mask.sum()),
            }
        )
    return pd.DataFrame(episode_rows), hits
