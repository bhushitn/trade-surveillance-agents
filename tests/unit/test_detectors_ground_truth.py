"""Detector recall and false-positive gates against the CI synthetic dataset.

These are the guarantees the detection layer makes about the generator's
answer key. The evaluation harness reports the same quantities with
confidence intervals on the full frozen dataset; these tests are the fast
regression gate.
"""

from __future__ import annotations

import pandas as pd
import pytest

from datagen.config import GeneratorConfig
from datagen.generator import generate
from detection.detectors import hits_to_models, run_detection
from detection.matching import label_hits
from schemas.events import OrderEvent


@pytest.fixture(scope="module")
def dataset() -> tuple[pd.DataFrame, list, dict[str, float]]:
    config = GeneratorConfig.ci()
    events, episodes = generate(config)
    ticks = {spec.symbol: spec.tick_size for spec in config.instruments}
    return events, episodes, ticks


@pytest.fixture(scope="module")
def labeled(dataset: tuple) -> tuple[pd.DataFrame, pd.DataFrame]:
    events, episodes, ticks = dataset
    hits = run_detection(events, ticks)
    return label_hits(hits, episodes)


def test_generator_is_deterministic(dataset: tuple) -> None:
    events, _, _ = dataset
    events2, _ = generate(GeneratorConfig.ci())
    pd.testing.assert_frame_equal(events, events2)


def test_events_conform_to_schema(dataset: tuple) -> None:
    events, _, _ = dataset
    sample = events.sample(200, random_state=0)
    for row in sample.to_dict("records"):
        OrderEvent(**row)


def test_every_episode_family_is_recalled(labeled: tuple) -> None:
    episodes, _ = labeled
    missed = episodes[~episodes.matched]
    assert missed.empty, f"missed episodes:\n{missed}"


def test_pattern_exact_recall_floor(labeled: tuple) -> None:
    episodes, _ = labeled
    exact = episodes.matched_exact.mean()
    assert exact >= 0.8, f"pattern-exact recall {exact:.2f} below floor"


def test_no_false_positive_hits(labeled: tuple) -> None:
    _, hits = labeled
    false_positives = hits[hits.matched_episode_id.isna()]
    assert false_positives.empty, (
        f"{len(false_positives)} hits match no episode:\n"
        f"{false_positives[['pattern', 'account_id', 'window_start', 'features']]}"
    )


def test_hits_validate_against_schema(labeled: tuple) -> None:
    _, hits = labeled
    models = hits_to_models(hits[hits.columns.drop(['matched_episode_id', 'pattern_exact'])])
    assert len(models) == len(hits)
    assert all(0.0 <= m.score <= 1.0 for m in models)
