"""Breakout scanner -> its own Telegram bot.

Two kinds of alerts, labelled separately:
  🔭 WATCH     - "about to break out": uptrend, tight range sitting just under
                 its 20-day high (resistance). Sent once each weekday morning.
  🚀 TRIGGERED - "breaking out now": price has cleared that 20-day high on
                 heavy volume. Checked every 30 min while the market is open;
                 each stock is alerted at most once per day.

Universe: US stocks (no ETFs/funds), price > $10, market cap > $1B,
avg volume > 1M - from FMP's company screener, falling back to FMP's
most-actives/gainers lists if the screener isn't available on the plan.
Daily bars come from Yahoo Finance (yfinance), which includes today's
partial bar during the session.

Read-only: never places trades.

Env: FMP_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
     STATE_FILE   json file remembering what was already sent today
     DRY_RUN=1    print messages instead of sending
     FORCE=watch|trigger|both   run that part regardless of the clock (manual runs)
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

CT = ZoneInfo("America/Chicago")
FMP = "https://financialmodelingprep.com/stable"

# Universe
MIN_PRICE = 10
MIN_MCAP = 1_000_000_000
MIN_AVG_VOL = 1_000_000
MAX_UNIVERSE = 1500

# Setup rules
LOOKBACK = 20            # resistance = highest high of the last 20 completed days
WATCH_MAX_DIST = 3.0     # within 3% below resistance
WATCH_MAX_RANGE = 12.0   # last 10 days' high-low range, % of price
WATCH_TOP_N = 10

# Trigger rules
TRIGGER_MIN_BREAK = 0.2  # at least 0.2% above resistance
TRIGGER_MAX_EXT = 5.0    # skip if already >5% above (too extended to chase)
TRIGGER_MIN_RVOL = 1.5   # today's volume pace vs the 50-day average

# Clock (Central time)
WATCH_FROM = dt.time(7, 30)
OPEN = dt.time(8, 30)
WATCH_UNTIL = dt.time(10, 0)  # GitHub cron can run late; still send the watchlist up to here
TRIGGER_UNTIL = dt.time(15, 0)


# ---------------------------------------------------------------- universe ---
def fmp_get(path, key, **params):
    r = requests.get(f"{FMP}/{path}", params={**params, "apikey": key}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        raise RuntimeError(str(data)[:200])
    return data


def clean_symbol(s):
    return isinstance(s, str) and s.isascii() and s.replace("-", "").isalpha() and len(s) <= 5


def universe(key):
    """List of symbols to scan, and where they came from."""
    try:
        rows = fmp_get("company-screener", key, priceMoreThan=MIN_PRICE, marketCapMoreThan=MIN_MCAP,
                       avgVolumeMoreThan=MIN_AVG_VOL, isEtf="false", isFund="false",
                       isActivelyTrading="true", country="US", exchange="NASDAQ,NYSE,AMEX",
                       limit=5000)
        rows = sorted(rows, key=lambda r: -(r.get("marketCap") or 0))
        syms = [r["symbol"].replace(".", "-") for r in rows if clean_symbol(r.get("symbol", "").replace(".", "-"))]
        if len(syms) >= 50:
            return syms[:MAX_UNIVERSE], "FMP screener"
        print(f"FMP screener returned only {len(syms)} symbols; using fallback lists.", file=sys.stderr)
    except Exception as ex:
        print(f"FMP screener failed ({ex}); using fallback lists.", file=sys.stderr)
    syms = []
    for path in ("most-actives", "biggest-gainers"):
        try:
            for r in fmp_get(path, key):
                s = r.get("symbol", "")
                if clean_symbol(s) and (r.get("price") or 0) >= MIN_PRICE and s not in syms:
                    syms.append(s)
        except Exception as ex:
            print(f"FMP {path} failed: {ex}", file=sys.stderr)
    return syms, "FMP most-actives/gainers"


def daily_bars(symbols, chunk=200, threads=True, retry=True, period="1y"):
    """{symbol: DataFrame[Open, High, Low, Close, Volume]} of ~1 year of daily bars."""
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        try:
            df = yf.download(part, period=period, interval="1d", group_by="ticker",
                             auto_adjust=False, progress=False, threads=threads)
        except Exception as ex:
            print(f"yfinance chunk {i} failed: {ex}", file=sys.stderr)
            continue
        for s in part:
            try:
                d = df[s] if isinstance(df.columns, pd.MultiIndex) else df
                d = d[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(d) >= 60:
                    d.index = pd.to_datetime(d.index).date
                    out[s] = d
            except KeyError:
                pass
    missing = [s for s in symbols if s not in out]
    if retry and 0 < len(missing) <= 50:   # one-off download hiccups; retry slowly once
        out.update(daily_bars(missing, threads=False, retry=False, period=period))
    return out


# ---------------------------------------------------------------- rules ------
def split_today(df, today):
    """(completed bars, today's partial bar or None)."""
    if len(df) and df.index[-1] == today:
        return df.iloc[:-1], df.iloc[-1]
    return df, None


def atr(d, n):
    prev = d["Close"].shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - prev).abs(), (d["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().iloc[-1]


def setup_stats(d):
    """Numbers describing the setup, from completed daily bars only."""
    close = d["Close"].iloc[-1]
    sma50 = d["Close"].rolling(50).mean().iloc[-1]
    sma200 = d["Close"].rolling(200).mean().iloc[-1] if len(d) >= 200 else np.nan
    res = d["High"].iloc[-LOOKBACK:].max()
    last10 = d.iloc[-10:]
    return {
        "close": float(close),
        "sma50": float(sma50),
        "sma200": float(sma200),
        "resistance": float(res),
        "dist": float((res - close) / close * 100),
        "range10": float((last10["High"].max() - last10["Low"].min()) / close * 100),
        "atr_ratio": float(atr(d, 10) / atr(d, 50)),
        "vol_ratio": float(d["Volume"].iloc[-5:].mean() / d["Volume"].iloc[-50:].mean()),
        "avg_vol50": float(d["Volume"].iloc[-50:].mean()),
        "high_52w": float(d["High"].iloc[-252:].max()),
        "ret_63": float(close / d["Close"].iloc[-63] - 1) if len(d) >= 63 else 0.0,
    }


def in_uptrend(st):
    trend = st["close"] > st["sma50"]
    if not np.isnan(st["sma200"]):
        trend = trend and st["sma50"] > st["sma200"]
    return trend


def watch_score(st, spy_ret_63=0.0):
    """0-100 score for an 'about to break out' setup, or None if it doesn't qualify."""
    if not in_uptrend(st) or not (0 <= st["dist"] <= WATCH_MAX_DIST) or st["range10"] > WATCH_MAX_RANGE:
        return None
    score = 30 * (1 - st["dist"] / WATCH_MAX_DIST)                       # close to the level
    score += 25 * max(0.0, 1 - st["range10"] / WATCH_MAX_RANGE)          # tight range
    score += 15 * min(1.0, max(0.0, (1 - st["atr_ratio"]) / 0.5))        # volatility contracting
    score += 10 * min(1.0, max(0.0, (1 - st["vol_ratio"]) / 0.5))        # volume drying up
    score += 10                                                           # uptrend (required)
    score += 10 if st["ret_63"] > spy_ret_63 else 0                       # beating SPY over 3 months
    return round(score)


def session_fraction(now_ct):
    """Share of the regular session (8:30-15:00 CT) that has passed, at least 5%."""
    mins = (now_ct.hour * 60 + now_ct.minute) - (8 * 60 + 30)
    return min(1.0, max(0.05, mins / 390))


def trigger(st, bar, now_ct):
    """Returns breakout details if today's bar has broken out, else None."""
    if bar is None or not in_uptrend(st):
        return None
    price, vol = float(bar["Close"]), float(bar["Volume"])
    above = (price - st["resistance"]) / st["resistance"] * 100
    if not (TRIGGER_MIN_BREAK <= above <= TRIGGER_MAX_EXT):
        return None
    rvol = vol / (st["avg_vol50"] * session_fraction(now_ct))
    if rvol < TRIGGER_MIN_RVOL:
        return None
    return {"price": price, "above": above, "rvol": rvol,
            "new_52w": price >= st["high_52w"]}


# ---------------------------------------------------------------- messages ---
def fmt_watch(today, picks, source, scanned):
    e = html.escape
    lines = [f"<b>🔭 Breakout WATCH — {today.strftime('%a %b %-d')}</b>",
             "<i>About to break out: tight, near the 20-day high, in an uptrend.</i>"]
    if not picks:
        lines.append("No setups passed the rules today.")
    for i, (s, sc, st) in enumerate(picks, 1):
        lines.append(f"{i}. <b>{e(s)}</b> ${st['close']:.2f} — breakout over <b>${st['resistance']:.2f}</b> "
                     f"({st['dist']:.1f}% away)\n    score {sc} · 10-day range {st['range10']:.1f}%"
                     + (" · near 52-wk high" if st["resistance"] >= st["high_52w"] * 0.98 else ""))
    lines.append(f"<i>{scanned} stocks scanned ({e(source)}). Not trade advice — paper/test first.</i>")
    return "\n\n".join(lines)


def fmt_trigger(now_ct, hits):
    e = html.escape
    lines = [f"<b>🚀 Breakout TRIGGERED — {now_ct.strftime('%-I:%M %p')} CT</b>"]
    for s, t, st, was_watched in hits:
        tags = []
        if was_watched:
            tags.append("was on WATCH ✅")
        if t["new_52w"]:
            tags.append("new 52-wk high")
        lines.append(f"• <b>{e(s)}</b> ${t['price']:.2f} — {t['above']:.1f}% over ${st['resistance']:.2f} · "
                     f"volume pace {t['rvol']:.1f}x" + (f"\n    {' · '.join(tags)}" if tags else ""))
    lines.append("<i>Prices from Yahoo, can lag a few minutes. Not trade advice.</i>")
    return "\n\n".join(lines)


# ---------------------------------------------------------------- telegram ---
def send(text):
    if os.getenv("DRY_RUN") == "1" or not os.getenv("TELEGRAM_BOT_TOKEN"):
        if os.getenv("DRY_RUN") != "1":
            print("Telegram secrets not set yet; printing instead of sending.")
        print("---- message not sent ----\n" + text)
        return
    from catalyst_alerts import find_chat_ids, _reason
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not chat:
        found = find_chat_ids(token)
        if len(found) != 1:
            try:
                me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30).json()
                up = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
                print(f"Token belongs to @{(me.get('result') or {}).get('username')}; "
                      f"getUpdates -> {up.status_code} {_reason(up) if not up.ok else str(len(up.json().get('result', []))) + ' updates'}")
            except Exception as ex:
                print(f"Telegram check failed: {ex}")
            raise SystemExit("BREAKOUT_TELEGRAM_CHAT_ID not set and couldn't tell which chat to use "
                             f"(chats found: {found or 'none - press Start in your new bot first'}).")
        chat = str(found[0][0])
        print(f"Using chat {chat}. Put this number in the BREAKOUT_TELEGRAM_CHAT_ID secret.")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True})
    if not r.ok:
        raise SystemExit(f"Telegram error {r.status_code}: {_reason(r)}")


