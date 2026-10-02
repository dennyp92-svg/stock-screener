"""Offline tests for live-mode bookkeeping (no network, no real orders).

Run:  python -m unittest tests.test_live_state -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auto_trader as at  # noqa: E402


class FakeLiveBroker(at.Broker):
    """Minimal non-paper broker that records the stop orders it is asked for."""

    def __init__(self, positions, cash=1000.0):
        self._pos = positions
        self._cash = cash
        self.stops_placed = []
        self.cancelled = []

    def get_cash(self): return self._cash
    def get_positions(self): return self._pos
    def buy(self, *a, **k): return {"ok": True}
    def sell(self, *a, **k): return {"ok": True}

    def place_stop(self, sym, qty, stop_px):
        cid = f"stop-{sym}-{len(self.stops_placed)}"
        self.stops_placed.append((sym, qty, stop_px))
        return {"ok": True, "client_order_id": cid}

    def cancel(self, cid):
        self.cancelled.append(cid)
        return {"ok": True}


class InMemoryStore(at.StateStore):
    """Stands in for the shared Supabase row: it outlives every 'run', while
    the local disk does not (see setUp)."""

    def __init__(self):
        self.doc = None

    def load(self, default):
        return self.doc if self.doc is not None else default

    def save(self, state):
        self.doc = state


class LiveStateTests(unittest.TestCase):
    def setUp(self):
        # Model an ephemeral CI runner: the local aux file never exists, so the
        # ONLY thing that can carry state between runs is the shared store.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        at._AUX_FILE = os.path.join(self.tmp.name, "does_not_persist.json")
        self.remote = InMemoryStore()
        orig = at.build_aux_store
        at.build_aux_store = lambda cfg: self.remote
        self.addCleanup(setattr, at, "build_aux_store", orig)
        os.environ["AUTO_TRADE_STATE_BACKEND"] = "file"
        self.addCleanup(os.environ.pop, "AUTO_TRADE_STATE_BACKEND", None)
        self.cfg = at.Config()

    def _engine(self, broker):
        return at.Engine(self.cfg, broker)  # a NEW Engine == a new process/run

    def test_stops_not_duplicated_across_runs(self):
        pos = {"AAA": {"qty": 3, "avg_price": 100.0}}
        b = FakeLiveBroker(pos)
        self._engine(b)._sync_protective_stops()
        self._engine(b)._sync_protective_stops()   # "next 30-minute run"
        self._engine(b)._sync_protective_stops()
        self.assertEqual(len(b.stops_placed), 1, b.stops_placed)

    def test_stop_forgotten_when_position_gone(self):
        b = FakeLiveBroker({"AAA": {"qty": 3, "avg_price": 100.0}})
        self._engine(b)._sync_protective_stops()
        b._pos = {}
        self.assertEqual(self._engine(b)._sync_protective_stops(), {})

    def test_take_profit_cancels_persisted_stop_once(self):
        b = FakeLiveBroker({"AAA": {"qty": 3, "avg_price": 100.0}})
        self._engine(b)._sync_protective_stops()
        # new run: price is above take-profit -> cancel resting stop, then sell
        snaps = {"AAA": {"price": 100.0 * (1 + self.cfg.take_profit_pct) + 1}}
        self._engine(b)._manage_open_positions(snaps)
        self.assertEqual(b.cancelled, ["stop-AAA-0"])
        self.assertNotIn("AAA", self._engine(b)._load_aux()["stops"])

    def test_day_start_equity_persists_across_runs(self):
        b = FakeLiveBroker({})
        first = self._engine(b)._day_start_equity("2026-10-05", 1000.0)
        later = self._engine(b)._day_start_equity("2026-10-05", 940.0)
        self.assertEqual(first, 1000.0)
        self.assertEqual(later, 1000.0)   # baseline NOT reset by a new run

    def test_daily_loss_limit_trips_across_runs(self):
        b = FakeLiveBroker({})
        self.cfg.daily_loss_limit_pct = 0.05
        self._engine(b)._day_guard(1000.0)           # run 1 sets baseline
        self.assertFalse(self._engine(b)._day_guard(900.0))  # run 2: -10%

    def test_live_blocked_on_ci_without_durable_state(self):
        os.environ["AUTO_TRADE_LIVE_CONFIRM"] = "I_UNDERSTAND_THE_RISK"
        os.environ["GITHUB_ACTIONS"] = "true"
        self.addCleanup(os.environ.pop, "AUTO_TRADE_LIVE_CONFIRM", None)
        self.addCleanup(os.environ.pop, "GITHUB_ACTIONS", None)
        cfg = at.Config()
        cfg.mode = "live"
        with self.assertRaises(SystemExit) as cm:
            at.build_broker(cfg)
        self.assertIn("durable state", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
