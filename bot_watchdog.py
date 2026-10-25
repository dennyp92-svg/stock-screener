"""Bot health watchdog -> Telegram. Runs on GitHub Actions, independent of the iMac and MacBook.

Three checks a weekday (Central time):
  09:15  morning  - bots started, today's picks written, no day trades left open overnight,
                    catalyst alert ran
  12:15  midday   - intraday + options bots still logging, auto-trader cycling
  15:25  close    - everything above, today's day trades actually closed, picks tracker ran,
                    plus a daily "all clear" so silence never means "watchdog broken"

Morning and midday only message when something is wrong. Read-only: it reads Supabase
and GitHub's run history and never touches a bot.

Env: SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GITHUB_TOKEN,
     GITHUB_REPOSITORY, SCHEDULE (the cron that fired), SLOT (manual: morning|midday|close),
     DRY_RUN=1 prints instead of sending.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import re
import sys
from zoneinfo import ZoneInfo

import requests

CT = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc

# cron -> (UTC offset of Chicago it is meant for, slot). Two crons per slot cover CDT and CST.
CRONS = {
    "15 14 * * 1-5": (-5, "morning"), "15 15 * * 1-5": (-6, "morning"),
    "15 17 * * 1-5": (-5, "midday"),  "15 18 * * 1-5": (-6, "midday"),
    "25 20 * * 1-5": (-5, "close"),   "25 21 * * 1-5": (-6, "close"),
}

INTRADAY_QUIET_MIN = 60     # intraday bot normally logs at least every ~35 min while trading
OPTIONS_QUIET_MIN = 30      # options bot normally logs every ~5-8 min
INTRADAY_WINDOW = ("08:40", "14:20")   # when the intraday bot should be active
OPTIONS_WINDOW = ("08:45", "14:45")


def slot_for(schedule: str | None, now: dt.datetime, manual: str | None = None):
    """Which check to run, or None if this cron isn't the one for today's daylight-saving offset."""
    if not schedule:
        return manual or "close"
    offset = int(now.astimezone(CT).utcoffset().total_seconds() // 3600)
    want = CRONS.get(schedule)
    return want[1] if want and want[0] == offset else None


# ---------------------------------------------------------------- checks -----
# Each check takes plain data plus the current Central time and returns a list of problems.

def _t(hhmm):
    return dt.time.fromisoformat(hhmm)


def _ct(ts):
    if not ts:
        return None
    return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(CT)


def market_day(now_ct, next_session: str | None):
    """False on weekends, or when the intraday bot's own calendar says the next session is later
    than today (exchange holidays). next_session comes from its 'Next session YYYY-MM-DD' log."""
    if now_ct.weekday() >= 5:
        return False
    if next_session and next_session > now_ct.date().isoformat():
        return False
    return True


def check_heartbeat(name, last_ts, now_ct, window, quiet_min):
    """A bot that should be running is silent for too long."""
    start, end = _t(window[0]), _t(window[1])
    if not (start <= now_ct.time()):
        return []
    last = _ct(last_ts)
    if last is None or last.date() != now_ct.date():
        return [f"{name}: no log entries today (should be running since {window[0]} CT)"]
    check_at = min(now_ct, now_ct.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0))
    quiet = (check_at - last).total_seconds() / 60
    if quiet > quiet_min:
        return [f"{name}: silent for {quiet:.0f} min (last log {last.strftime('%-I:%M %p')} CT)"]
    return []


def check_intraday_errors(events, now_ct):
    """ERROR-level events the intraday bot logged today."""
    errs = [e for e in events if (e.get("payload") or {}).get("level") == "ERROR"
            and (_ct(e.get("ts")) or now_ct).date() == now_ct.date()]
    if not errs:
        return []
    last = (errs[-1].get("payload") or {}).get("message", "")[:120]
    return [f"Intraday bot: {len(errs)} error(s) today, latest: {last}"]


def check_intraday_day_summary(events, now_ct):
    """After the close the intraday bot writes a day summary; missing means it didn't finish the day."""
    if not any((e.get("payload") or {}).get("kind") == "day_summary"
               and (_ct(e.get("ts")) or now_ct).date() == now_ct.date() for e in events):
        return ["Intraday bot: no end-of-day summary today (it may have stopped before the close)"]
    return []


def check_picks_written(picks, now_ct):
    kinds = {p.get("kind") for p in picks if p.get("pick_date") == now_ct.date().isoformat()}
    missing = [k for k in ("swing", "day") if k not in kinds]
    return [f"Picks system: no {k} picks recorded today (8:05/8:45 jobs on the MacBook Air)" for k in missing]


def check_day_trades(trades, now_ct, after_close: bool):
    """Day trades must not stay open overnight; after the close job none from today may be open."""
    out = []
    today = now_ct.date()
    for t in trades:
        if t.get("kind") != "day" or t.get("status") != "open":
            continue
        opened = _ct(t.get("submitted_at") or t.get("entry_time"))
        if opened is None:
            continue
        if opened.date() < today:
            out.append(f"Picks bot: day trade {t.get('symbol')} from {opened.strftime('%b %-d')} is still open "
                       "(should have closed at 2:55 PM that day)")
        elif after_close:
            out.append(f"Picks bot: today's day trade {t.get('symbol')} still open after the 2:55 PM close job")
    return out


def check_tracker_ran(daily_rows, now_ct):
    if not any(r.get("day") == now_ct.date().isoformat() for r in daily_rows):
        return ["Picks tracker: no daily report saved today (3:10 PM job on the MacBook Air)"]
    return []


def check_autotrader(state, now_ct):
    day = (state or {}).get("day")
    if day != now_ct.date().isoformat():
        return [f"Auto-trader: hasn't run today (last cycle day {day or 'unknown'}; GitHub schedule)"]
    return []


