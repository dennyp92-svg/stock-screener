"""Bot Performance tab for Stock Scanner Pro.

Compares Denny's paper-trading bots side by side. Read-only: it only reads the
Supabase tables the bots already write to and never touches the bots.

  Intraday bot  (iMac)        -> bot_log         rows where kind = 'trade'
  Options bot   (iMac)        -> option_bot_log  rows where kind = 'closed'
  Picks bot     (MacBook Air) -> quant_bot_trades (open + closed)
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

CT = ZoneInfo("America/Chicago")
MIN_TRADES = 30          # below this, results are flagged as too early to judge
BOTS = ["Intraday bot", "Options bot", "Picks bot"]

COLUMNS = ["bot", "symbol", "side", "strategy", "entry_time", "exit_time",
           "pnl", "r_multiple", "exit_reason"]


# ---------------------------------------------------------------- loading ----
def _fetch(sb, table, kind=None, page=1000):
    """Reads every row of a table (optionally one log kind), 1000 at a time."""
    out, start = [], 0
    while True:
        q = sb.table(table).select("*")
        if kind:
            q = q.eq("kind", kind)
        rows = q.range(start, start + page - 1).execute().data or []
        out.extend(rows)
        if len(rows) < page:
            return out
        start += page


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def intraday_frame(rows):
    out = []
    for r in rows:
        p = r.get("payload") or {}
        out.append({
            "bot": "Intraday bot",
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "strategy": p.get("strategy"),
            "entry_time": p.get("entry_ts"),
            "exit_time": p.get("exit_ts") or r.get("ts"),
            "pnl": _num(p.get("pnl_net")),
            "r_multiple": _num(p.get("r_multiple")),
            "exit_reason": p.get("exit_reason"),
        })
    return pd.DataFrame(out, columns=COLUMNS)


def options_frame(rows):
    out = []
    for r in rows:
        p = r.get("payload") or {}
        if p.get("mode") not in (None, "paper"):      # skip dry runs
            continue
        out.append({
            "bot": "Options bot",
            "symbol": p.get("ticker"),
            "side": p.get("spread"),
            "strategy": p.get("strategy"),
            "entry_time": p.get("held_since"),
            "exit_time": r.get("ts"),
            "pnl": _num(p.get("pnl")),
            "r_multiple": None,
            "exit_reason": p.get("reason"),
        })
    return pd.DataFrame(out, columns=COLUMNS)


def picks_frames(rows):
    """Returns (closed trades, open positions) for the picks bot."""
    closed, opened = [], []
    for r in rows:
        rec = {
            "bot": "Picks bot",
            "symbol": r.get("symbol"),
            "side": r.get("direction"),
            "strategy": r.get("kind"),
            "entry_time": r.get("entry_time") or r.get("submitted_at"),
            "exit_time": r.get("exit_time"),
            "pnl": _num(r.get("pnl")),
            "r_multiple": _num(r.get("r_multiple")),
            "exit_reason": r.get("exit_reason"),
        }
        (closed if r.get("status") == "closed" else opened).append(rec)
    return pd.DataFrame(closed, columns=COLUMNS), pd.DataFrame(opened, columns=COLUMNS)


def _times(df):
    df = df.copy()
    for c in ("entry_time", "exit_time"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce").dt.tz_convert(CT)
    return df


# ---------------------------------------------------------------- math -------
def max_drawdown(pnls):
    """Largest drop from a running peak of cumulative P/L (a negative number, or 0)."""
    peak, cum, worst = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return worst


def summarize(closed: pd.DataFrame, opened: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row of stats per bot. Every bot appears, even with no trades yet."""
    rows = []
    for bot in BOTS:
        t = closed[closed["bot"] == bot].dropna(subset=["pnl"]).sort_values("exit_time")
        o = opened[opened["bot"] == bot] if opened is not None and len(opened) else closed.iloc[0:0]
        n = len(t)
        wins, losses = t[t["pnl"] > 0]["pnl"], t[t["pnl"] < 0]["pnl"]
        gross_loss = -losses.sum()
        r = t["r_multiple"].dropna()
        rows.append({
            "Bot": bot,
            "Closed trades": n,
            "Win rate %": round(len(wins) / n * 100, 1) if n else None,
            "Total P/L $": round(t["pnl"].sum(), 2) if n else 0.0,
            "Avg win $": round(wins.mean(), 2) if len(wins) else None,
            "Avg loss $": round(losses.mean(), 2) if len(losses) else None,
            "Profit factor": round(wins.sum() / gross_loss, 2) if gross_loss > 0 else None,
            "Avg R": round(r.mean(), 2) if len(r) else None,
            "Biggest loss $": round(t["pnl"].min(), 2) if n else None,
            "Max drawdown $": round(max_drawdown(t["pnl"].tolist()), 2) if n else None,
            "Open now": len(o),
            "Open P/L $": round(o["pnl"].fillna(0).sum(), 2) if len(o) else None,
            "Enough data?": "yes" if n >= MIN_TRADES else f"no ({n}/{MIN_TRADES})",
        })
    return pd.DataFrame(rows)


