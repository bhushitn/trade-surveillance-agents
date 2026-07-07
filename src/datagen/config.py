"""Configuration presets for the synthetic event generator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentSpec:
    """One simulated instrument on one venue."""

    symbol: str
    venue: str
    initial_price: float
    tick_size: float
    related: str | None = None


DEFAULT_INSTRUMENTS: tuple[InstrumentSpec, ...] = (
    InstrumentSpec("ALPH", "XNYS", 48.50, 0.01, related="ALPH-F"),
    InstrumentSpec("ALPH-F", "XCME", 48.55, 0.01, related="ALPH"),
    InstrumentSpec("BRVO", "XNAS", 132.20, 0.01),
    InstrumentSpec("CRUX", "XNAS", 21.75, 0.01),
)


@dataclass(frozen=True)
class EpisodeCounts:
    spoofing: int
    layering: int
    wash_trading: int
    quote_stuffing: int

    @property
    def total(self) -> int:
        return self.spoofing + self.layering + self.wash_trading + self.quote_stuffing


@dataclass(frozen=True)
class GeneratorConfig:
    """Everything the generator needs. Two presets: full and ci.

    orders_per_second is the benign new-order arrival rate per instrument.
    Each new order also produces at most one execute or cancel event, so the
    total event rate is roughly 1.8x this figure per instrument.
    """

    seed: int
    duration_s: float
    n_accounts: int
    orders_per_second: float
    episodes: EpisodeCounts
    coordinated_fraction: float
    n_recidivists: int
    instruments: tuple[InstrumentSpec, ...] = DEFAULT_INSTRUMENTS

    @classmethod
    def full(cls) -> GeneratorConfig:
        """One 6.5 hour session, sized for the frozen evaluation dataset."""
        return cls(
            seed=42,
            duration_s=23400.0,
            n_accounts=40,
            orders_per_second=1.5,
            episodes=EpisodeCounts(spoofing=12, layering=10, wash_trading=8, quote_stuffing=6),
            coordinated_fraction=0.5,
            n_recidivists=3,
        )

    @classmethod
    def ci(cls) -> GeneratorConfig:
        """A 1.5 hour session, small enough for unit tests and CI."""
        return cls(
            seed=7,
            duration_s=5400.0,
            n_accounts=24,
            orders_per_second=1.5,
            episodes=EpisodeCounts(spoofing=4, layering=3, wash_trading=3, quote_stuffing=2),
            coordinated_fraction=0.5,
            n_recidivists=2,
        )
