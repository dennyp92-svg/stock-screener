"""Daily market regime label: trend from SPY, volatility from the VIX. Display/analysis only.

Trend (SPY daily close vs its moving averages):
  Uptrend   - close above the 50-day average and the 20-day above the 50-day
  Downtrend - close below the 50-day average and the 20-day below the 50-day
  Sideways  - anything in between
Volatility (VIX close): Calm < 15, Normal 15-20, Elevated 20-30, High >= 30.

Data: FMP's daily closes (the app's FMP_KEY). Nothing here touches any bot.
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

FMP = "https://financialmodelingprep.com/stable/historical-price-eod/light"
VIX_BANDS = [(15, "Calm"), (20, "Normal"), (30, "Elevated"), (float("inf"), "High")]
LOOKBACK_DAYS = 400     # calendar days of history: enough for a 50-day average well before the bots started


def fetch_closes(symbol, key, start, end=None, get=None):
    """Daily closes as a Series indexed by date (oldest first)."""
    import requests
    get = get or requests.get
    params = {"symbol": symbol, "from": start.isoformat(), "apikey": key}
    if end:
        params["to"] = end.isoformat()
    r = get(FMP, params=params, timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"no price data for {symbol}")
    s = pd.Series({dt.date.fromisoformat(x["date"]): float(x["price"]) for x in rows if x.get("price") is not None})
    return s.sort_index()


def vol_label(vix):
    if vix is None or pd.isna(vix):
        return None
    return next(name for limit, name in VIX_BANDS if vix < limit)


def classify(spy: pd.Series, vix: pd.Series) -> pd.DataFrame:
    """One row per SPY trading day: close, averages, trend, VIX, volatility label."""
    df = pd.DataFrame({"spy": spy})
    df["sma20"] = df["spy"].rolling(20).mean()
    df["sma50"] = df["spy"].rolling(50).mean()
    up = (df["spy"] > df["sma50"]) & (df["sma20"] > df["sma50"])
    down = (df["spy"] < df["sma50"]) & (df["sma20"] < df["sma50"])
    df["trend"] = "Sideways"
    df.loc[up, "trend"] = "Uptrend"
    df.loc[down, "trend"] = "Downtrend"
    df.loc[df["sma50"].isna(), "trend"] = None
    df["vix"] = vix.reindex(df.index).ffill()
    df["volatility"] = df["vix"].map(vol_label)
    return df


def as_of_prior_close(regimes: pd.DataFrame, day: dt.date):
    """The regime row known BEFORE `day` opened (the latest close strictly earlier), or None."""
    if regimes is None or regimes.empty or day is None:
        return None
    earlier = regimes.loc[regimes.index < day]
    return None if earlier.empty else earlier.iloc[-1]


def attach(df: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame:
    """Adds 'Market trend (SPY)' and 'VIX at entry' to a trade table that has an 'entry_date' column."""
    df = df.copy()
    if df.empty or regimes is None or regimes.empty or "entry_date" not in df:
        return df
    rows = [as_of_prior_close(regimes, d) for d in df["entry_date"]]
    df["Market trend (SPY)"] = [None if r is None else r["trend"] for r in rows]
    df["VIX at entry"] = [None if r is None else r["vix"] for r in rows]
    df["VIX at entry"] = pd.to_numeric(df["VIX at entry"], errors="coerce")
    return df


def headline(regimes: pd.DataFrame) -> str | None:
    if regimes is None or regimes.dropna(subset=["trend"]).empty:
        return None
    r = regimes.dropna(subset=["trend"]).iloc[-1]
    day = regimes.dropna(subset=["trend"]).index[-1]
    vix = f", VIX {r['vix']:.1f}" if pd.notna(r["vix"]) else ""
    return (f"🧭 **Market: {r['trend']} · {r['volatility'] or 'VIX n/a'}** "
            f"(as of the {day.strftime('%b %-d')} close: SPY {r['spy']:.2f} vs 50-day {r['sma50']:.2f}{vix})")


def _key():
    try:
        import streamlit as st
        k = st.secrets.get("FMP_KEY", None)
        if k:
            return k
    except Exception:
        pass
    return os.getenv("FMP_KEY")


def load(today=None):
    """Regimes from LOOKBACK_DAYS ago to today, or None when there's no key or FMP fails."""
    key = _key()
    if not key:
        return None
    today = today or dt.date.today()
    start = today - dt.timedelta(days=LOOKBACK_DAYS)
    return classify(fetch_closes("SPY", key, start), fetch_closes("^VIX", key, start))