def daily_cumulative(closed: pd.DataFrame) -> pd.DataFrame:
    """Cumulative closed-trade P/L per bot by exit day (Central time)."""
    t = closed.dropna(subset=["pnl", "exit_time"]).copy()
    if t.empty:
        return pd.DataFrame()
    t["day"] = t["exit_time"].dt.date
    daily = t.pivot_table(index="day", columns="bot", values="pnl", aggfunc="sum").fillna(0)
    return daily.sort_index().cumsum()


def display_summary(summary: pd.DataFrame) -> pd.DataFrame:
    """Bots as columns, every cell as tidy text ("—" when there's nothing to show yet)."""
    def fmt(col, v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        if col.endswith("$"):
            return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"
        if col.endswith("%"):
            return f"{v:.1f}%"
        return str(v)
    out = summary.copy()
    for col in out.columns:
        if col != "Bot":
            out[col] = [fmt(col, v) for v in out[col]]
    out.columns = [c.replace(" $", "").replace(" %", "") for c in out.columns]
    return out.set_index("Bot").T


# ---------------------------------------------------------------- page -------
@st.cache_data(ttl=300, show_spinner=False)
def _load(_sb):
    intraday = intraday_frame(_fetch(_sb, "bot_log", kind="trade"))
    options = options_frame(_fetch(_sb, "option_bot_log", kind="closed"))
    p_closed, p_open = picks_frames(_fetch(_sb, "quant_bot_trades"))
    closed = _times(pd.concat([intraday, options, p_closed], ignore_index=True))
    return closed, _times(p_open)


def render(get_supabase):
    st.subheader("🏁 Bot Performance")
    st.caption("All three paper bots side by side. Read-only — this tab never touches the bots. "
               "Refreshes every 5 minutes.")
    try:
        sb = get_supabase()
        closed, opened = _load(sb)
    except Exception as e:
        st.error(f"Couldn't load bot results from Supabase: {e}")
        return

    c1, c2 = st.columns([3, 1])
    with c1:
        start = st.date_input("Count trades closed since", value=None,
                              help="Leave empty to include every trade.")
    with c2:
        st.write("")
        if st.button("🔄 Refresh now"):
            _load.clear()
            st.rerun()
    if start:
        closed = closed[closed["exit_time"].dt.date >= start]

    st.info("**Paper trading only.** These bots use different account sizes and started on "
            f"different days, so compare win rate, profit factor and Avg R before dollars. "
            f"Under {MIN_TRADES} closed trades, a bot's numbers are mostly luck.")

    summary = summarize(closed, opened)
    st.dataframe(display_summary(summary), width="stretch")

    curve = daily_cumulative(closed)
    st.markdown("**Cumulative closed-trade P/L by day**")
    if curve.empty:
        st.write("No closed trades in this range yet.")
    else:
        st.line_chart(curve)

    st.markdown("**Trades**")
    pick = st.selectbox("Bot", ["All"] + BOTS)
    view = closed if pick == "All" else closed[closed["bot"] == pick]
    view = view.sort_values("exit_time", ascending=False).copy()
    view["exit_time"] = view["exit_time"].dt.strftime("%b %d %H:%M")
    view["entry_time"] = view["entry_time"].dt.strftime("%b %d %H:%M")
    st.dataframe(view.rename(columns={
        "bot": "Bot", "symbol": "Symbol", "side": "Side", "strategy": "Strategy",
        "entry_time": "Entry", "exit_time": "Exit", "pnl": "P/L $",
        "r_multiple": "R", "exit_reason": "Exit reason"}).round(2),
        width="stretch", hide_index=True)

    if len(opened) and pick in ("All", "Picks bot"):
        st.markdown("**Picks bot — open positions** (P/L not final until closed)")
        o = opened.copy()
        o["entry_time"] = o["entry_time"].dt.strftime("%b %d %H:%M")
        st.dataframe(o[["symbol", "side", "strategy", "entry_time", "pnl"]].rename(columns={
            "symbol": "Symbol", "side": "Side", "strategy": "Kind",
            "entry_time": "Entry", "pnl": "Open P/L $"}).round(2),
            width="stretch", hide_index=True)

    st.caption("Not included yet: the Webull options paper script (doesn't log to Supabase) and "
               "the old auto-trader (keeps no trade history).")
