import pandas as pd

import bot_performance_tab as bp


def _intraday(pnl, ts="2026-10-06T15:00:00+00:00", r=1.0):
    return {"ts": ts, "payload": {"symbol": "AAA", "side": "long", "strategy": "MOM",
                                  "entry_ts": ts, "exit_ts": ts, "pnl_net": pnl,
                                  "r_multiple": r, "exit_reason": "TARGET"}}


def test_max_drawdown():
    assert bp.max_drawdown([100, -50, -80, 40, -200]) == -290
    assert bp.max_drawdown([10, 20]) == 0
    assert bp.max_drawdown([]) == 0


def test_summary_counts_and_win_rate():
    closed = bp._times(bp.intraday_frame([_intraday(100), _intraday(-50), _intraday(-25)]))
    s = bp.summarize(closed).set_index("Bot")
    row = s.loc["Intraday bot"]
    assert row["Closed trades"] == 3
    assert row["Win rate %"] == 33.3
    assert row["Total P/L $"] == 25
    assert row["Profit factor"] == round(100 / 75, 2)
    assert row["Enough data?"] == "no (3/30)"
    # bots with no trades still appear, with zeros, not errors
    assert s.loc["Options bot", "Closed trades"] == 0
    assert s.loc["Picks bot", "Total P/L $"] == 0


def test_options_skips_dry_runs():
    rows = [{"ts": "2026-10-16T19:15:00+00:00", "payload": {"mode": "paper", "pnl": -5175, "ticker": "QQQ"}},
            {"ts": "2026-10-16T19:15:00+00:00", "payload": {"mode": "dry", "pnl": 999, "ticker": "QQQ"}}]
    df = bp.options_frame(rows)
    assert len(df) == 1 and df["pnl"].iloc[0] == -5175


def test_picks_split_open_and_closed():
    rows = [{"symbol": "OII", "status": "closed", "pnl": 638.13, "exit_time": "2026-10-22T15:28:00+00:00"},
            {"symbol": "DHR", "status": "open", "pnl": -203.56, "exit_time": None}]
    closed, opened = bp.picks_frames(rows)
    assert list(closed["symbol"]) == ["OII"] and list(opened["symbol"]) == ["DHR"]
    s = bp.summarize(bp._times(closed), bp._times(opened)).set_index("Bot")
    assert s.loc["Picks bot", "Open now"] == 1
    assert s.loc["Picks bot", "Open P/L $"] == -203.56


def test_daily_cumulative_uses_central_time():
    # 03:00 UTC on Oct 7 is still Oct 6 in Chicago
    closed = bp._times(bp.intraday_frame([_intraday(10, "2026-10-07T03:00:00+00:00"),
                                          _intraday(5, "2026-10-07T15:00:00+00:00")]))
    curve = bp.daily_cumulative(closed)
    assert [str(d) for d in curve.index] == ["2026-10-06", "2026-10-07"]
    assert list(curve["Intraday bot"]) == [10, 15]


def test_display_summary_is_all_text():
    closed = bp._times(bp.intraday_frame([_intraday(100), _intraday(-50)]))
    shown = bp.display_summary(bp.summarize(closed))
    assert list(shown.columns) == bp.BOTS
    assert shown.loc["Total P/L", "Intraday bot"] == "$50.00"
    assert shown.loc["Avg loss", "Intraday bot"] == "-$50.00"
    assert shown.loc["Win rate", "Intraday bot"] == "50.0%"
    assert shown.loc["Win rate", "Options bot"] == "—"
    assert all(isinstance(v, str) for v in shown.to_numpy().ravel())
