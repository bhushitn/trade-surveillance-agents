# trade-surveillance-agents

Multi-agent trade surveillance pipeline on the Strands Agents SDK and Amazon
Bedrock AgentCore. Synthetic order-book events, deterministic statistical
detection of spoofing, layering, wash trading, and quote stuffing, and a
four-agent graph that drafts investigator-ready case packages for a human
compliance reviewer. The system never files anything.

## The problem

Rule-based surveillance alerts fire constantly and mostly on noise. The
expensive part is not the alert; it is the investigation that turns one
statistical anomaly into a written, defensible case memo. This system
automates the investigation, not the decision:

```
 synthetic order-book events
          |
          v
 +--------------------+     alerting thresholds (recall-tuned)
 | detection layer    | --> 146 alerts on the frozen dataset
 | (vectorized numpy) |
 +--------------------+
          |  one graph run per alert
          v
 +------------------+
 | pattern_detector |  re-verifies the alert against canonical thresholds
 +------------------+
      |          |
      v          v
 +---------+ +------------+
 | context | | correlator |  account history       related instruments,
 +---------+ +------------+  via Gateway MCP       coordinated accounts
      |          |
      v          v
 +-------------+
 | case_writer |  citation-backed memo, no tools, JSON contract
 +-------------+
          |
          v
 +---------------+
 | review queue  |  human approves, edits, or dismisses
 +---------------+
          X  no node, tool, or endpoint files anything
```

## Where this sits commercially

This is the product category occupied by Nasdaq SMARTS, NICE Actimize
SURVEIL-X, Eventus Validus, and Solidus Labs HALO: venue and broker-dealer
trade surveillance platforms that detect manipulative trading and manage the
investigation workflow. Those platforms cover hundreds of patterns across
asset classes with production market connectivity. This repository
demonstrates the architecture of the emerging agent-assisted investigation
layer on top of such a platform, at portfolio scale: four patterns, synthetic
data, and a measured answer to whether multiple agents earn their cost.

## What this system does not do

- It does not file suspicious activity reports, notify exchanges, or act on
  accounts. The terminal state of every case is a draft awaiting human
  review. This is structural: no filing tool is defined, the graph ends at
  the Case Writer, and the review API validates only `approve` and `dismiss`
  (a test asserts no filing route exists).
- It does not compute statistics with an LLM. Every number in a memo comes
  from tested, vectorized detection code called as a tool.
- It does not touch real market data or real account identities. All data is
  synthetic with a documented generator and a committed answer key.
- It does not claim synthetic performance predicts production performance.
  Every metric below is a statement about the generator's ground truth.

## Results on the frozen dataset

Detection layer, canonical thresholds (notebook 02):

| Metric | Value |
| --- | --- |
| Episode recall (pattern family) | 1.000 (42/42, 95% CI 0.916 to 1.000) |
| Episode recall (exact pattern) | 0.976 (41/42) |
| Hit precision | 1.000 (130/130, 95% CI 0.971 to 1.000) |
| False positive rate | 0 of 59,098 benign account-instrument windows |

Two-stage surveillance, before and after triage (notebook 03):

| Stage | Alerts | Precision | Episode recall |
| --- | --- | --- | --- |
| Alerting thresholds alone | 146 | 0.973 | 42/42 |
| After triage re-verification | 141 escalated | 1.000 (95% CI 0.973 to 1.000) | 42/42 |

The second table is the measured marginal value of the triage stage: the
loosened alerting thresholds buy recall headroom, and the Pattern Detector's
deterministic re-verification restores precision without losing an episode.
Confidence intervals are Wilson score intervals, sized honestly for 42
episodes. The one exact-pattern miss is a spoofing episode detected as
layering, which lands in the same investigation queue.

## Architecture

Four Strands agents wired into a deterministic graph (no LLM routing), each
with its own tool set and a Pydantic contract for what it emits:

