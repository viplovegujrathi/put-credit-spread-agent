"""The mark a stop is allowed to fire on.

Written after a live paper loss. GOOGL 325/320 was sold for $1.1625 on
2026-09-01 at 14:39 UTC. The next morning at 13:50 UTC -- 9:50 ET, twenty
minutes into the session -- it marked at $3.2544, 2.8x the credit, and
`apply_exits` closed it for a realized -$209.31.

The stock had gone 336.64 -> 335.26. Down 0.41%, and still 3.1% clear of the
short strike, which was never touched. Nothing about GOOGL moved $2 of spread
value; the mark did that on its own.

The mechanism is `cost_to_close`'s worst case, `nat = sq.ask - lq.bid`. With
the long leg's bid at zero -- a free feed dropping a quiet strike, which
happens most often in exactly those first minutes -- the hedge stops
subtracting and a five-wide defined-risk spread gets valued as a naked short
put. `PutQuote.mid` degrades the same way one line earlier: with no broker mark
and no two-sided book it falls through to `last`, then to whichever side is
non-zero, and returns that as though it were a quote.

The stop was not wrong. It acted correctly on a number that was wrong, and the
account paid $209 for it.

These tests pin the guard: a spread that cannot be priced honestly is not
priced at all. That is deliberately the lenient direction -- refusing to mark
only delays a decision, and `mark` still reports the position under "could not
decide" where a human can see it. Marking badly makes the decision, and here it
makes it as a market order.
"""
import datetime as dt

import pytest
from conftest import make_chain

from pcs import exits
from pcs.chains import PutChain, PutQuote
from pcs.ledger import Ledger, Position
from pcs.paper_broker import cost_to_close, mark_positions, unpriceable

EXPIRY = (dt.date.today() + dt.timedelta(days=30)).isoformat()


def two_leg_chain(short=(3.10, 3.30), long_=(1.95, 2.15), spot=335.26,
                  mark=False, drop_long=False) -> PutChain:
    """A 325/320 chain with both legs quoted exactly as given.

    `short`/`long_` are (bid, ask); pass 0 for a side the feed did not supply.
    `mark` adds the broker's own mark, which Robinhood gives and Yahoo does not.
    """
    puts = []
    for strike, (bid, ask) in ((325.0, short), (320.0, long_)):
        if drop_long and strike == 320.0:
            continue
        puts.append(PutQuote(
            strike=strike, bid=bid, ask=ask, last=0.0, volume=40,
            open_interest=500, iv=0.30,
            mark=round((bid + ask) / 2, 4) if mark else 0.0))
    return PutChain("GOOGL", EXPIRY, spot, puts, "test", "now")


def googl() -> Position:
    """The position as the ledger actually recorded it."""
    return Position(
        id="x", symbol="GOOGL", sector="Communication Services", expiration=EXPIRY,
        short_strike=325.0, long_strike=320.0, width=5.0, contracts=1,
        credit_open=1.1625, credit_dollars=116.19, collateral=383.75,
        opened_at="2026-09-01T14:39:25", opened_spot=336.64, mark_spot=335.26)


# --- the incident ------------------------------------------------------------
def test_the_googl_stop_out_could_not_happen_now(settings):
    """The whole point. Same chain, same position: no mark, so no decision."""
    chain = two_leg_chain(long_=(0.0, 0.0))       # the feed dropped the long leg
    assert cost_to_close(chain, googl(), settings) is None


def test_that_chain_really_would_have_fired_the_stop(settings):
    """Guards the guard: if the old arithmetic no longer stops this position
    out, the test above passes for the wrong reason and proves nothing."""
    chain = two_leg_chain(long_=(0.0, 0.0))
    pos = googl()
    sq, lq = chain.at(325.0), chain.at(320.0)
    mid, nat = sq.mid - lq.mid, sq.ask - lq.bid
    old = round(max(mid + settings.paper_slippage_frac * (nat - mid), 0.0), 4)

    pos.mark_cost_to_close = old
    decision = exits.decide(pos, settings)
    assert decision.action == exits.STOP_LOSS and decision.act
    assert old * 100 >= pos.credit_open * 100 * settings.stop_loss_credit_multiple


def test_the_hedge_is_what_the_bad_mark_threw_away(settings):
    """Same short leg, long leg quoted: the spread is worth about a dollar,
    nowhere near the 2x stop. The $2 of 'loss' was the missing bid."""
    honest = cost_to_close(two_leg_chain(), googl(), settings)
    assert honest is not None
    assert honest < googl().credit_open * settings.stop_loss_credit_multiple


