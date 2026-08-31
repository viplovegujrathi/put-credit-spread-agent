"""Step 8 -- execution adapter, paper implementation.

The human-approval gate from section 3 lives here in code, not just in
instructions: `open_approved` refuses to do anything without an explicit
approval token naming the proposal, and the live adapter refuses outright.

Paper fills are deliberately pessimistic relative to the sizing basis -- the
proposal is sized at mid minus 15% of the package width, the fill is taken at
mid minus 25%, so a paper track record cannot flatter itself by assuming the
mid every time.
"""

from __future__ import annotations

import datetime as dt

from .chains import PutChain, get_chain
from .config import STRATEGY, Settings
from .ledger import EXPIRED, Ledger, Position, new_id
from .optimizer import Spread
from .session import SessionState, slippage_frac
from .session import state as session_state


class ApprovalRequired(RuntimeError):
    """Raised when something tries to open a position without human sign-off."""


PAPER_EXTRA_HAIRCUT = 0.10


def simulated_fill_credit(spread: Spread, settings: Settings,
                          sess: SessionState | None = None) -> float:
    """Where a marketable limit would realistically fill, per share.

    Must never come out better than the credit the proposal was sized at, or a
    paper track record flatters itself. Sizing slippage is session-scaled, so
    the paper haircut is derived from it rather than being a fixed constant --
    a fixed 0.25 was briefly *less* conservative than a 0.35 stale-quote sizing
    basis, which handed the paper account a fill better than the ticket.
    """
    sess = sess or session_state()
    slip = max(settings.paper_slippage_frac, slippage_frac(sess, settings) + PAPER_EXTRA_HAIRCUT)
    mid, nat = spread.credit_mid, spread.credit_nat
    return round(max(min(mid - slip * (mid - nat), spread.credit), nat), 4)


def open_approved(ledger: Ledger, spread: Spread, sector: str, contracts: int,
                  settings: Settings, proposal_id: str, approved_by: str,
                  sess: SessionState | None = None) -> Position:
    """Open a paper position. Requires an explicit approver -- section 3's gate."""
    if not approved_by:
        raise ApprovalRequired(
            "no approver recorded; every trade needs explicit per-trade human approval")
    if ledger.mode != "paper":
        raise ApprovalRequired(
            "this adapter only fills paper trades; live orders must be placed by a "
            "human through the broker after reviewing the ticket")

    fill = simulated_fill_credit(spread, settings, sess)
    fees = round(spread.fees * contracts, 2)
    gross = round(fill * 100 * contracts, 2)
    collateral = round((spread.width * 100 - fill * 100) * contracts, 2)

    pos = Position(
        id=new_id(), symbol=spread.symbol, sector=sector, expiration=spread.expiration,
        short_strike=spread.short_strike, long_strike=spread.long_strike,
        width=spread.width, contracts=contracts, credit_open=fill,
        credit_dollars=round(gross - fees, 2), collateral=collateral,
        opened_at=dt.datetime.now().isoformat(timespec="seconds"),
        opened_spot=spread.spot, mark_cost_to_close=fill, mark_spot=spread.spot,
        marked_at=dt.datetime.now().isoformat(timespec="seconds"),
        fees_paid=fees, proposal_id=proposal_id, approved_by=approved_by,
        basis=spread.basis, source=spread.source,
    )
    return ledger.open_position(pos)


def cost_to_close(chain: PutChain, pos: Position, settings: Settings) -> float | None:
    """Debit to buy the spread back, per share. None if the chain can't price it."""
    sq, lq = chain.at(pos.short_strike), chain.at(pos.long_strike)
    if sq is None or lq is None:
        return None
    mid = sq.mid - lq.mid
    nat = sq.ask - lq.bid          # worst case: lift the offer, hit the bid
    if mid <= 0 and nat <= 0:
        return 0.0
    return round(max(mid + settings.paper_slippage_frac * (nat - mid), 0.0), 4)


def settle_expired(pos: Position, spot: float) -> tuple[float, str]:
    """Intrinsic settlement at expiration, per share."""
    if spot >= pos.short_strike:
        return 0.0, "expired worthless (max profit)"
    if spot <= pos.long_strike:
        return pos.width, "expired at max loss (both legs in the money)"
    return round(pos.short_strike - spot, 4), "expired partially in the money"


def mark_positions(ledger: Ledger, settings: Settings, spots: dict[str, float]
                   ) -> list[tuple[Position, str]]:
    """Refresh every open position's mark. Returns (position, note) for anything
    that needs attention."""
    notes: list[tuple[Position, str]] = []
    today = dt.date.today()
    for pos in list(ledger.open_positions):
        spot = spots.get(pos.symbol, 0.0)
        if dt.date.fromisoformat(pos.expiration) < today:
            debit, reason = settle_expired(pos, spot or pos.mark_spot)
            ledger.close_position(pos, debit, reason, fees=0.0, status=EXPIRED)
            notes.append((pos, reason))
            continue
        chain = get_chain(pos.symbol, settings.chain_source, expiration=pos.expiration,
                          spot=spot)
        debit = cost_to_close(chain, pos, settings)
        if debit is None:
            notes.append((pos, f"could not mark: {chain.error or 'strikes missing from chain'}"))
            continue
        pos.mark_cost_to_close = debit
        pos.mark_spot = spot or chain.spot or pos.mark_spot
        pos.marked_at = dt.datetime.now().isoformat(timespec="seconds")
        note = management_note(pos)
        if note:
            notes.append((pos, note))
    ledger.log("marked", positions=len(ledger.open_positions))
    return notes


def management_note(pos: Position, strategy=STRATEGY) -> str:
    """Section 1.7 exit rules, as advice. Never acts on its own."""
    lo, hi = strategy.take_profit_band
    pct = pos.pct_of_max_credit
    if pct >= hi:
        return (f"TAKE PROFIT: at {pct:.0%} of max credit (band is {lo:.0%}-{hi:.0%}) "
                f"- close for a ${pos.mark_cost_to_close * 100 * pos.contracts:.0f} debit")
    if pct >= strategy.take_profit_pct:
        return f"take profit in range: {pct:.0%} of max credit - closing here is on-plan"
    if pos.mark_spot and pos.mark_spot <= pos.short_strike:
        if pos.dte <= strategy.defend_dte:
            return (f"SHORT STRIKE TESTED with {pos.dte} DTE: roll down-and-out for a "
                    f"further credit, or accept the ${pos.max_loss:.0f} defined max loss. "
                    f"Never remove the long leg.")
        return (f"short strike tested ({pos.mark_spot:.2f} vs {pos.short_strike:g}), "
                f"{pos.dte} DTE left - watch it")
    if pos.dte <= strategy.manage_dte and pct < 0:
        return f"{pos.dte} DTE and underwater ({pct:.0%}) - decide whether to roll"
    return ""
