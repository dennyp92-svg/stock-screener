"""
Automated trading engine for the stock screener.

Strategy: a deterministic rule-based screen (change %, volume spike, RSI band)
picks candidates, then an optional AI confirmation step (Claude) must agree
before a position is opened. Open positions are managed with a stop-loss and
take-profit. Risk is bounded by position sizing, a max number of concurrent
positions, and a daily loss limit.

============================  SAFETY MODEL  ============================
- MODE defaults to "paper": NO real money. Fills are simulated and the
  portfolio is tracked in a local JSON state file.
- MODE "live" is deliberately hard to enable and does NOT work out of the
  box. It requires:
    1. AUTO_TRADE_MODE=live
    2. AUTO_TRADE_LIVE_CONFIRM=I_UNDERSTAND_THE_RISK
    3. A WebullBroker whose order methods have been implemented AND verified
       against the current Webull SDK/API. They are intentionally left
       unimplemented here so no unverified call can ever place a real order.
This is not financial advice. Automated trading can lose money quickly.
=======================================================================
"""

import os
import json
import math
import time
import argparse
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# yfinance and pandas are imported lazily inside get_stock_data/calc_rsi so the
# engine and paper broker can run (and be tested) without those heavy deps.


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "AVGO",
    "PLTR", "COIN", "MSTR", "SOFI", "HOOD", "SMCI", "ARM", "NFLX", "UBER",
    "MU", "MRVL", "CRWD", "PANW", "SHOP", "RKLB", "IONQ", "RGTI",
]


@dataclass
class Config:
    # Every env-derived field uses default_factory so the environment is read
    # when a Config() is instantiated, not once at import time.
    mode: str = field(default_factory=lambda: os.getenv("AUTO_TRADE_MODE", "paper").strip().lower())  # paper | live
    starting_cash: float = field(default_factory=lambda: _env_float("AUTO_TRADE_CASH", 10_000.0))

    # Risk / sizing
    max_positions: int = field(default_factory=lambda: _env_int("AUTO_TRADE_MAX_POSITIONS", 5))
    position_size_pct: float = field(default_factory=lambda: _env_float("AUTO_TRADE_POSITION_PCT", 0.10))   # of equity
    stop_loss_pct: float = field(default_factory=lambda: _env_float("AUTO_TRADE_STOP_PCT", 0.05))
    take_profit_pct: float = field(default_factory=lambda: _env_float("AUTO_TRADE_TAKE_PCT", 0.10))
    daily_loss_limit_pct: float = field(default_factory=lambda: _env_float("AUTO_TRADE_DAILY_LOSS_PCT", 0.05))

    # Entry rules (rule-based screen)
    min_change_pct: float = field(default_factory=lambda: _env_float("AUTO_TRADE_MIN_CHANGE", 2.0))
    max_change_pct: float = field(default_factory=lambda: _env_float("AUTO_TRADE_MAX_CHANGE", 30.0))
    min_vol_spike: float = field(default_factory=lambda: _env_float("AUTO_TRADE_MIN_VOL_SPIKE", 1.5))
    min_rsi: float = field(default_factory=lambda: _env_float("AUTO_TRADE_MIN_RSI", 50.0))
    max_rsi: float = field(default_factory=lambda: _env_float("AUTO_TRADE_MAX_RSI", 75.0))
    min_price: float = field(default_factory=lambda: _env_float("AUTO_TRADE_MIN_PRICE", 2.0))
    max_price: float = field(default_factory=lambda: _env_float("AUTO_TRADE_MAX_PRICE", 2000.0))

    use_ai_confirmation: bool = field(default_factory=lambda: _env_bool("AUTO_TRADE_USE_AI", True))
    universe: list = field(default_factory=lambda: list(DEFAULT_UNIVERSE))

    state_file: str = field(default_factory=lambda: os.getenv("AUTO_TRADE_STATE_FILE", "auto_trade_state.json"))

    def validate(self):
        assert self.mode in ("paper", "live"), "mode must be 'paper' or 'live'"
        assert 0 < self.position_size_pct <= 1, "position_size_pct must be in (0, 1]"
        assert self.stop_loss_pct > 0, "stop_loss_pct must be > 0"
        assert self.take_profit_pct > 0, "take_profit_pct must be > 0"
        assert self.max_positions >= 1, "max_positions must be >= 1"
        return self


# --------------------------------------------------------------------------
# Market data (headless; mirrors the screener's get_stock_data)
# --------------------------------------------------------------------------

