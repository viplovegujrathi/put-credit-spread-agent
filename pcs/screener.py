"""Step 3 -- the screen (section 1.2).

Two hard conditions, both required: meaningfully off the 52-week high, AND
near the 50-day average. The 50dma test is the one most likely to get blurred,
so this module never collapses the cases -- every name lands in exactly one
labelled bucket and the buckets are reported separately, never merged into one
table.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

import pandas as pd

from .config import STRATEGY, Settings
from .marketdata import Snapshot
from .universe import Universe

# Buckets, in the order the report presents them.
PRIMARY = "PRIMARY"                      # 3-8% below the 50dma -- the intended setup
NEAR_TIGHT = "NEAR_BELOW_TIGHT"          # 0-3% below -- near, but little pullback
STRETCHED = "BELOW_STRETCHED"            # 8-12% below -- marginal, excluded by default
BROKEN = "BROKEN_DOWN"                   # >12% below -- excluded, different strategy
ABOVE = "ABOVE_50DMA"                    # above -- recovery/continuation, different thesis
NOT_BEATEN = "NOT_BEATEN_DOWN"           # <15% off the high
ILLIQUID = "ILLIQUID_UNDERLYING"
NO_DATA = "NO_DATA"

TRADEABLE = {PRIMARY}                    # what the optimizer is allowed to size
REVIEW = {NEAR_TIGHT}                    # surfaced separately for the user to opt in

_BUCKET_NOTE = {
    PRIMARY: "3-8% below the 50dma - pulling back to test support from underneath",
    NEAR_TIGHT: "within 3% below the 50dma - near the average but barely pulled back",
    STRETCHED: "8-12% below the 50dma - past the intended band, excluded by default",
    BROKEN: "more than 12% below the 50dma - broken down, not 'near the average'",
    ABOVE: "above the 50dma - recovery/continuation, a different thesis (not this screen)",
    NOT_BEATEN: "less than 15% off the 52-week high",
    ILLIQUID: "underlying average volume too low to expect a tradeable option chain",
    NO_DATA: "no usable price history",
}


@dataclass
class Candidate:
    symbol: str
    name: str
    sector: str
    bucket: str
    note: str
    spot: float
    dma50: float
    pct_from_dma50: float
    pct_off_high: float
    high_52w: float
    low_52w: float
    avg_vol_30d: float
    last_bar: str
    earnings_date: str | None = None
    earnings_source: str | None = None
    earnings_in_window: bool | None = None   # None = unknown, not "no"

    def as_dict(self) -> dict:
        return asdict(self)


def classify(pct_from_dma50: float, pct_off_high: float) -> str:
    s = STRATEGY
    if pct_off_high < s.min_pct_off_52w_high:
        return NOT_BEATEN
    p = pct_from_dma50
    if p > 0:
        return ABOVE
    if s.primary_band[0] <= p <= s.primary_band[1]:
        return PRIMARY
    if s.tight_band[0] < p <= s.tight_band[1]:
        return NEAR_TIGHT
    if s.stretched_band[0] <= p < s.stretched_band[1]:
        return STRETCHED
    return BROKEN


def screen(snapshots: dict[str, Snapshot], universe: Universe,
           settings: Settings, min_avg_vol: float = 300_000) -> list[Candidate]:
    """Classify every name in the universe. Returns all of them, bucketed --
    nothing is dropped silently, so exclusions stay auditable."""
    meta = universe.frame.set_index("symbol")
    out: list[Candidate] = []
    for sym in universe.symbols:
        info = meta.loc[sym] if sym in meta.index else None
        name = str(info["name"]) if info is not None else sym
        sector = str(info["sector"]) if info is not None else "Unknown"
        snap = snapshots.get(sym)
        if snap is None or snap.error or not snap.dma50 or not snap.high_52w:
            out.append(Candidate(sym, name, sector, NO_DATA, _BUCKET_NOTE[NO_DATA],
                                 0, 0, 0, 0, 0, 0, 0, ""))
            continue
        bucket = classify(snap.pct_from_dma50, snap.pct_off_high)
        if bucket in (PRIMARY, NEAR_TIGHT) and snap.avg_vol_30d < min_avg_vol:
            bucket = ILLIQUID
        out.append(Candidate(
            symbol=sym, name=name, sector=sector, bucket=bucket,
            note=_BUCKET_NOTE[bucket], spot=snap.spot, dma50=snap.dma50,
            pct_from_dma50=snap.pct_from_dma50, pct_off_high=snap.pct_off_high,
            high_52w=snap.high_52w, low_52w=snap.low_52w,
            avg_vol_30d=snap.avg_vol_30d, last_bar=snap.last_bar,
        ))
    return out


def apply_earnings_filter(candidates: list[Candidate],
                          snapshots: dict[str, Snapshot],
                          expiry: dt.date, settings: Settings) -> None:
    """Mark whether the next scheduled report lands inside the trade window.

    An unknown earnings date stays None -- the report must show it as unknown
    rather than letting a missing date read as a clean pass.
    """
    cutoff = expiry + dt.timedelta(days=settings.earnings_buffer_days)
    for c in candidates:
        snap = snapshots.get(c.symbol)
        if snap is None:
            continue
        c.earnings_date = snap.earnings_date
        c.earnings_source = snap.earnings_source
        if not c.earnings_date:
            c.earnings_in_window = None
        else:
            d = dt.date.fromisoformat(c.earnings_date)
            c.earnings_in_window = d <= cutoff


def to_frame(candidates: list[Candidate]) -> pd.DataFrame:
    return pd.DataFrame([c.as_dict() for c in candidates])


def summary(candidates: list[Candidate]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in candidates:
        counts[c.bucket] = counts.get(c.bucket, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
