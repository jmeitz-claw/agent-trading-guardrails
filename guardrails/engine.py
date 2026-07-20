"""engine — venue-agnostic pre-trade safety gate for autonomous trading agents.

`Guardrails(config).check(trade)` is READ-ONLY: it decides, it never trades.
Wire it in front of any order-placement call an LLM agent (or a plain bot) is
about to make. A blocked verdict means "do not send this order"; a passed
verdict carries the risk-sized dollar amount you should actually stake.

The rule identifiers (R-001, R-014, ...) are stable audit codes — they are the
vocabulary your compliance ledger and dashboards key on, so they are kept even
though the human-readable names live in RULE_NAMES.

Works for any binary / two-sided market priced in (0, 1): prediction markets
(Kalshi, Polymarket), and — by mapping a directional equity/option thesis onto a
YES/NO probability and a 0..1 "price" — brokerage venues too (see examples/).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .config import GuardrailConfig

RULE_NAMES = {
    "R-001": "fractional-Kelly position sizing (drawdown-aware)",
    "R-006": "minimum daily volume floor",
    "R-008": "capital guardrail",
    "R-009": "portfolio guardrail (advisory)",
    "R-014": "price floor / ceiling (anti-longshot)",
    "R-017": "extreme-divergence size cap",
    "R-025": "consecutive-losing-day circuit breaker",
    "R-039": "absolute-drawdown circuit breaker",
    "R-040": "daily-loss budget",
    "R-EDGE": "non-positive edge",
    "R-INPUT": "input validation",
}


@dataclass
class Verdict:
    """Result of a pre-trade check. `passed` gates the order; `size` is the
    risk-sized dollar stake to use when it passes."""
    passed: bool
    reasons: List[str]
    size: float = 0.0
    edge_pt: float = 0.0
    blocking_rules: List[str] = field(default_factory=list)
    advisories: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reasons": self.reasons,
            "size": round(self.size, 2),
            "edge_pt": round(self.edge_pt, 2),
            "blocking_rules": self.blocking_rules,
            "advisories": self.advisories,
        }


class Guardrails:
    """A configured pre-trade gate. Construct once, call :meth:`check` per order."""

    def __init__(self, config: Optional[GuardrailConfig] = None):
        self.config = config or GuardrailConfig.default()

    # ------------------------------------------------------------------ sizing
    def _drawdown_pct(self, balance: float, peak: Optional[float]) -> float:
        if peak is None:
            return 0.0
        try:
            peak_f, bal_f = float(peak), float(balance)
        except (TypeError, ValueError):
            return 0.0
        if peak_f <= 0.0 or bal_f >= peak_f:
            return 0.0
        return (peak_f - bal_f) / peak_f * 100.0

    def _dd_size_multiplier(self, drawdown_pct: float) -> float:
        c = self.config
        if drawdown_pct < c.dd_soft_pct:
            return 1.0
        if drawdown_pct < c.drawdown_freeze_pct:
            return c.dd_soft_multiplier
        return 0.0

    def _size_fractional_kelly(self, edge_pt: float, balance: float, drawdown_pct: float) -> float:
        c = self.config
        if edge_pt <= 0.0 or balance <= 0.0:
            return 0.0
        raw = balance * c.kelly_fraction * (edge_pt / 100.0)
        sized = raw * self._dd_size_multiplier(drawdown_pct)
        return max(0.0, min(sized, c.kelly_dollar_cap, balance * c.kelly_balance_frac_cap))

    def _size_exponential(self, edge_pt: float) -> float:
        c = self.config
        raw = c.exp_base_dollars * math.exp(c.exp_growth_k * (edge_pt - c.exp_edge_offset))
        return min(raw, c.exp_size_cap)

    def _select_size(self, edge_pt: float, balance: float, drawdown_pct: float) -> float:
        if self.config.sizing_method == "fractional_kelly":
            return self._size_fractional_kelly(edge_pt, balance, drawdown_pct)
        return self._size_exponential(edge_pt)

    # ---------------------------------------------------- circuit-breaker helpers
    @staticmethod
    def _consecutive_losing_days(recent_daily_pnl) -> int:
        if not recent_daily_pnl:
            return 0
        streak = 0
        for pnl in recent_daily_pnl:
            try:
                if float(pnl) < 0:
                    streak += 1
                else:
                    break
            except (TypeError, ValueError):
                break
        return streak

    def _effective_capital_cap_pct(self, recent_daily_pnl) -> float:
        streak = self._consecutive_losing_days(recent_daily_pnl)
        for min_days, cap_pct in self.config.circuit_breaker_tiers:
            if streak >= min_days:
                return cap_pct
        return self.config.capital_cap_pct

    # ---------------------------------------------------------------- the gate
    def check(self, trade: dict) -> Verdict:
        """Run the full pre-trade gate over a trade dict. See README for keys."""
        c = self.config
        reasons: List[str] = []
        blocking: List[str] = []
        advisories: List[str] = []

        # -- input validation ------------------------------------------------
        for k in ("side", "market_price", "model_prob", "balance"):
            if k not in trade:
                return Verdict(False, [f"R-INPUT: missing key '{k}'"], blocking_rules=["R-INPUT"])
        side = trade["side"]
        if side not in ("YES", "NO"):
            return Verdict(False, [f"R-INPUT: side must be YES or NO, got {side!r}"],
                           blocking_rules=["R-INPUT"])
        try:
            market_price = float(trade["market_price"])
            model_prob = float(trade["model_prob"])
            balance = float(trade["balance"])
        except (TypeError, ValueError):
            return Verdict(False, ["R-INPUT: market_price/model_prob/balance not numeric"],
                           blocking_rules=["R-INPUT"])
        if not (0.0 < market_price < 1.0):
            return Verdict(False, [f"R-INPUT: market_price {market_price} not in (0,1)"],
                           blocking_rules=["R-INPUT"])
        if not (0.0 <= model_prob <= 1.0):
            return Verdict(False, [f"R-INPUT: model_prob {model_prob} not in [0,1]"],
                           blocking_rules=["R-INPUT"])
        if balance <= 0.0:
            return Verdict(False, [f"R-INPUT: balance {balance} must be > 0"],
                           blocking_rules=["R-INPUT"])

        open_cost = float(trade.get("open_position_cost", 0.0) or 0.0)
        bankroll = float(trade.get("bankroll", balance) or balance)

        # -- edge ------------------------------------------------------------
        if trade.get("edge_pt") is not None:
            edge_pt = float(trade["edge_pt"])
        elif side == "YES":
            edge_pt = (model_prob - market_price) * 100.0
        else:  # NO wins with probability (1 - model_prob)
            edge_pt = ((1.0 - model_prob) - market_price) * 100.0

        if edge_pt <= 0.0:
            blocking.append("R-EDGE")
            reasons.append(f"R-EDGE non-positive edge: {edge_pt:.1f}pt")

        # -- R-014 price floor / ceiling ------------------------------------
        if side == "YES" and market_price < c.yes_price_floor:
            blocking.append("R-014")
            reasons.append(f"R-014 price floor: YES @ {market_price:.2f} < {c.yes_price_floor:.2f}")
        if side == "NO" and market_price > c.no_price_ceiling:
            blocking.append("R-014")
            reasons.append(f"R-014 price ceiling: NO @ {market_price:.2f} > {c.no_price_ceiling:.2f}")

        # -- R-006 volume floor (only when supplied) ------------------------
        if trade.get("market_volume") is not None:
            try:
                vol = float(trade["market_volume"])
                if vol < c.min_daily_volume:
                    blocking.append("R-006")
                    reasons.append(f"R-006 volume floor: ${vol:.0f} < ${c.min_daily_volume:.0f}")
            except (TypeError, ValueError):
                advisories.append("R-006 fallback: market_volume not numeric — skipped")

        # -- R-001 sizing (drawdown-aware) ----------------------------------
        drawdown_pct = self._drawdown_pct(balance, trade.get("peak_balance"))
        size = self._select_size(max(edge_pt, 0.0), bankroll, drawdown_pct)

        # -- R-017 extreme-divergence cap -----------------------------------
        if edge_pt > c.extreme_divergence_edge_pt and size > c.extreme_divergence_size_cap:
            size = c.extreme_divergence_size_cap
            advisories.append(
                f"R-017 extreme divergence: edge {edge_pt:.1f}pt > "
                f"{c.extreme_divergence_edge_pt:.0f}pt — size capped at "
                f"${c.extreme_divergence_size_cap:.0f} (possible model error)"
            )

        # -- R-039 absolute-drawdown circuit breaker ------------------------
        if trade.get("peak_balance") is not None:
            if drawdown_pct >= c.drawdown_halt_pct:
                blocking.append("R-039")
                reasons.append(
                    f"R-039 HALT: drawdown {drawdown_pct:.1f}% >= {c.drawdown_halt_pct:.0f}% "
                    f"— full halt, manual unfreeze required"
                )
            elif drawdown_pct >= c.drawdown_freeze_pct:
                blocking.append("R-039")
                reasons.append(
                    f"R-039 FREEZE: drawdown {drawdown_pct:.1f}% >= {c.drawdown_freeze_pct:.0f}% "
                    f"— new entries blocked (close-only)"
                )
        else:
            advisories.append("R-039 fallback: peak_balance absent — drawdown breaker skipped")

        # -- R-040 daily-loss budget ----------------------------------------
        if trade.get("today_pnl_dollars") is not None:
            try:
                today_pnl = float(trade["today_pnl_dollars"])
                start_bal = float(trade.get("starting_balance_dollars", balance) or balance)
                budget = -start_bal * c.daily_loss_budget_pct / 100.0
                if today_pnl < budget:
                    blocking.append("R-040")
                    reasons.append(
                        f"R-040 daily-loss budget: today ${today_pnl:.2f} < ${budget:.2f} "
                        f"(-{c.daily_loss_budget_pct:.0f}% of ${start_bal:.2f} start)"
                    )
            except (TypeError, ValueError):
                advisories.append("R-040 fallback: today_pnl_dollars not numeric — skipped")
        else:
            advisories.append("R-040 fallback: today_pnl_dollars absent — skipped")

        # -- R-008 / R-025 capital guardrail --------------------------------
        cap_pct = self._effective_capital_cap_pct(trade.get("recent_daily_pnl"))
        projected = open_cost + size
        cap_dollars = cap_pct * balance
        if projected > cap_dollars:
            rule = "R-025" if cap_pct < c.capital_cap_pct else "R-008"
            blocking.append(rule)
            reasons.append(
                f"{rule} capital guardrail: open ${open_cost:.2f} + size ${size:.2f} = "
                f"${projected:.2f} > {cap_pct*100:.0f}% cap (${cap_dollars:.2f})"
            )

        # -- R-009 portfolio guardrail (advisory) ---------------------------
        advisories.append(f"R-009 remaining balance after trade: ${balance - size - open_cost:.2f}")

        passed = len(blocking) == 0
        if passed:
            reasons.append(f"PASS: edge {edge_pt:.1f}pt, size ${size:.2f}")
        return Verdict(passed, reasons, size, edge_pt, blocking, advisories)


def check_trade(trade: dict, config: Optional[GuardrailConfig] = None) -> Verdict:
    """Convenience one-shot: build a :class:`Guardrails` and check one trade."""
    return Guardrails(config).check(trade)
