"""agent-trading-guardrails — a read-only pre-trade safety layer for autonomous
trading agents.

Put the brakes in front of any bot or LLM agent that can place real orders:

    from guardrails import Guardrails, GuardrailConfig, AuditLedger, KillSwitch

    gate = Guardrails(GuardrailConfig.conservative())
    ledger = AuditLedger("audit/trades.jsonl")
    halt = KillSwitch("audit/HALT")

    verdict = gate.check({
        "side": "YES", "market_price": 0.52, "model_prob": 0.66,
        "balance": 1000.0, "peak_balance": 1200.0,
    })
    ledger.record_decision(verdict, venue="kalshi", symbol="KXFED-26JUL", side="YES")
    if not halt.is_engaged() and verdict.passed:
        ...  # place the order for verdict.size dollars

It decides; it never trades.
"""
from .config import GuardrailConfig
from .engine import Guardrails, Verdict, check_trade, RULE_NAMES
from .ledger import AuditLedger, LedgerRow
from .killswitch import KillSwitch

__version__ = "0.1.0"

__all__ = [
    "GuardrailConfig",
    "Guardrails",
    "Verdict",
    "check_trade",
    "RULE_NAMES",
    "AuditLedger",
    "LedgerRow",
    "KillSwitch",
    "__version__",
]
