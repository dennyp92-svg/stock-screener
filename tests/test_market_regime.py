import datetime as dt

import pandas as pd

import market_regime as mr
import trade_patterns as tp


def days(n, start=dt.date(2026, 6, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def series(values, start=dt.date(2026, 6, 1)):
    return pd.Series(values, index=days(len(values), start), dtype=float)


def test_trend_labels():
    up = mr.classify(series([100 + i for i in range(70)]), series([14.0] * 70))
    assert up["trend"].iloc[-1] == "Uptrend" and pd.isna(up["trend"].iloc[10])   # no 50-day yet
    down = mr.classify(series([200 - i for i in range(70)]), series([25.0] * 70))
    assert down["trend"].iloc[-1] == "Downtrend" and down["volatility"].iloc[-1] == "Elevated"
    flat = mr.classify(series([100 + (i % 2) for i in range(70)]), series([16.0] * 70))
    assert flat["trend"].iloc[-1] == "Sideways" and flat["volatility"].iloc[-1] == "Normal"


def test_vix_bands():
    assert [mr.vol_label(v) for v in (12, 15, 19.99, 20, 29.9, 30, 45)] == \
        ["Calm", "Normal", "Normal", "Elevated", "Elevated", "High", "High"]
    assert mr.vol_label(None) is None


def test_missing_vix_day_carries_forward():
    spy = series([100.0] * 60)
    vix = series([15.0] * 60).drop(spy.index[30])
    assert mr.classify(spy, vix)["vix"].iloc[30] == 15.0


def test_no_lookahead_uses_prior_close():
    reg = mr.classify(series([100 + i for i in range(70)]), series([14.0 + i * 0.1 for i in range(70)]))
    d = reg.index[60]
    assert mr.as_of_prior_close(reg, d).name == reg.index[59]          # entered on day 60 -> day 59's close
    assert mr.as_of_prior_close(reg, reg.index[0]) is None
    weekend = d + dt.timedelta(days=(5 - d.weekday()) % 7 or 7)
    assert mr.as_of_prior_close(reg, weekend).name <= weekend - dt.timedelta(days=1)


def test_attach_adds_factors_used_by_breakdown():
    reg = mr.classify(series([100 + i for i in range(80)]), series([12 + (i % 9) for i in range(80)]))
    entries = list(reg.index[55:79])
    df = pd.DataFrame({"entry_date": entries, "pnl": [10.0, -5.0] * 12, "score": [1.0, -0.5] * 12})
    out = mr.attach(df, reg)
    assert set(out["Market trend (SPY)"]) == {"Uptrend"}
    assert out["VIX at entry"].notna().all()
    assert "Market trend (SPY)" in tp.factors(out) and "VIX at entry" in tp.factors(out)
    assert "entry_date" not in tp.factors(out)
    assert [g.split()[0] for g in tp.breakdown(out, "VIX at entry")["Group"]] == ["low", "mid", "high"]


def test_attach_without_regimes_is_unchanged():
    df = pd.DataFrame({"entry_date": [dt.date(2026, 10, 1)], "pnl": [1.0], "score": [1.0]})
    assert list(mr.attach(df, None).columns) == list(df.columns)


def test_headline_and_fetch():
    reg = mr.classify(series([100 + i for i in range(70)]), series([19.45] * 70))
    h = mr.headline(reg)
    assert "Uptrend · Normal" in h and "VIX 19.4" in h or "VIX 19.5" in h

    class R:
        def raise_for_status(self): pass
        def json(self): return [{"date": "2026-10-23", "price": 759.97}, {"date": "2026-10-22", "price": 763.96}]
    s = mr.fetch_closes("SPY", "k", dt.date(2026, 10, 1), get=lambda url, params, timeout: R())
    assert list(s.index) == [dt.date(2026, 10, 22), dt.date(2026, 10, 23)] and s.iloc[-1] == 759.97


def test_load_without_key_is_none(monkeypatch):
    monkeypatch.setattr(mr, "_key", lambda: None)
    assert mr.load() is None


def test_trade_frames_carry_entry_dates():
    df = tp.intraday_patterns([{"payload": {"pnl_net": 5, "r_multiple": 0.5, "entry_ts": "2026-10-23T03:30:00+00:00"}}])
    assert df["entry_date"].iloc[0] == dt.date(2026, 10, 22)            # 3:30 UTC = 10:30 PM CT the day before
    pk = tp.picks_patterns([{"status": "closed", "pnl": 1, "r_multiple": 1, "entry_time": "2026-10-22T13:45:41+00:00"}])
    assert pk["entry_date"].iloc[0] == dt.date(2026, 10, 22)
