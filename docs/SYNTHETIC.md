# Synthetic dataset

Every event in this repository is synthetic. There are no real orders, trades, account
identities, or venues. The generator exists so that detection precision and recall can be
measured against a known answer key, which no real dataset provides.

## Why synthetic

No verifiable public dataset labels real manipulation episodes at the order level. Real
limit order book data exists (LOBSTER distributes NASDAQ book reconstructions for academic
use) but carries no manipulation labels, and enforcement actions do not publish tick data.
Evaluating against injected patterns in a simulated market is the approach used in the
academic literature on spoofing, for example Wang and Wellman, "Spoofing the Limit Order
Book: An Agent-Based Model," AAMAS 2017
(https://www.ifaamas.org/Proceedings/aamas2017/pdfs/p651.pdf). This generator is simpler
than their agent-based market: it targets detection evaluation, not strategic equilibrium
analysis.

## What is modeled

- A per-second mid-price path per instrument (Gaussian random walk, about 2 basis points
  per second step standard deviation).
- Background order flow as a Poisson arrival process (1.5 new orders per second per
  instrument). Each order gets a side, a price offset from the mid (geometric, in ticks),
  a log-normal quantity (median around 55), and a lifecycle outcome by draw: 35 percent
  execute, 45 percent cancel (mean latency 40 s), 20 percent rest.
- Execute events carry the passive-side account (`counterparty_id`), as venue-level
  surveillance data does.
- Four instruments on three venues, including one related pair (ALPH on XNYS and ALPH-F
  on XCME) for cross-market coordination.
- 40 accounts, of which 3 are designated recidivists that appear in multiple episodes,
  so account history is a meaningful signal for the Context Agent.

## Injected patterns

Each pattern follows its regulatory definition (see docs/ADR.md for sources):

| Pattern | Construction |
| --- | --- |
| Spoofing | One to two orders of 1500 to 4000 shares at one tick from the mid. A small genuine order on the opposite side fills at the displaced price. The large orders cancel within 0.05 to 0.6 s of the fill. One to three cycles per episode. |
| Layering | Same cycle, but four to seven orders at successive price tiers. |
| Wash trading | Two accounts alternate as aggressor and passive against each other, 8 to 20 trades of similar size at the mid, no price displacement. |
| Quote stuffing | 80 to 200 messages per second for 4 to 12 s: new orders 3 to 10 ticks from the mid, each cancelled within 5 to 50 ms. |

While spoofing or layering orders rest, the generator displaces the mid path toward the
spoofed side, per cycle: a ramp to 4 to 10 ticks while the orders rest, then exponential
decay (3 s time constant) after the cancel. All subsequently placed background orders
price off the displaced mid, so price impact around cancellations is measurable from the
event stream alone.

Half of the spoofing and layering episodes on the related pair get a coordinated sibling:
the same account runs the same pattern on the related instrument in an overlapping window.
Ground truth links the two episodes.

## What is not modeled

Stating these plainly, because the detection layer's scope depends on them:

- No matching engine, queue priority, or partial fills. Lifecycle outcomes are drawn, not
  matched.
- No market-order versus limit-order distinction beyond price placement.
- No cross-venue latency effects or fee structures.
- Passive wash orders receive no terminal event (the tape records one execution per fill,
  on the aggressor's order).
- Displacement magnitudes and cancel latencies are set to be detectable by construction.
  Measured recall on this dataset is a statement about the answer key, not about
  production performance on a real venue. The README repeats this caveat wherever a
  number appears.

## Files and reproduction

`eval/dataset/` contains the frozen evaluation dataset:

- `events.parquet`: the event stream (`event_id`, `ts` in seconds since a nominal
  09:30 America/New_York open on 2026-06-15, `event_type`, `order_id`, `account_id`,
  `instrument`, `venue`, `side`, `price`, `quantity`, `counterparty_id`).
- `ground_truth.json`: the injection log, one record per episode with its pattern,
  accounts, window, order ids, and coordination link.
- `manifest.json`: generator config, row counts, and the SHA-256 of `events.parquet`.

Frozen dataset: 263,872 events, 42 episodes (14 spoofing, 14 layering, 8 wash trading,
6 quote stuffing; 12 of these are 6 coordinated pairs), seed 42, SHA-256
`e22b912a699db0e636aee412613fb595ed2e9ec291a3e922d91b8fb42fe28248`.

Regenerate (byte-identical for a given seed and library versions):

```
python -m datagen --preset full --out eval/dataset
```

`--preset ci` builds a 1.5 hour session used by unit tests and CI.
