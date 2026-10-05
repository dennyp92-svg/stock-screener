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
   - **Paper:** each cycle replays every 5-minute bar since the last check, so
     exits fill where resting orders would have: at the stop / target price, or
     at the bar's open if price gapped through it (stop assumed first when one
     bar touches both). Positions opened before this existed are checked on the
     next cycle's price once, then switch to bar replay.
   - **Live:** a GTC `STOP_LOSS` sell order rests at Webull for every open
     position (tracked in `.auto_trade_stops.json`, git-ignored), so the stop
     is enforced between cycles and overnight. On take-profit the engine cancels
     that stop first and does not sell if the cancel fails.

**Market hours:** every cycle — CLI, scheduled workflow or the app's "Run one
paper cycle" button — does nothing outside the regular session (weekdays
9:30–16:00 New York time), because snapshot prices are stale then. Exchange
holidays are not modelled. Every sale, full or partial, is written to the
journal, so the journal always adds up to the trade log's realized P&L.

## Run it (paper mode — default, no real money)

```bash
python auto_trader.py --once       # one cycle
python auto_trader.py --loop --interval 300   # every 5 minutes
python auto_trader.py --status     # print the paper portfolio
python auto_trader.py --journal    # closed trades, win rate, and a QQQ comparison
```

`--journal` ends with a benchmark: the account's return (realized + open
positions at the latest close) versus buying QQQ on the day of the first entry,
plus how each closed trade did versus QQQ over the same days (daily closes, so
approximate).

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
| `AUTO_TRADE_STATE_BACKEND` | `auto` | `auto` (Supabase if creds set, else file), `supabase`, or `file` |
| `AUTO_TRADE_STATE_TABLE` | `auto_trade_state` | Supabase table name for paper state |
| `AUTO_TRADE_STATE_ID` | `paper` | row id within that table (use different ids for separate portfolios) |
| `AUTO_TRADE_STATE_FILE` | `auto_trade_state.json` | file used when the backend is `file` |
| `SUPABASE_URL` / `SUPABASE_KEY` | — | enable durable, shared Supabase state |

## Durable paper state (Supabase)

By default the engine stores the paper portfolio in a **local JSON file**. If
`SUPABASE_URL` / `SUPABASE_KEY` are set (the same secrets the watchlist uses),
it instead stores the whole portfolio as one JSON row in Supabase — so state
**survives redeploys** and is **shared** between the Streamlit app and any
CLI/scheduled runner.

Create the table once in the Supabase SQL editor:

```sql
create table if not exists auto_trade_state (
  id text primary key,
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz default now()
);
```

If Row Level Security is enabled on the table, add policies that allow your
anon key to `select` and `upsert` (insert/update) rows, or the app will show a
clear error. Force a backend explicitly with `AUTO_TRADE_STATE_BACKEND=file`
or `=supabase`.

## Live mode (Webull) — NOT enabled by default

Live trading is deliberately hard to turn on. `WebullBroker` is wired against
the official **Webull OpenAPI Python SDK** (`webull-openapi-python-sdk`), using
verified calls (`account_v2.get_account_list`, `account_v2.get_account_balance`,
`order_v3.place_order`). Order placement stays **disarmed** and `get_positions`
intentionally raises until you complete the checklist below, so the Engine
cannot run live end-to-end on unverified data.

### Live state must be durable (CI / scheduled runs)

Live mode keeps two pieces of bookkeeping between cycles: the ids of the
resting protective stop orders, and the day's starting equity (the baseline for
the daily loss limit). They are stored with the same backend as the paper state,
in a separate row (`<AUTO_TRADE_STATE_ID>_live`, e.g. `paper_live`). With
Supabase configured they survive separate runs, including ephemeral GitHub
Actions runners. With the `file` backend they live in `.auto_trade_live_aux.json`
(git-ignored), which a CI runner loses after every run, so live mode **refuses
to start on CI (`GITHUB_ACTIONS`/`CI` set) unless Supabase is configured**.

Offline tests for this: `python -m unittest tests.test_live_state -v`.

### Prerequisites

- A Webull brokerage account is **not enough**. You need **Webull OpenAPI
  developer credentials**: generate an `app_key` + `app_secret` at
  <https://developer.webull.com>, and get your `account_id`.
- `pip install webull-openapi-python-sdk`

### Live credentials (env or Streamlit secrets)

| Variable | Meaning |
|---|---|
| `WEBULL_APP_KEY` / `WEBULL_APP_SECRET` | OpenAPI credentials |
| `WEBULL_ACCOUNT_ID` | the account to trade |
| `WEBULL_REGION` | region, default `us` |
| `WEBULL_API_ENDPOINT` | **optional** — leave unset; the SDK uses the default host for the region. Only set it (e.g. `api.webull.com`) if the connection fails without it. |
| `WEBULL_ARM_LIVE_ORDERS` | must equal `YES` to allow real orders (default: disarmed) |

### Staged go-live checklist (do these in order)

1. **Read-only connection test** — no orders:
   ```bash
   python auto_trader.py --webull-test
   ```
   Confirm it returns your account list and balance. `get_cash()` reads
   `total_cash_balance` and `get_positions()` maps `symbol`/`quantity`/
   `cost_price` — both verified against a real Individual-Margin response.
   Set `WEBULL_ACCOUNT_ID` to your **stock** (Individual Margin) account,
   not the futures account.
2. ~~Verify the positions response~~ — done; `get_positions` is implemented.
   Note: tiny fractional "dust" lots (e.g. 0.00002 shares) are returned as-is;
   handle/close those manually — the engine isn't meant to trade fractional dust.
3. **One manual test order** at the smallest possible size, armed explicitly:
   ```bash
   export AUTO_TRADE_MODE=live
   export AUTO_TRADE_LIVE_CONFIRM=I_UNDERSTAND_THE_RISK
   export WEBULL_ARM_LIVE_ORDERS=YES
   ```
   (`WEBULL_API_ENDPOINT` is optional — leave it unset unless the connection fails.)
   Place and then cancel one order by hand; confirm it appears in Webull.
4. **Only then** consider letting the Engine place orders automatically — and
   even then, keep the position size, stop-loss, and daily-loss limits tight.

Orders are LIMIT orders at the engine's reference price by default. The
protective stop is a `STOP_LOSS` / `GTC` sell order built from Webull's
documented example; like all orders it is only sent when
`WEBULL_ARM_LIVE_ORDERS=YES`. Before relying on it, place one stop by hand at
the smallest size and confirm it shows in Webull as a working GTC stop.

## Dependencies

Paper mode needs `anthropic` (only if AI confirmation is on). Fetching live
quotes needs `yfinance` and `pandas` (already in `requirements.txt`). Live
Webull execution additionally needs your chosen Webull SDK.
