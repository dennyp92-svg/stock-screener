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

Buy/sell help (exits picked by backtest_breakout.py, best of the variants tested):
  each TRIGGERED stock shows a stop (10-day low, at most 10% below), a target
  (3x the risk above entry) and a sell-by date (20 trading days). Text the bot
  "buy NVDA" (or "buy NVDA 182.50" with your fill) and it watches the stock and
  tells you when the stop, target or sell-by date is hit. "sell NVDA" stops
  tracking, "list" shows what's tracked. Commands are read on each run (every 30 min).

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

# Exits (best in the 5-year backtest)
STOP_MAX_PCT = 10.0      # stop = 10-day low, but never more than 10% below entry
TARGET_R = 3.0           # target = entry + 3x the risk
HOLD_DAYS = 20           # sell after 20 trading days if neither is hit

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
        "low10": float(last10["Low"].min()),
    }


def plan(entry, low10, day):
    """Stop, target and sell-by date for a buy at `entry` on `day`."""
    stop = max(low10, entry * (1 - STOP_MAX_PCT / 100))
    if stop >= entry:
        stop = entry * (1 - STOP_MAX_PCT / 100)
    target = entry + TARGET_R * (entry - stop)
    sell_by = np.busday_offset(np.datetime64(day), HOLD_DAYS, roll="forward").astype(dt.date)
    return {"entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
            "sell_by": sell_by.isoformat(), "bought": day.isoformat()}


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
        pl = plan(t["price"], st["low10"], now_ct.date())
        lines.append(f"• <b>{e(s)}</b> ${t['price']:.2f} — {t['above']:.1f}% over ${st['resistance']:.2f} · "
                     f"volume pace {t['rvol']:.1f}x" + (f"\n    {' · '.join(tags)}" if tags else "")
                     + f"\n    🟢 Buy ~${pl['entry']:.2f} · 🛑 stop ${pl['stop']:.2f} · 🎯 target ${pl['target']:.2f}"
                     f" · ⏰ sell by {_d(pl['sell_by'])}")
    lines.append("<i>Bought one? Text me <b>buy TICKER</b> (add your price, e.g. buy NVDA 182.50) "
                 "and I'll tell you when to sell. Prices from Yahoo, can lag a few minutes. Not trade advice.</i>")
    return "\n\n".join(lines)


def _d(iso):
    return dt.date.fromisoformat(iso).strftime("%a %b %-d")


def fmt_position(sym, p):
    return (f"<b>{html.escape(sym)}</b> bought ${p['entry']:.2f} · 🛑 ${p['stop']:.2f} · "
            f"🎯 ${p['target']:.2f} · ⏰ {_d(p['sell_by'])}")


# ---------------------------------------------------------------- positions --
def parse_command(text):
    """('buy', 'NVDA', 182.5) / ('buy', 'NVDA', None) / ('sell', 'NVDA', None) / ('list', None, None) / None."""
    parts = (text or "").strip().replace("$", "").split()
    if not parts:
        return None
    cmd = parts[0].lower().lstrip("/")
    if cmd in ("list", "positions", "status"):
        return ("list", None, None)
    if cmd in ("help", "start"):
        return ("help", None, None)
    if cmd in ("buy", "bought", "sell", "sold") and len(parts) >= 2 and clean_symbol(parts[1].upper()):
        price = None
        if len(parts) >= 3:
            try:
                price = float(parts[2])
            except ValueError:
                pass
        return ("buy" if cmd in ("buy", "bought") else "sell", parts[1].upper(), price)
    return None


def check_exit(p, bar, today):
    """Returns the reason to sell now, or None. `bar` is today's bar (or None)."""
    if bar is not None:
        if float(bar["Low"]) <= p["stop"]:
            return f"🛑 STOP hit (${p['stop']:.2f}) — sell"
        if float(bar["High"]) >= p["target"]:
            return f"🎯 TARGET hit (${p['target']:.2f}) — sell"
    if today.isoformat() >= p["sell_by"]:
        return "⏰ 20 trading days are up — sell"
    return None