# --- what still prices -------------------------------------------------------
def test_a_broker_mark_is_enough_without_a_two_sided_book(settings):
    """Robinhood supplies a mark and can leave a side empty. That is a quote,
    not a hole, and refusing it would blind the agent to its better feed."""
    chain = two_leg_chain(long_=(0.0, 2.15), mark=True)
    assert cost_to_close(chain, googl(), settings) is not None


def test_a_good_mark_still_stops_a_real_loser(settings):
    """The guard must not have quietly disabled the stop."""
    pos = googl()
    pos.mark_cost_to_close = cost_to_close(
        two_leg_chain(short=(4.60, 4.80), long_=(2.05, 2.25)), pos, settings)
    decision = exits.decide(pos, settings)
    assert decision.action == exits.STOP_LOSS and decision.act


def test_an_ordinary_chain_is_unaffected(settings):
    """Everything the optimizer builds against still marks as it always did."""
    chain = make_chain(spot=100.0)
    pos = Position(id="y", symbol="TST", sector="S", expiration=chain.expiration,
                   short_strike=97.0, long_strike=92.0, width=5.0, contracts=1,
                   credit_open=1.0, credit_dollars=100, collateral=400,
                   opened_at="", opened_spot=100.0)
    debit = cost_to_close(chain, pos, settings)
    assert debit >= chain.at(97.0).mid - chain.at(92.0).mid


# --- the arithmetic bound ----------------------------------------------------
def test_the_close_can_never_cost_more_than_the_width(settings):
    """Not caution -- the long leg caps the loss, so paying more than the width
    would be paying more than the max loss the position was opened against."""
    chain = two_leg_chain(short=(9.00, 9.60), long_=(1.00, 1.20))
    assert cost_to_close(chain, googl(), settings) == 5.0


# --- what the record says afterwards ----------------------------------------
def test_the_fault_names_the_leg_that_failed(settings):
    """"strikes missing from chain" was the only way this could once fail, so
    it was hard-coded. A one-sided quote is a different fault with a different
    fix, and one word for both sends whoever reads the journal to the wrong
    place."""
    why = unpriceable(two_leg_chain(long_=(0.0, 0.0)), googl())
    assert "long leg 320" in why and "one-sided" in why
    assert "missing from the chain" not in why

    gone = unpriceable(two_leg_chain(drop_long=True), googl())
    assert "long leg 320 missing from the chain" == gone

    assert unpriceable(two_leg_chain(), googl()) == ""


def test_an_unpriceable_position_is_withheld_from_the_exit_rules(
        settings, tmp_path, monkeypatch):
    """`fresh` is the contract between marking and deciding: exits.review skips
    anything not in it, so a refused mark cannot reach apply_exits at all."""
    import pcs.paper_broker as pb
    monkeypatch.setattr(pb, "get_chain",
                        lambda *a, **k: two_leg_chain(long_=(0.0, 0.0)))
    led = Ledger.load(settings, path=tmp_path / "l.json")
    led.positions = [googl()]

    notes, fresh = mark_positions(led, settings, {"GOOGL": 335.26})

    assert fresh == set()
    assert exits.review(led, settings, fresh) == []
    note = {p.symbol: n for p, n in notes}["GOOGL"]
    assert note.startswith("could not mark") and "one-sided" in note


def test_the_mark_is_left_alone_when_it_cannot_be_refreshed(
        settings, tmp_path, monkeypatch):
    """A refused mark must not overwrite the last good one with a zero -- the
    dashboard reads `marked_at` to say how stale it is, and a silent reset
    would make a three-day-old number look fresh."""
    import pcs.paper_broker as pb
    monkeypatch.setattr(pb, "get_chain",
                        lambda *a, **k: two_leg_chain(long_=(0.0, 0.0)))
    led = Ledger.load(settings, path=tmp_path / "l.json")
    pos = googl()
    pos.mark_cost_to_close, pos.marked_at = 0.94, "2026-09-01T14:39:25"
    led.positions = [pos]

    mark_positions(led, settings, {"GOOGL": 335.26})

    assert pos.mark_cost_to_close == 0.94
    assert pos.marked_at == "2026-09-01T14:39:25"


@pytest.mark.parametrize("short,long_", [((0.0, 0.0), (1.95, 2.15)),
                                         ((3.10, 3.30), (0.0, 0.0)),
                                         ((0.0, 3.30), (1.95, 2.15))])
def test_either_leg_without_a_book_refuses(settings, short, long_):
    assert cost_to_close(two_leg_chain(short, long_), googl(), settings) is None
