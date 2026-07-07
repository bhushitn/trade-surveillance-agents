# ADR 001: Trade Surveillance Agent System

Status: accepted
Date: 2026-07-07
Scope: agent roles and boundaries, orchestration pattern, AgentCore service selection, data and evaluation strategy, human review guarantee.

## Context

Trading venues and broker-dealers must detect and investigate manipulative trading: spoofing, layering, wash trading, and quote stuffing. Rule-based alerts fire constantly and mostly on noise. The bottleneck is investigation: a surveillance analyst spends hours turning one statistical anomaly into a written, defensible case memo. This system automates the investigation, not the decision. It ingests synthetic order and trade events, runs statistical detection, gathers account and cross-market context, and produces a draft case package that a human compliance officer approves, edits, or dismisses.

Pattern definitions follow their regulatory meanings, verified against current guidance before any detection code is written:

- Spoofing: bidding or offering with the intent to cancel the bid or offer before execution (Commodity Exchange Act section 4c(a)(5)(C), added by Dodd-Frank). FINRA usage narrows this to non-bona fide orders at or near the top of the book.
- Layering: entering multiple non-bona fide orders at multiple price tiers to create a false impression of depth or pressure (FINRA Regulatory Notice 15-09; FINRA Rule 5210 prohibits publishing quotations that are not bona fide).
- Wash trading: trades with no change in beneficial ownership, executed to create false volume (Securities Exchange Act section 9(a)(1)).
- Quote stuffing: submitting and cancelling large volumes of orders to burden the book, treated by FINRA as disruptive quoting activity under Rule 5210 supplementary material.

