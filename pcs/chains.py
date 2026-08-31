"""Step 4 -- option chain fetcher.

Three providers behind one interface, because the strategy doc is emphatic
that sizing must come off real bid/ask, not a model:

  yfinance   default. Free, real bid/ask/OI/volume/IV for the whole index.
  robinhood  snapshots written to data/rh_chains/ by the agent's MCP calls.
             This is the confirmation layer -- the broker's own book, which is
             what an order would actually meet. Required before a live trade.
  model      Black-Scholes fallback. Tagged `modeled` everywhere so it can
             never be mistaken for a quote.

Expirations are read off the live chain, never assumed: "32 days from today"
does not reliably land on a listed Friday.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import yfinance as yf

from .bs import put_price
from .config import RH_CHAIN_DIR, STRATEGY


@dataclass
class PutQuote:
    strike: float
    bid: float
    ask: float
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    iv: float = 0.0
    delta: float | None = None
    basis: str = "live"          # "live" | "modeled"
    # Broker-supplied extras. Robinhood provides all of these; Yahoo none.
    mark: float = 0.0            # the broker's own mark, better than (bid+ask)/2
    theta: float | None = None
    vega: float | None = None
    pop_short: float | None = None   # broker's chance_of_profit_short
    fill_sell: float | None = None   # price the broker expects a sell to fill at
    fill_buy: float | None = None    # ... and a buy

    @property
    def mid(self) -> float:
        """Fair value. Prefers the broker's mark, which already accounts for a
        lopsided book (e.g. 258 bid size vs 26 ask size)."""
        if self.mark > 0:
            return self.mark
        if self.bid > 0 and self.ask > 0:
            return round((self.bid + self.ask) / 2.0, 4)
        return self.last or max(self.bid, self.ask)

    @property
    def spread_abs(self) -> float:
        return max(self.ask - self.bid, 0.0)

    @property
    def spread_pct(self) -> float:
        m = self.mid
        return self.spread_abs / m if m > 0 else 1.0


@dataclass
class PutChain:
    symbol: str
    expiration: str              # ISO date
    spot: float
    puts: list[PutQuote]
    source: str
    fetched_at: str
    basis: str = "live"          # "live" | "modeled"
    error: str | None = None

    @property
    def dte(self) -> int:
        return (dt.date.fromisoformat(self.expiration) - dt.date.today()).days

    def strikes(self) -> list[float]:
        return sorted(q.strike for q in self.puts)

    def at(self, strike: float) -> PutQuote | None:
        for q in self.puts:
            if abs(q.strike - strike) < 1e-6:
                return q
        return None


# ---------------------------------------------------------------------------
# expiration selection
# ---------------------------------------------------------------------------
def pick_expiration(available: list[str], today: dt.date | None = None,
                    target_dte: int | None = None,
                    window: tuple[int, int] | None = None) -> str | None:
    """Nearest listed expiration to the DTE target, preferring Fridays.

    `available` must come from the live chain -- that is the whole point of
    section 1.5's "confirm the exact date against the live chain".
    """
    today = today or dt.date.today()
    target = target_dte or STRATEGY.target_dte
    lo, hi = window or STRATEGY.dte_window
    scored: list[tuple[float, str]] = []
    for iso in available:
        try:
            d = dt.date.fromisoformat(iso)
        except ValueError:
            continue
        dte = (d - today).days
        if not (lo <= dte <= hi):
            continue
        # Friday preferred; a non-Friday listed expiration costs 3 days of score.
        penalty = 0 if d.weekday() == 4 else 3
        scored.append((abs(dte - target) + penalty, iso))
    if not scored:
        return None
    return min(scored)[1]


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------
def _f(v, default=0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def fetch_yfinance(symbol: str, expiration: str | None = None,
                   target_dte: int | None = None) -> PutChain:
    now = dt.datetime.now().isoformat(timespec="seconds")
    try:
        tk = yf.Ticker(symbol)
        exps = list(tk.options or [])
        if not exps:
            return PutChain(symbol, "", 0.0, [], "yfinance", now, error="no expirations listed")
        exp = expiration or pick_expiration(exps, target_dte=target_dte)
        if exp is None:
            return PutChain(symbol, "", 0.0, [], "yfinance", now,
                            error=f"no listed expiration in the DTE window; have {exps[:6]}")
        raw = tk.option_chain(exp).puts
        spot = _f(getattr(tk, "fast_info", {}).get("last_price") if hasattr(tk, "fast_info") else None)
        puts = [
            PutQuote(
                strike=_f(r.get("strike")), bid=_f(r.get("bid")), ask=_f(r.get("ask")),
                last=_f(r.get("lastPrice")), volume=int(_f(r.get("volume"))),
                open_interest=int(_f(r.get("openInterest"))), iv=_f(r.get("impliedVolatility")),
            )
            for r in raw.to_dict("records")
        ]
        return PutChain(symbol, exp, spot, puts, "yfinance", now)
    except Exception as exc:
        return PutChain(symbol, expiration or "", 0.0, [], "yfinance", now, error=str(exc))


def rh_snapshot_path(symbol: str, expiration: str) -> Path:
    return RH_CHAIN_DIR / f"{symbol}_{expiration}.json"


def fetch_robinhood_cache(symbol: str, expiration: str, max_age_min: int = 60) -> PutChain:
    """Read a Robinhood chain snapshot written by the agent's MCP calls.

    Schema (see README): {symbol, expiration, spot, fetched_at, puts:[{strike,
    bid, ask, mark, volume, open_interest, iv, delta}]}
    """
    now = dt.datetime.now().isoformat(timespec="seconds")
    path = rh_snapshot_path(symbol, expiration)
    if not path.exists():
        return PutChain(symbol, expiration, 0.0, [], "robinhood", now,
                        error=f"no snapshot at {path.name} -- run the MCP chain pull first")
    blob = json.loads(path.read_text())
    fetched = blob.get("fetched_at", now)
    try:
        age_min = (dt.datetime.now() - dt.datetime.fromisoformat(fetched)).total_seconds() / 60
    except Exception:
        age_min = 0.0
    puts = [
        PutQuote(
            strike=_f(p.get("strike")), bid=_f(p.get("bid")), ask=_f(p.get("ask")),
            last=_f(p.get("mark") or p.get("last")), volume=int(_f(p.get("volume"))),
            open_interest=int(_f(p.get("open_interest"))), iv=_f(p.get("iv")),
            delta=p.get("delta"), mark=_f(p.get("mark")), theta=p.get("theta"),
            vega=p.get("vega"), pop_short=p.get("pop_short"),
            fill_sell=p.get("fill_sell"), fill_buy=p.get("fill_buy"),
        )
        for p in blob.get("puts", [])
    ]
    err = None if age_min <= max_age_min else f"snapshot is {age_min:.0f} min old"
    return PutChain(symbol, blob.get("expiration", expiration), _f(blob.get("spot")),
                    puts, "robinhood", fetched, error=err)


def fetch_modeled(symbol: str, spot: float, expiration: str, iv: float,
                  strikes: list[float] | None = None) -> PutChain:
    """FALLBACK ONLY -- Black-Scholes puts on a synthetic strike ladder."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    dte = (dt.date.fromisoformat(expiration) - dt.date.today()).days
    if strikes is None:
        step = 5.0 if spot > 100 else (2.5 if spot > 40 else 1.0)
        lo = math.floor(spot * 0.70 / step) * step
        hi = math.ceil(spot * 1.05 / step) * step
        strikes = [lo + i * step for i in range(int((hi - lo) / step) + 1)]
    puts = []
    for k in strikes:
        px = put_price(spot, k, iv, dte)
        puts.append(PutQuote(strike=k, bid=round(px * 0.95, 2), ask=round(px * 1.05, 2),
                             last=round(px, 2), iv=iv, basis="modeled"))
    return PutChain(symbol, expiration, spot, puts, "model:black-scholes", now, basis="modeled")


def get_chain(symbol: str, source: str = "yfinance", expiration: str | None = None,
              target_dte: int | None = None, spot: float = 0.0,
              iv: float = 0.30) -> PutChain:
    if source == "robinhood":
        if not expiration:
            raise ValueError("robinhood snapshots are keyed by expiration")
        return fetch_robinhood_cache(symbol, expiration)
    if source == "model":
        if not expiration:
            raise ValueError("modeled chains need an explicit expiration")
        return fetch_modeled(symbol, spot, expiration, iv)
    return fetch_yfinance(symbol, expiration, target_dte)


def save_chain(chain: PutChain, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = asdict(chain)
    path.write_text(json.dumps(blob, indent=2))
    return path