| Agent | Tools | Emits |
| --- | --- | --- |
| Pattern Detector | `recompute_window_features` over the detection layer | `DetectionReport` |
| Context Agent | account profile and alert history (Gateway MCP or in-process mocks) | `AccountContext` |
| Cross-Market Correlator | related instruments, account activity, counterparty concentration | `CorrelationFindings` |
| Case Writer | none, by design | `CaseMemo` |

AgentCore services used: Runtime (hosts the graph), Gateway (fronts the two
mock APIs as MCP tools), Memory (branch per agent within each case session),
Identity (OAuth2 M2M token for Gateway), Observability (OTel spans carrying a
per-case correlation id). The other eight services are skipped with stated
reasons in the ADR. Every service wrapper degrades to a documented local
fallback, so the whole system runs offline; `docs/DEPLOYMENT.md` connects a
real AWS account.

## Design decisions

Full record in `docs/ADR.md`. The three that shape everything else:

1. Graph over Swarm. The investigation workflow is fixed, and in a
   compliance setting execution order must be replayable and enforceable.
   Emergent handoff routing is a liability here, not a feature.
2. Detection outside the LLM. Precision/recall claims are meaningless if the
   arithmetic varies run to run, and "order-to-trade ratio 20:1, median
   cancellation latency 340ms" is evidence in a way an LLM's impression of
   raw events is not.
3. Human approval is structural, not configurable. Auto-filing above a
   confidence threshold was rejected because the costs are asymmetric: a
   false filing harms a named account holder; a held case costs analyst
   minutes.

## Quickstart

Offline, no AWS account or credentials required:

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[serve,dev]"
pytest -q                                  # 33 tests, includes the eval gate
uvicorn backend.app:app --port 8000        # review queue at localhost:8000
```

Or with Docker:

```
docker compose up --build
```

The queue loads the frozen dataset, runs alerting detection, and drafts one
case per alert. Without a model configured, memos come from a deterministic
template over the same verified statistics, labeled
`generated_by=offline-template`, so the review workflow is exercisable end to
end. With Bedrock access configured (`docs/DEPLOYMENT.md`), the four-agent
graph drafts them instead.

Notebooks: `01_synthetic_event_generation`, `02_detection_evaluation`,
`03_triage_evaluation` under `notebooks/`.

## Repository layout

```
src/datagen/      synthetic order-book generator with injected episodes
src/detection/    vectorized detection features, thresholds, episode matching
src/schemas/      event and detection-hit models
src/agents/       contracts, prompts, tools, and the case graph
src/mockapis/     deterministic account and market-reference services
src/evaluation/   metrics harness (Wilson CIs) and triage replay
src/services/     AgentCore wrappers: settings, memory, identity, gateway, otel
src/backend/      alert-to-case pipeline and the review API
frontend/         reviewer queue UI
eval/dataset/     frozen events, ground truth, and manifest
deploy/           AgentCore Runtime entrypoint and Gateway target specs
docs/             ADR, synthetic-data spec, deployment guide
```

## Cost estimate

Order-of-magnitude, for the agent-drafted path with a Claude Sonnet class
model (assumptions stated, check current AWS pricing before relying on this):

- Per case: four agent invocations, roughly 40k input and 5k output tokens
  including embedded schemas and tool results. At $3 per million input and
  $15 per million output tokens that is about $0.20 per case.
- The frozen dataset's 146 alerts: about $29 per full drafting run.
- At 1,000 cases per month: about $200 in model cost, plus AgentCore
  consumption charges (Runtime CPU-seconds, Gateway calls, Memory events)
  and CloudWatch ingestion, which are small relative to model cost at this
  volume.

The detection layer itself is numpy on commodity hardware and processes the
264k-event frozen dataset in seconds; clean windows never invoke an agent.

## Data and limits

All market data is synthetic. `docs/SYNTHETIC.md` documents the generator:
arrival processes, spread dynamics, injected episode mechanics, and the
ground-truth answer key. No real account identities appear anywhere.
Detection thresholds are calibrated to this generator; production deployment
would require recalibration against real (labeled) surveillance data that
this repository deliberately does not contain.

## License and contributing

MIT license. See `CONTRIBUTING.md` for development setup and the checks CI
enforces, including the eval regression gate.
