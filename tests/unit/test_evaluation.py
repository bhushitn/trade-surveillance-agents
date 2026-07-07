"""Harness math and the alert-then-triage pipeline on the CI dataset."""

from __future__ import annotations

import pytest

from datagen.config import GeneratorConfig
from datagen.generator import generate
from detection.config import DetectionConfig
from detection.detectors import run_detection
from detection.matching import label_hits
from evaluation.harness import Proportion, detection_metrics, triage_metrics, wilson_ci
from evaluation.replay import replay_triage


def test_wilson_ci_brackets_proportion() -> None:
    lo, hi = wilson_ci(8, 10)
    assert lo < 0.8 < hi
    assert wilson_ci(0, 0) == (0.0, 1.0)
    assert wilson_ci(10, 10)[1] == 1.0
    assert wilson_ci(0, 10)[0] == 0.0


def test_proportion_str() -> None:
    p = Proportion.of(3, 4)
    assert "0.750" in str(p) and "3/4" in str(p)


@pytest.fixture(scope="module")
def ci_run() -> tuple:
    config = GeneratorConfig.ci()
    events, episodes = generate(config)
    ticks = {s.symbol: s.tick_size for s in config.instruments}
    return events, episodes, ticks


def test_detection_metrics_on_ci(ci_run: tuple) -> None:
    events, episodes, ticks = ci_run
    m = detection_metrics(run_detection(events, ticks), episodes, events)
    assert m["family_recall"].value == 1.0
    assert m["false_positive_rate"].value == 0.0
    assert m["false_positive_rate"].n > 1000  # denominator is benign account-windows


def test_alerting_config_over_alerts_and_triage_restores_precision(ci_run: tuple) -> None:
    events, episodes, ticks = ci_run
    strict_hits = run_detection(events, ticks)
    alerts = run_detection(events, ticks, DetectionConfig.alerting())
    assert len(alerts) > len(strict_hits)
    _, labeled = label_hits(alerts, episodes)
    assert labeled["matched_episode_id"].isna().sum() > 0  # loose stage raises false alerts

    rec = replay_triage(labeled, events, ticks)
    m = triage_metrics(labeled, episodes, rec, events)
    assert m["escalation_precision"].value == 1.0
    assert m["episode_recall_after_triage"].value == 1.0
    assert m["n_escalated"] < m["n_alerts_in"]