def check_workflow(name, runs, now_ct):
    """The latest scheduled run of a GitHub workflow today failed."""
    today = [r for r in runs if (_ct(r.get("created_at")) or now_ct).date() == now_ct.date()]
    if today and today[0].get("conclusion") == "failure":
        return [f"{name}: today's GitHub run failed"]
    return []


# ---------------------------------------------------------------- data -------
class Supa:
    def __init__(self, url, key):
        self.url, self.h = url.rstrip("/") + "/rest/v1/", {"apikey": key, "Authorization": f"Bearer {key}"}

    def get(self, table, **params):
        r = requests.get(self.url + table, headers=self.h, params=params, timeout=30)
        r.raise_for_status()
        return r.json()


def gather(sb: Supa, now_ct):
    start_utc = now_ct.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()
    d = {}
    d["intraday_last"] = (sb.get("bot_log", select="ts", order="ts.desc", limit=1) or [{}])[0].get("ts")
    d["options_last"] = (sb.get("option_bot_log", select="ts", order="ts.desc", limit=1) or [{}])[0].get("ts")
    d["intraday_events"] = sb.get("bot_log", select="ts,payload", kind="eq.event", ts=f"gte.{start_utc}",
                                  order="ts.asc", limit=2000)
    idle = sb.get("bot_log", select="payload", kind="eq.event", order="ts.desc", limit=50)
    d["next_session"] = None
    for e in idle:
        m = re.search(r"Next session (\d{4}-\d{2}-\d{2})", (e.get("payload") or {}).get("message", ""))
        if m:
            d["next_session"] = m.group(1)
            break
    d["picks"] = sb.get("quant_picks", select="pick_date,kind", pick_date=f"eq.{now_ct.date().isoformat()}")
    d["bot_trades"] = sb.get("quant_bot_trades", select="symbol,kind,status,submitted_at,entry_time",
                             status="eq.open")
    d["daily"] = sb.get("quant_account_daily", select="day", day=f"eq.{now_ct.date().isoformat()}")
    at = sb.get("auto_trade_state", select="state", id="eq.paper")
    d["autotrader_state"] = at[0].get("state") if at else None
    return d


def workflow_runs(repo, token, workflow):
    if not (repo and token):
        return []
    r = requests.get(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs",
                     headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                     params={"per_page": 5, "event": "schedule"}, timeout=30)
    return r.json().get("workflow_runs", []) if r.ok else []


def run_checks(slot, d, now_ct, catalyst_runs=()):
    problems = []
    if slot in ("morning", "midday", "close"):
        problems += check_heartbeat("Intraday bot (iMac)", d["intraday_last"], now_ct, INTRADAY_WINDOW,
                                    INTRADAY_QUIET_MIN)
        problems += check_heartbeat("Options bot (iMac)", d["options_last"], now_ct, OPTIONS_WINDOW,
                                    OPTIONS_QUIET_MIN)
        problems += check_intraday_errors(d["intraday_events"], now_ct)
        problems += check_picks_written(d["picks"], now_ct)
        problems += check_day_trades(d["bot_trades"], now_ct, after_close=(slot == "close"))
    if slot == "morning":
        problems += check_workflow("Catalyst alert", list(catalyst_runs), now_ct)
    if slot in ("midday", "close"):
        problems += check_autotrader(d["autotrader_state"], now_ct)
    if slot == "close":
        problems += check_intraday_day_summary(d["intraday_events"], now_ct)
        problems += check_tracker_ran(d["daily"], now_ct)
    return problems


def build_message(slot, problems, now_ct):
    """Problems -> alert text. With no problems only the close check sends (a daily all-clear)."""
    when = now_ct.strftime("%a %b %-d, %-I:%M %p CT")
    if problems:
        lines = [f"<b>🚨 Bot watchdog — {len(problems)} problem(s)</b> ({when})"]
        lines += [f"• {html.escape(p, quote=False)}" for p in problems]
        lines.append("\n<i>Paper trading. Check the bot named above.</i>")
        return "\n".join(lines)
    if slot == "close":
        return f"<b>✅ Bot watchdog — all checks passed</b> ({when})"
    return None


# ---------------------------------------------------------------- main -------
def main():
    now = dt.datetime.now(UTC)
    now_ct = now.astimezone(CT)
    slot = slot_for(os.getenv("SCHEDULE"), now, os.getenv("SLOT"))
    if not slot:
        print("Not this run's daylight-saving slot; skipping.")
        return 0
    sb = Supa(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    try:
        d = gather(sb, now_ct)
    except Exception as e:
        problems = [f"Watchdog couldn't read Supabase: {type(e).__name__}: {str(e)[:150]}"]
        d = None
    if d is not None:
        if not market_day(now_ct, d["next_session"]) and not os.getenv("SLOT"):
            print(f"Market closed today (next session {d['next_session']}); nothing to check.")
            return 0
        runs = workflow_runs(os.getenv("GITHUB_REPOSITORY"), os.getenv("GITHUB_TOKEN"), "catalyst_alerts.yml")
        problems = run_checks(slot, d, now_ct, runs)
    print(f"slot={slot} problems={problems}")
    text = build_message(slot, problems, now_ct)
    if not text:
        print("All clear; nothing sent.")
        return 0
    if os.getenv("DRY_RUN") == "1" or not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("---- message not sent ----\n" + text)
        return 0
    from catalyst_alerts import send_telegram
    send_telegram(text, os.environ["TELEGRAM_BOT_TOKEN"], os.getenv("TELEGRAM_CHAT_ID") or "0")
    print("Sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
