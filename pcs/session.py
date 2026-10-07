"""US equity/option session awareness.

Quote quality is not constant through the day, and the difference matters:
a 2.35/3.25 market at the closing bell is a real market, while the same market
at 18:00 ET tells you nothing about what an order would fill at tomorrow. The
agent labels every run with the session state so a proposal built on a stale
book is never presented as though it were a live one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# The first minutes after the bell are the widest, thinnest book of the day:
# the opening auction is still clearing, overnight orders are being absorbed,
# and spreads have not converged. Positions wait it out -- paper included, so
# the paper record reflects fills the live account could actually have gotten.
OPENING_SETTLE_MINUTES = 30

# Full-day US market closures. Refresh annually; `doctor` warns 60 days out.
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}
HOLIDAYS_2027 = {
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
HOLIDAYS = HOLIDAYS_2026 | HOLIDAYS_2027

# Half days: the session ends at 13:00 ET. Same NYSE announcement as the
# holidays, so refresh the two together. NYSE gives "eligible options" until
# 13:15 -- the broad index and ETF products, not the single-name chains here.
EARLY_CLOSES = {"2026-11-27", "2026-12-24", "2027-11-26"}
REGULAR_CLOSE = dt.time(16, 0)
EARLY_CLOSE = dt.time(13, 0)


def close_time(day: dt.date) -> dt.time:
    return EARLY_CLOSE if day.isoformat() in EARLY_CLOSES else REGULAR_CLOSE


def calendar_ends() -> dt.date:
    """The last day the tables above know about. Past it nothing fails: every
    holiday reads as a trading day and every half day as a full one."""
    return dt.date(max(int(d[:4]) for d in HOLIDAYS), 12, 31)


@dataclass
class SessionState:
    now_et: dt.datetime
    is_trading_day: bool
    # "premarket" | "opening_range" | "open" | "closed" | "holiday" | "weekend"
    phase: str
    quote_quality: str    # "live" | "closing_snapshot" | "stale"
    banner: str
    settle_until: dt.datetime | None = None   # when the opening range ends

    @property
    def is_open(self) -> bool:
        """The market is trading -- true inside the opening range too."""
        return self.phase in ("open", "opening_range")

    @property
    def in_opening_range(self) -> bool:
        return self.phase == "opening_range"

    @property
    def can_open_positions(self) -> bool:
        """Whether a position may be opened right now. False only inside the
        opening range; every other phase is governed by the quote-quality
        warnings, not a hard block."""
        return not self.in_opening_range

    @property
    def open_block_reason(self) -> str:
        if self.can_open_positions:
            return ""
        until = f"{self.settle_until:%H:%M} ET" if self.settle_until else "the settle time"
        mins = int((self.settle_until - self.now_et).total_seconds() // 60) + 1
        return (f"inside the opening range - no positions are opened until {until} "
                f"({mins} min away). The opening book is the widest and thinnest of "
                f"the day; a fill taken here is not one the live account could count on.")


def state(now: dt.datetime | None = None,
          settle_minutes: int | None = None) -> SessionState:
    now = (now or dt.datetime.now(ET)).astimezone(ET)
    settle_minutes = OPENING_SETTLE_MINUTES if settle_minutes is None else settle_minutes
    day = now.date().isoformat()
    if now.weekday() >= 5:
        return SessionState(now, False, "weekend", "stale",
                            "Market closed (weekend) - quotes are last Friday's close.")
    if day in HOLIDAYS:
        return SessionState(now, False, "holiday", "stale",
                            "Market closed (holiday) - quotes are the prior session's close.")

    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    # Hard-coded to 16:00 this read a half day's 13:00-16:00 as live: the
    # 13:05..15:50 marks priced exits off a book that had stopped trading.
    shut = close_time(now.date())
    close_t = now.replace(hour=shut.hour, minute=shut.minute, second=0, microsecond=0)
    settle_until = open_t + dt.timedelta(minutes=settle_minutes)
    if now < open_t:
        return SessionState(now, True, "premarket", "stale",
                            "Pre-market - option quotes are yesterday's close; "
                            "re-run after 09:30 ET.")
    if now < settle_until:
        return SessionState(
            now, True, "opening_range", "live",
            f"Market open {int((now - open_t).total_seconds() // 60)} min - inside the "
            f"{settle_minutes}-minute opening range. Quotes are live but the book is "
            f"still settling; no positions are opened until {settle_until:%H:%M} ET.",
            settle_until=settle_until)
    if now <= close_t:
        half = f" Early close today at {close_t:%H:%M} ET." if shut != REGULAR_CLOSE else ""
        return SessionState(now, True, "open", "live",
                            "Market open and settled - option quotes are live." + half)
    # Robinhood/Yahoo keep serving the closing print for a while after the bell.
    if (now - close_t).total_seconds() <= 4 * 3600:
        return SessionState(now, True, "closed", "closing_snapshot",
                            f"Market closed - quotes are today's {close_t:%H:%M} ET closing "
                            "snapshot. "
                            "Closing books are wider than intraday; re-confirm during RTH before approving.")
    return SessionState(now, True, "closed", "stale",
                        "Market closed - quotes are a stale end-of-day snapshot. "
                        "Re-run during RTH before approving anything.")


def spread_tolerance_multiplier(st: SessionState) -> float:
    """Widen the bid/ask gates outside RTH -- a wide closing book is normal and
    should not be reported as an illiquid strike."""
    return {"live": 1.0, "closing_snapshot": 1.8, "stale": 2.5}[st.quote_quality]


def state_for(settings, now: dt.datetime | None = None) -> SessionState:
    """Session state honouring the user's configured opening-range window."""
    return state(now, getattr(settings, "opening_settle_minutes", OPENING_SETTLE_MINUTES))


def slippage_frac(st: SessionState, settings) -> float:
    """How far from the mid to size, given how much the book can be trusted."""
    table = getattr(settings, "slippage_by_quote_quality", None) or {}
    return float(table.get(st.quote_quality, settings.slippage_frac))
