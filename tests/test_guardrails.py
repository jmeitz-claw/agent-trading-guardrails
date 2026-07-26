"""stdlib unittest mirror of the smoke self-tests, for `python -m pytest` / CI."""
import tempfile
import unittest
from pathlib import Path

from guardrails import Guardrails, GuardrailConfig, AuditLedger, KillSwitch, check_trade


BASE = {"side": "YES", "market_price": 0.50, "model_prob": 0.70, "balance": 250.0}


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.g = Guardrails(GuardrailConfig.default())

    def test_happy_path(self):
        v = self.g.check(dict(BASE))
        self.assertTrue(v.passed)
        self.assertGreater(v.size, 0)
        self.assertEqual(v.blocking_rules, [])

    def test_input_validation(self):
        self.assertFalse(self.g.check({"side": "YES", "market_price": 0.5, "model_prob": 0.7}).passed)
        self.assertFalse(self.g.check(dict(BASE, side="MAYBE")).passed)
        self.assertFalse(self.g.check(dict(BASE, market_price=1.5)).passed)
        self.assertFalse(self.g.check(dict(BASE, balance=0)).passed)

    def test_r014_floors(self):
        self.assertIn("R-014", self.g.check(dict(BASE, market_price=0.30, model_prob=0.80)).blocking_rules)
        self.assertIn("R-014", self.g.check(dict(BASE, side="NO", market_price=0.70, model_prob=0.10)).blocking_rules)

    def test_edge(self):
        self.assertIn("R-EDGE", self.g.check(dict(BASE, market_price=0.75, model_prob=0.70)).blocking_rules)
        v = self.g.check(dict(BASE, side="NO", market_price=0.55, model_prob=0.30))
        self.assertTrue(v.passed and v.edge_pt > 0)

    def test_volume(self):
        self.assertIn("R-006", self.g.check(dict(BASE, market_volume=100.0)).blocking_rules)
        self.assertNotIn("R-006", self.g.check(dict(BASE, market_volume=5000.0)).blocking_rules)

    def test_drawdown_breaker(self):
        self.assertIn("R-039", self.g.check(dict(BASE, balance=55.0, peak_balance=100.0)).blocking_rules)
        v = self.g.check(dict(BASE, balance=30.0, peak_balance=100.0))
        self.assertIn("R-039", v.blocking_rules)
        self.assertTrue(any("HALT" in r for r in v.reasons))
        self.assertNotIn("R-039", self.g.check(dict(BASE, balance=90.0, peak_balance=100.0)).blocking_rules)

    def test_daily_loss_budget(self):
        self.assertIn("R-040", self.g.check(dict(BASE, today_pnl_dollars=-20.0, starting_balance_dollars=250.0)).blocking_rules)
        self.assertNotIn("R-040", self.g.check(dict(BASE, today_pnl_dollars=-5.0, starting_balance_dollars=250.0)).blocking_rules)

    def test_capital_guardrail(self):
        self.assertIn("R-008", self.g.check(dict(BASE, balance=100.0, open_position_cost=85.0)).blocking_rules)
        self.assertIn("R-025", self.g.check(dict(BASE, balance=100.0, open_position_cost=20.0,
                                                  recent_daily_pnl=[-1] * 5)).blocking_rules)

    def test_extreme_divergence(self):
        gx = Guardrails(GuardrailConfig(sizing_method="exponential"))
        v = gx.check(dict(BASE, market_price=0.36, model_prob=0.95, balance=100000.0))
        self.assertLessEqual(v.size, gx.config.extreme_divergence_size_cap)
        self.assertTrue(any("R-017" in a for a in v.advisories))

    def test_conservative_preset_is_stricter(self):
        self.assertLess(check_trade(dict(BASE), GuardrailConfig.conservative()).size,
                        self.g.check(dict(BASE)).size)


class TestLedger(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(Path(d) / "t.jsonl")
            v = Guardrails().check(dict(BASE))
            self.assertTrue(led.record_decision(v, venue="kalshi", symbol="KXFED", side="YES"))
            self.assertTrue(led.record_fill(venue="kalshi", symbol="KXFED", side="YES",
                                            intended_size=10, filled_size=10, fill_price=0.50))
            rows = led.rows()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["cost_dollars"], 5.0)
            self.assertEqual(len(led.rows_for_symbol("KXFED")), 2)


class TestMalformedInput(unittest.TestCase):
    """Garbage in must mean refuse-to-trade, not "decide with the rules off"."""

    def setUp(self):
        self.g = Guardrails(GuardrailConfig.default())

    def test_nonfinite_fields_fail_closed(self):
        # NaN compares False against every `>`/`>=` in the gate, so it does not
        # fail a rule — it silently disables it. Each of these used to return
        # passed=True with the touched guardrail switched off.
        for field in ("balance", "edge_pt", "open_position_cost", "bankroll",
                      "peak_balance", "today_pnl_dollars", "starting_balance_dollars",
                      "market_volume"):
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=bad):
                    v = self.g.check(dict(BASE, **{field: bad}))
                    self.assertFalse(v.passed)
                    self.assertIn("R-INPUT", v.blocking_rules)
                    self.assertIn(field, " ".join(v.reasons))

    def test_nonfinite_pnl_entry_fails_closed(self):
        v = self.g.check(dict(BASE, recent_daily_pnl=[-1.0, float("nan"), -1.0]))
        self.assertFalse(v.passed)
        self.assertIn("recent_daily_pnl[1]", " ".join(v.reasons))

    def test_bad_sign_optionals_fail_closed(self):
        # A negative open cost subtracts from projected exposure and loosens the
        # R-008/R-025 cap; bankroll=0 was swallowed by a falsy `or balance` and
        # silently re-sized off the full balance.
        self.assertFalse(self.g.check(dict(BASE, open_position_cost=-1000.0)).passed)
        self.assertFalse(self.g.check(dict(BASE, bankroll=0.0)).passed)
        self.assertFalse(self.g.check(dict(BASE, bankroll=-5.0)).passed)
        # omitting them is still the documented default, and still passes
        self.assertTrue(self.g.check(dict(BASE)).passed)

    def test_gate_decides_never_raises(self):
        # These escaped as uncaught ValueError/TypeError, leaving fail-safety to
        # whatever the caller happened to wrap check() in.
        for bad in (dict(BASE, open_position_cost="x"), dict(BASE, bankroll="x"),
                    dict(BASE, edge_pt="x"), dict(BASE, edge_pt=[]),
                    dict(BASE, recent_daily_pnl=5), dict(BASE, recent_daily_pnl="x"),
                    dict(BASE, market_volume="x"), dict(BASE, peak_balance="x")):
            with self.subTest(trade=bad):
                self.assertIsNotNone(self.g.check(bad))

    def test_nonnumeric_edge_falls_back_to_derived(self):
        v = self.g.check(dict(BASE, edge_pt="x"))
        self.assertTrue(v.passed)
        self.assertAlmostEqual(v.edge_pt, 20.0, places=6)


class TestKillSwitch(unittest.TestCase):
    def test_engage_release(self):
        with tempfile.TemporaryDirectory() as d:
            ks = KillSwitch(Path(d) / "HALT")
            self.assertFalse(ks.is_engaged())
            self.assertTrue(ks.engage("test"))
            self.assertTrue(ks.is_engaged())
            self.assertIn("test", ks.reason() or "")
            self.assertTrue(ks.release())
            self.assertFalse(ks.is_engaged())


if __name__ == "__main__":
    unittest.main()
