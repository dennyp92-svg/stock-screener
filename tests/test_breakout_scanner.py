import datetime as dt

import numpy as np
import pandas as pd

import breakout_scanner as bs


def make_bars(n=260, base=50.0, tight_last=12, res_bump=1.0):
    """Steady uptrend, then a tight base just under a 20-day high."""
    days = pd.bdate_range(end="2026-11-11", periods=n).date
    close = np.linspace(base * 0.7, base, n)
    close[-tight_last:] = base * 0.985
    high = close * 1.01
    high[-15] = base * res_bump  # the 20-day high (resistance) = base
    low = close * 0.99
    vol = np.full(n, 2_000_000.0)
    vol[-5:] = 1_200_000  # drying up
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol}, index=days)


def test_watch_finds_tight_base_near_resistance():
    st = bs.setup_stats(make_bars())
    assert abs(st["resistance"] - 50.0) < 1e-6
    assert 1.0 < st["dist"] < 2.0
    sc = bs.watch_score(st)
    assert sc is not None and sc >= 60


def test_watch_rejects_downtrend_and_far_from_level():
    d = make_bars()
    d["Close"] = d["Close"].values[::-1]
    assert bs.watch_score(bs.setup_stats(d)) is None
    far = make_bars(res_bump=1.10)  # resistance 10% above
    assert bs.watch_score(bs.setup_stats(far)) is None


def test_trigger_needs_break_and_volume():
    st = bs.setup_stats(make_bars())
    noon = dt.datetime(2026, 11, 12, 11, 45, tzinfo=bs.CT)  # half the session gone
    good = pd.Series({"Close": 50.6, "Volume": 2_000_000.0})   # 1.2% over, 2x pace
    t = bs.trigger(st, good, noon)
    assert t and 1.1 < t["above"] < 1.3 and t["rvol"] > 1.5
    assert bs.trigger(st, pd.Series({"Close": 50.6, "Volume": 500_000.0}), noon) is None   # weak volume
    assert bs.trigger(st, pd.Series({"Close": 49.9, "Volume": 3_000_000.0}), noon) is None  # not broken
    assert bs.trigger(st, pd.Series({"Close": 54.0, "Volume": 3_000_000.0}), noon) is None  # too extended
    assert bs.trigger(st, None, noon) is None


def test_split_today_separates_partial_bar():
    d = make_bars()
    done, bar = bs.split_today(d, d.index[-1])
    assert len(done) == len(d) - 1 and bar is not None
    done, bar = bs.split_today(d, dt.date(2030, 1, 1))
    assert len(done) == len(d) and bar is None


def test_messages_render():
    st = bs.setup_stats(make_bars())
    w = bs.fmt_watch(dt.date(2026, 11, 12), [("ABC", 80, st)], "FMP screener", 900)
    assert "WATCH" in w and "ABC" in w and "$50.00" in w
    t = bs.fmt_trigger(dt.datetime(2026, 11, 12, 9, 45, tzinfo=bs.CT),
                       [("ABC", {"price": 50.6, "above": 1.2, "rvol": 2.1, "new_52w": True}, st, True)])
    assert "TRIGGERED" in t and "was on WATCH" in t and "new 52-wk high" in t


def test_state_resets_each_day(tmp_path):
    p = tmp_path / "s.json"
    s = bs.load_state(p, dt.date(2026, 11, 12))
    s["watch_sent"] = True
    s["triggered"] = ["ABC"]
    bs.save_state(p, s)
    assert bs.load_state(p, dt.date(2026, 11, 12))["triggered"] == ["ABC"]
    assert bs.load_state(p, dt.date(2026, 11, 13))["triggered"] == []