# ---------------------------------------------------------------- state ------
def load_state(path, today):
    try:
        with open(path) as f:
            st = json.load(f)
        if st.get("date") == today.isoformat():
            return st
    except (OSError, ValueError):
        pass
    return {"date": today.isoformat(), "watch_sent": False, "watch": [], "triggered": []}


def save_state(path, st):
    if path:
        with open(path, "w") as f:
            json.dump(st, f)


# ---------------------------------------------------------------- main -------
def main(now=None):
    now = now or dt.datetime.now(CT)
    today, t = now.date(), now.time()
    force = (os.getenv("FORCE") or "").lower()
    if today.weekday() >= 5 and not force:
        print("Weekend; nothing to do.")
        return 0
    state_path = os.getenv("STATE_FILE", "breakout_state.json")
    state = load_state(state_path, today)

    do_watch = force in ("watch", "both") or (not force and not state["watch_sent"] and WATCH_FROM <= t < WATCH_UNTIL)
    do_trigger = force in ("trigger", "both") or (not force and OPEN <= t <= TRIGGER_UNTIL)
    if not (do_watch or do_trigger):
        print(f"{t.strftime('%H:%M')} CT: outside alert windows; nothing to do.")
        save_state(state_path, state)
        return 0

    syms, source = universe(os.environ["FMP_KEY"])
    bars = daily_bars(syms + ["SPY"])
    spy = bars.pop("SPY", None)
    spy_ret = 0.0
    if spy is not None:
        c = split_today(spy, today)[0]["Close"]
        spy_ret = float(c.iloc[-1] / c.iloc[-63] - 1)
    print(f"universe: {len(syms)} from {source}; bars for {len(bars)}")

    stats, today_bar = {}, {}
    for s, df in bars.items():
        done, bar = split_today(df, today)
        if len(done) < 60:
            continue
        try:
            stats[s] = setup_stats(done)
            today_bar[s] = bar
        except Exception as ex:
            print(f"{s}: {ex}", file=sys.stderr)

    if do_watch:
        scored = [(s, watch_score(st, spy_ret), st) for s, st in stats.items()]
        picks = sorted([x for x in scored if x[1] is not None], key=lambda x: -x[1])[:WATCH_TOP_N]
        print(f"watch setups passing: {sum(1 for x in scored if x[1] is not None)}")
        send(fmt_watch(today, picks, source, len(stats)))
        state["watch_sent"] = True
        state["watch"] = [s for s, _, _ in picks]

    if do_trigger:
        if not any(b is not None for b in today_bar.values()):
            print("No bars for today yet (pre-market or holiday); no trigger check.")
        else:
            hits = []
            for s, st in stats.items():
                if s in state["triggered"]:
                    continue
                tr = trigger(st, today_bar[s], now)
                if tr:
                    hits.append((s, tr, st, s in state["watch"]))
            hits.sort(key=lambda h: (not h[3], -h[1]["rvol"]))
            print(f"new triggers: {[h[0] for h in hits]}")
            if hits:
                send(fmt_trigger(now, hits[:15]))
                state["triggered"] += [h[0] for h in hits]

    save_state(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
