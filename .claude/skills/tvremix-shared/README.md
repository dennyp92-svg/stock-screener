# tvremix skills

Slash commands for TradingView market analysis, adapted from the
[tvremix Claude Code plugin](https://github.com/tvremix/claude-plugin) so they
work in Claude Code cloud sessions for this repo. Cloud sessions don't install
plugins that a repo declares in `.claude/settings.json`, but they do load
skills committed under `.claude/skills/`.

- Source: https://github.com/tvremix/claude-plugin
- Commit: `f65841716f66472ed54ef0e1cceb29a9f570786c` (2026-04-26)
- License: Apache-2.0, see [LICENSE](LICENSE)

## Commands

| Command | What it does |
|---|---|
| `/tvremix-setup` | Check the TradingView connection and list the commands |
| `/tvremix-smc` | Smart Money Concepts: BOS/CHoCH, order blocks, FVGs, liquidity, premium/discount |
| `/tvremix-swing` | Swing setup: Fibonacci, pivots, pullback zones, R:R |
| `/tvremix-momentum` | Multi-timeframe RSI / MACD / ADX / volume |
| `/tvremix-options` | Options chain, Greeks, IV skew, strategy ideas |
| `/tvremix-levels` | Support/resistance ladder: pivots, order blocks, FVGs, liquidity, Fib |
| `/tvremix-confluence` | Do 15m / 1h / 4h / 1D / 1W agree? |
| `/tvremix-chart` | Quick price-action read |

They need the TradingView connector (the hosted tvremix server) connected on
claude.ai. Its tools appear in sessions as `mcp__Tradingview_AI__*`.

## Changes from the plugin

- Each skill has its own folder, `tvremix-<name>/`, and is named
  `tvremix-<name>`, so commands are `/tvremix-smc` rather than `/tvremix:smc`.
  References between skills were updated to match.
- The shared references `mcp-tools.md` and `presentation.md` live in this
  folder, and the skills' paths to them were updated.
- Tool names changed from `mcp__tvremix__*` to `mcp__Tradingview_AI__*`: the
  skills use the claude.ai connector instead of the plugin's own MCP server,
  which needs its own sign-in.
- `tvremix-setup/SKILL.md` and the "Connection & Auth" section of
  `mcp-tools.md` describe the connector instead of the plugin's OAuth sign-in.
- Each changed file carries a comment pointing here. `presentation.md` and
  `LICENSE` are unchanged. The plugin's `.mcp.json`, `plugin.json` and
  `marketplace.json` aren't included.

## Updating

To pick up a newer plugin version, copy the files from the source repo again
and re-apply the changes above.
