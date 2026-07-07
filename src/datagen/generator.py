"""Synthetic order-book event generator with injected manipulation episodes.

This is a stylized event stream, not a matching engine. Each benign order
receives its lifecycle outcome (execute, cancel, or rest) by draw, and prices
come from a per-second mid path plus a tick offset. Injected episodes displace
the mid path while their non-bona-fide orders rest, so price impact around
cancellations is measurable from the stream alone. docs/SYNTHETIC.md states
exactly what is and is not modeled.

The injection log returned by generate() is the evaluation answer key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from datagen.config import GeneratorConfig, InstrumentSpec
from schemas.events import EVENT_COLUMNS, EventType, GroundTruthEpisode, PatternType, Side

_EPISODE_PAD_S = 120.0
_DECAY_S = 30.0

_DURATION_RANGES: dict[PatternType, tuple[float, float]] = {
    PatternType.SPOOFING: (15.0, 45.0),
    PatternType.LAYERING: (20.0, 60.0),
    PatternType.WASH_TRADING: (40.0, 120.0),
    PatternType.QUOTE_STUFFING: (4.0, 12.0),
}

_DISPLACEMENT_RANGES: dict[PatternType, tuple[float, float]] = {
    PatternType.SPOOFING: (4.0, 8.0),
    PatternType.LAYERING: (5.0, 10.0),
    PatternType.WASH_TRADING: (0.0, 0.0),
    PatternType.QUOTE_STUFFING: (0.0, 0.0),
}


class _OrderIds:
    """Sequential order ids per symbol, unique across the dataset."""

    def __init__(self) -> None:
        self._next: dict[str, int] = {}

    def take(self, symbol: str, n: int) -> list[str]:
        start = self._next.get(symbol, 0)
        self._next[symbol] = start + n
        return [f"{symbol}-{i:07d}" for i in range(start, start + n)]


@dataclass
class _Planned:
    episode_id: str
    pattern: PatternType
    instrument: InstrumentSpec
    account_ids: list[str]
    start_ts: float
    duration_s: float
    side: Side
    displacement_ticks: float
    coordinated_with: str | None = None
    order_ids: list[str] = field(default_factory=list)
    cycles: list[tuple[float, float, float]] = field(default_factory=list)

    @property
    def end_ts(self) -> float:
        return self.start_ts + self.duration_s


def generate(config: GeneratorConfig) -> tuple[pd.DataFrame, list[GroundTruthEpisode]]:
    """Build the full event stream and its ground-truth injection log."""
    rng = np.random.default_rng(config.seed)
    accounts = [f"ACCT-{i:03d}" for i in range(config.n_accounts)]
    recidivists = accounts[: config.n_recidivists]

    planned = _schedule(config, rng, accounts, recidivists)
    for ep in planned:
        if ep.pattern in (PatternType.SPOOFING, PatternType.LAYERING):
            _plan_cycles(ep, rng)

    mids = {spec.symbol: _mid_path(spec, config, rng) for spec in config.instruments}
    for ep in planned:
        if ep.displacement_ticks > 0:
            _apply_displacement(mids[ep.instrument.symbol], ep, ep.instrument.tick_size)

    ids = _OrderIds()
    frames = [
        _benign_events(spec, mids[spec.symbol], config, rng, ids, accounts)
        for spec in config.instruments
    ]
    for ep in planned:
        frames.append(_episode_events(ep, mids[ep.instrument.symbol], rng, ids))

    events = pd.concat(frames, ignore_index=True)
    events = events.sort_values("ts", kind="stable").reset_index(drop=True)
    events["event_id"] = np.arange(len(events))
    events = events[EVENT_COLUMNS]

    ground_truth = [
        GroundTruthEpisode(
            episode_id=ep.episode_id,
            pattern=ep.pattern,
            account_ids=ep.account_ids,
            instrument=ep.instrument.symbol,
            venue=ep.instrument.venue,
            start_ts=round(ep.start_ts, 3),
            end_ts=round(ep.end_ts, 3),
            order_ids=ep.order_ids,
            coordinated_with=ep.coordinated_with,
        )
        for ep in planned
    ]
    return events, ground_truth


def _schedule(
    config: GeneratorConfig,
    rng: np.random.Generator,
    accounts: list[str],
    recidivists: list[str],
) -> list[_Planned]:
    """Place episodes on instruments with no same-instrument overlap."""
    planned: list[_Planned] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"EP-{counter:04d}"

    def fits(spec: InstrumentSpec, start: float, dur: float) -> bool:
        for other in planned:
            if other.instrument.symbol != spec.symbol:
                continue
            pad_end = start + dur + _EPISODE_PAD_S
            if start < other.end_ts + _EPISODE_PAD_S and other.start_ts < pad_end:
                return False
        return True

    def place(pattern: PatternType, spec: InstrumentSpec | None = None) -> _Planned | None:
        lo, hi = _DURATION_RANGES[pattern]
        for _ in range(200):
            if spec is not None:
                instrument = spec
            else:
                instrument = config.instruments[rng.integers(len(config.instruments))]
            dur = float(rng.uniform(lo, hi))
            start = float(rng.uniform(0.05 * config.duration_s, 0.88 * config.duration_s))
            if fits(instrument, start, dur):
                d_lo, d_hi = _DISPLACEMENT_RANGES[pattern]
                return _Planned(
                    episode_id=next_id(),
                    pattern=pattern,
                    instrument=instrument,
                    account_ids=_pick_accounts(pattern, rng, accounts, recidivists),
                    start_ts=start,
                    duration_s=dur,
                    side=Side.BUY if rng.uniform() < 0.5 else Side.SELL,
                    displacement_ticks=float(rng.uniform(d_lo, d_hi)),
                )
        return None

    requested = [
        (PatternType.SPOOFING, config.episodes.spoofing),
        (PatternType.LAYERING, config.episodes.layering),
        (PatternType.WASH_TRADING, config.episodes.wash_trading),
        (PatternType.QUOTE_STUFFING, config.episodes.quote_stuffing),
    ]
    for pattern, count in requested:
        for _ in range(count):
            ep = place(pattern)
            if ep is not None:
                planned.append(ep)

    by_symbol = {spec.symbol: spec for spec in config.instruments}
    for ep in list(planned):
        if ep.pattern not in (PatternType.SPOOFING, PatternType.LAYERING):
            continue
        related = ep.instrument.related
        if related is None or rng.uniform() > config.coordinated_fraction:
            continue
        sibling = place(ep.pattern, by_symbol[related])
        if sibling is None:
            continue
        sibling.account_ids = list(ep.account_ids)
        sibling.start_ts = min(
            ep.start_ts + float(rng.uniform(-5.0, 5.0)), 0.88 * config.duration_s
        )
        if not fits(by_symbol[related], sibling.start_ts, sibling.duration_s):
            continue
        sibling.coordinated_with = ep.episode_id
        ep.coordinated_with = sibling.episode_id
        planned.append(sibling)

    return sorted(planned, key=lambda e: e.start_ts)


def _pick_accounts(
    pattern: PatternType,
    rng: np.random.Generator,
    accounts: list[str],
    recidivists: list[str],
) -> list[str]:
    def one() -> str:
        if recidivists and rng.uniform() < 0.6:
            return str(rng.choice(recidivists))
        return str(rng.choice(accounts))

    first = one()
    if pattern is not PatternType.WASH_TRADING:
        return [first]
    second = first
    while second == first:
        second = str(rng.choice(accounts))
    return [first, second]


def _mid_path(
    spec: InstrumentSpec, config: GeneratorConfig, rng: np.random.Generator
) -> np.ndarray:
    n = int(config.duration_s) + int(_DECAY_S) + 31
    steps = rng.normal(0.0, spec.initial_price * 2e-5, n)
    mid = spec.initial_price + np.cumsum(steps)
    return np.maximum(mid, spec.tick_size * 10)


def _plan_cycles(ep: _Planned, rng: np.random.Generator) -> None:
    """Plan spoof cycles up front so mid displacement and events agree on timing.

    Each cycle: (t_start, t_gen, t_fill). Non-bona-fide orders are placed at
    t_start, the genuine opposite-side order at t_gen fills at t_fill, and the
    resting orders cancel within 0.6 s of the fill.
    """
    cycles: list[tuple[float, float, float]] = []
    t = ep.start_ts
    while t < ep.end_ts - 8.0:
        t_gen = t + float(rng.uniform(2.0, 5.0))
        t_fill = t_gen + float(rng.uniform(0.3, 1.5))
        cycles.append((t, t_gen, t_fill))
        t = t_fill + 0.6 + float(rng.uniform(4.0, 9.0))
    ep.cycles = cycles


def _apply_displacement(mid: np.ndarray, ep: _Planned, tick: float) -> None:
    """Per cycle: push the mid toward the spoofed side while orders rest, decay after cancel."""
    sign = 1.0 if ep.side is Side.BUY else -1.0
    peak = sign * ep.displacement_ticks * tick
    for t_start, _t_gen, t_fill in ep.cycles:
        t_cxl = t_fill + 0.6
        a = int(t_start)
        b = min(max(int(t_fill), a + 1), len(mid) - 1)
        c = min(max(int(t_cxl) + 1, b + 1), len(mid) - 1)
        mid[a:b] += np.linspace(0.0, peak, b - a)
        mid[b:c] += peak
        tail = min(c + int(_DECAY_S), len(mid))
        if tail > c:
            mid[c:tail] += peak * np.exp(-np.arange(tail - c) / 3.0)


def _benign_events(
    spec: InstrumentSpec,
    mid: np.ndarray,
    config: GeneratorConfig,
    rng: np.random.Generator,
    ids: _OrderIds,
    accounts: list[str],
) -> pd.DataFrame:
    """Background flow: limit orders that execute (35 percent), cancel (45), or rest (20)."""
    n = int(rng.poisson(config.orders_per_second * config.duration_s))
    t_new = np.sort(rng.uniform(0.0, config.duration_s, n))
    is_buy = rng.uniform(size=n) < 0.5
    offset = rng.geometric(0.35, n)
    sec = t_new.astype(int)
    raw = mid[sec] + np.where(is_buy, -1.0, 1.0) * offset * spec.tick_size
    price = np.round(raw / spec.tick_size) * spec.tick_size
    qty = np.maximum(1, rng.lognormal(4.0, 0.9, n).astype(int))
    acct_idx = rng.integers(0, len(accounts), n)
    order_id = ids.take(spec.symbol, n)

    u = rng.uniform(size=n)
    t_exec = t_new + rng.exponential(15.0, n)
    t_cancel = t_new + rng.exponential(40.0, n)
    executes = (u < 0.35) & (t_exec < config.duration_s)
    cancels = (u >= 0.35) & (u < 0.80) & (t_cancel < config.duration_s)

    cp_idx = rng.integers(0, len(accounts), n)
    same = cp_idx == acct_idx
    cp_idx[same] = (cp_idx[same] + 1) % len(accounts)

    account = np.array(accounts)[acct_idx]
    counterparty = np.array(accounts)[cp_idx]
    side = np.where(is_buy, Side.BUY.value, Side.SELL.value)
    oid = np.array(order_id)

    def frame(mask: np.ndarray, ts: np.ndarray, etype: EventType, with_cp: bool) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "event_id": 0,
                "ts": ts[mask],
                "event_type": etype.value,
                "order_id": oid[mask],
                "account_id": account[mask],
                "instrument": spec.symbol,
                "venue": spec.venue,
                "side": side[mask],
                "price": price[mask],
                "quantity": qty[mask],
                "counterparty_id": counterparty[mask] if with_cp else None,
            }
        )

    all_mask = np.ones(n, dtype=bool)
    return pd.concat(
        [
            frame(all_mask, t_new, EventType.NEW, False),
            frame(executes, t_exec, EventType.EXECUTE, True),
            frame(cancels, t_cancel, EventType.CANCEL, False),
        ],
        ignore_index=True,
    )


def _episode_events(
    ep: _Planned, mid: np.ndarray, rng: np.random.Generator, ids: _OrderIds
) -> pd.DataFrame:
    if ep.pattern in (PatternType.SPOOFING, PatternType.LAYERING):
        return _spoof_like_events(ep, mid, rng, ids)
    if ep.pattern is PatternType.WASH_TRADING:
        return _wash_events(ep, mid, rng, ids)
    return _stuffing_events(ep, mid, rng, ids)


def _row(
    ts: float,
    etype: EventType,
    order_id: str,
    account: str,
    ep: _Planned,
    side: Side,
    price: float,
    qty: int,
    counterparty: str | None = None,
) -> dict[str, Any]:
    tick = ep.instrument.tick_size
    return {
        "event_id": 0,
        "ts": ts,
        "event_type": etype.value,
        "order_id": order_id,
        "account_id": account,
        "instrument": ep.instrument.symbol,
        "venue": ep.instrument.venue,
        "side": side.value,
        "price": round(round(price / tick) * tick, 6),
        "quantity": qty,
        "counterparty_id": counterparty,
    }


def _spoof_like_events(
    ep: _Planned, mid: np.ndarray, rng: np.random.Generator, ids: _OrderIds
) -> pd.DataFrame:
    """Spoofing (one to two large orders at the touch) or layering (four to seven tiers).

    Cycle: place non-bona-fide size on ep.side, let a small genuine order on the
    opposite side fill at the displaced price, then cancel the resting size
    within a second of the fill. CEA 4c(a)(5)(C) intent-to-cancel, made literal.
    """
    tick = ep.instrument.tick_size
    spoof_sign = -1.0 if ep.side is Side.BUY else 1.0
    genuine_side = Side.SELL if ep.side is Side.BUY else Side.BUY
    account = ep.account_ids[0]
    rows: list[dict[str, Any]] = []
    n_levels = (1, 2) if ep.pattern is PatternType.SPOOFING else (4, 7)

    for t_start, t_gen, t_fill in ep.cycles:
        k = int(rng.integers(n_levels[0], n_levels[1] + 1))
        spoof_ids = ids.take(ep.instrument.symbol, k)
        ep.order_ids.extend(spoof_ids)
        prices: dict[str, float] = {}
        for level, oid in enumerate(spoof_ids, start=1):
            t_place = t_start + float(rng.uniform(0.0, 0.4))
            price = mid[int(t_place)] + spoof_sign * level * tick
            prices[oid] = price
            qty = int(rng.integers(1500, 4000))
            rows.append(_row(t_place, EventType.NEW, oid, account, ep, ep.side, price, qty))

        gen_id = ids.take(ep.instrument.symbol, 1)[0]
        ep.order_ids.append(gen_id)
        gen_price = mid[int(t_gen)] + (tick if genuine_side is Side.SELL else -tick)
        gen_qty = int(rng.integers(50, 200))
        rows.append(
            _row(t_gen, EventType.NEW, gen_id, account, ep, genuine_side, gen_price, gen_qty)
        )
        rows.append(
            _row(
                t_fill,
                EventType.EXECUTE,
                gen_id,
                account,
                ep,
                genuine_side,
                gen_price,
                gen_qty,
                counterparty=f"ACCT-{int(rng.integers(0, 20)):03d}",
            )
        )

        for oid in spoof_ids:
            t_cxl = t_fill + float(rng.uniform(0.05, 0.6))
            rows.append(_row(t_cxl, EventType.CANCEL, oid, account, ep, ep.side, prices[oid], 0))

    return pd.DataFrame(rows)


def _wash_events(
    ep: _Planned, mid: np.ndarray, rng: np.random.Generator, ids: _OrderIds
) -> pd.DataFrame:
    """Two accounts trading with each other: false volume, no price movement."""
    a, b = ep.account_ids
    n_trades = int(rng.integers(8, 21))
    times = np.sort(rng.uniform(ep.start_ts, ep.end_ts - 1.0, n_trades))
    base_qty = int(rng.integers(300, 800))
    rows: list[dict[str, Any]] = []

    for i, t in enumerate(times):
        aggressor, passive = (a, b) if i % 2 == 0 else (b, a)
        agg_side = Side.BUY if i % 2 == 0 else Side.SELL
        pas_side = Side.SELL if agg_side is Side.BUY else Side.BUY
        price = mid[int(t)] + float(rng.integers(-1, 2)) * ep.instrument.tick_size
        qty = max(1, int(base_qty * rng.uniform(0.9, 1.1)))
        pas_id, agg_id = ids.take(ep.instrument.symbol, 2)
        ep.order_ids.extend([pas_id, agg_id])
        t_pas = float(t) - float(rng.uniform(0.05, 0.5))
        rows.append(_row(t_pas, EventType.NEW, pas_id, passive, ep, pas_side, price, qty))
        rows.append(_row(float(t), EventType.NEW, agg_id, aggressor, ep, agg_side, price, qty))
        rows.append(
            _row(
                float(t) + 0.01,
                EventType.EXECUTE,
                agg_id,
                aggressor,
                ep,
                agg_side,
                price,
                qty,
                counterparty=passive,
            )
        )

    return pd.DataFrame(rows)


def _stuffing_events(
    ep: _Planned, mid: np.ndarray, rng: np.random.Generator, ids: _OrderIds
) -> pd.DataFrame:
    """A burst of new-and-cancel messages far from the touch."""
    account = ep.account_ids[0]
    rate = float(rng.uniform(80.0, 200.0))
    n = int(rate * ep.duration_s)
    t_new = np.sort(rng.uniform(ep.start_ts, ep.end_ts, n))
    sign = -1.0 if ep.side is Side.BUY else 1.0
    rows: list[dict[str, Any]] = []
    oids = ids.take(ep.instrument.symbol, n)
    ep.order_ids.extend(oids)

    for i in range(n):
        offset = int(rng.integers(3, 11))
        price = mid[int(t_new[i])] + sign * offset * ep.instrument.tick_size
        qty = int(rng.integers(50, 150))
        rows.append(_row(float(t_new[i]), EventType.NEW, oids[i], account, ep, ep.side, price, qty))
        t_cxl = float(t_new[i]) + float(rng.uniform(0.005, 0.05))
        rows.append(_row(t_cxl, EventType.CANCEL, oids[i], account, ep, ep.side, price, 0))

    return pd.DataFrame(rows)


def write_dataset(
    events: pd.DataFrame,
    episodes: list[GroundTruthEpisode],
    out_dir: Path,
    config: GeneratorConfig,
) -> dict[str, Any]:
    """Write events.parquet, ground_truth.json, and manifest.json. Returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.parquet"
    events.to_parquet(events_path, index=False)

    truth_path = out_dir / "ground_truth.json"
    truth_path.write_text(
        json.dumps([ep.model_dump() for ep in episodes], indent=2) + "\n", encoding="utf-8"
    )

    manifest = {
        "events_sha256": hashlib.sha256(events_path.read_bytes()).hexdigest(),
        "n_events": int(len(events)),
        "n_episodes": len(episodes),
        "episodes_by_pattern": {
            p.value: sum(1 for e in episodes if e.pattern is p) for p in PatternType
        },
        "session": {"date": "2026-06-15", "open": "09:30:00 America/New_York"},
        "config": asdict(config),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
