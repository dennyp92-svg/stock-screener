import pandas as pd

import trade_patterns as tp


def _row(pnl, r, hour=9, strat="MOM", rsi=50.0, mkt=0):
    return {"payload": {"pnl_net": pnl, "r_multiple": r, "strategy": strat, "side": "long",
                        "regime": "TREND", "vol_bucket": "mid", "ext_entry": 0,
                        "features": {"hour_ct": hour, "rsi": rsi, "mkt_dir": mkt}}}


def test_welch_t_sign_and_degenerate_cases():
    assert tp.welch_t([2, 3, 2.5, 3.2], [-1, -0.5, -1.2, -0.8]) > 2
    assert tp.welch_t([1], [1, 2, 3]) == 0.0
    assert tp.welch_t([1, 1, 1], [1, 1, 1]) == 0.0


def test_strong_group_is_likely_real_and_noise_is_luck():
    rows = [_row(100, 1.5 + (i % 3) * 0.1, hour=8) for i in range(15)]          # 8 AM: clearly good
    rows += [_row(-50, -1.0 + (i % 3) * 0.1, hour=10) for i in range(15)]       # 10 AM: clearly bad
    rows += [_row(10 if i % 2 else -10, 0.1 if i % 2 else -0.1, hour=11) for i in range(4)]
    df = tp.intraday_patterns(rows)
    b = tp.breakdown(df, "Entry hour (CT)").set_index("Group")
    assert list(b.index) == ["8 AM", "10 AM", "11 AM"]                 # clock order
    assert b.loc["8 AM", "Verdict"] == "likely real ✅"
    assert b.loc["10 AM", "Verdict"] == "likely real ⚠️"
    assert b.loc["11 AM", "Verdict"] == "too few trades"
    assert b.loc["8 AM", "Win rate %"] == 100.0


def test_mixed_results_are_called_luck():
    rows = [_row(50 if i % 2 else -50, 1 if i % 2 else -1, strat=s) for s in ("MOM", "VWAP") for i in range(14)]
    b = tp.breakdown(tp.intraday_patterns(rows), "Strategy")
    assert set(b["Verdict"]) == {"could be luck"}


def test_numeric_factor_splits_into_thirds_in_order():
    rows = [_row(1, 0.1, rsi=float(v)) for v in range(30, 90, 2)]
    b = tp.breakdown(tp.intraday_patterns(rows), "RSI")
    assert [g.split()[0] for g in b["Group"]] == ["low", "mid", "high"]
    assert b["Trades"].sum() == 30


def test_market_direction_labels_and_missing_values_dropped():
    rows = [_row(1, 0.2, mkt=1)] * 12 + [_row(-1, -0.2, mkt=-1)] * 12
    rows.append({"payload": {"pnl_net": 5, "r_multiple": 0.5, "features": {}}})
    b = tp.breakdown(tp.intraday_patterns(rows), "Market direction")
    assert set(b["Group"]) == {"Market up", "Market down"} and b["Trades"].sum() == 24


def test_key_findings_only_reports_real_ones():
    rows = [_row(100, 1.5 + (i % 3) * 0.1, hour=8) for i in range(15)]
    rows += [_row(-50, -1.0 + (i % 3) * 0.1, hour=10) for i in range(15)]
    found = tp.key_findings(tp.intraday_patterns(rows))
    assert found and all(r["Verdict"].startswith("likely real") for _, r in found)


def test_autotrader_uses_utc_entry_times():
    df = tp.autotrader_patterns({"journal": [
        {"pnl": -39.37, "pnl_pct": -5.0, "entry_ts": "2026-10-19 19:13:24",
         "entry_meta": {"chg": 4.98, "rsi": 64.1, "vol_spike": 1.04}},
        {"pnl": 112.05, "pnl_pct": 11.26, "entry_ts": None, "entry_meta": {}}]})
    assert df["Entry hour (CT)"].iloc[0] == 14          # 19:13 UTC = 2 PM CT
    assert df["Entry weekday"].iloc[0] == "Mon"
    assert pd.isna(df["Entry hour (CT)"].iloc[1])


def test_picks_only_closed():
    df = tp.picks_patterns([{"status": "closed", "pnl": 10, "r_multiple": 1, "kind": "day", "direction": "long"},
                            {"status": "open", "pnl": 5, "r_multiple": None, "kind": "swing", "direction": "long"}])
    assert len(df) == 1


def test_tiny_comparison_group_is_not_called_real():
    # 7 outliers vs 30 others: neither side may be called real, since one side is too small
    rows = [_row(300, 4.0) for _ in range(7)] + [_row(1 if i % 2 else -1, 0.2 if i % 2 else -0.2) for i in range(30)]
    for i, r in enumerate(rows):
        r["payload"]["ext_entry"] = 1 if i < 7 else 0
    b = tp.breakdown(tp.intraday_patterns(rows), "Session")
    assert set(b["Verdict"]) == {"too few trades"}


def test_worth_watching_tier():
    import math
    rows = [_row(1, 0.35 + 0.8 * math.sin(i), hour=8) for i in range(20)]
    rows += [_row(1, 0.8 * math.sin(i + 1), hour=9) for i in range(20)]
    b = tp.breakdown(tp.intraday_patterns(rows), "Entry hour (CT)").set_index("Group")
    t = b.loc["8 AM", "vs rest (t)"]
    assert 1.5 <= abs(t) < 2 and b.loc["8 AM", "Verdict"] == "worth watching 👀"
