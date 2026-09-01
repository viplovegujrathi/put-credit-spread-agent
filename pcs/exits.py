"""Section 1.7 exit rules, as decisions the agent acts on.

`paper_broker.management_note` describes a position; this module DECIDES about
it. The split matters: a stop loss that waits for a human is not a stop loss,
and a profit target only works if it is taken mechanically rather than argued
with each time.

What the agent may act on here is deliberately bounded:

  * Closing only. Every exit in this module REDUCES risk -- it buys back a
    short option the account already carries. Nothing here can open exposure,
    and the per-trade human approval gate on opening is untouched.
  * Paper only. `apply_exits` refuses to run against a live ledger; in live
    mode the same decision is rendered as an order ticket for a human to place,
    exactly like an entry.
  * Fresh marks only. A decision is only as good as the price behind it, so the
    caller passes the set of positions that actually re-marked this run. A
    stale mark decides nothing.

Exits are NOT blocked during the opening range. That gate exists so the account
does not take on new risk at the day's worst prices; refusing to let a stop
fire for the same reason would leave a losing position open exactly while it is
moving against us.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import STRATEGY, Settings
from .ledger import Position

TAKE_PROFIT = "take_profit"
STOP_LOSS = "stop_loss"
DEFEND = "defend"
ROLL = "roll"
HOLD = "hold"


@dataclass
class ExitDecision:
    action: str          # one of the constants above
    act: bool            # True = close it now
    reason: str          # human-readable, always names the number that fired
    debit: float = 0.0   # per share, what buying it back costs at the mark
    pl: float = 0.0      # dollars if closed here

    def __bool__(self) -> bool:
        return self.act

    @property
    def headline(self) -> str:
        return self.action.replace("_", " ").upper() if self.act else self.action


def decide(pos: Position, settings: Settings, strategy=STRATEGY) -> ExitDecision:
    """What to do with one open position, given its current mark.

    Order matters: risk first. A position cannot be simultaneously at a profit
    target and at a stop, but checking the loss branches first means no future
    edit can accidentally let a profit rule mask a stop.
    """
    n = pos.contracts
    debit, pl = pos.mark_cost_to_close, pos.open_pl
    credit_in = round(pos.credit_open * 100 * n, 2)
    cost_now = round(debit * 100 * n, 2)

    # --- stop loss ---------------------------------------------------------
    # The classic credit-spread stop: exit when buying the spread back costs a
    # multiple of what was taken in. At 2x, the loss equals the credit.
    mult = settings.stop_loss_credit_multiple
    if credit_in > 0 and mult > 0 and cost_now >= round(credit_in * mult, 2):
        return ExitDecision(
            STOP_LOSS, True,
            f"buying it back costs ${cost_now:,.0f}, {cost_now / credit_in:.1f}x the "
            f"${credit_in:,.0f} credit taken in (stop is {mult:g}x) -- down "
            f"${-pl:,.0f}, cut it",
            debit, pl)

    # Backstop for spreads whose credit is large relative to width, where no
    # multiple of the credit is reachable inside the max loss.
    cap = round(settings.stop_loss_pct_of_max_loss * pos.collateral, 2)
    if pl < 0 and cap > 0 and -pl >= cap:
        return ExitDecision(
            STOP_LOSS, True,
            f"down ${-pl:,.0f}, which is {-pl / pos.collateral:.0%} of the "
            f"${pos.collateral:,.0f} defined max loss (stop is "
            f"{settings.stop_loss_pct_of_max_loss:.0%})",
            debit, pl)

    # --- defend: assignment risk near expiration ---------------------------
    if (pos.dte <= strategy.defend_dte and pos.mark_spot
            and pos.mark_spot <= pos.short_strike):
        return ExitDecision(
            DEFEND, True,
            f"short strike breached ({pos.mark_spot:,.2f} vs {pos.short_strike:g}) with "
            f"{pos.dte} DTE -- close rather than carry assignment risk into expiration. "
            f"Rolling down-and-out for a credit is the alternative; never remove the "
            f"long leg.",
            debit, pl)

    # --- profit booking ----------------------------------------------------
    pct = pos.pct_of_max_credit
    if pct >= strategy.take_profit_pct:
        lo, hi = strategy.take_profit_band
        return ExitDecision(
            TAKE_PROFIT, True,
            f"at {pct:.0%} of max credit (target {strategy.take_profit_pct:.0%}, band "
            f"{lo:.0%}-{hi:.0%}) -- book ${pl:,.0f} for a ${cost_now:,.0f} debit rather "
            f"than grind the last {1 - pct:.0%} against {pos.dte} days of gamma",
            debit, pl)

    # --- advisory: nothing to act on yet -----------------------------------
    if pos.dte <= strategy.manage_dte and pct < 0:
        return ExitDecision(
            ROLL, False,
            f"{pos.dte} DTE and underwater ({pct:.0%}) -- decide whether to roll out",
            debit, pl)
    if pos.mark_spot and pos.mark_spot <= pos.short_strike:
        return ExitDecision(
            HOLD, False,
            f"short strike tested ({pos.mark_spot:,.2f} vs {pos.short_strike:g}), "
            f"{pos.dte} DTE left -- watch it",
            debit, pl)
    return ExitDecision(HOLD, False, "", debit, pl)


def review(ledger, settings: Settings, fresh: set[str] | None = None
           ) -> list[tuple[Position, ExitDecision]]:
    """Decide about every open position. Pure -- mutates nothing."""
    out = []
    for pos in ledger.open_positions:
        if fresh is not None and pos.id not in fresh:
            continue
        out.append((pos, decide(pos, settings)))
    return out


def ticket(pos: Position, d: ExitDecision) -> str:
    """The closing order a human would place, for live mode."""
    return "\n".join([
        f"  CLOSE  {pos.symbol} {pos.expiration} {pos.short_strike:g}/{pos.long_strike:g}p"
        f"  x{pos.contracts}",
        f"    BUY  {pos.contracts}x ${pos.short_strike:g} put",
        f"    SELL {pos.contracts}x ${pos.long_strike:g} put",
        f"    net debit ~${d.debit:.2f} per share (${d.debit * 100 * pos.contracts:,.0f} total)",
        f"    why: {d.reason}",
    ])
