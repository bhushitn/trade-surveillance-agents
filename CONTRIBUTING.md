# Contributing

## Development setup

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[serve,dev]"
```

Python 3.12 or newer is required.

## Checks

CI runs these three commands on every pull request; run them locally first:

```
ruff check src tests
mypy src
pytest -q
```

The test suite includes `tests/unit/test_detectors_ground_truth.py`, which
asserts recall, precision, and false-positive floors against the frozen
dataset in `eval/dataset/`. A change that regresses detection quality fails
CI by design. If your change intentionally shifts detection behavior,
regenerate the evaluation notebooks (02 and 03), update the asserted floors
with the new measured values, and explain the tradeoff in the pull request.

## Ground rules

- The frozen dataset is the answer key. Do not edit `eval/dataset/` to make
  a test pass; fix the detector or justify a regenerated dataset with a new
  manifest.
- No code path may file, escalate, or transmit a case anywhere. The review
  API validates only `approve` and `dismiss`, and
  `test_no_filing_surface` enforces this. Pull requests adding a filing
  surface will be declined regardless of implementation quality.
- Detection statistics stay in `src/detection/` as tested, vectorized
  functions. Agents interpret their output; they do not compute.
- Regulatory citations must be verifiable. If a source cannot be verified,
  write "source unavailable" rather than inventing one.
- Type hints and docstrings on public functions; keep `mypy src` clean.

## Pull requests

Keep them small and single-purpose. State what changed, why, and how you
verified it. If the change touches detection or evaluation, include the
before/after metrics from the notebooks.