def read_commands(state, token, chat):
    """New messages from your chat since the last run, as parsed commands."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30,
                         params={"offset": state.get("tg_offset", 0), "timeout": 0})
        updates = r.json().get("result", []) if r.ok else []
    except Exception as ex:
        print(f"Couldn't read Telegram commands: {ex}", file=sys.stderr)
        return []
    cmds = []
    for u in updates:
        state["tg_offset"] = max(state.get("tg_offset", 0), u["update_id"] + 1)
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat):
            continue
        c = parse_command(msg.get("text"))
        if c:
            cmds.append(c)
    return cmds


def handle_commands(state, cmds, today, now_ct):
    """Applies buy/sell/list commands; returns reply text or None."""
    if not cmds:
        return None
    pos = state.setdefault("positions", {})
    replies = []
    buys = [c for c in cmds if c[0] == "buy"]
    bars = daily_bars([c[1] for c in buys], retry=False) if buys else {}
    for cmd, sym, price in cmds:
        if cmd == "buy":
            df = bars.get(sym)
            if df is None:
                replies.append(f"❓ Couldn't find price data for {html.escape(sym)}.")
                continue
            done, bar = split_today(df, today)
            entry = price or float((bar if bar is not None else done.iloc[-1])["Close"])
            pos[sym] = plan(entry, float(done["Low"].iloc[-10:].min()), today)
            replies.append("✅ Tracking " + fmt_position(sym, pos[sym])
                           + ("" if price else "\n    <i>(used the current price; send buy "
                              f"{html.escape(sym)} PRICE to set your fill)</i>"))
        elif cmd == "sell":
            replies.append(f"👋 Stopped tracking {html.escape(sym)}." if pos.pop(sym, None)
                           else f"{html.escape(sym)} wasn't being tracked.")
        elif cmd == "list":
            replies.append("<b>Tracking:</b>\n" + "\n".join("• " + fmt_position(s, p) for s, p in pos.items())
                           if pos else "Not tracking anything. Text <b>buy TICKER</b> after you buy.")
        else:
            replies.append("Commands: <b>buy TICKER</b> (or buy TICKER PRICE), <b>sell TICKER</b>, <b>list</b>. "
                           "I check every 30 min while the market's open.")
    return "\n\n".join(replies)


def exit_alerts(state, today, now_ct):
    """Checks tracked stocks; returns alert text or None. Alerted stocks stop being tracked."""
    pos = state.get("positions") or {}
    if not pos:
        return None
    bars = daily_bars(list(pos), retry=False)
    lines = []
    for sym, p in list(pos.items()):
        df = bars.get(sym)
        bar = split_today(df, today)[1] if df is not None else None
        why = check_exit(p, bar, today)
        if why:
            last = f" (now ${float(bar['Close']):.2f})" if bar is not None else ""
            chg = (float(bar["Close"]) / p["entry"] - 1) * 100 if bar is not None else None
            lines.append(f"<b>{html.escape(sym)}</b>: {why}{last}"
                         + (f" · {chg:+.1f}% from ${p['entry']:.2f}" if chg is not None else ""))
            pos.pop(sym)
    if not lines:
        return None
    return (f"<b>🔔 SELL alert — {now_ct.strftime('%-I:%M %p')} CT</b>\n\n" + "\n\n".join(lines)
            + "\n\n<i>Stopped tracking these. Not trade advice.</i>")


# ---------------------------------------------------------------- telegram ---
def resolve_chat(token):
    chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if chat:
        return chat
    from catalyst_alerts import find_chat_ids
    found = find_chat_ids(token)
    return str(found[0][0]) if len(found) == 1 else None


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
    """Daily fields reset each day; tracked positions and the Telegram offset carry over."""
    st = {}
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        pass
    keep = {"positions": st.get("positions", {}), "tg_offset": st.get("tg_offset", 0)}
    if st.get("date") == today.isoformat():
        return {**st, **keep}
    return {"date": today.isoformat(), "watch_sent": False, "watch": [], "triggered": [], **keep}


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

    dry = os.getenv("DRY_RUN") == "1"
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token and not dry:
        chat = resolve_chat(token)
        if chat:
            reply = handle_commands(state, read_commands(state, token, chat), today, now)
            if reply:
                send(reply)
    if state.get("positions") and OPEN <= t <= dt.time(15, 15):
        alert = exit_alerts(state, today, now)
        if alert:
            send(alert)

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
