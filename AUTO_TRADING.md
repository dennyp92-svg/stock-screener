# Automated Trading (`auto_trader.py`)

A rule-based + AI-confirmed momentum trading engine with a **paper** (simulated)
mode and a gated **live** (Webull) mode.

> ⚠️ **Not financial advice.** Automated trading can lose money quickly. Run in
> paper mode until you fully trust the behaviour, then enable live at your own risk.

## How it works

1. **Snapshot** a universe of tickers (price, % change, volume spike, RSI).
2. **Rule screen** — a candidate must pass: `% change` in range, `volume spike`
   above threshold, `RSI` inside a band, and price in range.
3. **AI confirmation** (optional) — Claude must return `AI RATING: BUY`.
4. **Size & buy** — position size is a fixed % of current equity, capped by a
   max number of concurrent positions.
5. **Manage exits** — each open position is closed on a **stop-loss** or
   **take-profit**. A **daily loss limit** halts new entries after a bad day.

## Run it (paper mode — default, no real money)

```bash
python auto_trader.py --once       # one cycle
python auto_trader.py --loop --interval 300   # every 5 minutes
python auto_trader.py --status     # print the paper portfolio
```

Paper state persists in `auto_trade_state.json` (git-ignored).

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `AUTO_TRADE_MODE` | `paper` | `paper` or `live` |
| `AUTO_TRADE_CASH` | `10000` | starting paper cash |
| `AUTO_TRADE_MAX_POSITIONS` | `5` | max concurrent positions |
| `AUTO_TRADE_POSITION_PCT` | `0.10` | fraction of equity per position |
| `AUTO_TRADE_STOP_PCT` | `0.05` | stop-loss (5%) |
| `AUTO_TRADE_TAKE_PCT` | `0.10` | take-profit (10%) |
| `AUTO_TRADE_DAILY_LOSS_PCT` | `0.05` | halt new entries after this daily drawdown |
| `AUTO_TRADE_MIN_CHANGE` / `AUTO_TRADE_MAX_CHANGE` | `2` / `30` | entry % change band |
| `AUTO_TRADE_MIN_VOL_SPIKE` | `1.5` | min volume spike (x normal) |
| `AUTO_TRADE_MIN_RSI` / `AUTO_TRADE_MAX_RSI` | `50` / `75` | entry RSI band |
| `AUTO_TRADE_USE_AI` | `true` | require AI confirmation before buying |
| `ANTHROPIC_KEY` | — | needed only if AI confirmation is on |

## Live mode (Webull) — NOT enabled by default

Live trading is deliberately hard to turn on and **does not work out of the box**.
`WebullBroker` is a scaffold: its `get_cash` / `get_positions` / `buy` / `sell`
methods are intentionally unimplemented so no unverified call can place a real
order. To go live you must, in order:

1. Set `AUTO_TRADE_MODE=live` **and** `AUTO_TRADE_LIVE_CONFIRM=I_UNDERSTAND_THE_RISK`.
2. Implement the four `WebullBroker` methods against the **current Webull SDK/API**,
   verifying every method name and parameter against Webull's official docs
   (they are not guessed in this codebase on purpose).
3. Test with the **smallest possible size** before trusting it.

## Dependencies

Paper mode needs `anthropic` (only if AI confirmation is on). Fetching live
quotes needs `yfinance` and `pandas` (already in `requirements.txt`). Live
Webull execution additionally needs your chosen Webull SDK.
