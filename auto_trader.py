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
    import logging as _logging
    _logging.getLogger("dotenv").setLevel(_logging.ERROR)  # silence parse warnings
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
    """Resolve a secret with the sidecar file taking priority, then the
    environment, then Streamlit secrets.

    The sidecar file (e.g. .webull_app_key next to this module) is a
    manually-corrected store for values whose characters break .env parsing
    (a leading '#'/'$' or an embedded quote). It wins so a stale or truncated
    value in .env or .streamlit/secrets.toml can't override the corrected one.
    Only Webull/Supabase creds ever have a sidecar; everything else falls
    through to env/Streamlit unchanged. Works both in the app and headless."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "." + name.lower())) as f:
            data = f.read().strip()
            if data:
                return data
    except Exception:
        pass
    val = os.getenv(name)
    if val:
        return val
    try:
        import streamlit as st
        v = st.secrets.get(name, None)
        if v:
            return v
    except Exception:
        pass
    return None


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
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD",
    "ORCL", "PLTR", "CRM", "SNOW", "DDOG", "NET", "ARM", "SMCI", "SOFI",
    "MSTR", "COIN", "NFLX", "DIS", "ROKU", "SPOT", "UBER", "ABNB", "SQ",
    "PYPL", "HOOD", "NU", "V", "MA", "JPM", "BAC", "WFC", "GS", "MS", "XOM",
    "CVX", "COP", "OXY", "JNJ", "PFE", "MRNA", "LLY", "ABBV", "BMY", "MRK",
    "AMGN", "COST", "WMT", "TGT", "HD", "LOW", "BA", "LMT", "RTX", "NOC",
    "NIO", "RIVN", "LCID", "XPEV", "F", "GM", "INTC", "QCOM", "MU", "AMAT",
    "KLAC", "TXN", "ADI", "MRVL", "ENPH", "FSLR", "ALAB", "AEHR", "IOT",
    "COHR", "SITM", "MARA", "RIOT", "CRWD", "PANW", "ZM", "SHOP", "BABA",
    "JD", "PDD", "RKLB", "ASTS", "GME", "AMC", "IREN", "CLSK", "HUT", "IONQ",
    "RGTI", "QUBT", "ACHR", "JOBY", "LYFT", "ASML", "AXON", "VRTX", "REGN",
    "BIIB", "ILMN", "ALNY", "CRSP", "BEAM", "NTLA", "JAZZ",
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
    # Absolute daily loss cap in dollars. When > 0 it takes precedence over the
    # percentage limit. Halts NEW entries once the day's loss reaches this.
    daily_loss_limit_usd: float = field(default_factory=lambda: _env_float("AUTO_TRADE_DAILY_LOSS_USD", 25.0))

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


def ai_postmortem(record: dict) -> Optional[str]:
    """One-sentence 'why it won/lost + a lesson' for a closed trade.

    Runs whenever an ANTHROPIC_KEY is available (independent of the entry-side
    AI confirmation flag). Returns None if unavailable. This does NOT change the
    strategy on its own — it's a journal note for a human to learn from.
    """
    akey = os.getenv("ANTHROPIC_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not akey:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=akey)
        outcome = "made a PROFIT" if record.get("pnl", 0) >= 0 else "took a LOSS"
        meta = record.get("entry_meta", {}) or {}
        prompt = (
            "A short-term paper momentum trade just closed. "
            f"Symbol {record.get('symbol')}: bought at ${record.get('entry_price')}, "
            f"sold at ${record.get('exit_price')} ({record.get('exit_reason')}), "
            f"P&L ${record.get('pnl')} ({record.get('pnl_pct')}%). "
            f"Entry conditions were: change {meta.get('chg')}%, RSI {meta.get('rsi')}, "
            f"volume spike {meta.get('vol_spike')}x. "
            f"In ONE plain sentence, say why this trade {outcome} and one lesson for "
            "next time. Research only, not financial advice."
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        return (msg.content[0].text or "").strip() or None
    except Exception:
        return None


def get_bars_since(ticker: str, since_epoch: float) -> Optional[list]:
    """5-minute regular-session bars that end after `since_epoch`, oldest
    first, as [(open, high, low)]. Returns None if the download fails."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d", interval="5m")
        hist = hist.dropna(subset=["Open", "High", "Low"])
    except Exception:
        return None
    return [(float(r["Open"]), float(r["High"]), float(r["Low"]))
            for ts, r in hist.iterrows() if ts.timestamp() + 300 > since_epoch]


