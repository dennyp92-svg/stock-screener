"""'What's working' section of the Bot Performance tab.

Breaks each bot's closed trades down by the conditions it logged at entry
(strategy, entry hour, market direction, RSI, volume, ...) and shows which
conditions made or lost money. Read-only.

Slicing a few hundred trades many ways always turns up some differences by luck,
so every group is compared with the rest of that bot's trades (Welch's t-test on
the per-trade score) and labelled "likely real" or "could be luck".
"""
from __future__ import annotations

import math
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

CT = ZoneInfo("America/Chicago")
MIN_GROUP = 10        # groups smaller than this are shown but never called real
T_REAL = 2.0          # |t| at or above this counts as "likely real" (about 95% confidence)
T_WATCH = 1.5         # |t| from here up to T_REAL is "worth watching": a hint, not proven
MIN_BOT_TRADES = 20   # below this a bot has too few trades to break down


# ---------------------------------------------------------------- frames -----
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _entry_date(ts):
    """Central-time calendar date a trade was entered, or None."""
    t = pd.to_datetime(ts, utc=True, errors="coerce")
    return None if pd.isna(t) else t.tz_convert(CT).date()


def _hour_label(h):
    if h is None or pd.isna(h):
        return None
    h = int(h)
    return f"{(h - 1) % 12 + 1} {'AM' if h < 12 else 'PM'}"


def intraday_patterns(rows):
    """One row per closed intraday trade: score = R multiple, plus entry conditions."""
    out = []
    for r in rows:
        p = r.get("payload") or {}
        f = p.get("features") or {}
        out.append({
            "entry_date": _entry_date(p.get("entry_ts")),
            "pnl": _f(p.get("pnl_net")),
            "score": _f(p.get("r_multiple")),
            "Strategy": p.get("strategy") or None,
            "Long or short": p.get("side") or None,
            "Entry hour (CT)": _f(f.get("hour_ct")),
            "Market direction": {-1: "Market down", 0: "Market flat", 1: "Market up"}.get(f.get("mkt_dir")),
            "Volatility": p.get("vol_bucket") or None,
            "Bot's regime tag": None if p.get("regime") in (None, "?") else p["regime"],
            "Session": {0: "Regular hours", 1: "Pre/after-hours"}.get(p.get("ext_entry")),
            "RSI": _f(f.get("rsi")),
            "ADX (trend strength)": _f(f.get("adx")),
            "Volume vs normal": _f(f.get("vol_ratio")),
            "Distance from VWAP": _f(f.get("vwap_dev")),
            "Signal strength": _f(f.get("strength")),
        })
    return pd.DataFrame(out)


def autotrader_patterns(state):
    """One row per closed auto-trader trade: score = % gain/loss. Its times are UTC."""
    out = []
    for j in (state or {}).get("journal") or []:
        m = j.get("entry_meta") or {}
        entry = pd.to_datetime(j.get("entry_ts"), utc=True, errors="coerce")
        out.append({
            "entry_date": None if pd.isna(entry) else entry.tz_convert(CT).date(),
            "pnl": _f(j.get("pnl")),
            "score": _f(j.get("pnl_pct")),
            "Entry hour (CT)": None if pd.isna(entry) else entry.tz_convert(CT).hour,
            "Entry weekday": None if pd.isna(entry) else entry.tz_convert(CT).strftime("%a"),
            "Day's move at entry %": _f(m.get("chg")),
            "RSI": _f(m.get("rsi")),
            "Volume vs normal": _f(m.get("vol_spike")),
        })
    return pd.DataFrame(out)


def picks_patterns(rows):
    out = []
    for r in rows:
        if r.get("status") != "closed":
            continue
        out.append({
            "entry_date": _entry_date(r.get("entry_time") or r.get("submitted_at")),
            "pnl": _f(r.get("pnl")),
            "score": _f(r.get("r_multiple")),
            "Day or swing": r.get("kind"),
            "Long or short": r.get("direction"),
        })
    return pd.DataFrame(out)


SCORE_NAME = {"Intraday bot": "Avg R", "Auto-trader": "Avg % per trade", "Picks bot": "Avg R"}


# ---------------------------------------------------------------- math -------
def welch_t(a, b):
    """Welch's t statistic for mean(a) - mean(b); 0 when either side can't be measured."""
    a, b = pd.Series(a, dtype=float).dropna(), pd.Series(b, dtype=float).dropna()
    if len(a) < 2 or len(b) < 2:
        return 0.0
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return 0.0 if se == 0 else (a.mean() - b.mean()) / se


def bucket(df, col):
    """Group labels for one factor: categories as-is, numbers split into low/mid/high thirds."""
    s = df[col]
    if col == "Entry hour (CT)":
        return s.map(_hour_label)
    if pd.api.types.is_numeric_dtype(s) and s.nunique(dropna=True) > 4:
        try:
            cats = pd.qcut(s, 3, duplicates="drop")
        except ValueError:
            return s
        names = ["low", "mid", "high"] if len(cats.cat.categories) == 3 else [""] * len(cats.cat.categories)
        labels = {iv: f"{n} ({iv.left:.3g} to {iv.right:.3g})".strip()
                  for iv, n in zip(cats.cat.categories, names)}
        return cats.map(labels).astype("object")
    return s.astype("object")


def _order_key(label):
    """Sort hours by clock, low/mid/high in that order, everything else alphabetically."""
    if isinstance(label, str):
        for i, w in enumerate(("low", "mid", "high")):
            if label.startswith(w + " "):
                return (0, i, label)
        parts = label.split()
        if len(parts) == 2 and parts[1] in ("AM", "PM") and parts[0].isdigit():
            h = int(parts[0]) % 12 + (12 if parts[1] == "PM" else 0)
            return (0, h, label)
    return (1, 0, str(label))


