---
name: tvremix-setup
description: Verify the tvremix MCP connection and tour the available commands
---

<!-- Adapted from https://github.com/tvremix/claude-plugin at commit f658417 (Apache-2.0). Changed for this repo: see ../tvremix-shared/README.md -->

Walk the user through confirming the tvremix skills can reach their TradingView connector and introduce them to the available commands. Keep the tone conversational.

## Step 1 — Confirm the environment

Tell the user you'll run a harmless smoke test. If they're seeing this skill, Claude Code is clearly running, so just confirm briefly.

## Step 2 — Check the connection

These skills don't bring their own MCP server. They use the user's claude.ai TradingView connector, which is the hosted tvremix server:

- **tvremix** — `https://tvremix.xyz/api/mcp/v1` (TradingView data + SMC/swing analyzers + options + web search)

Its tools appear as `mcp__Tradingview_AI__<name>`. Sign-in is handled by the connector on claude.ai, so there's nothing to authenticate inside the session.

If no `mcp__Tradingview_AI__*` tools are available, tell the user the TradingView connector isn't connected or isn't enabled for this session, and that they can check it in their claude.ai connector settings. Stop there.

## Step 3 — Smoke test

Call `mcp__Tradingview_AI__get_quote` with `symbol="NASDAQ:AAPL"`. This is the cheapest, safest call — public data, no side effects. Show the user the resulting price + change% + volume as a single line, e.g.:

> **AAPL** — $185.23 (+1.2%) on 41.8M shares — tvremix MCP is live.

If the call fails:
- `401 Unauthorized` → the connector's sign-in has lapsed. Ask the user to reconnect the TradingView connector on claude.ai, then try again.
- `429 Too Many Requests` → rate limit (60/min/account). Wait a minute and retry.
- Any other error → report the full message and suggest they file an issue at `https://github.com/tvremix/claude-plugin/issues`.

## Step 4 — Quick tour

Tell the user about the available slash commands. All start with `/tvremix-`:

| Command | What it does | Example |
|---|---|---|
| `/tvremix-setup` | This verification skill | `/tvremix-setup` |
| `/tvremix-smc` | Smart Money Concepts analysis (BOS, CHoCH, OBs, FVGs, bias) | `/tvremix-smc NASDAQ:NVDA 4h` |
| `/tvremix-swing` | Swing trading setup with Fibonacci, pivots, R:R | `/tvremix-swing NASDAQ:AAPL` |
| `/tvremix-momentum` | Multi-timeframe RSI/MACD/volume momentum read | `/tvremix-momentum BINANCE:BTCUSDT` |
| `/tvremix-options` | Options chain, Greeks, strategy suggestions | `/tvremix-options NASDAQ:TSLA` |
| `/tvremix-levels` | Key S/R, order blocks, FVGs, liquidity levels | `/tvremix-levels NASDAQ:META` |
| `/tvremix-confluence` | Multi-timeframe trend alignment check | `/tvremix-confluence NASDAQ:MSFT` |
| `/tvremix-chart` | Quick price-action read (quote + bars + trend) | `/tvremix-chart NYSE:JPM` |

They can also just ask Claude anything about a symbol — the commands are shortcuts for common workflows.

## Step 5 — What's next

Suggest they try `/tvremix-smc NASDAQ:NVDA 4h` as a first real test — it exercises the most sophisticated tool (`analyze_smc_tool`) and produces a visually interesting structured read.

Remind them:
- Not a Claude Code user? The same tools are available to Claude Desktop, Claude.ai web, Cursor, OpenClaw, Hermes, and any other MCP client — see `https://tvremix.xyz/mcp`.
- Need API keys for CLI tools? `https://tvremix.xyz/account#api-keys`.
