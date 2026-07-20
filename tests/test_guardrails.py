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
