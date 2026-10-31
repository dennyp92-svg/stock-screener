"""Earnings & catalyst alerts -> Telegram, weekday mornings before the open.

Checks the stocks Denny's bots hold or are watching for upcoming earnings, plus
high-impact US economic events (Fed, CPI, jobs, PCE, ...), and sends one Telegram
message. Read-only: it reads Supabase and FMP and never touches the bots.

Stocks checked ("held + watched"):
  held    - picks bot open positions        (quant_bot_trades, status open)
          - auto-trader open positions      (auto_trade_state row 'paper')
          - options bot open spreads        (option_bot_log fills not yet closed)
  watched - latest day's picks              (quant_picks)

Env: FMP_KEY, SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
     DRY_RUN=1 prints the message instead of sending it.
     SCHEDULE (the cron that fired, from GitHub) - see should_run().
"""
from __future__ import annotations

import datetime as dt
import html
import os
import re
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

import requests

CT = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc
FMP = "https://financialmodelingprep.com/stable"
EARNINGS_DAYS = 7           # warn about earnings this many calendar days ahead
OPTIONS_MAX_DAYS = 60       # for option spreads, look ahead to expiry (capped)
ECON_TRADING_DAYS = 5       # high-impact events over the next week of trading days
AUTO_TRADER_STATE_ID = "paper"
# The two crons in the workflow: 12:30 UTC is 7:30 CT in summer (CDT),
# 13:30 UTC is 7:30 CT in winter (CST). Only the one matching today's clock sends.
CRON_FOR_UTC_OFFSET = {-5: "30 12 * * 1-5", -6: "30 13 * * 1-5"}


