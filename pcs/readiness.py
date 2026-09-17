"""How close this is to being trustworthy with real money.

The go-live list in the README is prose, which means it can be nodded at. This
turns it into a computed score against observable state, so the honest answer
is visible on the dashboard instead of being a matter of opinion.

Two kinds of criterion, deliberately mixed:

  * things the agent can observe for itself (closed trades, win rate, whether a
    loss has actually been taken, whether a stop has ever fired, whether any
    proposal was sized on a live market rather than a closing snapshot)
  * things only the broker knows, recorded in Settings after being checked
    (option level, real buying power) and stamped with when they were checked,
    because both go stale

A criterion that cannot be evaluated counts as NOT met. Unknown is never a pass
-- the same rule the earnings filter follows, for the same reason.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .config import Settings
from .exits import DEFEND, STOP_LOSS
from .ledger import LOSS_FLOOR, Ledger


@dataclass
class Criterion:
    label: str
    ok: bool
    detail: str
    blocking: bool = True     # a False here is advisory, not a hard gate


@dataclass
class Readiness:
    criteria: list[Criterion]

    @property
    def met(self) -> int:
        return sum(1 for c in self.criteria if c.ok)

    @property
    def total(self) -> int:
        return len(self.criteria)

    @property
    def pct(self) -> float:
        return self.met / self.total if self.total else 0.0

    @property
    def blockers(self) -> list[Criterion]:
        return [c for c in self.criteria if not c.ok and c.blocking]

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def verdict(self) -> str:
        if self.ready:
            return "every check passes -- a human still places every live order"
        n = len(self.blockers)
        return f"{n} blocking check{'s' if n != 1 else ''} outstanding"


def _stale(stamp: str, days: int = 7) -> bool:
    if not stamp:
        return True
    try:
        return (dt.date.today() - dt.date.fromisoformat(stamp[:10])).days > days
    except ValueError:
        return True


def assess(led: Ledger, settings: Settings) -> Readiness:
    closed = led.closed_positions
    wins = [p for p in closed if p.realized_pl > 0]
    # `LOSS_FLOOR` is the ledger's own line between a loss and a scratch, and
    # `learning._result` already draws it. Drawing a second one here (`<= 0`)
    # let a twelve-cent fee-only close satisfy the criterion whose entire job is
    # to prove the downside path has been exercised. One value, one home
    # (LEARNING.md 26). A scratch still counts in the win-rate denominator,
    # which is the strict direction and stays.
    losses = [p for p in closed if p.realized_pl <= LOSS_FLOOR]
    scratches = [p for p in closed if LOSS_FLOOR < p.realized_pl <= 0]
    win_rate = len(wins) / len(closed) if closed else 0.0
    # Structured, not prose. This was `"stop_loss" in " ".join(close_reason)` --
    # a substring search over free text that `close --reason` writes verbatim,
    # so the check could be satisfied by typing the words into a manual close.
    # `close_action` is written by whichever code path actually closed the
    # position (LEARNING.md 24: a machine check must not depend on prose, and
    # this one gates real money where the nginx marker only gated a redeploy).
    stopped = [p for p in closed if p.close_action in (STOP_LOSS, DEFEND)]
    unproven = [p for p in closed if not p.close_action]

    need_n = settings.min_closed_trades_for_live
    need_wr = settings.min_win_rate_for_live
    lvl = settings.broker_option_level
    bp = settings.broker_buying_power
    checked = settings.broker_checked_at

    c: list[Criterion] = [
        Criterion(
            "Broker permits spreads",
            lvl == "option_level_3" and not _stale(checked, 30),
            (f"{lvl or 'unknown'}"
             + (f", checked {checked[:10]}" if checked else ", never checked")
             + ("" if lvl == "option_level_3" else " -- spreads need option_level_3")
             + (" (stale, re-check)" if lvl == "option_level_3"
                and _stale(checked, 30) else ""))),
        Criterion(
            "Account can fund a position",
            bp >= settings.max_total_collateral and not _stale(checked, 7),
            (f"${bp:,.2f} buying power vs ${settings.max_total_collateral:,.0f} of "
             f"planned collateral"
             + (" -- figure is stale, re-check the broker" if _stale(checked, 7) else ""))),
        Criterion(
            f"At least {need_n} closed paper trades",
            len(closed) >= need_n,
            f"{len(closed)} closed"),
        Criterion(
            f"Win rate at or above {need_wr:.0%}",
            bool(closed) and win_rate >= need_wr,
            f"{win_rate:.0%} on {len(closed)} trade(s)" if closed else "no trades yet"),
        Criterion(
            "A loss has actually been taken",
            bool(losses),
            (f"{len(losses)} losing trade(s) -- the downside is real and measured"
             if losses
             else f"{len(scratches)} closed inside ${abs(LOSS_FLOOR):.2f} of flat -- "
                  "fees are not a loss" if scratches
             else "every closed trade won; the loss path is untested")),
        Criterion(
            "A stop or defend has fired at least once",
            bool(stopped),
            ("the exit engine has cut a position for real" if stopped
             else f"no stop recorded; {len(unproven)} closed trade(s) predate the "
                  "field and cannot be checked" if unproven
             else "no stop has ever fired -- the mechanism is unproven here")),
        Criterion(
            "Sized on a live market, not a closing snapshot",
            any(p.quote_quality == "live" and p.basis == "live"
                for p in closed + led.open_positions),
            "at least one position filled against a live market"
            if any(p.quote_quality == "live" for p in closed + led.open_positions)
            else "every position so far was sized outside RTH -- "
                 "the opening book is not what these fills assumed"),
        Criterion(
            "Confirmed against the broker's own chain",
            any(p.source == "robinhood" for p in closed + led.open_positions),
            "Robinhood chain used for sizing" if any(
                p.source == "robinhood" for p in closed + led.open_positions)
            else "Yahoo only -- it drops strikes the broker lists (see LEARNING.md §1)",
            blocking=False),
    ]
    return Readiness(c)
