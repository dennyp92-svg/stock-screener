"""Earnings rules: don't buy into earnings, sell the afternoon before."""
import datetime as dt
import os, sys, tempfile, unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_trader as at

CT = ZoneInfo("America/Chicago")


def ct(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=CT)


class Helpers(unittest.TestCase):
    def test_exit_afternoon_before_only(self):
        # Wed Oct 28: MRK reports Thu Oct 29
        self.assertFalse(at.earnings_exit_due("2026-10-29", ct(2026, 10, 28, 9, 0)))
        self.assertTrue(at.earnings_exit_due("2026-10-29", ct(2026, 10, 28, 14, 0)))
        self.assertFalse(at.earnings_exit_due("2026-10-30", ct(2026, 10, 28, 14, 30)))   # two days out

    def test_friday_sells_before_monday_earnings(self):
        self.assertTrue(at.earnings_exit_due("2026-11-02", ct(2026, 10, 30, 14, 5)))

    def test_missed_window_sells_any_time(self):
        self.assertTrue(at.earnings_exit_due("2026-10-29", ct(2026, 10, 29, 8, 35)))
        self.assertFalse(at.earnings_exit_due(None, ct(2026, 10, 29, 8, 35)))

    def test_entry_block_window(self):
        today = dt.date(2026, 10, 26)
        self.assertTrue(at.earnings_blocks_entry("2026-11-02", today, 7))
        self.assertFalse(at.earnings_blocks_entry("2026-11-03", today, 7))
        self.assertFalse(at.earnings_blocks_entry("2026-10-28", today, 0))   # off
        self.assertFalse(at.earnings_blocks_entry(None, today, 7))

    def test_calendar_without_key_is_none(self):
        with mock.patch.object(at, "_secret", return_value=None):
            self.assertIsNone(at.earnings_dates(7))

    def test_calendar_keeps_earliest_date(self):
        class R:
            def raise_for_status(self): pass
            def json(self): return [{"symbol": "MRK", "date": "2026-11-05"}, {"symbol": "MRK", "date": "2026-10-29"},
                                    {"symbol": "ABBV", "date": "2026-10-30"}, {"date": "2026-10-30"}]
        with mock.patch.object(at, "_secret", return_value="k"), mock.patch("requests.get", return_value=R()):
            self.assertEqual(at.earnings_dates(7, dt.date(2026, 10, 26)), {"MRK": "2026-10-29", "ABBV": "2026-10-30"})

    def test_calendar_failure_is_none(self):
        with mock.patch.object(at, "_secret", return_value="k"), mock.patch("requests.get", side_effect=OSError("down")):
            self.assertIsNone(at.earnings_dates(7))


def snap(t, price=100.0):
    return {"ticker": t, "price": price, "chg": 5.0, "rsi": 60.0, "vol_spike": 2.0}


class EngineCycle(unittest.TestCase):
    def make(self, **kw):
        cfg = at.Config(use_ai_confirmation=False, reentry_cooldown_days=0, regime_filter=False,
                        universe=["AAA", "BBB"], **kw)
        broker = at.PaperBroker(cfg, at.FileStateStore(os.path.join(tempfile.mkdtemp(), "s.json")))
        return broker, at.Engine(cfg, broker)

    def run_cycle(self, eng, earnings, now):
        class FakeDT(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now
        with mock.patch.object(at, "market_is_open", return_value=True), \
             mock.patch.object(at, "get_stock_data", side_effect=lambda t: snap(t)), \
             mock.patch.object(at, "get_bars_since", return_value=None), \
             mock.patch.object(at, "ai_postmortem", return_value=None), \
             mock.patch.object(at, "earnings_dates", return_value=earnings), \
             mock.patch("datetime.datetime", FakeDT):
            eng.run_once()

    def test_does_not_buy_into_earnings(self):
        broker, eng = self.make()
        self.run_cycle(eng, {"AAA": "2026-10-29"}, ct(2026, 10, 26, 10))
        self.assertNotIn("AAA", broker.get_positions())
        self.assertIn("BBB", broker.get_positions())

    def test_sells_held_stock_afternoon_before(self):
        broker, eng = self.make()
        broker.buy("MRK", 5, 100.0)
        self.run_cycle(eng, {"MRK": "2026-10-29"}, ct(2026, 10, 28, 14, 30))
        self.assertNotIn("MRK", broker.get_positions())
        self.assertEqual(broker.state["journal"][-1]["exit_reason"], "EARNINGS 2026-10-29")

    def test_keeps_held_stock_in_the_morning(self):
        broker, eng = self.make()
        broker.buy("MRK", 5, 100.0)
        self.run_cycle(eng, {"MRK": "2026-10-29"}, ct(2026, 10, 28, 10, 0))
        self.assertIn("MRK", broker.get_positions())

    def test_rules_off_and_calendar_down_change_nothing(self):
        broker, eng = self.make(earnings_skip_days=0, earnings_exit=False)
        broker.buy("MRK", 5, 100.0)
        self.run_cycle(eng, {"AAA": "2026-10-27", "MRK": "2026-10-27"}, ct(2026, 10, 26, 14, 30))
        self.assertIn("MRK", broker.get_positions())
        self.assertIn("AAA", broker.get_positions())
        broker, eng = self.make()
        self.run_cycle(eng, None, ct(2026, 10, 26, 14, 30))
        self.assertIn("AAA", broker.get_positions())


if __name__ == "__main__":
    unittest.main()
