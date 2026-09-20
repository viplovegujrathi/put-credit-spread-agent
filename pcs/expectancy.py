"""What win rate the exit rules require, and what the record actually delivers.

The dashboard has shown win rate and average capture side by side for weeks,
under a note saying that a high win rate at low capture and a few full-size
stops is a losing book that reads as a winning one. That note names the failure
exactly and then leaves the reader to do the arithmetic in their head. Nobody
does. This module does it instead.

The number is the **break-even win rate**: the fraction of trades that has to
win for the configuration to make nothing. Two settings fix it and nothing else
touches it --

    win  = take_profit_pct          x credit     book at 50% of max credit
    loss = (stop_loss_multiple - 1) x credit     buy back at 2x the credit
    break-even = loss / (loss + win)

At the shipped defaults that is 1.00 / 1.50 = **66.7%**. No screen, no entry
filter and no amount of stock-picking moves it -- they can only change how often
you clear it. On 2026-09-17 this account was delivering 61.5% against a measured
requirement of 67.9%, +$11.79 across 13 closed trades, while
`min_win_rate_for_live` sat at 60% -- a go-live bar underneath the
configuration's own break-even. See LEARNING.md 44.

Everything here is pure and reads only what the ledger already recorded.
Nothing in this module may propose, size, open or close anything; like
`learning`, it exists to tell the reader something, and the reader decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .ledger import LOSS_FLOOR, Ledger, Position


@dataclass(frozen=True)
class Payoff:
    """A win and a loss, both as multiples of the credit taken in.

    Credit multiples rather than dollars, because the two sides have to be
    comparable across positions sized from $98 to $420 of premium. `capture` in
    `learning` is the same normalisation, deliberately.
    """

    win: float           # a winning trade: +0.50 means it kept half the credit
    loss: float          # a losing trade, POSITIVE: 1.00 means it gave the credit back
    sample: int = 0      # trades measured; 0 means derived from settings, not observed

    @property
    def breakeven(self) -> float:
        """Win rate at which this payoff makes exactly nothing."""
        total = self.win + self.loss
        return self.loss / total if total > 0 else 0.0

    def expectancy(self, win_rate: float) -> float:
        """Credit multiples per trade at that win rate. Positive is an edge."""
        return win_rate * self.win - (1.0 - win_rate) * self.loss


def implied(settings: Settings, strategy: object | None = None) -> Payoff | None:
    """What the exit settings require, before a single trade has happened.

    None when `stop_loss_credit_multiple` is at or below 1x, where the rule
    stops being expressible in credit multiples at all: at exactly 1x a "loss"
    costs the credit and nothing more, and below it the stop would fire in
    profit. In both cases the real bound is the max-loss backstop, which needs a
    width -- see `governing_loss`.
    """
    st = strategy or settings.strategy()
    mult = settings.stop_loss_credit_multiple
    if mult <= 1.0 or st.take_profit_pct <= 0:
        return None
    return Payoff(win=st.take_profit_pct, loss=mult - 1.0)


def crossover_credit_frac(settings: Settings) -> float:
    """Credit, as a fraction of width, above which the backstop governs instead.

    `pct / (mult - 1 + pct)` -- 33% at the defaults. Below it the credit
    multiple fires first; above it the 50%-of-max-loss backstop does. The skill
    prefers the narrowest width that clears $100 of credit, and a $2.50-wide
    spread cannot clear $100 without taking in 40% of its width, so the
    optimizer's width choice silently decides which stop is live.
    """
    mult, pct = settings.stop_loss_credit_multiple, settings.stop_loss_pct_of_max_loss
    denom = (mult - 1.0) + pct
    return pct / denom if denom > 0 else 0.0


def governing_loss(width: float, credit: float, settings: Settings) -> float:
    """The stop that actually fires for ONE spread, in credit multiples.

    Two settings compete and the tighter one wins. `credit` is per share, the
    same units as `width`, so a $5-wide spread sold for $1.16 is (5.0, 1.16).
    """
    if credit <= 0 or width <= credit:
        return 0.0
    by_credit = max(settings.stop_loss_credit_multiple - 1.0, 0.0)
    by_max_loss = settings.stop_loss_pct_of_max_loss * (width - credit) / credit
    live = [x for x in (by_credit, by_max_loss) if x > 0]
    return min(live) if live else 0.0


def _max_credit(p: Position) -> float:
    """The most this position could ever have made, gross.

    `credit_open x 100 x contracts`, matching `learning.Outcome.max_credit`.
    Not `credit_dollars`, which is net of the fill fee -- the take-profit rule
    is written against the gross figure, so the payoff has to be measured
    against the same one or the two disagree by the fees.
    """
    return p.credit_open * 100 * p.contracts


def realised(closed: list[Position]) -> Payoff | None:
    """What the closed record actually paid, in the same units as `implied`.

    None unless BOTH sides have a trade: a ratio needs a numerator and a
    denominator, and a book of eight wins and no losses has not yet measured
    what a loss costs. Reporting a break-even of 0% there would be the most
    dangerous possible output -- it would read as "any win rate is fine".

    Scratches are excluded from both sides, on the same `LOSS_FLOOR` line
    `learning._result` draws: a twelve-cent fee-only close measures nothing.
    """
    wins, losses = [], []
    for p in closed:
        mc = _max_credit(p)
        if mc <= 0:
            continue
        if p.realized_pl > 0:
            wins.append(p.realized_pl / mc)
        elif p.realized_pl <= LOSS_FLOOR:
            losses.append(-p.realized_pl / mc)
    if not wins or not losses:
        return None
    return Payoff(win=sum(wins) / len(wins), loss=sum(losses) / len(losses),
                  sample=len(wins) + len(losses))


def decided_win_rate(closed: list[Position]) -> tuple[float, int]:
    """Win rate over trades that said something, and how many that was.

    Scratches sit in neither bucket, so they are out of the denominator here --
    otherwise the rate being compared against a break-even would be measuring a
    different population from the ratio that produced it.
    """
    wins = sum(1 for p in closed if p.realized_pl > 0)
    losses = sum(1 for p in closed if p.realized_pl <= LOSS_FLOOR)
    n = wins + losses
    return (wins / n if n else 0.0), n


@dataclass(frozen=True)
class Summary:
    """Everything the panel needs, computed once."""

    implied: Payoff | None       # what the settings require
    realised: Payoff | None      # what the record has paid so far
    win_rate: float              # delivered, over decided trades
    decided: int

    @property
    def payoff(self) -> Payoff | None:
        """The one to judge against: measured if there is one, else the settings.

        A measured payoff beats a derived one because the exits overshoot --
        take-profit fires on the first mark at or above the target, so it books
        55% against a 50% rule, and a stop set at 2.0x fills at 2.1x or 2.8x.
        Both drifts move the requirement, and only one of them is in the reader's
        favour.
        """
        return self.realised or self.implied

    @property
    def breakeven(self) -> float | None:
        p = self.payoff
        return p.breakeven if p else None

    @property
    def edge(self) -> float | None:
        """Credit multiples per trade at the delivered win rate."""
        p = self.payoff
        return p.expectancy(self.win_rate) if p and self.decided else None

    @property
    def clears(self) -> bool | None:
        """Is the delivered win rate above what this configuration needs?"""
        be = self.breakeven
        return None if be is None or not self.decided else self.win_rate >= be


def summary(led: Ledger, settings: Settings) -> Summary:
    closed = led.closed_positions
    rate, n = decided_win_rate(closed)
    return Summary(implied=implied(settings), realised=realised(closed),
                   win_rate=rate, decided=n)
