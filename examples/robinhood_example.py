"""Robinhood / Alpaca (directional equity) — mapping a stock thesis onto the gate.

The engine speaks binary-market language (YES/NO, price in 0..1). To reuse it as
a risk gate for a *directional* equity trade, express the thesis as a win
probability and translate it into the engine's inputs:

    side        = "YES"                      # a long thesis: "this goes up"
    model_prob  = P(thesis correct)          # your model's confidence, 0..1
    market_price= 1 - expected_edge_fraction # so edge_pt = (model_prob - market_price)*100 > 0

Concretely: pick `market_price` as the break-even probability implied by your
stop/target geometry (reward-to-risk). If a trade risks 1R to make 1R, break-even
is 0.50; a 2:1 target lowers break-even to ~0.33. Then `model_prob` above that is
genuine edge, and Kelly sizing, the drawdown breaker, daily-loss budget, and
capital guardrail all apply unchanged. `balance` is your account equity and
`sized_dollars` is how much to risk — convert to shares with your stop distance.

This keeps ONE audited risk brain in front of every venue you trade.
"""
from dataclasses import replace

from guardrails import Guardrails, GuardrailConfig, AuditLedger

# The R-014 anti-longshot price floor/ceiling is a *prediction-market* rule (don't
# buy a 3c contract). It is meaningless for the equity break-even mapping below —
# a 2:1 target legitimately implies a ~0.33 "price" — so disable it for brokerage
# use. Every other guardrail (Kelly sizing, drawdown breaker, daily-loss budget,
# capital cap) still applies unchanged.
equity_config = replace(GuardrailConfig.conservative(), yes_price_floor=0.0, no_price_ceiling=1.0)
gate = Guardrails(equity_config)                    # tighter caps for a live brokerage
ledger = AuditLedger("audit/robinhood.jsonl")

account_equity = 10_000.0
reward_to_risk = 2.0                       # 2:1 target vs stop
breakeven_prob = 1.0 / (1.0 + reward_to_risk)   # ~0.333
model_confidence = 0.55                    # model: 55% this long works

trade = {
    "side": "YES",
    "market_price": round(breakeven_prob, 3),   # 0.333
    "model_prob": model_confidence,             # edge = (0.55 - 0.333)*100 ~= 21.7pt
    "balance": account_equity,
    "peak_balance": 11_000.0,
    "today_pnl_dollars": -120.0,                # feeds the daily-loss budget
    "starting_balance_dollars": account_equity,
    "strategy": "swing_stack:PULLBACK",
}

verdict = gate.check(trade)
ledger.record_decision(verdict, venue="robinhood", symbol="ABBV", side="BUY",
                       strategy=trade["strategy"])

print(verdict.to_dict())
if verdict.passed:
    stop_distance_per_share = 6.50          # dollars from entry to stop
    shares = int(verdict.size / stop_distance_per_share)
    print(f"RISK ${verdict.size:.2f} -> {shares} shares (stop ${stop_distance_per_share}/sh)")
else:
    print(f"BLOCKED by {verdict.blocking_rules}: {verdict.reasons}")
