"""ledger — append-only audit trail for every guardrail decision and fill.

This is the compliance artifact: an immutable, timestamped JSONL record of what
the agent tried to do, what the gate decided, and what actually filled. It is
what makes an autonomous strategy defensible ("show me every order and why it
was allowed") — the feature that matters the moment real money or other people's
money is involved.

Design rules:
- Append-only. Rows are never mutated in place.
- A write failure NEVER raises to the caller. Logging must not block trading;
  :meth:`record` returns True/False and swallows IO errors so an order path can
  keep going. (Attach your own alerting to a False return.)
- Venue-agnostic schema — works for Kalshi, Polymarket, Robinhood, Alpaca, etc.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# status values a fill row may carry
STATUSES = ("DECISION", "FILLED", "PARTIAL", "REJECTED", "CANCELLED", "BLOCKED")


@dataclass
class LedgerRow:
    ts_utc: str
    event: str                       # "decision" | "fill"
    venue: str
    symbol: str
    side: str                        # "YES" | "NO" | "BUY" | "SELL"
    passed: Optional[bool] = None    # gate verdict (decision rows)
    blocking_rules: List[str] = field(default_factory=list)
    edge_pt: Optional[float] = None
    sized_dollars: Optional[float] = None
    intended_size: Optional[float] = None
    filled_size: Optional[float] = None
    market_price: Optional[float] = None
    fill_price: Optional[float] = None
    cost_dollars: Optional[float] = None
    status: str = "DECISION"
    order_id: Optional[str] = None
    strategy: Optional[str] = None
    notes: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLedger:
    """Append-only JSONL ledger. One file; each line is one :class:`LedgerRow`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # -------------------------------------------------------------- writing
    def _append(self, row: LedgerRow) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row.to_dict(), separators=(",", ":")) + "\n")
            return True
        except OSError:
            # Never block the trading path on a logging failure.
            return False

    def record_decision(self, verdict, *, venue: str, symbol: str, side: str,
                        market_price: Optional[float] = None, strategy: Optional[str] = None,
                        order_id: Optional[str] = None, notes: str = "") -> bool:
        """Log a guardrail :class:`~guardrails.engine.Verdict` as a decision row."""
        row = LedgerRow(
            ts_utc=_now_iso(), event="decision", venue=venue, symbol=symbol, side=side,
            passed=getattr(verdict, "passed", None),
            blocking_rules=list(getattr(verdict, "blocking_rules", []) or []),
            edge_pt=getattr(verdict, "edge_pt", None),
            sized_dollars=getattr(verdict, "size", None),
            market_price=market_price, strategy=strategy, order_id=order_id,
            status="DECISION" if getattr(verdict, "passed", False) else "BLOCKED",
            notes=notes,
        )
        return self._append(row)

    def record_fill(self, *, venue: str, symbol: str, side: str, intended_size: float,
                    filled_size: float, fill_price: float, status: str = "FILLED",
                    cost_dollars: Optional[float] = None, order_id: Optional[str] = None,
                    strategy: Optional[str] = None, notes: str = "") -> bool:
        """Log an actual fill (or partial/rejection) row."""
        if cost_dollars is None:
            cost_dollars = round(filled_size * fill_price, 4)
        row = LedgerRow(
            ts_utc=_now_iso(), event="fill", venue=venue, symbol=symbol, side=side,
            intended_size=intended_size, filled_size=filled_size, fill_price=fill_price,
            cost_dollars=cost_dollars, status=status, order_id=order_id,
            strategy=strategy, notes=notes,
        )
        return self._append(row)

    # -------------------------------------------------------------- reading
    def rows(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # tolerate a torn final line
        return out

    def rows_for_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        return [r for r in self.rows() if r.get("symbol") == symbol]

    def blocked_decisions(self) -> List[Dict[str, Any]]:
        return [r for r in self.rows() if r.get("event") == "decision" and r.get("passed") is False]
