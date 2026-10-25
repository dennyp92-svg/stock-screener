import datetime as dt

import catalyst_alerts as ca

UTC = dt.timezone.utc


def test_should_run_picks_the_730_ct_cron():
    summer = dt.datetime(2026, 10, 26, 12, 31, tzinfo=UTC)   # CDT, UTC-5
    winter = dt.datetime(2026, 11, 2, 13, 31, tzinfo=UTC)    # CST, UTC-6 (DST ends Nov 1)
    assert ca.should_run("30 12 * * 1-5", summer)
    assert not ca.should_run("30 13 * * 1-5", summer)
    assert ca.should_run("30 13 * * 1-5", winter)
    assert not ca.should_run("30 12 * * 1-5", winter)
    # a late-running job still counts, since the decision uses which cron fired
    assert ca.should_run("30 12 * * 1-5", dt.datetime(2026, 10, 26, 13, 5, tzinfo=UTC))
    assert ca.should_run(None) and ca.should_run("")       # manual runs always go


def test_options_open_pairs_fills_with_closes():
    fills = [{"ts": "2026-10-13", "payload": {"mode": "paper", "ticker": "QQQ", "expiration": "2026-11-20"}},
             {"ts": "2026-10-22", "payload": {"mode": "paper", "ticker": "XLE", "expiration": "2026-11-27"}},
             {"ts": "2026-10-08", "payload": {"mode": "dry", "ticker": "SPY", "expiration": "2026-11-13"}}]
    closed = [{"payload": {"mode": "paper", "ticker": "QQQ"}}]
    assert ca.options_open(fills, closed) == {"XLE": "2026-11-27"}


def test_next_trading_day_skips_weekend():
    assert ca.next_trading_day(dt.date(2026, 10, 23)) == dt.date(2026, 10, 26)   # Fri -> Mon
    assert ca.next_trading_day(dt.date(2026, 10, 26)) == dt.date(2026, 10, 27)


def test_message_sections_and_labels():
    today = dt.date(2026, 10, 26)  # Monday
    held = {"V": {"picks bot"}, "XLE": {"options bot"}}
    watched = {"MSFT"}
    msg = ca.build_message(
        today, held, watched, {"XLE": "2026-11-27"},
        {"V": "2026-10-27", "XLE": "2026-11-05", "MSFT": "2026-10-26"},
        [(dt.datetime(2026, 10, 26, 13, 0, tzinfo=ca.CT), "Fed Interest Rate Decision")])
    assert "HOLD" in msg and "<b>V</b> TOMORROW — picks bot" in msg
    assert "XLE</b> Thu Nov 5 (before option expiry 2026-11-27)" in msg
    assert "MSFT TODAY" in msg
    assert "Today 1:00 PM CT — Fed Interest Rate Decision" in msg


def test_friday_labels_monday_as_next_trading_day():
    msg = ca.build_message(dt.date(2026, 10, 23), {"V": {"picks bot"}}, set(), {}, {"V": "2026-10-26"}, [])
    assert "next trading day" in msg


def test_no_message_when_nothing_coming():
    assert ca.build_message(dt.date(2026, 10, 26), {"V": {"picks bot"}}, set(), {}, {}, []) is None


def test_symbols_are_html_escaped():
    msg = ca.build_message(dt.date(2026, 10, 26), {"A&B": {"x"}}, set(), {}, {"A&B": "2026-10-26"}, [])
    assert "A&amp;B" in msg


def test_group_events_merges_report_variants():
    t = dt.datetime(2026, 10, 27, 7, 30, tzinfo=ca.CT)
    f = dt.datetime(2026, 10, 28, 13, 0, tzinfo=ca.CT)
    out = ca.group_events([(t, "Durable Goods Orders MoM (Sep)"), (t, "Durable Goods Orders Ex Transp MoM (Sep)"),
                           (t, "Durable Goods Orders Ex Defense MoM (Sep)"), (f, "Fed Interest Rate Decision")])
    assert out == [(t, "Durable Goods Orders (3 reports)"), (f, "Fed Interest Rate Decision")]


def test_trading_days_ahead():
    assert ca.trading_days_ahead(dt.date(2026, 10, 26), 4) == dt.date(2026, 10, 30)   # Mon -> Fri