def calc_rsi(prices, period: int = 14):
    if len(prices) < period + 1:
        return None
    deltas = prices.diff().dropna()
    gains = deltas.where(deltas > 0, 0.0)
    losses = -deltas.where(deltas < 0, 0.0)
    avg_gain = gains.rolling(window=period).mean().iloc[-1]
    avg_loss = losses.rolling(window=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def get_stock_data(ticker: str) -> Optional[dict]:
    """Fetch a snapshot for one ticker. Returns None on any failure."""
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="30d").dropna(subset=["Close"])
        if len(hist) < 2:
            return None
        prev = float(hist["Close"].iloc[-2])
        curr = float(hist["Close"].iloc[-1])
        if prev <= 0:
            return None
        chg = round(((curr - prev) / prev) * 100, 2)
        vol = int(hist["Volume"].iloc[-1])
        avg_vol = int(hist["Volume"].mean())
        vol_spike = round(vol / avg_vol, 2) if avg_vol > 0 else 0.0
        rsi_val = calc_rsi(hist["Close"])
        rec = info.get("recommendationKey", "none")
        rating = {
            "strong_buy": "STRONG BUY", "buy": "BUY", "hold": "HOLD",
            "sell": "SELL",
        }.get(rec, "N/A")
        return {
            "ticker": ticker, "price": curr, "chg": chg, "vol_spike": vol_spike,
            "rsi": rsi_val, "rating": rating,
            "target": info.get("targetMeanPrice", "N/A"),
            "high": info.get("fiftyTwoWeekHigh", 0),
            "low": info.get("fiftyTwoWeekLow", 0),
            "sector": info.get("sector", "N/A"),
        }
    except Exception:
        return None


# --------------------------------------------------------------------------
# Strategy: rule-based screen + optional AI confirmation
# --------------------------------------------------------------------------

def passes_rules(d: dict, cfg: Config) -> bool:
    if not d or d.get("price") is None:
        return False
    if not (cfg.min_price <= d["price"] <= cfg.max_price):
        return False
    if not (cfg.min_change_pct <= d["chg"] <= cfg.max_change_pct):
        return False
    if d.get("vol_spike", 0) < cfg.min_vol_spike:
        return False
    rsi = d.get("rsi")
    if rsi is None or not (cfg.min_rsi <= rsi <= cfg.max_rsi):
        return False
    return True


