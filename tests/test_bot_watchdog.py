import datetime as dt

import bot_watchdog as wd

CT = wd.CT
UTC = dt.timezone.utc


def ct(d, hh, mm=0):
    return dt.datetime(2026, 10, d, hh, mm, tzinfo=CT)


def utc_iso(t):
    return t.astimezone(UTC).isoformat()


def test_slot_matches_daylight_saving():
    summer = dt.datetime(2026, 10, 26, 14, 16, tzinfo=UTC)       # 9:16 CDT
    assert wd.slot_for("15 14 * * 1-5", summer) == "morning"
    assert wd.slot_for("15 15 * * 1-5", summer) is None
    winter = dt.datetime(2026, 11, 2, 21, 26, tzinfo=UTC)        # 3:26 CST
    assert wd.slot_for("25 21 * * 1-5", winter) == "close"
    assert wd.slot_for(None, summer, "midday") == "midday"
    assert wd.slot_for(None, summer) == "close"


def test_market_day_uses_bots_next_session():
    assert wd.market_day(ct(26, 9), "2026-10-26")
    assert not wd.market_day(ct(25, 9), None)                     # Sunday
    assert not wd.market_day(dt.datetime(2026, 11, 26, 9, tzinfo=CT), "2026-11-27")   # holiday
    assert wd.market_day(ct(28, 9), "2026-10-23")                 # stale info -> still check


def test_heartbeat_quiet_and_missing():
    now = ct(26, 12, 15)
    assert wd.check_heartbeat("X", utc_iso(ct(26, 12, 0)), now, ("08:40", "14:20"), 60) == []
    assert "silent for 75 min" in wd.check_heartbeat("X", utc_iso(ct(26, 11, 0)), now, ("08:40", "14:20"), 60)[0]
    assert "no log entries today" in wd.check_heartbeat("X", utc_iso(ct(23, 14, 20)), now, ("08:40", "14:20"), 60)[0]
    assert wd.check_heartbeat("X", None, ct(26, 8, 0), ("08:40", "14:20"), 60) == []   # before window


def test_heartbeat_after_window_measures_to_window_end():
    # intraday stops at 2:20; at 3:25 a last log of 2:20 is fine, 12:30 is not
    assert wd.check_heartbeat("X", utc_iso(ct(26, 14, 20)), ct(26, 15, 25), ("08:40", "14:20"), 60) == []
    assert wd.check_heartbeat("X", utc_iso(ct(26, 12, 30)), ct(26, 15, 25), ("08:40", "14:20"), 60)


def test_day_trades_left_open_overnight_is_caught():
    trades = [{"symbol": "FPS", "kind": "day", "status": "open", "submitted_at": "2026-10-22T09:46:00-05:00"},
              {"symbol": "ROKU", "kind": "swing", "status": "open", "submitted_at": "2026-10-22T09:06:00-05:00"},
              {"symbol": "NEW", "kind": "day", "status": "open", "submitted_at": "2026-10-26T08:46:00-05:00"}]
    morning = wd.check_day_trades(trades, ct(26, 9, 15), after_close=False)
    assert len(morning) == 1 and "FPS from Oct 22 is still open" in morning[0]
    close = wd.check_day_trades(trades, ct(26, 15, 25), after_close=True)
    assert len(close) == 2 and any("NEW still open after the 2:55 PM close" in p for p in close)


def test_picks_tracker_autotrader_workflow_checks():
    now = ct(26, 15, 25)
    assert wd.check_picks_written([{"pick_date": "2026-10-26", "kind": "swing"}], now) == [
        "Picks system: no day picks recorded today (8:05/8:45 jobs on the MacBook Air)"]
    assert wd.check_tracker_ran([], now)
    assert wd.check_tracker_ran([{"day": "2026-10-26"}], now) == []
    assert wd.check_autotrader({"day": "2026-10-23"}, now)
    assert wd.check_autotrader({"day": "2026-10-26"}, now) == []
    runs = [{"created_at": "2026-10-26T12:31:00Z", "conclusion": "failure"}]
    assert wd.check_workflow("Catalyst alert", runs, now)
    assert wd.check_workflow("Catalyst alert", [{"created_at": "2026-10-23T12:31:00Z", "conclusion": "failure"}], now) == []


def test_intraday_errors_and_summary_today_only():
    now = ct(26, 15, 25)
    ev = [{"ts": utc_iso(ct(26, 10)), "payload": {"level": "ERROR", "message": "Order failed for USAR"}},
          {"ts": utc_iso(ct(23, 10)), "payload": {"level": "ERROR", "message": "old"}},
          {"ts": utc_iso(ct(23, 14, 50)), "payload": {"kind": "day_summary"}}]
    assert wd.check_intraday_errors(ev, now) == ["Intraday bot: 1 error(s) today, latest: Order failed for USAR"]
    assert wd.check_intraday_day_summary(ev, now)
    ev.append({"ts": utc_iso(ct(26, 14, 50)), "payload": {"kind": "day_summary"}})
    assert wd.check_intraday_day_summary(ev, now) == []


def _healthy(now):
    last = utc_iso(now.replace(hour=min(now.hour, 14), minute=15))
    return {"intraday_last": last, "options_last": last,
            "intraday_events": [{"ts": utc_iso(now.replace(hour=14, minute=50)), "payload": {"kind": "day_summary"}}],
            "next_session": "2026-10-26", "picks": [{"pick_date": "2026-10-26", "kind": "swing"},
                                                    {"pick_date": "2026-10-26", "kind": "day"}],
            "bot_trades": [], "daily": [{"day": "2026-10-26"}], "autotrader_state": {"day": "2026-10-26"}}


def test_healthy_day_messages():
    for slot, now in (("morning", ct(26, 9, 15)), ("midday", ct(26, 12, 15)), ("close", ct(26, 15, 25))):
        assert wd.run_checks(slot, _healthy(now), now) == [], slot
    assert wd.build_message("morning", [], ct(26, 9, 15)) is None
    assert "all checks passed" in wd.build_message("close", [], ct(26, 15, 25))


def test_problem_message_escapes_and_counts():
    msg = wd.build_message("midday", ["A <b>", "B"], ct(26, 12, 15))
    assert "2 problem(s)" in msg and "A &lt;b&gt;" in msg
