"""Step 2 -- market data feed.

Everything the screener needs per ticker: spot, 52-week high/low, 50-day simple
moving average, average volume, and the next scheduled earnings date.

Price history comes from Yahoo (free, bulk-downloadable for the whole index in
a handful of requests). Earnings dates are pulled per-ticker and only for names
that already survived the price screen, because that call is slow and the
screen cuts 503 names down to a couple dozen.

Every record carries `source` so the audit log can prove what a decision was
made on.
"""

from __future__ import annotations

import datetime as dt
import json
import warnings
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

from .config import DATA_DIR

warnings.filterwarnings("ignore", category=FutureWarning)

SNAPSHOT_CACHE = DATA_DIR / "snapshots.json"


@dataclass
class Snapshot:
    symbol: str
    spot: float
    high_52w: float
    low_52w: float
    dma50: float
    dma200: float | None
    avg_vol_30d: float
    last_bar: str                      # date of the last bar used
    source: str = "yfinance"
    earnings_date: str | None = None   # ISO date, next scheduled report
    earnings_source: str | None = None
    error: str | None = None

    @property
    def pct_off_high(self) -> float:
        """Positive = this far below the 52-week high."""
        return (self.high_52w - self.spot) / self.high_52w if self.high_52w else 0.0

    @property
    def pct_from_dma50(self) -> float:
        """Signed: negative = below the 50-day average."""
        return (self.spot - self.dma50) / self.dma50 if self.dma50 else 0.0


def _chunks(seq: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _from_history(symbol: str, hist: pd.DataFrame) -> Snapshot | None:
    hist = hist.dropna(subset=["Close"])
    if len(hist) < 60:                      # need 50 bars for the average, plus slack
        return None
    close = hist["Close"].astype(float)
    high = hist["High"].astype(float)
    low = hist["Low"].astype(float)
    vol = hist["Volume"].astype(float) if "Volume" in hist else pd.Series([0.0])
    return Snapshot(
        symbol=symbol,
        spot=float(close.iloc[-1]),
        high_52w=float(high.tail(252).max()),
        low_52w=float(low.tail(252).min()),
        dma50=float(close.rolling(50).mean().iloc[-1]),
        dma200=float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None,
        avg_vol_30d=float(vol.tail(30).mean()),
        last_bar=str(pd.Timestamp(hist.index[-1]).date()),
    )


def fetch_snapshots(symbols: list[str], period: str = "1y",
                    batch_size: int = 40, progress=None) -> dict[str, Snapshot]:
    """Bulk-download price history and reduce it to one Snapshot per symbol."""
    out: dict[str, Snapshot] = {}
    batches = list(_chunks(list(symbols), batch_size))
    for i, batch in enumerate(batches, 1):
        try:
            raw = yf.download(batch, period=period, interval="1d", group_by="ticker",
                              auto_adjust=False, progress=False, threads=True,
                              actions=False)
        except Exception as exc:                      # whole batch failed
            for s in batch:
                out[s] = Snapshot(s, 0, 0, 0, 0, None, 0, "", error=f"download: {exc}")
            continue
        for sym in batch:
            try:
                hist = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
                snap = _from_history(sym, hist)
                if snap:
                    out[sym] = snap
            except Exception:
                continue
        if progress:
            progress(i, len(batches), len(out))
    return out


def _clean_date(val) -> str | None:
    if val is None:
        return None
    try:
        ts = pd.Timestamp(val)
        return None if pd.isna(ts) else str(ts.date())
    except Exception:
        return None


def next_earnings_date(symbol: str) -> tuple[str | None, str]:
    """Next scheduled earnings date, ISO. Returns (date, source-or-reason).

    Yahoo exposes this two ways and both go stale for some tickers -- callers
    must treat a None as 'unknown', not as 'no earnings', and say so.
    """
    today = pd.Timestamp(dt.date.today())
    tk = yf.Ticker(symbol)
    try:
        cal = tk.calendar
        val = None
        if isinstance(cal, dict):
            val = cal.get("Earnings Date")
            if isinstance(val, (list, tuple)):
                val = val[0] if val else None
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            val = cal.loc["Earnings Date"].iloc[0]
        d = _clean_date(val)
        if d and pd.Timestamp(d) >= today:
            return d, "yfinance.calendar"
    except Exception:
        pass
    try:
        df = tk.get_earnings_dates(limit=16)
        if df is not None and len(df):
            idx = pd.to_datetime(df.index).tz_localize(None)
            future = sorted(x for x in idx if x >= today)
            if future:
                return str(future[0].date()), "yfinance.earnings_dates"
    except Exception:
        pass
    return None, "unknown"


def attach_earnings(snapshots: dict[str, Snapshot], symbols: list[str]) -> None:
    """Fill in earnings dates in place, for the given (already screened) names."""
    for sym in symbols:
        snap = snapshots.get(sym)
        if snap is None:
            continue
        snap.earnings_date, snap.earnings_source = next_earnings_date(sym)


def live_quote(symbols: list[str]) -> dict[str, float]:
    """Intraday last price, for marking positions between daily bars."""
    out: dict[str, float] = {}
    try:
        raw = yf.download(symbols, period="1d", interval="1m", group_by="ticker",
                          auto_adjust=False, progress=False, threads=True)
        for s in symbols:
            try:
                col = raw[s]["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
                col = col.dropna()
                if len(col):
                    out[s] = float(col.iloc[-1])
            except Exception:
                continue
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# snapshot cache
# ---------------------------------------------------------------------------
# Downloading a year of history for 500 names takes minutes. Within one
# session the bars do not change, so cache them and let the caller decide how
# stale is too stale. Earnings dates are deliberately not cached -- they are
# cheap relative to the risk of trading on a moved report date.
def save_cache(snaps: dict[str, Snapshot], path: Path = SNAPSHOT_CACHE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "snapshots": {k: asdict(v) for k, v in snaps.items()},
    }, indent=2))
    return path


def load_cache(max_age_min: float, path: Path = SNAPSHOT_CACHE
               ) -> tuple[dict[str, Snapshot], str] | None:
    """Return (snapshots, age-note) if the cache is fresh enough, else None."""
    if not path.exists():
        return None
    blob = json.loads(path.read_text())
    try:
        age = (dt.datetime.now() - dt.datetime.fromisoformat(blob["at"])).total_seconds() / 60
    except Exception:
        return None
    if age > max_age_min:
        return None
    snaps = {k: Snapshot(**v) for k, v in blob["snapshots"].items()}
    return snaps, f"reusing price snapshots cached {age:.0f} min ago ({blob['at']})"
