# agent-trading-guardrails

**The brakes for autonomous trading agents.** A read-only, dependency-free
pre-trade safety gate + audit ledger + kill switch you drop in front of any bot
or LLM agent that can place real orders.

Everyone selling into the "AI agent that trades" space sells *alpha* — strategies
that decay. This sells the opposite: **the thing that stops your agent from
blowing up the account.** A safety layer's credibility compounds with every
incident it prevents, instead of decaying like an edge. It is the picks-and-shovels
play on the whole autonomous-trading gold rush.

> It decides; it never trades. `check()` is pure and side-effect-free — it
> returns a verdict and a risk-sized dollar stake. Your code still places the order.

This engine is extracted from a real-money trading system's pre-trade rule
checker — the same gate that runs in front of live prediction-market and
brokerage execution.

## Why it exists

An LLM wired to Robinhood / Kalshi / Polymarket / Alpaca will, eventually:

- fat-finger an order size,
- revenge-trade straight into a drawdown,
- chase a longshot at 3¢,
- or silently die mid-session with an open position and no stop.

These are not strategy problems; they are *safety* problems, and they are the
same across every venue. This package encodes the boring, battle-tested controls
that catch them — as configuration, not code you have to write.

## Install

```bash
pip install -e .          # from this directory
python -m guardrails.selftest
```

Stdlib-only. No numpy, no pandas, nothing to audit or pin.

## Quick start

```python
from guardrails import Guardrails, GuardrailConfig, AuditLedger, KillSwitch

gate   = Guardrails(GuardrailConfig.conservative())
ledger = AuditLedger("audit/trades.jsonl")
halt   = KillSwitch("audit/HALT")

verdict = gate.check({
    "side": "YES",          # YES = "this resolves true / goes up"
    "market_price": 0.52,   # the ask, in (0, 1)
    "model_prob": 0.66,     # your model's probability of YES
    "balance": 1000.0,
    "peak_balance": 1200.0, # enables the drawdown breaker
})

ledger.record_decision(verdict, venue="kalshi", symbol="KXFED-26JUL", side="YES")

if not halt.is_engaged() and verdict.passed:
    place_order(dollars=verdict.size)      # <- your code
else:
    print(verdict.blocking_rules, verdict.reasons)
```

## The rules

Every threshold is a field on `GuardrailConfig` — set your own without touching
engine code. Stable rule codes (`R-0xx`) are what your ledger and dashboards key
on.

| Code   | Guardrail | Blocks? |
|--------|-----------|---------|
| R-001  | Drawdown-aware fractional-Kelly position sizing | sizes (never negative) |
| R-006  | Minimum daily volume floor (skip thin markets) | ✅ when volume supplied |
| R-008  | Capital guardrail — open cost + new size ≤ cap% of balance | ✅ |
| R-009  | Portfolio guardrail — remaining balance after trade | advisory |
| R-014  | Price floor / ceiling — no YES < 0.35, no NO > 0.65 (anti-longshot) | ✅ |
| R-017  | Extreme-divergence cap — edge > 50pt ⇒ size capped (possible model error) | caps + advisory |
| R-025  | Consecutive-losing-day circuit breaker — tightens the capital cap | ✅ |
| R-039  | Absolute-drawdown breaker — freeze ≥ 40%, halt ≥ 60% | ✅ |
| R-040  | Daily-loss budget — block once today's loss exceeds % of day-start | ✅ |
| R-EDGE | Non-positive edge — nothing to trade | ✅ |

`edge_pt = (win_prob − market_price) × 100`, where `win_prob` is `model_prob`
for a YES buy and `1 − model_prob` for a NO buy.

## Trade dict — keys

**Required:** `side` (`"YES"`/`"NO"`), `market_price` (0–1), `model_prob` (0–1),
`balance` ($).

**Optional (each enables a rule; absent ⇒ that rule is skipped, logged as an
advisory):** `edge_pt`, `open_position_cost`, `bankroll`, `market_volume`,
`peak_balance`, `recent_daily_pnl` (most-recent-first), `today_pnl_dollars`,
`starting_balance_dollars`, `strategy`. Unknown keys are ignored, so you can pass
your own trade objects through untouched.

## Works on any venue

- **Prediction markets (Kalshi, Polymarket)** — native; `market_price` is the
  contract price. See [`examples/kalshi_example.py`](examples/kalshi_example.py).
- **Brokerage (Robinhood, Alpaca)** — map a directional thesis onto a win
  probability and a break-even "price" from your reward-to-risk; `verdict.size`
  is dollars-to-risk, convert to shares with your stop distance. Disable the
  R-014 anti-longshot floor for this mapping (it's a prediction-market rule) —
  see [`examples/robinhood_example.py`](examples/robinhood_example.py).

## The audit ledger

`AuditLedger` writes an append-only JSONL row for every decision and every fill —
the compliance artifact that makes an autonomous strategy defensible. Writes
**never raise**: a logging failure returns `False` instead of blocking your order
path.

## The kill switch

`KillSwitch` is a file-based, out-of-band halt. Any human or monitor can create
the halt file and the next `is_engaged()` refuses new entries — no process, no
network, no coordination with the agent. **Keep your exit path unconditional:**
halt entries, never exits.

## Testing

```bash
python -m guardrails.selftest     # zero-dependency smoke path
python -m pytest tests/           # full unittest suite
```

## License

MIT. Not financial advice — a risk-control tool, not a guarantee against loss.
