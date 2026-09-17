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


def _secret(name: str):
    """Read a secret from Streamlit secrets (if running under Streamlit) then
    from the environment. Lets the same code work in the app and headless."""
    try:
        import streamlit as st
        val = st.secrets.get(name, None)
        if val is not None:
            return val
    except Exception:
        pass
    return os.getenv(name)


def _supabase_creds():
    """Return (url, key) with the same normalization the watchlist uses, or
    (None, None) if not configured."""
    url = (_secret("SUPABASE_URL") or "").strip().strip('"').strip("'").rstrip("/")
    key = (_secret("SUPABASE_KEY") or "").strip()
    if not url or not key:
        return None, None
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url, key


# Candidate keys for the account's available cash / buying power, most specific
# first. Used to read the Webull balance response without hard-coding one
# guessed field; if none match, the caller raises and asks for verification.
_CASH_KEYS = ("total_cash_balance", "cash_balance", "settled_funds",
              "available_funds", "day_buying_power", "buying_power", "cash")


def _find_cash_field(obj):
    """Best-effort recursive lookup of a cash-like numeric value in a JSON
    response. Returns a float or None. Verify against a real response before
    trusting it for live position sizing."""
    def as_num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    if isinstance(obj, dict):
        for key in _CASH_KEYS:
            if key in obj:
                n = as_num(obj[key])
                if n is not None:
                    return n
        for v in obj.values():
            found = _find_cash_field(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_cash_field(v)
            if found is not None:
                return found
    return None


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

    # State persistence backend: "auto" (Supabase if creds present, else file),
    # "supabase", or "file".
    state_backend: str = field(default_factory=lambda: os.getenv("AUTO_TRADE_STATE_BACKEND", "auto").strip().lower())
    state_table: str = field(default_factory=lambda: os.getenv("AUTO_TRADE_STATE_TABLE", "auto_trade_state"))
    state_row_id: str = field(default_factory=lambda: os.getenv("AUTO_TRADE_STATE_ID", "paper"))

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
# State persistence (paper portfolio)
# --------------------------------------------------------------------------

def _default_state(starting_cash: float) -> dict:
    return {"cash": starting_cash, "positions": {}, "trades": [],
            "day": None, "day_start_equity": None}


class StateStore(ABC):
    @abstractmethod
    def load(self, default: dict) -> dict: ...

    @abstractmethod
    def save(self, state: dict) -> None: ...


class FileStateStore(StateStore):
    def __init__(self, path: str):
        self.path = path

    def load(self, default: dict) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def save(self, state: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.path)


class SupabaseStateStore(StateStore):
    """Stores the whole paper-state document as one JSONB row.

    Table (create once in Supabase):
        create table if not exists auto_trade_state (
            id text primary key,
            state jsonb not null default '{}'::jsonb,
            updated_at timestamptz default now()
        );
    """

    def __init__(self, url: str, key: str, table: str, row_id: str):
        from supabase import create_client
        self.sb = create_client(url, key)
        self.table = table
        self.row_id = row_id

    def load(self, default: dict) -> dict:
        res = self.sb.table(self.table).select("state").eq("id", self.row_id).execute()
        if res.data:
            return res.data[0]["state"]
        # First run: seed the row with the default state.
        self.sb.table(self.table).upsert({"id": self.row_id, "state": default}).execute()
        return default

    def save(self, state: dict) -> None:
        self.sb.table(self.table).upsert({"id": self.row_id, "state": state}).execute()


def build_state_store(cfg: Config) -> StateStore:
    backend = cfg.state_backend
    if backend == "file":
        return FileStateStore(cfg.state_file)
    url, key = _supabase_creds()
    if url and key:
        return SupabaseStateStore(url, key, cfg.state_table, cfg.state_row_id)
    if backend == "supabase":
        raise RuntimeError(
            "AUTO_TRADE_STATE_BACKEND=supabase but SUPABASE_URL/SUPABASE_KEY "
            "are not set."
        )
    return FileStateStore(cfg.state_file)  # backend == "auto", no creds


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
    """Fully-simulated broker. Persists state via a StateStore (Supabase or file)."""

    def __init__(self, cfg: Config, store: Optional[StateStore] = None):
        self.store = store or build_state_store(cfg)
        self.state = self.store.load(_default_state(cfg.starting_cash))

    def _save(self):
        self.store.save(self.state)

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
    """LIVE broker adapter for the Webull OpenAPI Python SDK.

    Wired against verified SDK calls (webull-openapi-python-sdk):
        api_client = ApiClient(app_key, app_secret, region)
        api_client.add_endpoint(region, endpoint)
        trade_client = TradeClient(api_client)
        trade_client.account_v2.get_account_list()
        trade_client.account_v2.get_account_balance(account_id)
        trade_client.order_v3.place_order(account_id, [order])
        trade_client.order_v3.cancel_order(account_id, client_order_id)

    SAFETY: order placement is DISARMED unless WEBULL_ARM_LIVE_ORDERS=YES, and
    get_positions() intentionally raises until the live positions response has
    been verified — so the automated Engine cannot run live end-to-end until a
    human has confirmed the balance and positions mappings and done a manual
    test order. Read-only methods (test_connection, get_account_balance_raw)
    are safe to call for verifying credentials.

    Credentials (env or Streamlit secrets):
        WEBULL_APP_KEY, WEBULL_APP_SECRET, WEBULL_ACCOUNT_ID,
        WEBULL_REGION (default "us"), WEBULL_API_ENDPOINT (region endpoint).
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.app_key = _secret("WEBULL_APP_KEY")
        self.app_secret = _secret("WEBULL_APP_SECRET")
        self.account_id = _secret("WEBULL_ACCOUNT_ID")
        self.region = (_secret("WEBULL_REGION") or "us").strip().lower()
        self.endpoint = _secret("WEBULL_API_ENDPOINT")
        self._trade_client = None

    def _client(self):
        if self._trade_client is not None:
            return self._trade_client
        if not (self.app_key and self.app_secret and self.account_id):
            raise RuntimeError(
                "Missing Webull credentials. Set WEBULL_APP_KEY, "
                "WEBULL_APP_SECRET, and WEBULL_ACCOUNT_ID (generate the "
                "app key/secret at developer.webull.com)."
            )
        # Import the SDK lazily so the module loads without it installed.
        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient
        api_client = ApiClient(self.app_key, self.app_secret, self.region)
        if self.endpoint:
            api_client.add_endpoint(self.region, self.endpoint)
        self._trade_client = TradeClient(api_client)
        return self._trade_client

    # ---- read-only (safe) ----
    def test_connection(self) -> dict:
        """Verify credentials by listing accounts. No orders. Returns json."""
        res = self._client().account_v2.get_account_list()
        return {"status_code": getattr(res, "status_code", None),
                "body": res.json() if hasattr(res, "json") else res}

    def get_account_balance_raw(self) -> dict:
        res = self._client().account_v2.get_account_balance(self.account_id)
        return {"status_code": getattr(res, "status_code", None),
                "body": res.json() if hasattr(res, "json") else res}

    def get_cash(self) -> float:
        body = self.get_account_balance_raw().get("body", {})
        cash = _find_cash_field(body)
        if cash is None:
            raise RuntimeError(
                "Could not locate a cash/buying-power field in the Webull "
                "balance response. Run `python auto_trader.py --webull-test` "
                "and confirm the exact field before enabling live sizing."
            )
        return float(cash)

    def get_positions(self) -> dict:
        """Map Webull equity positions to {symbol: {qty, avg_price}}.

        Verified against a real account_v2.get_account_position response:
        each item has symbol, quantity, cost_price, instrument_type.
        """
        res = self._client().account_v2.get_account_position(self.account_id)
        body = res.json() if hasattr(res, "json") else res
        items = body if isinstance(body, list) else None
        if items is None and isinstance(body, dict):
            for v in body.values():
                if isinstance(v, list):
                    items = v
                    break
        out = {}
        for p in (items or []):
            if not isinstance(p, dict):
                continue
            if p.get("instrument_type", "EQUITY") != "EQUITY":
                continue
            sym = p.get("symbol")
            try:
                qty = float(p.get("quantity", 0))
                avg = float(p.get("cost_price", 0))
            except (TypeError, ValueError):
                continue
            if not sym or qty <= 0:
                continue
            out[sym] = {"qty": qty, "avg_price": avg}
        return out

    # ---- order placement (DISARMED by default) ----
    def _build_order(self, symbol: str, qty: int, price: float, side: str) -> dict:
        import uuid
        return {
            "combo_type": "NORMAL",
            "client_order_id": uuid.uuid4().hex,
            "symbol": symbol,
            "instrument_type": "EQUITY",
            "market": os.getenv("WEBULL_MARKET", "US"),
            "order_type": "LIMIT",
            "limit_price": str(round(price, 2)),
            "quantity": str(int(qty)),
            "support_trading_session": "CORE",
            "side": side,
            "time_in_force": "DAY",
            "entrust_type": "QTY",
        }

    def _place(self, symbol: str, qty: int, price: float, side: str) -> dict:
        if os.getenv("WEBULL_ARM_LIVE_ORDERS", "").strip().upper() != "YES":
            raise RuntimeError(
                "Live orders are DISARMED. Set WEBULL_ARM_LIVE_ORDERS=YES only "
                "after a successful --webull-test and a manual smallest-size "
                "test order."
            )
        order = self._build_order(symbol, qty, price, side)
        res = self._client().order_v3.place_order(self.account_id, [order])
        ok = getattr(res, "status_code", None) == 200
        body = res.json() if hasattr(res, "json") else res
        return {"ok": ok, "symbol": symbol, "qty": qty, "price": price,
                "side": side, "response": body}

    def buy(self, symbol: str, qty: int, price: float) -> dict:
        return self._place(symbol, qty, price, "BUY")

    def sell(self, symbol: str, qty: int, price: float) -> dict:
        return self._place(symbol, qty, price, "SELL")


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
                "I_UNDERSTAND_THE_RISK. Also verify credentials with "
                "--webull-test and note orders stay disarmed until "
                "WEBULL_ARM_LIVE_ORDERS=YES."
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
    ap.add_argument("--webull-test", action="store_true",
                    help="READ-ONLY: verify Webull credentials (account list "
                         "+ balance). Places no orders.")
    args = ap.parse_args()

    if args.webull_test:
        print("Webull connection test (read-only, no orders):")
        # Show which credentials the code actually sees (lengths, not values).
        for _k in ("WEBULL_APP_KEY", "WEBULL_APP_SECRET", "WEBULL_ACCOUNT_ID"):
            _v = _secret(_k) or ""
            mark = "OK" if _v else "MISSING"
            print(f"  {_k}: {len(str(_v))} chars [{mark}]")
        wb = WebullBroker(Config())
        try:
            print("account_list:", json.dumps(wb.test_connection(), indent=2, default=str))
            print("account_balance:", json.dumps(wb.get_account_balance_raw(), indent=2, default=str))
        except Exception as e:
            print("Webull test FAILED:", e)
        return

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
