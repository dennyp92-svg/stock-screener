# Pine Script — Supertrend + Hull MA + Risk Management

`supertrend_hull_risk.pine` is a TradingView **Pine Script v5 strategy** that combines three ideas:

1. **Supertrend** (ATR-band trend filter, in the style popularized by *KivancOzbilgic*) — flips long/short when
   price crosses the ATR band. Includes the "change ATR method" toggle (Wilder RMA vs. SMA of True Range).
2. **Hull Moving Average** (nested-WMA smoother, in the style popularized by *InSilico*) — used as an optional
   slope filter so entries only fire when the Hull MA agrees with the Supertrend direction.
3. **Risk management + profit taking** — percent-of-equity position sizing, choice of stop method
   (ATR / Percent / Supertrend line), and two profit modes:
   - **Single R-multiple** — one take-profit at `R x stop distance`.
   - **Staged TP1/TP2/TP3** (default) — scale out a configurable % of the position at three increasing
     R-multiple targets. The final leg closes the remainder, or rides an ATR trailing stop if enabled.
   - Optional **move-to-break-even**: once TP1 fills, the stop is pulled to the entry price so the trade
     can no longer become a loss (the stop line turns blue on the chart when this is active).

### Profit-taking defaults

| Level | R multiple | % of position closed |
|-------|-----------|----------------------|
| TP1   | 1.0R      | 40%                  |
| TP2   | 2.0R      | 30%                  |
| TP3   | 3.0R      | remaining 30%        |

Scale-out sizes are computed as absolute quantities relative to the **original** position size, so TP2's
30% is 30% of the entry size — not 30% of whatever is left after TP1.

## How to use

1. Open TradingView → **Pine Editor**.
2. Paste the contents of `supertrend_hull_risk.pine`.
3. Click **Add to chart**. Open **Settings** to adjust inputs, and the **Strategy Tester** tab to backtest.

## Important caveats

- This is an **independent implementation** of the standard, publicly documented algorithms. It is **not** a
  verbatim copy of any specific author's proprietary published script. Author names describe the *style* of
  each component, nothing more.
- Backtest results are **not** a guarantee of live performance. `process_orders_on_close = true` means signals
  are evaluated on bar close; real fills, slippage, and spread will differ. Commission is set to a placeholder
  `0.04%` — change it to match your broker.
- Verify the behavior on your own charts and instruments before risking real money.
