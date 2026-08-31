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

# Full-day US market closures. Refresh annually.
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}
HOLIDAYS_2027 = {
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
HOLIDAYS = HOLIDAYS_2026 | HOLIDAYS_2027


@dataclass
class SessionState:
    now_et: dt.datetime
    is_trading_day: bool
    phase: str            # "premarket" | "open" | "closed" | "holiday" | "weekend"
    quote_quality: str    # "live" | "closing_snapshot" | "stale"
    banner: str

    @property
    def is_open(self) -> bool:
        return self.phase == "open"


def state(now: dt.datetime | None = None) -> SessionState:
    now = (now or dt.datetime.now(ET)).astimezone(ET)
    day = now.date().isoformat()
    if now.weekday() >= 5:
        return SessionState(now, False, "weekend", "stale",
                            "Market closed (weekend) - quotes are last Friday's close.")
    if day in HOLIDAYS:
        return SessionState(now, False, "holiday", "stale",
                            "Market closed (holiday) - quotes are the prior session's close.")

    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < open_t:
        return SessionState(now, True, "premarket", "stale",
                            "Pre-market - option quotes are yesterday's close; re-run after 09:30 ET.")
    if now <= close_t:
        return SessionState(now, True, "open", "live",
                            "Market open - option quotes are live.")
    # Robinhood/Yahoo keep serving the 16:00 print for a while after the bell.
    if (now - close_t).total_seconds() <= 4 * 3600:
        return SessionState(now, True, "closed", "closing_snapshot",
                            "Market closed - quotes are today's 16:00 ET closing snapshot. "
                            "Closing books are wider than intraday; re-confirm during RTH before approving.")
    return SessionState(now, True, "closed", "stale",
                        "Market closed - quotes are a stale end-of-day snapshot. "
                        "Re-run during RTH before approving anything.")


def spread_tolerance_multiplier(st: SessionState) -> float:
    """Widen the bid/ask gates outside RTH -- a wide closing book is normal and
    should not be reported as an illiquid strike."""
    return {"live": 1.0, "closing_snapshot": 1.8, "stale": 2.5}[st.quote_quality]


def slippage_frac(st: SessionState, settings) -> float:
    """How far from the mid to size, given how much the book can be trusted."""
    table = getattr(settings, "slippage_by_quote_quality", None) or {}
    return float(table.get(st.quote_quality, settings.slippage_frac))
