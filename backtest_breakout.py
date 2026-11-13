"""Backtest of the breakout scanner's rules on daily bars, with several stop/target exits.

Signals (same rules as breakout_scanner.py, on daily bars):
  breakout     day's high clears the prior 20-day high by 0.2%, volume >= 1.5x the 50-day
               average, open not already >5% over the level, uptrend the day before
               (close > SMA50 > SMA200).
  from_watch   breakout that came out of a WATCH setup the day before (within 3% under the
               level, 10-day range <= 12%).
  no_vol       breakout without the volume rule (to see if the volume filter helps).
  random       random uptrend days, bought at the open (the baseline any edge must beat).

Entry: max(open, level * 1.002) - a buy-stop at the breakout level. 0.1% round-trip cost.
Fills on daily bars are conservative: on the entry day only the close counts; on later
days a stop and target hit the same day counts as the stop. One open trade per stock
per variant at a time. Every trade also has a 20-trading-day time stop.

Caveats printed with the results:
  - Universe is TODAY's >$1B stocks (survivorship bias flatters all groups, including random).
  - Volume rule uses the full day's volume; live alerts use the intraday pace.

Env: FMP_KEY, YEARS (default 5)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

import breakout_scanner as bs

COST = 0.001
MAX_HOLD = 20
rng = np.random.default_rng(7)

STOPS = {
    "ATR 1.5x": lambda e, x: e - 1.5 * x["atr"],
    "3% under level": lambda e, x: x["R"] * 0.97,
    "base low": lambda e, x: max(x["low10"], e * 0.90),
    "5% fixed": lambda e, x: e * 0.95,
}
EXITS = ["2R target", "3R target", "trail 10EMA", "half 1.5R + trail", "3%/6% halves (quant bot)"]


def features(df):
    c, h, l, o, v = (df[k].to_numpy(float) for k in ("Close", "High", "Low", "Open", "Volume"))
    s = lambda a: pd.Series(a)
    prev_c = s(c).shift(1)
    tr = pd.concat([s(h) - s(l), (s(h) - prev_c).abs(), (s(l) - prev_c).abs()], axis=1).max(axis=1)
    f = pd.DataFrame({
        "o": o, "h": h, "l": l, "c": c, "v": v,
        "sma50": s(c).rolling(50).mean().shift(1),
        "sma200": s(c).rolling(200).mean().shift(1),
        "prev_c": prev_c,
        "R": s(h).rolling(20).max().shift(1),
        "avgv": s(v).rolling(50).mean().shift(1),
        "atr": tr.rolling(14).mean().shift(1),
        "low10": s(l).rolling(10).min().shift(1),
        "high10": s(h).rolling(10).max().shift(1),
        "ema10": s(c).ewm(span=10, adjust=False).mean(),
    })
    f["up"] = (f.prev_c > f.sma50) & (f.sma50 > f.sma200)
    f["dist"] = (f.R - f.prev_c) / f.prev_c * 100
    f["range10"] = (f.high10 - f.low10) / f.prev_c * 100
    brk = f.up & (f.h >= f.R * 1.002) & (f.o <= f.R * 1.05)
    f["no_vol"] = brk
    f["breakout"] = brk & (f.v >= 1.5 * f.avgv)
    f["from_watch"] = f.breakout & f.dist.between(0, 3) & (f.range10 <= 12)
    return f


def simulate(a, t, e, stop, rule):
    """Returns (return %, days held, exit index). a = dict of numpy arrays."""
    o, h, l, c, ema = a["o"], a["h"], a["l"], a["c"], a["ema10"]
    risk = e - stop
    if risk <= 0:
        return None
    if rule == "2R target":
        legs = [(1.0, e + 2 * risk)]
    elif rule == "3R target":
        legs = [(1.0, e + 3 * risk)]
    elif rule == "trail 10EMA":
        legs = [(1.0, None)]
    elif rule == "half 1.5R + trail":
        legs = [(0.5, e + 1.5 * risk), (0.5, None)]
    else:  # quant bot style: 3% stop handled by caller, halves at +3% and +6%
        legs = [(0.5, e * 1.03), (0.5, e * 1.06)]
    open_legs, pnl, cur_stop = list(legs), 0.0, stop
    last = min(t + MAX_HOLD, len(c) - 1)
    for d in range(t, last + 1):
        first = d == t
        lo, hi, op = (c[d], c[d], c[d]) if first else (l[d], h[d], o[d])
        if lo <= cur_stop:
            px = min(op, cur_stop)
            pnl += sum(w for w, _ in open_legs) * (px / e - 1)
            return pnl * 100 - COST * 100, d - t, d
        still = []
        for w, tgt in open_legs:
            if tgt is not None and hi >= tgt:
                pnl += w * (max(op, tgt) / e - 1)
                cur_stop = max(cur_stop, e)  # after first target, stop to breakeven
            elif tgt is None and not first and c[d] < ema[d]:
                pnl += w * (c[d] / e - 1)
            else:
                still.append((w, tgt))
        open_legs = still
        if not open_legs:
            return pnl * 100 - COST * 100, d - t, d
    pnl += sum(w for w, _ in open_legs) * (c[last] / e - 1)
    return pnl * 100 - COST * 100, last - t, last


def run(bars):
    rows = []
    for sym, df in bars.items():
        f = features(df)
        if len(f) < 260:
            continue
        a = {k: f[k].to_numpy(float) for k in ("o", "h", "l", "c", "ema10", "R", "atr", "low10")}
        years = pd.Index(df.index).map(lambda d: d.year).to_numpy()
        n_sig = int(f.breakout.sum())
        rand_days = f.index[f.up.fillna(False) & f.atr.notna()].to_numpy()
        rand_pick = set(rng.choice(rand_days, size=min(len(rand_days), max(2, 2 * n_sig)), replace=False)) if len(rand_days) else set()
        for group in ("breakout", "from_watch", "no_vol", "random"):
            days = sorted(rand_pick) if group == "random" else list(np.flatnonzero(f[group].fillna(False).to_numpy()))
            for sname, sfn in STOPS.items():
                for rule in EXITS:
                    if rule.startswith("3%/6%") and sname != "5% fixed":
                        continue
                    busy_until = -1
                    for t in days:
                        if t <= busy_until or t < 200:
                            continue
                        x = {"R": a["R"][t], "atr": a["atr"][t], "low10": a["low10"][t]}
                        e = a["o"][t] if group == "random" else max(a["o"][t], x["R"] * 1.002)
                        stop = e * 0.97 if rule.startswith("3%/6%") else sfn(e, x)
                        if np.isnan(stop) or (e - stop) / e > 0.15:
                            continue
                        res = simulate(a, t, e, stop, rule)
                        if res is None:
                            continue
                        ret, held, exit_i = res
                        busy_until = exit_i
                        exit_name = "3% stop, halves +3/+6%" if rule.startswith("3%/6%") else rule
                        stop_name = "-" if rule.startswith("3%/6%") else sname
                        rows.append((group, stop_name, exit_name, sym, int(years[t]), ret, held,
                                     ret / ((e - stop) / e * 100)))
    return pd.DataFrame(rows, columns=["group", "stop", "exit", "symbol", "year", "ret", "days", "r"])


def summarize(tr):
    def stats(g):
        wins, losses = g.ret[g.ret > 0].sum(), -g.ret[g.ret <= 0].sum()
        return pd.Series({"trades": len(g), "win%": (g.ret > 0).mean() * 100, "avg%": g.ret.mean(),
                          "median%": g.ret.median(), "avgR": g.r.mean(),
                          "PF": wins / losses if losses else np.nan, "days": g.days.mean()})
    s = tr.groupby(["group", "stop", "exit"]).apply(stats).reset_index()
    rand = s[s.group == "random"].set_index(["stop", "exit"])["avg%"]
    s["edge vs random"] = s.apply(lambda r: r["avg%"] - rand.get((r["stop"], r["exit"]), np.nan), axis=1)
    return s.round(2)


def main():
    years = int(os.getenv("YEARS", "5"))
    syms, source = bs.universe(os.environ["FMP_KEY"])
    bars = bs.daily_bars(syms, period=f"{years}y")
    print(f"universe {len(syms)} from {source}; bars for {len(bars)}; {years} years")
    tr = run(bars)
    tr.to_csv("breakout_backtest_trades.csv", index=False)
    s = summarize(tr)
    s.to_csv("breakout_backtest_summary.csv", index=False)
    pd.set_option("display.width", 250, "display.max_rows", 500, "display.max_columns", 20)
    print("\n=== ALL VARIANTS (avg% is per trade after costs; edge = avg% minus random entries, same exits) ===")
    print(s.sort_values(["group", "avg%"], ascending=[True, False]).to_string(index=False))
    best = s[s.group != "random"].sort_values("avg%", ascending=False).head(8)
    print("\n=== TOP 8 NON-RANDOM ===\n" + best.to_string(index=False))
    for g, st, ex in best[["group", "stop", "exit"]].head(3).itertuples(index=False):
        sub = tr[(tr.group == g) & (tr.stop == st) & (tr.exit == ex)]
        by = sub.groupby("year").ret.agg(["count", "mean", lambda r: (r > 0).mean() * 100]).round(2)
        by.columns = ["trades", "avg%", "win%"]
        print(f"\n--- by year: {g} | {st} | {ex} ---\n" + by.to_string())
    summ = os.getenv("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a") as fh:
            fh.write("## Breakout backtest\n\n```\n" + s.to_string(index=False) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