def breakdown(df, factor):
    """Per-group stats for one factor, with a luck check against the rest of the trades."""
    d = df.dropna(subset=["score"]).copy()
    d["group"] = bucket(d, factor) if len(d) else []
    d = d.dropna(subset=["group"])
    rows = []
    for g, part in d.groupby("group"):
        rest = d[d["group"] != g]
        t = welch_t(part["score"], rest["score"])
        n = len(part)
        if n < MIN_GROUP or len(rest) < MIN_GROUP:      # both sides need enough trades
            verdict = "too few trades"
        elif abs(t) >= T_REAL:
            verdict = "likely real ✅" if t > 0 else "likely real ⚠️"
        elif abs(t) >= T_WATCH:
            verdict = "worth watching 👀"
        else:
            verdict = "could be luck"
        rows.append({"Group": g, "Trades": n,
                     "Win rate %": round((part["pnl"] > 0).mean() * 100, 1),
                     "Avg score": round(part["score"].mean(), 2),
                     "Total P/L $": round(part["pnl"].sum(), 2),
                     "vs rest (t)": round(t, 1), "Verdict": verdict})
    out = pd.DataFrame(rows, columns=["Group", "Trades", "Win rate %", "Avg score", "Total P/L $",
                                      "vs rest (t)", "Verdict"])
    if len(out):
        out = out.iloc[sorted(range(len(out)), key=lambda i: _order_key(out["Group"].iloc[i]))]
    return out.reset_index(drop=True)


def factors(df):
    return [c for c in df.columns if c not in ("pnl", "score", "entry_date") and df[c].notna().sum() >= MIN_GROUP]


def key_findings(df, limit=6, prefix="likely real"):
    """The strongest groups whose verdict starts with `prefix`, strongest first."""
    found = []
    for f in factors(df):
        b = breakdown(df, f)
        for _, r in b[b["Verdict"].str.startswith(prefix)].iterrows():
            found.append((abs(r["vs rest (t)"]), f, r))
    found.sort(key=lambda x: -x[0])
    return [(f, r) for _, f, r in found[:limit]]


# ---------------------------------------------------------------- page -------
@st.cache_data(ttl=300, show_spinner=False)
def _load(_sb, _fetch):
    intraday = intraday_patterns(_fetch(_sb, "bot_log", kind="trade"))
    at = (_sb.table("auto_trade_state").select("state").eq("id", "paper").execute().data or [{}])
    auto = autotrader_patterns(at[0].get("state"))
    picks = picks_patterns(_fetch(_sb, "quant_bot_trades"))
    return {"Intraday bot": intraday, "Auto-trader": auto, "Picks bot": picks}


def render(sb, fetch, regimes=None):
    st.divider()
    st.subheader("🔍 What's working")
    st.caption("Each bot's closed trades split by the conditions it logged when it entered. "
               "Every group is tested against the rest of that bot's trades: **likely real ✅** "
               "means it does reliably better, **likely real ⚠️** reliably worse, **worth watching 👀** "
               "is close but not proven, and **could be luck** means the gap is too small to trust. "
               f"Groups under {MIN_GROUP} trades are never called real.")
    try:
        data = _load(sb, fetch)
    except Exception as e:
        st.error(f"Couldn't load trade details: {e}")
        return

    if regimes is not None:
        st.caption("Market trend and VIX are matched to each trade as of the previous close, "
                   "i.e. what the bot could have known when it entered.")
    bot = st.selectbox("Bot to break down", list(data), key="patterns_bot")
    df = data[bot]
    if regimes is not None:
        import market_regime
        df = market_regime.attach(df, regimes)
    n = int(df["score"].notna().sum()) if len(df) else 0
    if n < MIN_BOT_TRADES:
        st.info(f"{bot} has {n} closed trades with enough detail. Come back after "
                f"{MIN_BOT_TRADES}+; below that, any pattern is mostly noise.")
        return
    score = SCORE_NAME[bot]

    st.markdown(f"**Key findings** — {n} trades, scored by {score}")
    findings = key_findings(df)
    if not findings:
        st.write("Nothing is proven yet: no condition does reliably better or worse than the rest.")
        findings = key_findings(df, prefix="worth watching")
        if findings:
            st.markdown("**Worth watching** (close, but could still be luck):")
    for f, r in findings:
        better = r["vs rest (t)"] > 0
        proven = r["Verdict"].startswith("likely real")
        icon = ("✅" if better else "⚠️") if proven else ("👀 ↑" if better else "👀 ↓")
        st.markdown(f"{icon} **{f}: {r['Group']}** — {r['Trades']} trades, "
                    f"{r['Win rate %']:.0f}% win rate, {score} {r['Avg score']:+.2f}, "
                    f"total {'+' if r['Total P/L $'] >= 0 else '-'}${abs(r['Total P/L $']):,.0f} "
                    f"({'better' if better else 'worse'} than its other trades)")
    st.caption("Paper trading. A pattern being real in the past doesn't guarantee it continues; "
               "re-check after more trades before changing a bot.")

    factor = st.selectbox("Break down by", factors(df), key="patterns_factor")
    b = breakdown(df, factor)
    if b.empty:
        st.write("No trades have this detail logged.")
        return
    chart = b.set_index("Group")[["Avg score"]].rename(columns={"Avg score": score})
    st.bar_chart(chart, horizontal=True)
    st.dataframe(b.rename(columns={"Avg score": score}), width="stretch", hide_index=True)
