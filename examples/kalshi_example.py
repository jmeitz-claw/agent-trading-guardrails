"""Kalshi (prediction market) — the native case.

A binary contract already trades in (0, 1), so `market_price` is the contract
ask and `model_prob` is your model's probability the YES outcome resolves true.
"""
from guardrails import Guardrails, GuardrailConfig, AuditLedger, KillSwitch

gate = Guardrails(GuardrailConfig.default())
ledger = AuditLedger("audit/kalshi.jsonl")
halt = KillSwitch("audit/HALT")

# FOMC-rate market: our model says 66% YES; the contract is offered at 0.52.
trade = {
    "side": "YES",
    "market_price": 0.52,
    "model_prob": 0.66,
    "balance": 1000.0,
    "peak_balance": 1200.0,        # feeds the drawdown circuit breaker
    "market_volume": 25_000.0,     # feeds the thin-market floor
    "recent_daily_pnl": [12.0, -4.0],   # most-recent-first, feeds the loss-streak breaker
    "strategy": "v2-fomc-scanner",
}

verdict = gate.check(trade)
ledger.record_decision(verdict, venue="kalshi", symbol="KXFED-26JUL", side=trade["side"],
                       market_price=trade["market_price"], strategy=trade["strategy"])

print(verdict.to_dict())
if halt.is_engaged():
    print(f"HALTED: {halt.reason()} — no new entries")
elif verdict.passed:
    print(f"PLACE ORDER: stake ${verdict.size:.2f} of YES @ {trade['market_price']}")
    # ... call the Kalshi client here, then:
    # ledger.record_fill(venue="kalshi", symbol="KXFED-26JUL", side="YES",
    #                    intended_size=contracts, filled_size=filled, fill_price=avg_price)
else:
    print(f"BLOCKED by {verdict.blocking_rules}: {verdict.reasons}")