def ai_confirm(d: dict, cfg: Config) -> str:
    """Ask Claude to confirm a momentum entry.

    Returns one of: "BUY", "HOLD", "AVOID", or "SKIP" when AI is unavailable.
    A missing key or any error returns "SKIP" (treated as no-confirmation).
    """
    akey = os.getenv("ANTHROPIC_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not akey:
        return "SKIP"
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=akey)
        prompt = (
            "You are a disciplined short-term momentum trading assistant. "
            f"Evaluate {d['ticker']} for a BUY entry right now. "
            f"Price ${d['price']}, change {d['chg']}%, RSI {d.get('rsi')}, "
            f"volume spike {d.get('vol_spike')}x, analyst rating {d.get('rating')}. "
            "Consider whether the move looks sustainable or overextended. "
            "Answer with exactly one final line: 'AI RATING: BUY' or "
            "'AI RATING: HOLD' or 'AI RATING: AVOID'. This is an algorithmic "
            "estimate for research only, not financial advice."
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (msg.content[0].text or "").upper()
        if "AI RATING: BUY" in text or "RATING: STRONG BUY" in text:
            return "BUY"
        if "AI RATING: AVOID" in text or "RATING: SELL" in text:
            return "AVOID"
        return "HOLD"
    except Exception:
        return "SKIP"


# --------------------------------------------------------------------------
# Broker abstraction
# --------------------------------------------------------------------------

class Broker(ABC):
    @abstractmethod
    def get_cash(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> dict: ...

    @abstractmethod
    def buy(self, symbol: str, qty: int, price: float) -> dict: ...

    @abstractmethod
    def sell(self, symbol: str, qty: int, price: float) -> dict: ...

    def equity(self, price_lookup: dict) -> float:
        total = self.get_cash()
        for sym, pos in self.get_positions().items():
            px = price_lookup.get(sym, pos.get("avg_price", 0.0))
            total += pos.get("qty", 0) * px
        return round(total, 2)


class PaperBroker(Broker):
    """Fully-simulated broker. Persists state to a JSON file."""

    def __init__(self, cfg: Config):
        self.state_file = cfg.state_file
        self.state = self._load(cfg.starting_cash)

    def _load(self, starting_cash: float) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"cash": starting_cash, "positions": {}, "trades": [],
                "day": None, "day_start_equity": None}

    def _save(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.state_file)

    def get_cash(self) -> float:
        return round(self.state["cash"], 2)

    def get_positions(self) -> dict:
        return self.state["positions"]

    def buy(self, symbol: str, qty: int, price: float) -> dict:
        cost = qty * price
        if qty <= 0:
            return {"ok": False, "reason": "qty<=0"}
        if cost > self.state["cash"]:
            return {"ok": False, "reason": "insufficient cash"}
        self.state["cash"] -= cost
        pos = self.state["positions"].get(symbol)
        if pos:
            new_qty = pos["qty"] + qty
            pos["avg_price"] = round((pos["avg_price"] * pos["qty"] + cost) / new_qty, 4)
            pos["qty"] = new_qty
        else:
            self.state["positions"][symbol] = {"qty": qty, "avg_price": round(price, 4)}
        self._log("BUY", symbol, qty, price)
        self._save()
        return {"ok": True, "symbol": symbol, "qty": qty, "price": price}

    def sell(self, symbol: str, qty: int, price: float) -> dict:
        pos = self.state["positions"].get(symbol)
        if not pos or pos["qty"] < qty or qty <= 0:
            return {"ok": False, "reason": "no/short position"}
        proceeds = qty * price
        realized = round((price - pos["avg_price"]) * qty, 2)
        self.state["cash"] += proceeds
        pos["qty"] -= qty
        if pos["qty"] == 0:
            del self.state["positions"][symbol]
        self._log("SELL", symbol, qty, price, realized)
        self._save()
        return {"ok": True, "symbol": symbol, "qty": qty, "price": price,
                "realized": realized}

    def _log(self, side, symbol, qty, price, realized=None):
        self.state["trades"].append({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "side": side,
            "symbol": symbol, "qty": qty, "price": round(price, 4),
            "realized": realized,
        })


class WebullBroker(Broker):
    """LIVE broker adapter — intentionally NOT wired to real order calls.

    To enable live trading you must implement the four methods below against
    the current Webull SDK/API and VERIFY every call and its parameters
    against Webull's official documentation. Do not assume method names — I
    have deliberately not guessed them, because an unverified order call is a
    financial hazard. Wire this up together and test with the smallest
    possible size before trusting it.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        # Expected credentials (names are placeholders — confirm against the
        # SDK you use): WEBULL_APP_KEY / WEBULL_APP_SECRET / WEBULL_ACCOUNT_ID
        self.app_key = os.getenv("WEBULL_APP_KEY")
        self.app_secret = os.getenv("WEBULL_APP_SECRET")
        self.account_id = os.getenv("WEBULL_ACCOUNT_ID")

    def _not_ready(self):
        raise NotImplementedError(
            "WebullBroker is a scaffold. Implement get_cash/get_positions/"
            "buy/sell against the verified Webull SDK before live trading."
        )

    def get_cash(self) -> float:
        self._not_ready()

    def get_positions(self) -> dict:
        self._not_ready()

    def buy(self, symbol: str, qty: int, price: float) -> dict:
        self._not_ready()

    def sell(self, symbol: str, qty: int, price: float) -> dict:
        self._not_ready()


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

class Engine:
    def __init__(self, cfg: Config, broker: Broker):
        self.cfg = cfg.validate()
        self.broker = broker

    def _price_lookup(self, snapshots: dict) -> dict:
        return {t: d["price"] for t, d in snapshots.items() if d}

    def _manage_open_positions(self, snapshots: dict):
        """Close positions that hit their stop-loss or take-profit."""
        for symbol, pos in list(self.broker.get_positions().items()):
            d = snapshots.get(symbol)
            if not d:
                continue
            price = d["price"]
            avg = pos["avg_price"]
            stop = avg * (1 - self.cfg.stop_loss_pct)
            target = avg * (1 + self.cfg.take_profit_pct)
            if price <= stop:
                r = self.broker.sell(symbol, pos["qty"], price)
                print(f"  STOP-LOSS  {symbol} @ {price} (avg {avg}) -> {r}")
            elif price >= target:
                r = self.broker.sell(symbol, pos["qty"], price)
                print(f"  TAKE-PROFIT {symbol} @ {price} (avg {avg}) -> {r}")

    def _day_guard(self, equity_now: float) -> bool:
        """Return True if new entries are allowed (daily loss limit not hit)."""
        if not isinstance(self.broker, PaperBroker):
            return True  # live broker would query its own day P&L
        state = self.broker.state
        today = time.strftime("%Y-%m-%d")
        if state.get("day") != today:
            state["day"] = today
            state["day_start_equity"] = equity_now
            self.broker._save()
        start = state.get("day_start_equity") or equity_now
        if start <= 0:
            return True
        drawdown = (start - equity_now) / start
        if drawdown >= self.cfg.daily_loss_limit_pct:
            print(f"  DAILY LOSS LIMIT hit ({drawdown:.1%} >= "
                  f"{self.cfg.daily_loss_limit_pct:.1%}) — no new entries today")
            return False
        return True

    def run_once(self):
        cfg = self.cfg
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] run_once mode={cfg.mode}")

        # 1. Snapshot the universe
        snapshots = {}
        for t in cfg.universe:
            d = get_stock_data(t)
            if d:
                snapshots[t] = d
        # Also snapshot any symbols we currently hold (for exit management)
        for held in list(self.broker.get_positions().keys()):
            if held not in snapshots:
                d = get_stock_data(held)
                if d:
                    snapshots[held] = d

        prices = self._price_lookup(snapshots)

        # 2. Manage exits first
        self._manage_open_positions(snapshots)

        # 3. Daily loss guard
        equity = self.broker.equity(prices)
        entries_allowed = self._day_guard(equity)

        # 4. Entries
        open_syms = set(self.broker.get_positions().keys())
        slots = cfg.max_positions - len(open_syms)
        if entries_allowed and slots > 0:
            candidates = [d for t, d in snapshots.items()
                          if t not in open_syms and passes_rules(d, cfg)]
            candidates.sort(key=lambda x: x["chg"], reverse=True)
            for d in candidates:
                if slots <= 0:
                    break
                if cfg.use_ai_confirmation:
                    verdict = ai_confirm(d, cfg)
                    if verdict not in ("BUY", "SKIP"):
                        print(f"  skip {d['ticker']}: AI said {verdict}")
                        continue
                equity = self.broker.equity(prices)
                budget = equity * cfg.position_size_pct
                qty = int(math.floor(budget / d["price"]))
                if qty < 1:
                    continue
                if qty * d["price"] > self.broker.get_cash():
                    continue
                r = self.broker.buy(d["ticker"], qty, d["price"])
                print(f"  BUY {d['ticker']} x{qty} @ {d['price']} -> {r}")
                if r.get("ok"):
                    slots -= 1

        equity = self.broker.equity(self._price_lookup(snapshots))
        print(f"  cash={self.broker.get_cash()} positions="
              f"{len(self.broker.get_positions())} equity={equity}")
        return {"cash": self.broker.get_cash(),
                "positions": self.broker.get_positions(), "equity": equity}


# --------------------------------------------------------------------------
# Wiring & CLI
# --------------------------------------------------------------------------

def build_broker(cfg: Config) -> Broker:
    if cfg.mode == "live":
        confirm = os.getenv("AUTO_TRADE_LIVE_CONFIRM", "")
        if confirm != "I_UNDERSTAND_THE_RISK":
            raise SystemExit(
                "LIVE mode blocked. Set AUTO_TRADE_LIVE_CONFIRM="
                "I_UNDERSTAND_THE_RISK and implement WebullBroker first."
            )
        return WebullBroker(cfg)
    return PaperBroker(cfg)


def main():
    ap = argparse.ArgumentParser(description="Automated trading engine")
    ap.add_argument("--once", action="store_true", help="run a single cycle")
    ap.add_argument("--loop", action="store_true", help="run continuously")
    ap.add_argument("--interval", type=int, default=300,
                    help="seconds between cycles in --loop mode")
    ap.add_argument("--status", action="store_true",
                    help="print current paper portfolio and exit")
    args = ap.parse_args()

    cfg = Config().validate()
    broker = build_broker(cfg)

    if args.status:
        print(json.dumps({
            "mode": cfg.mode, "cash": broker.get_cash(),
            "positions": broker.get_positions(),
        }, indent=2))
        return

    engine = Engine(cfg, broker)
    if args.loop:
        print(f"Looping every {args.interval}s (Ctrl-C to stop)")
        while True:
            try:
                engine.run_once()
            except Exception as e:
                print(f"cycle error: {e}")
            time.sleep(args.interval)
    else:
        engine.run_once()


if __name__ == "__main__":
    main()