# ---------------------------------------------------------------- schedule ---
def should_run(schedule: str | None, now: dt.datetime | None = None) -> bool:
    """True for manual runs, or for the cron that is 7:30 CT under today's daylight-saving rule."""
    if not schedule:
        return True
    now = now or dt.datetime.now(UTC)
    offset_h = int(now.astimezone(CT).utcoffset().total_seconds() // 3600)
    return CRON_FOR_UTC_OFFSET.get(offset_h) == schedule


# ---------------------------------------------------------------- symbols ----
def _rows(sb, table, **eq):
    q = sb.table(table).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.limit(5000).execute().data or []


def options_open(fills, closed):
    """Open option spreads: fills not matched by a later 'closed' row for the same ticker.

    Returns {ticker: expiration-date-or-None}.
    """
    open_by_ticker = defaultdict(list)
    for f in sorted(fills, key=lambda r: r.get("ts") or ""):
        p = f.get("payload") or {}
        if p.get("mode", "paper") == "paper" and p.get("ticker"):
            open_by_ticker[p["ticker"]].append(p.get("expiration"))
    for c in closed:
        p = c.get("payload") or {}
        if p.get("mode", "paper") == "paper" and open_by_ticker.get(p.get("ticker")):
            open_by_ticker[p["ticker"]].pop(0)
    return {t: (max((e for e in exps if e), default=None)) for t, exps in open_by_ticker.items() if exps}


def gather_symbols(sb):
    """Returns (held, watched, options_expiry, counts).

    held:     {symbol: set of bot names}
    watched:  set of symbols from the latest day's picks (minus anything held)
    options_expiry: {symbol: 'YYYY-MM-DD'} for open option spreads
    counts:   rows read per source, so an empty result can be told apart from no access
    """
    held, counts = defaultdict(set), {}

    picks_open = _rows(sb, "quant_bot_trades", status="open")
    counts["picks bot open"] = len(picks_open)
    for r in picks_open:
        held[r["symbol"]].add("picks bot")

    at = _rows(sb, "auto_trade_state", id=AUTO_TRADER_STATE_ID)
    positions = ((at[0].get("state") or {}).get("positions") or {}) if at else {}
    counts["auto-trader open"] = len(positions)
    for sym in positions:
        held[sym].add("auto-trader")

    opt = options_open(_rows(sb, "option_bot_log", kind="fill"),
                       _rows(sb, "option_bot_log", kind="closed"))
    counts["options bot open"] = len(opt)
    for sym in opt:
        held[sym].add("options bot")

    latest = (sb.table("quant_picks").select("pick_date").order("pick_date", desc=True)
              .limit(1).execute().data or [])
    watched, day = set(), None
    if latest:
        day = latest[0]["pick_date"]
        watched = {r["symbol"] for r in _rows(sb, "quant_picks", pick_date=day)}
    counts["latest picks"] = len(watched)
    counts["picks_date"] = day
    return dict(held), watched - set(held), opt, counts


# ---------------------------------------------------------------- data -------
def fmp_get(path, key, **params):
    r = requests.get(f"{FMP}/{path}", params={**params, "apikey": key}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and ("Error Message" in data or "error" in data):
        raise RuntimeError(str(data)[:200])
    return data if isinstance(data, list) else []


def earnings_fmp(symbols, start, end, key):
    """{symbol: 'YYYY-MM-DD'} for the first earnings date in [start, end], in 14-day requests."""
    found, d = {}, start
    while d <= end:
        stop = min(d + dt.timedelta(days=13), end)
        for e in fmp_get("earnings-calendar", key, **{"from": d.isoformat(), "to": stop.isoformat()}):
            s = e.get("symbol")
            if s in symbols and e.get("date") and (s not in found or e["date"] < found[s]):
                found[s] = e["date"]
        d = stop + dt.timedelta(days=1)
    return found


def earnings_yahoo(symbols, start, end):
    """Fallback if FMP's calendar isn't available: Yahoo Finance, one symbol at a time."""
    import yfinance as yf
    found = {}
    for s in symbols:
        try:
            cal = yf.Ticker(s).calendar or {}
            dates = cal.get("Earnings Date") or []
            dates = [x for x in dates if start <= x <= end]
            if dates:
                found[s] = min(dates).isoformat()
        except Exception as e:
            print(f"yahoo earnings lookup failed for {s}: {e}", file=sys.stderr)
    return found


def econ_events(start, end, key):
    """High-impact US events between start and end (inclusive), as (datetime CT, name)."""
    out = []
    for e in fmp_get("economic-calendar", key, **{"from": start.isoformat(), "to": end.isoformat()}):
        if e.get("country") != "US" or e.get("impact") != "High" or not e.get("date"):
            continue
        when = dt.datetime.fromisoformat(e["date"]).replace(tzinfo=UTC).astimezone(CT)
        if start <= when.date() <= end:
            out.append((when, e.get("event", "").strip()))
    return group_events(out)


def _base(name):
    """'Durable Goods Orders Ex Transp MoM (Sep)' -> 'Durable Goods Orders'."""
    return re.split(r" (?:MoM|YoY|QoQ|Ex |\(|4-Week)", name)[0].strip() or name


def group_events(events):
    """Merges same-time variants of one report into a single line, with a count."""
    groups = {}
    for when, name in sorted(events):
        key = (when, _base(name))
        groups[key] = groups.get(key, 0) + 1
    return [(when, base if n == 1 else f"{base} ({n} reports)") for (when, base), n in groups.items()]


def trading_days_ahead(d, n):
    for _ in range(n):
        d = next_trading_day(d)
    return d


def next_trading_day(d):
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


# ---------------------------------------------------------------- message ----
def _when(date_str, today):
    d = dt.date.fromisoformat(date_str)
    if d == today:
        return "TODAY"
    if d == next_trading_day(today):
        return "next trading day" if d - today > dt.timedelta(days=1) else "TOMORROW"
    return d.strftime("%a %b %-d")


def build_message(today, held, watched, options_expiry, earnings, events, picks_date=None):
    """Returns the Telegram text (HTML), or None when there's nothing to warn about."""
    e = html.escape
    lines = [f"<b>📅 Catalyst check — {today.strftime('%a %b %-d')}</b>"]

    held_hits = sorted((d, s) for s, d in earnings.items() if s in held)
    if held_hits:
        lines.append("<b>⚠️ Earnings — stocks your bots HOLD</b>")
        for d, s in held_hits:
            note = ""
            exp = options_expiry.get(s)
            if exp and d <= exp:
                note = f" (before option expiry {exp})"
            lines.append(f"• <b>{e(s)}</b> {_when(d, today)}{note} — {e(', '.join(sorted(held[s])))}")

    watch_hits = sorted((d, s) for s, d in earnings.items() if s in watched)
    if watch_hits:
        if picks_date and str(picks_date) != today.isoformat():
            label = f"the latest picks ({dt.date.fromisoformat(str(picks_date)[:10]).strftime('%a %b %-d')})"
        else:
            label = "today's picks"
        lines.append(f"<b>👀 Earnings — on {label}</b>")
        lines += [f"• {e(s)} {_when(d, today)}" for d, s in watch_hits]

    if events:
        lines.append("<b>🏛 High-impact economic events</b>")
        by_time = {}
        for when, name in events:
            by_time.setdefault(when, []).append(name)
        for when, names in by_time.items():
            day = "Today" if when.date() == today else when.strftime("%a %b %-d")
            lines.append(f"• {day} {when.strftime('%-I:%M %p')} CT — {e(', '.join(names))}")

    if len(lines) == 1:
        return None
    lines.append("<i>Paper trading. Dates from FMP; confirm before trading around them.</i>")
    # Blank line between every line so the alert is easier to read on a phone.
    return "\n\n".join(lines)


def _plain(text):
    return html.unescape(re.sub(r"</?[bi]>", "", text))


def send_telegram(text, token, chat_id):
    """Sends the message. On failure, prints Telegram's own reason (never the token)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": str(chat_id).strip(), "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    r = requests.post(url, json=dict(payload), timeout=30)
    reason = _reason(r)
    if r.status_code == 400 and "parse" in reason.lower():
        print(f"Telegram rejected the formatting ({reason}); resending as plain text.")
        payload.pop("parse_mode")
        payload["text"] = _plain(text)
        r = requests.post(url, json=dict(payload), timeout=30)
        reason = _reason(r)
    if not r.ok and r.status_code == 400 and "chat" in reason.lower():
        found = find_chat_ids(token)
        print(f"Saved chat ID didn't work ({reason}). Chats that have messaged this bot: "
              + (", ".join(f"{cid} ({name})" for cid, name in found) or "none"))
        if len(found) == 1:
            payload["chat_id"] = str(found[0][0])
            r = requests.post(url, json=dict(payload), timeout=30)
            reason = _reason(r)
            if r.ok:
                print(f"Sent to chat {found[0][0]}. Put this number in the "
                      f"CATALYST_TELEGRAM_CHAT_ID secret so it's used from now on.")
                return
        elif not found:
            reason += " (no chats found: open your bot in Telegram, press Start or send it any message, then run again)"
        else:
            reason += " (several chats found; put the right number in CATALYST_TELEGRAM_CHAT_ID)"
    if not r.ok:
        hint = {400: "check CATALYST_TELEGRAM_CHAT_ID, and send your bot a message first",
                401: "check CATALYST_TELEGRAM_BOT_TOKEN",
                403: "the bot was blocked or can't post in that chat",
                404: "check CATALYST_TELEGRAM_BOT_TOKEN"}.get(r.status_code, "")
        raise SystemExit(f"Telegram error {r.status_code}: {reason}" + (f" -> {hint}" if hint else ""))


def find_chat_ids(token):
    """[(chat_id, name)] of private chats that recently messaged the bot (Telegram keeps ~24h)."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
        updates = r.json().get("result", []) if r.ok else []
    except Exception:
        return []
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            chats[chat["id"]] = chat.get("first_name") or chat.get("username") or ""
    return list(chats.items())


def _reason(r):
    try:
        return r.json().get("description", "") or ""
    except ValueError:
        return r.text[:200]


# ---------------------------------------------------------------- main -------
def main():
    if not should_run(os.getenv("SCHEDULE")):
        print("Not the 7:30 CT run for today's daylight-saving offset; skipping.")
        return 0

    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    key = os.environ["FMP_KEY"]
    today = dt.datetime.now(CT).date()

    held, watched, options_expiry, counts = gather_symbols(sb)
    print("rows read:", counts)
    if not any(counts.values()):
        print("WARNING: every source came back empty - check the Supabase key can read these tables.")

    # Look further ahead for option spreads, up to their expiry.
    last_exp = max((dt.date.fromisoformat(x) for x in options_expiry.values() if x), default=today)
    end = min(max(today + dt.timedelta(days=EARNINGS_DAYS), last_exp),
              today + dt.timedelta(days=OPTIONS_MAX_DAYS))
    symbols = set(held) | watched
    try:
        earnings = earnings_fmp(symbols, today, end, key)
        source = "FMP"
    except Exception as ex:
        print(f"FMP earnings calendar failed ({ex}); falling back to Yahoo.", file=sys.stderr)
        earnings, source = earnings_yahoo(symbols, today, end), "Yahoo"
    # Stock earnings only count inside the normal window; option spreads use their expiry.
    window_end = (today + dt.timedelta(days=EARNINGS_DAYS)).isoformat()
    earnings = {s: d for s, d in earnings.items()
                if d <= window_end or (s in options_expiry and options_expiry[s] and d <= options_expiry[s])}
    print(f"earnings source: {source}; symbols checked: {len(symbols)}; hits: {earnings}")

    try:
        events = econ_events(today, trading_days_ahead(today, ECON_TRADING_DAYS - 1), key)
    except Exception as ex:
        print(f"FMP economic calendar failed: {ex}", file=sys.stderr)
        events = []

    text = build_message(today, held, watched, options_expiry, earnings, events, counts.get("picks_date"))
    if text and source != "FMP":
        text = text.replace("Dates from FMP", f"Dates from {source}")
    if not text:
        print("Nothing coming up; no message sent.")
        return 0
    if os.getenv("DRY_RUN") == "1" or not os.getenv("TELEGRAM_BOT_TOKEN"):
        if os.getenv("DRY_RUN") != "1":
            print("Telegram secrets not set yet; printing instead of sending.")
        print("---- message not sent ----\n" + text)
        return 0
    send_telegram(text, os.environ["TELEGRAM_BOT_TOKEN"], os.getenv("TELEGRAM_CHAT_ID") or "0")
    print("Sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
