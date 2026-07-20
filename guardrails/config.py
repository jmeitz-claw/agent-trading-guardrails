"""Configuration for the trading-agent guardrail engine.

Every threshold the pre-trade gate uses is a field on :class:`GuardrailConfig`,
so an integrator sets their own risk limits without editing engine code. Two
presets are provided: :meth:`GuardrailConfig.default` (balanced) and
:meth:`GuardrailConfig.conservative` (tighter caps, quarter of the exposure).

The defaults are the ones battle-tested in production real-money trading; treat
them as a sane starting point, not gospel — tune to your bankroll and venue.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Tuple


@dataclass
class GuardrailConfig:
    # --- Sizing (fractional Kelly by default) ------------------------------
    sizing_method: str = "fractional_kelly"   # "fractional_kelly" | "exponential"
    kelly_fraction: float = 0.10              # tenth-Kelly; conservative for noisy edges
    kelly_dollar_cap: float = 50.0            # hard ceiling per trade ($)
    kelly_balance_frac_cap: float = 0.05      # max fraction of balance per trade

    # Legacy exponential sizing (edge -> dollars), retained behind the switch.
    exp_base_dollars: float = 8.0
    exp_growth_k: float = 0.0842
    exp_edge_offset: float = 15.0
    exp_size_cap: float = 500.0

    # --- Capital guardrail + consecutive-loss circuit breaker --------------
    capital_cap_pct: float = 0.80             # (open cost + new size) <= cap% of balance
    # Most-severe-first: (min consecutive losing days, tightened cap fraction).
    circuit_breaker_tiers: List[Tuple[int, float]] = field(
        default_factory=lambda: [(5, 0.15), (3, 0.30)]
    )

    # --- Price floor / ceiling (anti-longshot) -----------------------------
    yes_price_floor: float = 0.35             # reject BUY-YES below this price
    no_price_ceiling: float = 0.65            # reject BUY-NO above this price

    # --- Volume floor ------------------------------------------------------
    min_daily_volume: float = 500.0           # skip thin markets when volume supplied

    # --- Extreme-divergence cap (possible model error) ---------------------
    extreme_divergence_edge_pt: float = 50.0
    extreme_divergence_size_cap: float = 50.0

    # --- Drawdown-tier sizing multipliers ----------------------------------
    # Below freeze: 1.0x under `dd_soft_pct`, else `dd_soft_multiplier`.
    dd_soft_pct: float = 20.0
    dd_soft_multiplier: float = 0.5

    # --- Absolute-drawdown circuit breaker ---------------------------------
    drawdown_freeze_pct: float = 40.0         # >= freeze: block new entries (close-only)
    drawdown_halt_pct: float = 60.0           # >= halt: full halt, manual unfreeze

    # --- Daily-loss budget -------------------------------------------------
    daily_loss_budget_pct: float = 5.0        # block entries once today's loss exceeds this % of day-start

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def default(cls) -> "GuardrailConfig":
        """Balanced production defaults."""
        return cls()

    @classmethod
    def conservative(cls) -> "GuardrailConfig":
        """Tighter caps for small accounts or unproven strategies."""
        return cls(
            kelly_fraction=0.05,
            kelly_dollar_cap=25.0,
            kelly_balance_frac_cap=0.02,
            capital_cap_pct=0.50,
            daily_loss_budget_pct=3.0,
            drawdown_freeze_pct=30.0,
            drawdown_halt_pct=50.0,
        )
