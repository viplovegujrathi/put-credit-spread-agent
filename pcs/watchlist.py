"""Step 10 -- the watchlist: what the agent is watching but has not opened.

Everything here is OBSERVATION. This module imports no broker and holds no
reference to a ledger it can mutate: it reads the screen, reads the chain, and
reports. That boundary is the reason a watchlist can refresh around the clock
while opening a position cannot -- watching at 02:00 is free, and filling at
02:00 is a price nobody could have traded on.

Which means the one thing this must never do is present a stale quote as a
tradeable premium. Every entry carries the session grade it was priced under,
and `Watchlist.tradeable` says plainly whether these numbers are live.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field

from . import risk, screener
from .config import DATA_DIR, Settings
from .ledger import Ledger
from .optimizer import Spread

WATCHLIST_JSON = DATA_DIR / "watchlist.json"

# Ordered by how close the name is to being traded. The dashboard sorts on this.
HOLDING = "HOLDING"        # already open -- shown so the book is one list
READY = "READY"            # sized, risk-clear: the next propose run opens it
BLOCKED = "BLOCKED"        # clears the per-trade rules, a portfolio cap says no
EARNINGS = "EARNINGS"      # excluded: earnings inside the expiration window
NO_FIT = "NO_FIT"          # passed the screen, no strike/width clears the rules
NEAR = "NEAR"              # 0-3% below the 50dma: close to the setup, not in it
STRETCHED = "STRETCHED"    # 8-12% below: drifting toward broken-down

_RANK = {HOLDING: 0, READY: 1, BLOCKED: 2, EARNINGS: 3, NO_FIT: 4, NEAR: 5, STRETCHED: 6}

_SIGNAL_NOTE = {
    HOLDING: "already open",
    READY: "clears every rule -- opens on the next run",
    BLOCKED: "a portfolio cap is in the way, not the trade itself",
    EARNINGS: "earnings land inside the expiration window",
    NO_FIT: "no strike and width clears the credit floor at a safe cushion",
    NEAR: "near the 50dma but the pullback is shallow",
    STRETCHED: "further below the 50dma than the setup wants",
}


@dataclass
class Entry:
    symbol: str
    name: str
    sector: str
    signal: str
    reason: str
    bucket: str
    spot: float
    dma50: float
    pct_from_dma50: float
    pct_off_high: float
    # The spread the agent would take, when one exists.
    expiration: str = ""
    dte: int = 0
    short_strike: float = 0.0
    long_strike: float = 0.0
    width: float = 0.0
    credit_dollars: float = 0.0       # the potential premium
    credit_nat_dollars: float = 0.0   # ... at the worst realistic fill
    collateral: float = 0.0
    roc: float = 0.0                  # the rate: credit / collateral
    cushion: float = 0.0
    pop_est: float | None = None
    quote_quality: str = ""
    blockers: list[str] = field(default_factory=list)

    @property
    def rank(self) -> int:
        return _RANK.get(self.signal, 9)

    @property
    def has_spread(self) -> bool:
        return self.short_strike > 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Watchlist:
    generated_at: str
    quote_quality: str
    phase: str
    tradeable: bool          # were these prices live when taken?
    entries: list[Entry]

    def as_dict(self) -> dict:
        return {"generated_at": self.generated_at, "quote_quality": self.quote_quality,
                "phase": self.phase, "tradeable": self.tradeable,
                "entries": [e.as_dict() for e in self.entries]}

    def count(self, signal: str) -> int:
        return sum(1 for e in self.entries if e.signal == signal)


def _from_spread(e: Entry, sp: Spread, contracts: int = 1) -> Entry:
    e.expiration, e.dte = sp.expiration, sp.dte
    e.short_strike, e.long_strike, e.width = sp.short_strike, sp.long_strike, sp.width
    e.credit_dollars = round(sp.credit_dollars * contracts, 2)
    e.credit_nat_dollars = round(sp.credit_nat_dollars * contracts, 2)
    e.collateral = round(sp.collateral * contracts, 2)
    e.roc, e.cushion, e.pop_est = sp.roc, sp.cushion, sp.pop_est
    e.quote_quality = sp.quote_quality
    return e


def build(res, sized: list, led: Ledger, settings: Settings,
          contracts: int = 1) -> Watchlist:
    """Turn a screen + sizing pass into the watchlist.

    `res` is a pipeline.ScreenResult and `sized` a list of SizedCandidate. They
    are not imported for typing because pipeline imports this module's siblings
    and the cycle is not worth the annotation.
    """
    held = {p.symbol for p in led.open_positions}
    pv = risk.PortfolioView(led.collateral_held, len(led.open_positions),
                            led.sector_counts(), held, led.cash, led.buying_power)
    by_symbol = {sc.candidate.symbol: sc for sc in sized}
    entries: list[Entry] = []

    for c in res.candidates:
        if c.bucket not in (screener.PRIMARY, screener.NEAR_TIGHT, screener.STRETCHED):
            continue
        e = Entry(symbol=c.symbol, name=c.name, sector=c.sector, signal=NO_FIT,
                  reason="", bucket=c.bucket, spot=c.spot, dma50=c.dma50,
                  pct_from_dma50=c.pct_from_dma50, pct_off_high=c.pct_off_high)

        if c.symbol in held:
            e.signal = HOLDING
        elif c.bucket == screener.NEAR_TIGHT:
            e.signal = NEAR
        elif c.bucket == screener.STRETCHED:
            e.signal = STRETCHED

        sc = by_symbol.get(c.symbol)
        if sc and sc.spreads:
            _from_spread(e, sc.spreads[0], contracts)
            if e.signal == NO_FIT:      # a primary name with a real spread
                if c.earnings_in_window is not False:
                    e.signal = EARNINGS
                    e.reason = ("earnings date unknown -- treated as inside the window"
                                if c.earnings_in_window is None else "")
                else:
                    v = risk.check(sc.spreads[0], c.sector, pv, settings,
                                   sess=res.session, contracts=contracts)
                    e.signal, e.blockers = (READY, []) if v.ok else (BLOCKED, list(v.reasons))
        elif sc and e.signal == NO_FIT:
            e.reason = sc.chain_error or ""

        e.reason = e.reason or _SIGNAL_NOTE[e.signal]
        entries.append(e)

    entries.sort(key=lambda x: (x.rank, -x.roc, -x.pct_off_high))
    sess = res.session
    return Watchlist(
        generated_at=dt.datetime.now().isoformat(timespec="seconds"),
        quote_quality=sess.quote_quality, phase=sess.phase,
        tradeable=sess.quote_quality == "live", entries=entries)


def save(wl: Watchlist, path=WATCHLIST_JSON):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(wl.as_dict(), indent=2))
    return path


def load(path=WATCHLIST_JSON) -> Watchlist | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return Watchlist(raw["generated_at"], raw.get("quote_quality", ""),
                     raw.get("phase", ""), raw.get("tradeable", False),
                     [Entry(**e) for e in raw.get("entries", [])])
