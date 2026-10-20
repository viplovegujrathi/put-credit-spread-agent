"""What a spread would have booked had it simply been held to expiration.

Every exit this agent takes is a bet that closing early beats waiting: a
take-profit gives up the last 40-50% of the credit to be sure of the rest, and a
stop pays 2x the credit to avoid the full width. The record could measure how
each trade ENDED but never what the alternative would have been, so neither rule
could be judged -- see LEARNING.md 53. By 2026-10-15 all 15 wins were
take-profits and all 8 losses were stops, and nothing had ever been left to run.

Settlement needs only the underlying's close on the expiration date, so this
holds no option quote and keeps no running mark. After a closed position's
expiration passes, the close on that date is fetched once and the spread is
settled with `paper_broker.settle_expired` -- the same function that books a real
expiry -- so "held" means exactly what an expired row would have booked.

Research only. Nothing here reads a decision, changes a rule, or touches cash:
`shadow_*` fields are written and nothing in the exit path reads them.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from . import exits
from .ledger import CLOSE_MANUAL, CLOSED, Ledger, Position
from .paper_broker import settle_expired

# Per run, not per position: the first run after this ships has every
# already-closed trade to back-fill, and `mark` holds the ledger lock while it
# works. A cap spreads the back-fill over a few 15-minute runs so a stop due on
# the next mark is not queued behind twenty price downloads.
MAX_PER_RUN = 6

Fetch = Callable[[str, str], "float | None"]


def settle_close(symbol: str, expiration: str) -> float | None:
    """The underlying's close on `expiration`, or None if there is not one yet.

    Unadjusted: a settlement price is the print, and a dividend adjustment
    would move it. None for anything that is not one clean bar for that exact
    date -- a missing bar, a NaN, an empty frame -- because a settlement booked
    off the wrong day is worse than a row that stays unresolved.
    """
    import pandas as pd
    import yfinance as yf

    day = dt.date.fromisoformat(expiration)
    try:
        h = yf.download(symbol, start=day.isoformat(),
                        end=(day + dt.timedelta(days=1)).isoformat(),
                        interval="1d", auto_adjust=False, progress=False)
    except Exception:
        return None
    if h is None or len(h) == 0:
        return None
    if isinstance(h.columns, pd.MultiIndex):
        h.columns = h.columns.get_level_values(0)
    h = h[[pd.Timestamp(i).date() == day for i in h.index]]
    if len(h) != 1 or "Close" not in h:
        return None
    close = h["Close"].iloc[0]
    if close != close or float(close) <= 0:         # NaN, or a zero fill
        return None
    return float(close)


def kind(pos: Position) -> str:
    """How a position was closed, as one of: take_profit, stop_loss, defend,
    manual, other.

    `close_action` is blank on every row closed before that field existed, and
    blank is "not recorded", not "not a stop" -- so those fall back to the
    prefix of the prose reason, which `exits` has always written first.
    """
    if pos.close_action in (exits.TAKE_PROFIT, exits.STOP_LOSS, exits.DEFEND):
        return pos.close_action
    if pos.close_action == CLOSE_MANUAL:
        return "manual"
    head = (pos.close_reason or "").split(":", 1)[0].strip()
    return head if head in (exits.TAKE_PROFIT, exits.STOP_LOSS, exits.DEFEND) else "other"


def held_pl(pos: Position, settle_spot: float) -> float:
    """Dollars the position would have booked held to expiration at this close.

    Same arithmetic as a real expiry: no closing fee (`mark_positions` settles
    with fees=0), against `credit_dollars`, which is already net of the fee at
    the fill. So it compares directly with `realized_pl`.
    """
    debit, _ = settle_expired(pos, settle_spot)
    return round(pos.credit_dollars - debit * 100 * pos.contracts, 2)


def due(pos: Position, today: dt.date) -> bool:
    """Closed early, expiration strictly past, and not yet settled in shadow.

    Strictly past: the bar for the expiration date is only final once that
    session is over. A position that ran to expiry (status "expired") already
    has its real settlement and is not a shadow.
    """
    return (pos.status == CLOSED
            and pos.shadow_settle_spot is None
            and dt.date.fromisoformat(pos.expiration) < today)


def resolve(led: Ledger, fetch: Fetch | None = None, today: dt.date | None = None,
            limit: int = MAX_PER_RUN) -> list[Position]:
    """Fill in the shadow outcome for every position that has come due.

    Returns the positions it resolved; the caller saves. A position whose fetch
    fails is tried at most once per calendar day -- 26 marks a day must not turn
    one missing bar into 26 downloads -- and one failure never stops the rest.
    """
    fetch = fetch or settle_close            # looked up now, so a test can swap it
    today = today or dt.date.today()
    stamp = today.isoformat()
    done: list[Position] = []
    for pos in led.positions:
        if len(done) >= limit:
            break
        if not due(pos, today) or pos.shadow_tried_on == stamp:
            continue
        try:
            spot = fetch(pos.symbol, pos.expiration)
        except Exception:
            spot = None
        pos.shadow_tried_on = stamp
        if spot is None:
            continue
        pos.shadow_settle_spot = round(spot, 4)
        pos.shadow_pl = held_pl(pos, spot)
        done.append(pos)
        led.log("shadow_settled", id=pos.id, symbol=pos.symbol, exit=kind(pos),
                actual=pos.realized_pl, held=pos.shadow_pl)
    return done


@dataclass
class Row:
    kind: str
    n: int
    actual: float
    held: float
    held_better: int            # trades where holding would have booked more

    @property
    def delta(self) -> float:
        """Held minus actual. Positive: the early exit cost money."""
        return round(self.held - self.actual, 2)


def comparison(positions: list[Position]) -> tuple[list[Row], int]:
    """(one row per exit kind, count of early closes still unresolved)."""
    early = [p for p in positions if p.status == CLOSED]
    groups: dict[str, list[Position]] = {}
    for p in early:
        if p.shadow_pl is not None:
            groups.setdefault(kind(p), []).append(p)
    order = [exits.TAKE_PROFIT, exits.STOP_LOSS, exits.DEFEND, "manual", "other"]
    rows = [Row(k, len(g), round(sum(p.realized_pl for p in g), 2),
                round(sum(p.shadow_pl for p in g), 2),
                sum(1 for p in g if p.shadow_pl > p.realized_pl))
            for k in order if (g := groups.get(k))]
    return rows, sum(1 for p in early if p.shadow_pl is None)


def report_lines(positions: list[Position]) -> list[str]:
    rows, pending = comparison(positions)
    if not rows:
        return [f"no early close has passed its expiration yet ({pending} waiting)."]
    out = [f"  {'exit':<12}{'n':>3}{'actual':>10}{'held':>10}{'held-actual':>13}"
           f"{'held better':>13}"]
    for r in rows:
        out.append(f"  {r.kind:<12}{r.n:>3}{r.actual:>+10,.0f}{r.held:>+10,.0f}"
                   f"{r.delta:>+13,.0f}{f'{r.held_better} of {r.n}':>13}")
    if len(rows) > 1:
        a, h = sum(r.actual for r in rows), sum(r.held for r in rows)
        out.append(f"  {'all':<12}{sum(r.n for r in rows):>3}{a:>+10,.0f}{h:>+10,.0f}"
                   f"{h - a:>+13,.0f}")
    out.append(f"  {pending} early close(s) not yet past expiration." if pending
               else "  every early close has settled.")
    return out