Sources: [FINRA manipulative trading guidance](https://www.finra.org/rules-guidance/guidance/reports/2023-finras-examination-and-risk-monitoring-program/manipulative-trading), [FINRA Regulatory Notice 15-09](https://www.finra.org/rules-guidance/notices/15-09).

## Decision 1: Orchestrate with a Strands Graph, not a Swarm

Use the Strands Agents SDK Graph pattern: a directed graph where each agent is a node, edges define execution order, and output from one node becomes input to its dependents.

The investigation workflow is fixed and known in advance: detect, contextualize, correlate, write. Graph executes nodes deterministically according to edge dependencies, which gives two properties a Swarm cannot:

1. Replayability. A compliance reviewer (or a regulator examining the venue's surveillance program) must be able to reconstruct exactly which agent ran, in what order, on what input, for every case. Graph execution order is a property of the topology. Swarm execution order is a property of runtime LLM handoff decisions, so two identical alerts could take different paths through the system.
2. Enforceable boundaries. In a Graph, the Case Writer structurally cannot run before the Pattern Detector, and no agent can hand off to a node that does not exist. In a Swarm, agents choose their own handoffs, and the guarantee that every case passes through every required stage becomes a prompt instruction rather than a structural fact.

Swarm was rejected, not ignored. Swarm fits problems where the decomposition is unknown up front and emergent routing adds value (open-ended research, triage across unpredictable domains). This investigation has a known decomposition, and emergent routing is a liability in a compliance setting, not a feature.

A conditional edge after the Pattern Detector short-circuits the graph when no detection threshold fires, so clean windows cost one agent invocation, not four.

Reference: [Strands Graph pattern](https://strandsagents.com/docs/user-guide/concepts/multi-agent/graph/), [Strands Swarm pattern](https://strandsagents.com/docs/user-guide/concepts/multi-agent/swarm/).

## Decision 2: Four specialist agents with typed handoff contracts

Each agent is a distinct Strands `Agent` with its own system prompt, its own tool set, and a Pydantic model defining what it emits to the next node. The contract is the schema, not the prose.

| Agent | Job | Tools | Emits |
| --- | --- | --- | --- |
| Pattern Detector | Report which statistical thresholds fired and by how much | Detection functions from `src/detection/` (local Python tools) | `DetectionReport` |
| Context Agent | Establish whether this account has done this before | Account-history API via Gateway; long-term memory lookup | `AccountContext` |
| Cross-Market Correlator | Check related instruments and venues for correlated activity in the same window | Market-data API via Gateway; detection functions scoped to related instruments | `CorrelationFindings` |
| Case Writer | Synthesize a citation-backed draft case memo with recommended severity | None (writes from upstream structured evidence only) | `CaseMemo` |

Rejected alternative 1: one agent with four persona prompts. A single context window forces every stage to see every other stage's raw exploration, and there is no way to measure the marginal value of any stage. The eval harness (Decision 7) depends on being able to run the Pattern Detector alone versus the full graph.

Rejected alternative 2: Strands agents-as-tools (a supervisor agent calling specialist agents as callable tools). This reintroduces emergent routing through the supervisor's tool choices, and the supervisor's context accumulates every specialist's output, recreating the pollution problem the graph structure avoids.

The Case Writer deliberately has no tools. It receives three validated Pydantic objects and produces prose. If it cannot cite a claim to a field in its inputs, the claim does not belong in the memo. This is the cheapest hallucination control available: remove the ability to fetch new facts at the synthesis stage.

## Decision 3: Detection statistics are deterministic Python, outside the LLM

Order-to-trade ratio, cancellation-to-fill timing, price impact around cancellations, and cross-account correlation are vectorized functions in `src/detection/` with unit tests against known synthetic patterns. Agents call these functions as tools and interpret their structured output. No agent computes a statistic by reasoning over raw numbers in a prompt.

Three reasons, in order of weight:

1. Correctness is measurable only if the computation is deterministic. A precision/recall claim against ground truth is meaningless if the detector's arithmetic varies run to run.
2. These are regulatory terms of art. "Order-to-trade ratio exceeded 20:1 with median cancellation latency of 340ms" is evidence. An LLM's impression of the same events is not, and no compliance officer would sign a memo built on one.
3. Cost and latency. The detection layer runs on every event window. LLM invocations happen only after a threshold fires.

Rejected alternative: AgentCore Code Interpreter, letting agents write and run analysis code in a sandbox at investigation time. This trades tested, versioned detection logic for code generated per-case, which destroys reproducibility across cases and makes the CI regression gate impossible. Code Interpreter fits exploratory analysis, not a fixed statistical battery.

## Decision 4: AgentCore service selection

AgentCore currently ships thirteen services. This system uses five. Each row states the alternative that was rejected.

### Used

| Service | Role here | Rejected alternative |
| --- | --- | --- |
| Runtime | Hosts the deployed graph; session isolation per case | Self-managed ECS/Fargate service: rebuilds session isolation, scaling, and auth by hand for no demonstrated benefit |
| Gateway | Wraps the mock account-history and market-data APIs as MCP tools | Direct SDK calls from agent code: every agent binary embeds API credentials and its tool surface is invisible to governance. Gateway centralizes auth, gives one place to audit and revoke tool access, and makes the tool inventory inspectable (which is how we prove no filing tool exists, Decision 5) |
| Memory | Short-term memory per case session with a branch per agent; long-term memory across cases for the Context Agent's repeat-offender check | Shared flat session history: the Correlator's exploratory tool calls would pollute the Case Writer's context. A hand-rolled Postgres store: rebuilds event ordering, branching, and retrieval that Memory already provides |
| Identity | Agents authenticate to Gateway targets via managed credential exchange | Embedded API keys in agent environment variables: credential rotation becomes a redeploy, and a leaked agent image leaks the keys |
| Observability | OpenTelemetry traces to CloudWatch, correlation ID per case, full investigation replay from logs | Hand-rolled structured logging: no trace propagation across agent boundaries, no span-level view of tool calls |

### Skipped

| Service | Why skipped |
| --- | --- |
| Harness | Managed agent loop with its own orchestration. We bring our own orchestration (the Strands graph) because the topology is the audit trail. Delegating orchestration to a managed loop hides exactly the thing this system needs to expose |
| Code Interpreter | Rejected in Decision 3: detection code must be versioned and tested, not generated per-case |
| Browser | No agent interacts with a web page |
| Policy | Cedar rules intercepting tool calls at Gateway would strengthen the no-filing guarantee. Skipped for scope: the guarantee is currently structural (no such tool exists to call). Policy is the first service to add if the tool inventory ever grows beyond this repo's control |
| Evaluations | Evaluates agent behavior from traces (task completion, trajectory quality). Our primary metric is detection precision/recall against synthetic ground truth, which requires a bespoke harness with an answer key that AgentCore Evaluations has no concept of. Trace-level evaluation is a reasonable future addition on top of Observability data |
| Optimization | A/B testing of prompts against production traffic. There is no production traffic |
| Payments | No paid third-party APIs |
| Registry | Organizational catalog for tool and agent discovery. This is a single repo, not a platform team |

Deployment parity note: Strands agents are plain Python and run identically on a laptop and in Runtime; the deployment wrapper adds an entrypoint, not behavior. Local mode uses the same graph against dockerized mock APIs, with Memory and Identity swapped for local fakes behind an interface. This claim is a stated design property of the SDK and is re-verified in Phase 8 before the deployment docs assert it.

## Decision 5: Human approval is structural, not configurable

No code path files, escalates, or transmits a case to any external party. The system's terminal output is a draft case in `pending_review` state. The only transitions out of that state (`approved`, `dismissed`, `edited`) require an authenticated human action through the reviewer API.

This is enforced three ways, so removing it requires deliberate work in three places:

1. Tool inventory: no filing or escalation tool is registered in Gateway or defined locally. The Gateway tool list is the auditable proof.
2. Schema: `CaseMemo.status` has no auto-approved value; the state machine in the backend rejects transitions not attributable to a human principal.
3. Graph topology: the graph's terminal node is the Case Writer. There is no node after it.

Rejected alternative: confidence-thresholded auto-escalation (file automatically above 0.95 confidence). The asymmetry of costs makes this wrong, not just risky: a false positive filed with a regulator causes concrete harm to a named account holder and to the venue's credibility, while a false negative held for human review costs analyst minutes. A confidence score from this system is a model output, not a legal judgment, and no threshold converts one into the other.

## Decision 6: Synthetic order-book data with injected ground truth

A documented generator (`data/SYNTHETIC.md`) produces tick-level order and trade events with realistic microstructure (arrival processes, spread dynamics, cancellations) and injects spoofing, layering, wash-trading, and quote-stuffing episodes at known locations. The injection log is the answer key.

No verifiable public dataset labels real manipulation episodes at the order level. Real limit order book data exists (LOBSTER distributes NASDAQ book reconstructions for academic use) but carries no manipulation labels, and real enforcement cases do not come with tick data attached. Rather than train or evaluate against unlabeled real data and assert results that cannot be checked, the generator trades realism for a measurable answer key. This mirrors the standard practice in the surveillance literature, where injected patterns against a simulated book are the accepted evaluation method; a specific citation is deferred to `SYNTHETIC.md` and stated as "source unavailable" if none can be verified.

No real account identities, no live market connectivity, and no claim that synthetic detection performance predicts production performance. The README carries these limits explicitly.

## Decision 7: Evaluation is in-repo and gates CI

`eval/harness.py` runs the pipeline against a frozen synthetic dataset and reports precision, recall, and false positive rate with confidence intervals sized to the episode count. CI fails if either precision or recall regresses past a stated threshold against the committed baseline.

The harness runs in two configurations:

1. Pattern Detector alone (detection thresholds mapped directly to case severity).
2. Full graph (Context Agent and Cross-Market Correlator contributing to the final severity call).

The delta between them is the measured marginal value of the multi-agent design. If that delta is not positive and distinguishable from noise, the README reports that honestly and the architecture section explains what the extra agents buy instead (context quality in the memo, not detection lift). This comparison is the repository's central claim about itself and it is a number, not an assertion.

## Consequences

- The graph topology is committed code, so any change to the investigation workflow is a reviewable diff.
- Adding a fifth agent means adding a node, an edge, a Pydantic contract, and an eval configuration. The pattern scales without redesign.
- Skipping Policy leaves the no-filing guarantee resting on tool inventory and schema. Acceptable at this scope; revisit if tools are ever registered from outside this repo.
- The system is bounded by its synthetic data. Every reported metric is a statement about the generator's answer key, and the docs say so wherever a number appears.
