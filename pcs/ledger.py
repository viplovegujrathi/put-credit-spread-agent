"""Step 9 -- position logger and paper account state.

One JSON file is the whole book: cash, open positions, closed positions, and
an append-only event log. Section 3's design principle is that every proposal
and every outcome stays auditable, so nothing is ever mutated in place without
an event being written alongside it.

Paper accounting for a short vertical:
    cash           = starting + credits received - debits paid - fees
    collateral     = (width x 100 - credit x 100) x contracts -- the max loss
    capital at risk = width x 100 x contracts -- the gross amount the account
                     can be called on to pay if the spread goes to max loss
    buying power   = cash - capital at risk
    net liq        = cash - cost to close every open spread right now

Buying power subtracts capital at risk, NOT collateral. `cash` already
includes the credit received and `collateral` is measured net of that same
credit, so `cash - collateral` double-counts the premium and overstates the
free balance by exactly the credit taken in. The identity that falls out is
the intuitive one:

    buying power = starting cash + realised P/L - collateral - fees on open

so an account can never commit more than it holds.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import LEDGER_JSON, Settings

OPEN, CLOSED, EXPIRED = "open", "closed", "expired"

# A close inside a dollar either way is fees, not a result -- the same line
# `learning._result` draws between LOSS and SCRATCH. A $0.12 fee-only close is
# not the loss the re-entry cooldown exists for.
LOSS_FLOOR = -0.5


@dataclass
class Position:
    id: str
    symbol: str
    sector: str
    expiration: str
    short_strike: float
    long_strike: float
    width: float
    contracts: int
    credit_open: float          # per share, per contract
    credit_dollars: float       # total received, all contracts, net of fees
    collateral: float           # total held, all contracts
    opened_at: str
    opened_spot: float
    status: str = OPEN
    # marks, refreshed by `mark`
    mark_cost_to_close: float = 0.0   # per share
    mark_spot: float = 0.0
    marked_at: str = ""
    # close-out
    closed_at: str = ""
    close_debit: float = 0.0          # per share
    realized_pl: float = 0.0          # dollars, net of fees
    close_reason: str = ""
    fees_paid: float = 0.0
    proposal_id: str = ""
    approved_by: str = ""
    basis: str = "live"          # "live" (real chain) | "modeled" (Black-Scholes)
    source: str = ""             # which provider the chain came from
    quote_quality: str = ""      # session grade at fill: live|closing_snapshot|stale

    # What was true about the name when the trade was opened. These are the two
    # conditions the whole screen is built on plus the two numbers that describe
    # the strike chosen -- and until now none of them survived the fill, so the
    # journal could measure how a trade ENDED but nothing about why it was
    # picked. They can only ever be learned from trades opened after this
    # existed, which is why they are recorded at the cheapest possible moment.
    #
    # None means NOT RECORDED, and that is the point of the type: a position
    # opened before this change must not read as "0% off its high".
    pct_off_high_at_open: float | None = None      # (high - spot) / high
    pct_from_dma50_at_open: float | None = None    # signed: (spot - dma50) / dma50
    short_delta_at_open: float | None = None       # computed while sizing
    iv_at_open: float | None = None                # short-leg implied vol

    @property
    def max_loss(self) -> float:
        return self.collateral

    @property
    def dte(self) -> int:
        return (dt.date.fromisoformat(self.expiration) - dt.date.today()).days

    @property
    def gross_credit(self) -> float:
        """Credit before fees -- the number the option chain quoted."""
        return round(self.credit_open * 100 * self.contracts, 2)

    @property
    def open_fees(self) -> float:
        """Fees taken out at fill, recovered from the two credits.

        `fees_paid` also holds this figure -- but only while the position is
        open. `close_position` adds the closing fee to the same field, so after
        a close `fees_paid` is open+close and no longer answers this question.
        `credit_dollars` is banked net of the fill fee (paper_broker), so its
        distance from the quoted credit is the opening fee for the whole life
        of the position."""
        return round(self.gross_credit - self.credit_dollars, 2)

    @property
    def open_pl(self) -> float:
        """Unrealized dollars: credit banked minus what it costs to buy back.

        Banked, not quoted. `credit_dollars` is what actually reached cash, so
        this reconciles with `Ledger.net_liq` exactly. Using the gross quote
        here instead used to overstate every open position by its fill fees,
        which put two totals on the dashboard that did not agree: unrealized
        P&L said one number and net liq minus starting cash said another.
        """
        return round(self.credit_dollars - self.mark_cost_to_close * 100 * self.contracts, 2)

    @property
    def pct_of_max_credit(self) -> float:
        """Fraction of the banked credit captured. Same basis as `open_pl`, so
        the exit rules fire on money the account actually keeps."""
        return round(self.open_pl / self.credit_dollars, 4) if self.credit_dollars else 0.0

    @property
    def breakeven(self) -> float:
        """Where the underlying has to close for this spread to break even.

        Derivable from two stored fields and, until now, never shown. Between
        the short strike and here the position loses money without any exit
        rule having fired.
        """
        return round(self.short_strike - self.credit_open, 2)

    @property
    def cushion(self) -> float | None:
        """How far the underlying sits above the short strike, as a fraction.

        Negative means through it. None means we have no spot to compare -- an
        unknown cushion and a comfortable one must not render the same.
        """
        if not self.mark_spot:
            return None
        return round((self.mark_spot - self.short_strike) / self.mark_spot, 4)

    @property
    def mark_age_minutes(self) -> float | None:
        """Age of the price behind every P&L figure on this row.

        None means never marked: the numbers shown are the fill, not a mark.
        """
        if not self.marked_at:
            return None
        try:
            at = dt.datetime.fromisoformat(self.marked_at)
        except ValueError:
            return None
        return max(0.0, (dt.datetime.now() - at).total_seconds() / 60)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update(dte=self.dte, open_pl=self.open_pl,
                 pct_of_max_credit=self.pct_of_max_credit, max_loss=self.max_loss,
                 breakeven=self.breakeven, cushion=self.cushion)
        return d


@dataclass
class Ledger:
    mode: str
    starting_cash: float
    cash: float
    created_at: str
    positions: list[Position] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    path: Path = LEDGER_JSON

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def load(cls, settings: Settings, path: Path | None = None) -> Ledger:
        path = path or LEDGER_JSON
        if not path.exists():
            led = cls(mode=settings.mode, starting_cash=settings.starting_cash,
                      cash=settings.starting_cash,
                      created_at=dt.datetime.now().isoformat(timespec="seconds"),
                      path=path)
            led.log("account_opened", cash=led.cash, mode=led.mode)
            led.save()
            return led
        raw = json.loads(path.read_text())
        led = cls(mode=raw["mode"], starting_cash=raw["starting_cash"], cash=raw["cash"],
                  created_at=raw["created_at"],
                  positions=[Position(**p) for p in raw.get("positions", [])],
                  events=raw.get("events", []), path=path)
        return led

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "mode": self.mode, "starting_cash": self.starting_cash, "cash": round(self.cash, 2),
            "created_at": self.created_at,
            "positions": [asdict(p) for p in self.positions],
            "events": self.events,
        }, indent=2))
        return self.path

    def log(self, kind: str, **payload) -> None:
        self.events.append({"at": dt.datetime.now().isoformat(timespec="seconds"),
                            "kind": kind, **payload})

    # -- views -------------------------------------------------------------
    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == OPEN]

    @property
    def closed_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status != OPEN]

    @property
    def collateral_held(self) -> float:
        return round(sum(p.collateral for p in self.open_positions), 2)

    @property
    def premium_collected(self) -> float:
        """Credit banked from the open book, net of fill fees. This is the one
        number that explains why cash sits above starting cash."""
        return round(sum(p.credit_dollars for p in self.open_positions), 2)

    @property
    def gross_premium(self) -> float:
        """What the open spreads sold for, before fill fees.

        `premium_collected` is the net figure and is the one that explains
        cash. This is the one `collateral_held` is netted against -- collateral
        is width less the QUOTED credit -- so a page that says "pay out X, keep
        the premium, lose the difference" has to use this or it misses by the
        fees and reads as an arithmetic error."""
        return round(sum(p.gross_credit for p in self.open_positions), 2)

    @property
    def fees_on_open(self) -> float:
        return round(sum(p.open_fees for p in self.open_positions), 2)

    @property
    def capital_at_risk(self) -> float:
        """Gross dollars the open book could be called on to pay -- the full
        strike width, not the width net of credit."""
        return round(sum(p.width * 100 * p.contracts for p in self.open_positions), 2)

    @property
    def free_cash(self) -> float:
        """Cash less the collateral a broker locks -- the broker's own figure.

        This is what a statement calls buying power: a defined-risk spread is
        margined at its max loss, and the credit is already in cash. It is the
        number to reconcile against the brokerage account.

        It is NOT what this agent opens against -- see `buying_power`."""
        return round(self.cash - self.collateral_held, 2)

    @property
    def buying_power(self) -> float:
        """What the agent will open against: cash less the FULL width at risk.

        Deliberately stricter than `free_cash`, and the difference is not
        conservatism for its own sake. Under the broker's basis an account can
        be filled until the sum of widths exceeds cash: open $400-collateral
        spreads against $3,000 and the gate permits nine, $4,500 of width
        against $3,900 of cash -- a correlated wipeout settles at -$600. Holding
        the gross width caps it at seven and leaves +$200.

        A book of beaten-down names in two sectors is exactly the tail where
        every position lands at once, and a cash account has no margin line to
        absorb it. `test_the_balance_can_never_be_driven_negative` pins this."""
        return round(self.cash - self.capital_at_risk, 2)

    @property
    def cost_to_close(self) -> float:
        """What buying the whole open book back would cost at current marks.

        The bridge between premium and profit. The credit is cash in hand and
        this is what is still owed against it; the difference is the only P&L
        that exists. `net_liq` computed it inline and threw it away, which left
        the page showing $1,046 of premium beside $53 of profit with nothing in
        between to explain the gap."""
        return round(sum(p.mark_cost_to_close * 100 * p.contracts
                         for p in self.open_positions), 2)

    @property
    def net_liq(self) -> float:
        """Cash minus the cost to close every open spread at its current mark."""
        return round(self.cash - self.cost_to_close, 2)

    @property
    def realized_pl(self) -> float:
        return round(sum(p.realized_pl for p in self.closed_positions), 2)

    @property
    def unrealized_pl(self) -> float:
        return round(sum(p.open_pl for p in self.open_positions), 2)

    @property
    def total_return(self) -> float:
        return round((self.net_liq - self.starting_cash) / self.starting_cash, 4)

    def sector_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.open_positions:
            out[p.sector] = out.get(p.sector, 0) + 1
        return out

    def ticker_counts(self) -> dict[str, int]:
        """Open positions per symbol.

        A count, not a set. `max_positions_per_ticker` above 1 is meaningless
        against a set -- membership answers "any?", and the cap asks "how
        many?". The risk gate held a set and compared it with `in`, so the
        setting printed its own number in the refusal message while behaving as
        1 whatever it was set to.
        """
        out: dict[str, int] = {}
        for p in self.open_positions:
            out[p.symbol] = out.get(p.symbol, 0) + 1
        return out

    def cooling_off(self, settings: Settings,
                    today: dt.date | None = None) -> dict[str, str]:
        """Symbols too recently closed at a loss to re-enter -> the date each clears.

        The gap this fills: `max_positions_per_ticker` stops the account
        HOLDING two spreads on a name at once. Nothing stopped it re-opening
        one minutes after a stop fired, and the agent did exactly that -- GOOGL
        was stopped out at 13:50 and re-proposed the same run, blocked only
        because the master switch happened to be off.

        That matters most in the case a stop is least trustworthy. A stop reads
        a mark, and a mark can be wrong; re-entering immediately re-establishes
        the same risk at a worse price, so one bad print gets paid for twice.
        Waiting a few days costs at most one entry in a name that is one of
        several hundred candidates.

        Losses only. A take-profit close leaves nothing to cool off from.
        """
        days = settings.reentry_cooldown_days
        if days <= 0:
            return {}
        today = today or dt.date.today()
        out: dict[str, str] = {}
        for p in self.closed_positions:
            if p.realized_pl >= LOSS_FLOOR or not p.closed_at:
                continue
            try:
                closed = dt.date.fromisoformat(p.closed_at[:10])
            except ValueError:
                continue          # an unparseable stamp must not bench forever
            clear = (closed + dt.timedelta(days=days)).isoformat()
            if clear > today.isoformat() and clear > out.get(p.symbol, ""):
                out[p.symbol] = clear
        return out

    def by_id(self, pid: str) -> Position | None:
        for p in self.positions:
            if p.id == pid or p.id.startswith(pid):
                return p
        return None

    # -- mutations ---------------------------------------------------------
    def open_position(self, pos: Position) -> Position:
        self.cash = round(self.cash + pos.credit_dollars, 2)
        self.positions.append(pos)
        self.log("position_opened", id=pos.id, symbol=pos.symbol,
                 strikes=f"{pos.short_strike:g}/{pos.long_strike:g}",
                 expiration=pos.expiration, contracts=pos.contracts,
                 credit=pos.credit_dollars, collateral=pos.collateral,
                 proposal_id=pos.proposal_id, approved_by=pos.approved_by)
        return pos

    def close_position(self, pos: Position, debit: float, reason: str,
                       fees: float = 0.0, status: str = CLOSED) -> Position:
        cost = round(debit * 100 * pos.contracts + fees, 2)
        self.cash = round(self.cash - cost, 2)
        pos.close_debit = debit
        pos.closed_at = dt.datetime.now().isoformat(timespec="seconds")
        pos.close_reason = reason
        pos.status = status
        pos.fees_paid = round(pos.fees_paid + fees, 2)
        pos.realized_pl = round(pos.credit_dollars - cost, 2)
        self.log("position_closed", id=pos.id, symbol=pos.symbol, reason=reason,
                 debit=debit, realized_pl=pos.realized_pl, status=status)
        return pos


def new_id() -> str:
    return uuid.uuid4().hex[:8]
