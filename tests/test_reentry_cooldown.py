"""Re-entry cooldown: the bot must not buy a symbol back right after selling it."""
import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_trader as at


class CooldownHelper(unittest.TestCase):
    def test_same_day_blocked_next_day_allowed_at_1_day(self):
        self.assertTrue(at.in_reentry_cooldown("2026-10-09 18:45:42", "2026-10-09 18:45:48", 1))
        self.assertFalse(at.in_reentry_cooldown("2026-10-09 18:45:42", "2026-10-10 14:00:00", 1))

    def test_two_day_cooldown_blocks_next_day(self):
        self.assertTrue(at.in_reentry_cooldown("2026-10-09 18:45:42", "2026-10-10 14:00:00", 2))
        self.assertFalse(at.in_reentry_cooldown("2026-10-09 18:45:42", "2026-10-11 14:00:00", 2))

    def test_off_or_no_history_or_bad_data_never_blocks(self):
        self.assertFalse(at.in_reentry_cooldown("2026-10-09 18:45:42", "2026-10-09 19:00:00", 0))
        self.assertFalse(at.in_reentry_cooldown(None, "2026-10-09 19:00:00", 1))
        self.assertFalse(at.in_reentry_cooldown("garbage", "2026-10-09 19:00:00", 1))


def snap(t, price=100.0):
    # passes the default rules: change, volume, RSI and price all in range
    return {"ticker": t, "price": price, "chg": 5.0, "rsi": 60.0, "vol_spike": 2.0}


class EngineCycle(unittest.TestCase):
    def make(self, days):
        cfg = at.Config(use_ai_confirmation=False, reentry_cooldown_days=days,
                        regime_filter=False, universe=["AAA", "BBB"])
        path = os.path.join(tempfile.mkdtemp(), "state.json")
        broker = at.PaperBroker(cfg, at.FileStateStore(path))
        return cfg, broker, at.Engine(cfg, broker)

    def run_cycle(self, eng):
        with mock.patch.object(at, "market_is_open", return_value=True), \
             mock.patch.object(at, "get_stock_data", side_effect=lambda t: snap(t)), \
             mock.patch.object(at, "get_bars_since", return_value=None), \
             mock.patch.object(at, "ai_postmortem", return_value=None):
            eng.run_once()

    def test_just_sold_symbol_is_not_rebought(self):
        cfg, broker, eng = self.make(1)
        broker.buy("AAA", 1, 100.0)
        broker.sell("AAA", 1, 100.0, exit_reason="TAKE-PROFIT")
        self.run_cycle(eng)
        self.assertNotIn("AAA", broker.get_positions())   # cooled down
        self.assertIn("BBB", broker.get_positions())      # others still trade

    def test_cooldown_off_rebuys(self):
        cfg, broker, eng = self.make(0)
        broker.buy("AAA", 1, 100.0)
        broker.sell("AAA", 1, 100.0, exit_reason="TAKE-PROFIT")
        self.run_cycle(eng)
        self.assertIn("AAA", broker.get_positions())

    def test_old_sale_does_not_block(self):
        cfg, broker, eng = self.make(1)
        broker.state["trades"].append({"ts": "2026-01-02 15:00:00", "side": "SELL",
                                       "symbol": "AAA", "qty": 1, "price": 100, "realized": 0})
        self.run_cycle(eng)
        self.assertIn("AAA", broker.get_positions())

    def test_stop_hit_this_cycle_is_not_rebought_same_cycle(self):
        cfg, broker, eng = self.make(1)
        broker.buy("AAA", 1, 120.0)                       # now 100 = -16.7% -> stop
        self.run_cycle(eng)
        self.assertNotIn("AAA", broker.get_positions())
        self.assertEqual([t["side"] for t in broker.state["trades"] if t["symbol"] == "AAA"],
                         ["BUY", "SELL"])                 # no second BUY


if __name__ == "__main__":
    unittest.main()
