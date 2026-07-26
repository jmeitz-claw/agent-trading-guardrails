"""Dependency-free self-tests. Run with `python -m guardrails.selftest`.

A parallel stdlib-`unittest` suite lives in tests/ for CI; this module keeps a
zero-dependency smoke path so an integrator can verify a checkout in one line.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .config import GuardrailConfig
from .engine import Guardrails, check_trade, nonfinite_fields
from .ledger import AuditLedger
from .killswitch import KillSwitch


def run() -> int:
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)

    g = Guardrails(GuardrailConfig.default())
    base = {"side": "YES", "market_price": 0.50, "model_prob": 0.70, "balance": 250.0}

    # --- sizing / drawdown helpers ---
    check("dd basic", abs(g._drawdown_pct(75.0, 100.0) - 25.0) < 1e-9)
    check("dd no-peak", g._drawdown_pct(75.0, None) == 0.0)
    check("dd above-peak", g._drawdown_pct(120.0, 100.0) == 0.0)
    check("dd mult <20", g._dd_size_multiplier(10.0) == 1.0)
    check("dd mult 20-40", g._dd_size_multiplier(30.0) == 0.5)
    check("dd mult >=40", g._dd_size_multiplier(45.0) == 0.0)
    check("fk 30pt/$250", abs(g._size_fractional_kelly(30.0, 250.0, 0.0) - 7.5) < 1e-9)
    check("fk balance-frac cap", abs(g._size_fractional_kelly(50.0, 250.0, 0.0) - 12.5) < 1e-9)
    check("fk dollar cap", g._size_fractional_kelly(100.0, 20000.0, 0.0) == g.config.kelly_dollar_cap)
    check("fk zero-edge", g._size_fractional_kelly(0.0, 250.0, 0.0) == 0.0)
    check("streak count", g._consecutive_losing_days([-1, -2, -3, 4, -5]) == 3)
    check("cap tier 5", g._effective_capital_cap_pct([-1] * 5) == 0.15)
    check("cap tier 3", g._effective_capital_cap_pct([-1] * 3) == 0.30)
    check("cap tier none", g._effective_capital_cap_pct([1, -1]) == 0.80)

    # --- gate: happy path ---
    r = g.check(dict(base))
    check("happy pass", r.passed and r.size > 0 and not r.blocking_rules)

    # --- input validation ---
    check("missing balance", not g.check({"side": "YES", "market_price": 0.5, "model_prob": 0.7}).passed)
    check("bad side", not g.check(dict(base, side="MAYBE")).passed)
    check("price OOB", not g.check(dict(base, market_price=1.5)).passed)

    # --- R-014 floors ---
    check("R-014 yes floor", "R-014" in g.check(dict(base, market_price=0.30, model_prob=0.80)).blocking_rules)
    check("R-014 no ceil", "R-014" in g.check(dict(base, side="NO", market_price=0.70, model_prob=0.10)).blocking_rules)

    # --- edge ---
    r = g.check(dict(base, side="NO", market_price=0.55, model_prob=0.30))
    check("NO-side edge pass", r.passed and r.edge_pt > 0)
    check("non-positive edge", "R-EDGE" in g.check(dict(base, market_price=0.75, model_prob=0.70)).blocking_rules)

    # --- R-006 volume ---
    check("R-006 thin", "R-006" in g.check(dict(base, market_volume=100.0)).blocking_rules)
    check("R-006 ok", "R-006" not in g.check(dict(base, market_volume=5000.0)).blocking_rules)

    # --- R-039 freeze / halt ---
    check("R-039 freeze", "R-039" in g.check(dict(base, balance=55.0, peak_balance=100.0)).blocking_rules)
    r = g.check(dict(base, balance=30.0, peak_balance=100.0))
    check("R-039 halt", "R-039" in r.blocking_rules and any("HALT" in x for x in r.reasons))
    check("R-039 ok <20", "R-039" not in g.check(dict(base, balance=90.0, peak_balance=100.0)).blocking_rules)

    # --- R-040 daily-loss budget ---
    check("R-040 breach", "R-040" in g.check(dict(base, today_pnl_dollars=-20.0, starting_balance_dollars=250.0)).blocking_rules)
    check("R-040 within", "R-040" not in g.check(dict(base, today_pnl_dollars=-5.0, starting_balance_dollars=250.0)).blocking_rules)

    # --- R-008 / R-025 capital guardrail ---
    check("R-008 block", "R-008" in g.check(dict(base, balance=100.0, open_position_cost=85.0)).blocking_rules)
    check("R-025 tightened", "R-025" in g.check(dict(base, balance=100.0, open_position_cost=20.0,
                                                     recent_daily_pnl=[-1] * 5)).blocking_rules)

    # --- R-017 extreme divergence (exponential sizing exposes it) ---
    gx = Guardrails(GuardrailConfig(sizing_method="exponential"))
    r = gx.check(dict(base, market_price=0.36, model_prob=0.95, balance=100000.0))
    check("R-017 cap", r.size <= gx.config.extreme_divergence_size_cap and any("R-017" in a for a in r.advisories))

    # --- conservative preset is stricter ---
    rc = check_trade(dict(base), GuardrailConfig.conservative())
    check("conservative smaller size", rc.size < g.check(dict(base)).size)

    # --- non-finite inputs fail closed ---
    # NaN compares False against every `>`/`>=` in the gate, so before this
    # guard each of these returned passed=True with the rules it touched
    # silently disabled (NaN balance -> R-008/R-025/R-040 off; NaN edge_pt ->
    # R-EDGE/R-017 off; NaN open_position_cost -> capital guardrail off).
    nan, inf = float("nan"), float("inf")
    for fname in ("balance", "edge_pt", "open_position_cost", "bankroll",
                  "peak_balance", "today_pnl_dollars", "starting_balance_dollars",
                  "market_volume"):
        for bad in (nan, inf):
            v = g.check(dict(base, **{fname: bad}))
            check(f"non-finite {fname} blocked", not v.passed and "R-INPUT" in v.blocking_rules)
            check(f"non-finite {fname} named", fname in " ".join(v.reasons))
    check("non-finite pnl entry blocked",
          not g.check(dict(base, recent_daily_pnl=[-1.0, nan])).passed)
    check("nonfinite_fields clean", nonfinite_fields(dict(base)) == [])

    # --- bad-sign optionals fail closed ---
    # Negative open cost loosens the R-008 cap; bankroll=0 was swallowed by a
    # falsy `or balance` and silently re-sized off the full balance.
    check("negative open cost blocked", not g.check(dict(base, open_position_cost=-1000.0)).passed)
    check("zero bankroll blocked", not g.check(dict(base, bankroll=0.0)).passed)
    check("negative bankroll blocked", not g.check(dict(base, bankroll=-5.0)).passed)
    check("bankroll omitted still passes", g.check(dict(base)).passed)

    # --- the gate decides, it never raises ---
    for label, bad_trade in [
        ("open_position_cost", dict(base, open_position_cost="x")),
        ("bankroll", dict(base, bankroll="x")),
        ("edge_pt", dict(base, edge_pt="x")),
        ("edge_pt list", dict(base, edge_pt=[])),
        ("recent_daily_pnl int", dict(base, recent_daily_pnl=5)),
    ]:
        try:
            check(f"no-raise {label}", g.check(bad_trade) is not None)
        except Exception as exc:  # noqa: BLE001 — must decide, never raise
            failures.append(f"no-raise {label} (raised {type(exc).__name__}: {exc})")
    rf = g.check(dict(base, edge_pt="x"))
    check("edge_pt fallback derives", rf.passed and abs(rf.edge_pt - 20.0) < 1e-9)

    # --- ledger round-trip ---
    with tempfile.TemporaryDirectory() as d:
        led = AuditLedger(Path(d) / "trades.jsonl")
        v = g.check(dict(base))
        check("ledger decision write", led.record_decision(v, venue="kalshi", symbol="KXFED", side="YES"))
        check("ledger fill write", led.record_fill(venue="kalshi", symbol="KXFED", side="YES",
                                                    intended_size=10, filled_size=10, fill_price=0.50))
        rows = led.rows()
        check("ledger rows", len(rows) == 2)
        check("ledger cost derived", rows[1]["cost_dollars"] == 5.0)
        check("ledger symbol filter", len(led.rows_for_symbol("KXFED")) == 2)

    # --- kill switch ---
    with tempfile.TemporaryDirectory() as d:
        ks = KillSwitch(Path(d) / "HALT")
        check("ks initially off", not ks.is_engaged())
        check("ks engage", ks.engage("selftest") and ks.is_engaged())
        check("ks reason", "selftest" in (ks.reason() or ""))
        check("ks release", ks.release() and not ks.is_engaged())

    if failures:
        print(f"SELFTEST FAILED ({len(failures)}): {failures}")
        return 1
    print("SELFTEST OK — all guardrail, ledger, and kill-switch checks pass")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(run())