def resting_exit(bars: list, stop: float, target: float):
    """Where a resting stop-loss / take-profit pair would have filled across
    `bars`. A gap through either level fills at the bar's open; when one bar
    touches both levels the stop is assumed first (conservative).
    Returns (fill_price, reason) or None."""
    for o, h, l in bars:
        if o <= stop:
            return o, "STOP-LOSS"
        if o >= target:
            return o, "TAKE-PROFIT"
        if l <= stop:
            return stop, "STOP-LOSS"
        if h >= target:
            return target, "TAKE-PROFIT"
    return None


def _close_on(series, day):
    """Last close on or before `day` (a date) from a yfinance Close series."""
    import pandas as pd
    idx = series.index.tz_localize(None) if series.index.tz is not None else series.index
    prior = series[idx.normalize() <= pd.Timestamp(day)]
    return float(prior.iloc[-1]) if len(prior) else None


def benchmark_report(state: dict, symbol: str = "QQQ") -> list:
    """Compare the paper account with simply holding `symbol` over the same
    period, and each closed trade with `symbol` over that trade's holding
    window (daily closes, so per-trade figures are approximate)."""
    import pandas as pd
    import yfinance as yf
    journal = state.get("journal", [])
    positions = state.get("positions", {})
    days = sorted(str(x.get("entry_ts"))[:10] for x in
                  list(journal) + list(positions.values()) if x.get("entry_ts"))
    if not days:
        return []
    first = pd.Timestamp(days[0]).date()
    start = (pd.Timestamp(first) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    bench = yf.Ticker(symbol).history(start=start)["Close"].dropna()
    b0 = _close_on(bench, first)
    if not b0 or bench.empty:
        return [f"(could not load {symbol} prices for the benchmark)"]

    # Starting cash = cash now + cost of open positions - all realized P&L.
    realized = sum((t.get("realized") or 0) for t in state.get("trades", [])
                   if t.get("side") == "SELL")
    cost = sum(p["qty"] * p["avg_price"] for p in positions.values())
    start_cash = state.get("cash", 0.0) + cost - realized
    unrealized, missing = 0.0, []
    for sym, p in positions.items():
        h = yf.Ticker(sym).history(period="5d")["Close"].dropna()
        if h.empty:
            missing.append(sym)
            continue
        unrealized += (float(h.iloc[-1]) - p["avg_price"]) * p["qty"]
    equity = start_cash + realized + unrealized
    acct = (equity / start_cash - 1) * 100 if start_cash else 0.0
    bret = (float(bench.iloc[-1]) / b0 - 1) * 100

    lines = [f"=== vs {symbol} (since first entry {first}) ===",
             f"account: {acct:+.2f}%  (realized ${realized:.2f}, "
             f"open positions ${unrealized:+.2f})",
             f"{symbol} buy-and-hold: {bret:+.2f}%  ->  difference "
             f"{acct - bret:+.2f} pts"]
    if missing:
        lines.append(f"(no price for {', '.join(missing)}; excluded from open P&L)")
    excess = []
    for x in journal:
        q0 = _close_on(bench, pd.Timestamp(str(x.get("entry_ts"))[:10]).date())
        q1 = _close_on(bench, pd.Timestamp(str(x.get("exit_ts"))[:10]).date())
        if q0 and q1 and x.get("pnl_pct") is not None:
            excess.append(x["pnl_pct"] - (q1 / q0 - 1) * 100)
    if excess:
        lines.append(f"closed trades: avg {sum(excess) / len(excess):+.2f} pts "
                     f"better than {symbol} over the same days "
                     f"({sum(e > 0 for e in excess)}/{len(excess)} beat it)")
    return lines


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
    def buy(self, symbol: str, qty: int, price: float, entry_meta: dict = None) -> dict: ...

    @abstractmethod
    def sell(self, symbol: str, qty: int, price: float, exit_reason: str = None) -> dict: ...

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

    def buy(self, symbol: str, qty: int, price: float, entry_meta: dict = None) -> dict:
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
            self.state["positions"][symbol] = {
                "qty": qty, "avg_price": round(price, 4),
                "entry_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "entry_meta": entry_meta or {},
                # bars after this moment are checked against the resting
                # stop / take-profit on the next cycle
                "checked_at": time.time(),
            }
        self._log("BUY", symbol, qty, price)
        self._save()
        return {"ok": True, "symbol": symbol, "qty": qty, "price": price}

    def sell(self, symbol: str, qty: int, price: float, exit_reason: str = None) -> dict:
        pos = self.state["positions"].get(symbol)
        if not pos or pos["qty"] < qty or qty <= 0:
            return {"ok": False, "reason": "no/short position"}
        proceeds = qty * price
        avg = pos["avg_price"]
        realized = round((price - avg) * qty, 2)
        pnl_pct = round((price / avg - 1) * 100, 2) if avg else 0.0
        self.state["cash"] += proceeds
        pos["qty"] -= qty
        closed_record = None
        if pos["qty"] == 0:
            closed_record = {
                "symbol": symbol, "qty": qty,
                "entry_price": avg, "entry_ts": pos.get("entry_ts"),
                "entry_meta": pos.get("entry_meta", {}),
                "exit_price": round(price, 4),
                "exit_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "exit_reason": exit_reason, "pnl": realized, "pnl_pct": pnl_pct,
                "lesson": None,
            }
            self.state.setdefault("journal", []).append(closed_record)
            del self.state["positions"][symbol]
        self._log("SELL", symbol, qty, price, realized)
        self._save()
        return {"ok": True, "symbol": symbol, "qty": qty, "price": price,
                "realized": realized, "pnl_pct": pnl_pct,
                "closed_record": closed_record}

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

    def _build_stop_order(self, symbol: str, qty: int, stop_price: float) -> dict:
        """Protective sell stop, resting at the broker until filled or
        cancelled (GTC), per Webull's STOP_LOSS order example."""
        import uuid
        return {
            "combo_type": "NORMAL",
            "client_order_id": uuid.uuid4().hex,
            "symbol": symbol,
            "instrument_type": "EQUITY",
            "market": os.getenv("WEBULL_MARKET", "US"),
            "order_type": "STOP_LOSS",
            "stop_price": f"{stop_price:.2f}",
            "quantity": str(int(qty)),
            "support_trading_session": "CORE",
            "side": "SELL",
            "time_in_force": "GTC",
            "entrust_type": "QTY",
        }

    @staticmethod
    def _require_armed():
        if os.getenv("WEBULL_ARM_LIVE_ORDERS", "").strip().upper() != "YES":
            raise RuntimeError(
                "Live orders are DISARMED. Set WEBULL_ARM_LIVE_ORDERS=YES only "
                "after a successful --webull-test and a manual smallest-size "
                "test order."
            )

    def _submit(self, order: dict) -> dict:
        self._require_armed()
        res = self._client().order_v3.place_order(self.account_id, [order])
        ok = getattr(res, "status_code", None) == 200
        body = res.json() if hasattr(res, "json") else res
        return {"ok": ok, "response": body}

    def _place(self, symbol: str, qty: int, price: float, side: str) -> dict:
        r = self._submit(self._build_order(symbol, qty, price, side))
        return {**r, "symbol": symbol, "qty": qty, "price": price, "side": side}

    def place_stop(self, symbol: str, qty: int, stop_price: float) -> dict:
        order = self._build_stop_order(symbol, qty, stop_price)
        r = self._submit(order)
        return {**r, "symbol": symbol, "qty": qty, "stop": stop_price,
                "client_order_id": order["client_order_id"]}

    def cancel(self, client_order_id: str) -> dict:
        self._require_armed()
        res = self._client().order_v3.cancel_order(self.account_id, client_order_id)
        body = res.json() if hasattr(res, "json") else res
        return {"ok": getattr(res, "status_code", None) == 200, "response": body}

    def buy(self, symbol: str, qty: int, price: float, entry_meta: dict = None) -> dict:
        return self._place(symbol, qty, price, "BUY")  # entry_meta unused live

    def sell(self, symbol: str, qty: int, price: float, exit_reason: str = None) -> dict:
        return self._place(symbol, qty, price, "SELL")  # exit_reason unused live


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

class Engine:
    def __init__(self, cfg: Config, broker: Broker):
        self.cfg = cfg.validate()
        self.broker = broker

    def _price_lookup(self, snapshots: dict) -> dict:
        return {t: d["price"] for t, d in snapshots.items() if d}

    _STOPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               ".auto_trade_stops.json")

    def _sync_protective_stops(self) -> dict:
        """Live only: keep one resting broker stop-loss per open position.
        Returns {symbol: {client_order_id, stop, qty}} for stops in place."""
        try:
            with open(self._STOPS_FILE) as f:
                stops = json.load(f)
        except Exception:
            stops = {}
        positions = self.broker.get_positions()
        for sym in list(stops):
            if sym not in positions:        # stop filled or position sold
                del stops[sym]
        for sym, pos in positions.items():
            if sym in stops:
                continue
            qty = int(pos["qty"])
            if qty < 1:
                continue
            stop_px = round(pos["avg_price"] * (1 - self.cfg.stop_loss_pct), 2)
            try:
                r = self.broker.place_stop(sym, qty, stop_px)
            except Exception as e:
                print(f"  could not place protective stop for {sym}: {e}")
                continue
            if r.get("ok"):
                stops[sym] = {"client_order_id": r["client_order_id"],
                              "stop": stop_px, "qty": qty}
                print(f"  protective STOP placed {sym} x{qty} @ {stop_px}")
            else:
                print(f"  protective stop REJECTED for {sym}: {r.get('response')}")
        try:
            with open(self._STOPS_FILE, "w") as f:
                json.dump(stops, f)
        except Exception:
            pass
        return stops

    def _manage_open_positions(self, snapshots: dict):
        """Close positions that hit their stop-loss or take-profit, and write a
        journal post-mortem for each closed trade.

        Paper: every 5-minute bar since the last check is tested against the
        levels, so fills match what resting orders would have done (a stop
        fills at the stop, or at the open on a gap) instead of at whatever the
        price is when the next cycle happens to run.
        Live: the stop-loss rests at the broker; this loop only takes profit
        (cancelling the resting stop first)."""
        paper = isinstance(self.broker, PaperBroker)
        stops = self._sync_protective_stops() if hasattr(self.broker, "place_stop") else {}
        for symbol, pos in list(self.broker.get_positions().items()):
            d = snapshots.get(symbol)
            if not d:
                continue
            price = d["price"]
            avg = pos["avg_price"]
            stop = avg * (1 - self.cfg.stop_loss_pct)
            target = avg * (1 + self.cfg.take_profit_pct)
            reason = None
            bars = get_bars_since(symbol, pos["checked_at"]) if paper and pos.get("checked_at") else None
            if bars is not None:
                hit = resting_exit(bars, stop, target)
                if hit:
                    price, reason = hit
            elif price <= stop and symbol not in stops:
                reason = "STOP-LOSS"
            elif price >= target:
                reason = "TAKE-PROFIT"
            if paper and not reason and (bars is not None or not pos.get("checked_at")):
                pos["checked_at"] = time.time()
                self.broker._save()
            if reason and symbol in stops:
                try:
                    c = self.broker.cancel(stops[symbol]["client_order_id"])
                except Exception as e:
                    c = {"ok": False, "response": str(e)}
                if not c.get("ok"):
                    print(f"  could not cancel resting stop for {symbol}, "
                          f"not selling this cycle: {c.get('response')}")
                    continue
                del stops[symbol]
                try:
                    with open(self._STOPS_FILE, "w") as f:
                        json.dump(stops, f)
                except Exception:
                    pass
            if reason:
                r = self.broker.sell(symbol, pos["qty"], price, exit_reason=reason)
                print(f"  {reason} {symbol} @ {price} (avg {avg}) "
                      f"P&L ${r.get('realized')} ({r.get('pnl_pct')}%)")
                rec = r.get("closed_record")
                if rec is not None:
                    lesson = ai_postmortem(rec)   # journal note only; no auto-tuning
                    if lesson:
                        rec["lesson"] = lesson
                        self.broker._save()
                        print(f"    lesson: {lesson}")

    def _day_start_equity(self, today: str, equity_now: float) -> float:
        """Return (and persist) the equity at the start of `today`.

        Paper uses the broker's persisted state; live (or any non-paper broker)
        uses a small JSON file next to this module so the day's starting point
        survives across separate process runs.
        """
        if isinstance(self.broker, PaperBroker):
            state = self.broker.state
            if state.get("day") != today:
                state["day"] = today
                state["day_start_equity"] = equity_now
                self.broker._save()
            return state.get("day_start_equity") or equity_now
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".auto_trade_day.json")
        data = {}
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            pass
        if data.get("day") != today:
            data = {"day": today, "day_start_equity": equity_now}
            try:
                with open(path, "w") as f:
                    json.dump(data, f)
            except Exception:
                pass
        return data.get("day_start_equity") or equity_now

    def _day_guard(self, equity_now: float) -> bool:
        """Return True if new entries are allowed (daily loss limit not hit).
        Works for both paper and live brokers."""
        today = time.strftime("%Y-%m-%d")
        start = self._day_start_equity(today, equity_now)
        loss = start - equity_now  # positive means the account is down today

        limit_usd = self.cfg.daily_loss_limit_usd
        if limit_usd and limit_usd > 0:
            if loss >= limit_usd:
                print(f"  DAILY LOSS LIMIT hit (down ${loss:.2f} >= "
                      f"${limit_usd:.2f}) — no new entries today")
                return False
            return True
        if start > 0 and (loss / start) >= self.cfg.daily_loss_limit_pct:
            print(f"  DAILY LOSS LIMIT hit ({loss / start:.1%} >= "
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
                entry_meta = {"chg": d.get("chg"), "rsi": d.get("rsi"),
                              "vol_spike": d.get("vol_spike")}
                r = self.broker.buy(d["ticker"], qty, d["price"], entry_meta=entry_meta)
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
    ap.add_argument("--journal", action="store_true",
                    help="print the paper trade journal (closed trades + "
                         "lessons + win rate) and exit")
    ap.add_argument("--webull-test", action="store_true",
                    help="READ-ONLY: verify Webull credentials (account list "
                         "+ balance). Places no orders.")
    ap.add_argument("--webull-test-order", action="store_true",
                    help="Place ONE small limit order (you confirm with YES). "
                         "Priced not to fill; cancel it in the Webull app.")
    ap.add_argument("--symbol", default="AXTX", help="test-order ticker")
    ap.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    ap.add_argument("--qty", default="1", help="test-order share quantity")
    ap.add_argument("--price", default="1.00",
                    help="test-order LIMIT price (default $1.00 — a BUY here "
                         "sits unfilled on a higher-priced stock)")
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

    if args.webull_test_order:
        qty = int(float(args.qty))
        price = float(args.price)
        print("=== LIVE Webull test order ===")
        print(f"  {args.side} {qty} share(s) of {args.symbol} as a LIMIT at ${price:.2f}")
        print("  This is a REAL order on your Webull account. It is priced so it")
        print("  should NOT fill; you will cancel it in the Webull app afterward.")
        confirm = input("Type YES (capitals) to place this real order: ").strip()
        if confirm != "YES":
            print("Cancelled — no order placed.")
            return
        os.environ["WEBULL_ARM_LIVE_ORDERS"] = "YES"  # armed only after explicit YES
        wb = WebullBroker(Config())
        try:
            if args.side.upper() == "BUY":
                r = wb.buy(args.symbol, qty, price)
            else:
                r = wb.sell(args.symbol, qty, price)
            print("Order response:", json.dumps(r, indent=2, default=str))
            print("\nNow open the Webull app -> Orders. You should see this pending")
            print("order. CANCEL it there to finish the test.")
        except Exception as e:
            print("Order FAILED:", e)
        return

    cfg = Config().validate()
    broker = build_broker(cfg)

    if args.status:
        print(json.dumps({
            "mode": cfg.mode, "cash": broker.get_cash(),
            "positions": broker.get_positions(),
        }, indent=2))
        return

    if args.journal:
        state = getattr(broker, "state", {})
        j = state.get("journal", [])
        wins = [x for x in j if (x.get("pnl") or 0) >= 0]
        realized = round(sum((x.get("pnl") or 0) for x in j), 2)
        print(f"=== TRADE JOURNAL: {len(j)} closed trades ===")
        if j:
            print(f"win rate: {round(100 * len(wins) / len(j))}%  "
                  f"realized P&L: ${realized}")
        for x in j:
            print(f"- {x.get('symbol')}: ${x.get('entry_price')} -> "
                  f"${x.get('exit_price')}  {x.get('pnl_pct')}% "
                  f"({x.get('exit_reason')})  P&L ${x.get('pnl')}")
            if x.get("lesson"):
                print(f"    lesson: {x['lesson']}")
        pos = broker.get_positions()
        print(f"--- open positions ({len(pos)}): "
              f"{', '.join(pos.keys()) if pos else 'none'} ---")
        print(f"cash=${broker.get_cash()}")
        try:
            for line in benchmark_report(state):
                print(line)
        except Exception as e:
            print(f"(benchmark unavailable: {e})")
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
