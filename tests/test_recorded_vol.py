"""A constant is not a reading: the entry vol the record was storing.

LEARNING.md 47 left one question open -- did the stops on untouched strikes
fire on vol? -- and made `iv_at_open` and `mark_iv` the fields that would
answer it. Both had a guard, `... or None`, written to keep a fabricated value
out. Both only ever caught a zero:

  * the optimizer fell back to a flat 0.30 when a chain quoted no IV, so the
    placeholder became the spread's IV, its delta and its POP estimate, and
    `spread.iv or None` wrote 0.30 onto the position as the vol it was sold at;
  * a leg with no IV borrowed the OTHER leg's, which is a different strike;
  * the model provider stamps its 0.30 input on every strike, and
    `sq.iv or None` stored that at every mark.

The test that claimed to cover it fed in 0.0, a value no provider produces.
"""
import pytest
from conftest import make_chain

from pcs.ledger import Ledger
from pcs.optimizer import build_spreads
from pcs.paper_broker import open_approved
from pcs.proposer import Proposal, ticket


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def _fill(led, sp, settings, sess):
    return open_approved(led, sp, "Energy", 1, settings, "P1", "human", sess=sess)


def _borrowed(settings, live_session):
    """A chain whose short strikes quote no vol but whose lower strikes do: the
    delta is estimated off the long leg's. Returns the first spread on 95."""
    chain = make_chain(iv=0.0)
    for q in chain.puts:
        if q.strike < 95:
            q.iv = 0.35
    sps, _ = build_spreads(chain, 100.0, settings, live_session)
    return next(s for s in sps if s.short_strike == 95)


def test_no_quoted_vol_means_no_spread_rather_than_a_placeholder(settings, live_session):
    """The delta ceiling needs a delta. With no vol on either leg there is none,
    and the spread is not taken -- it is not given a constant to pass on."""
    sps, rejects = build_spreads(make_chain(iv=0.0), 100.0, settings, live_session)
    assert sps == []
    assert any("no delta to check" in r for r in rejects)


def test_the_placeholder_never_reaches_the_position(settings, led, live_session):
    pos = _fill(led, _borrowed(settings, live_session), settings, live_session)
    assert pos.iv_at_open is None


def test_the_long_legs_vol_is_not_recorded_as_the_short_legs(settings, led, live_session):
    """Borrowing the long leg's IV to estimate a delta is a fair approximation.
    Writing it down as the short leg's vol at entry is not -- it is another
    strike, and `iv_change` would compare the short leg at the mark against it."""
    sp = _borrowed(settings, live_session)
    assert sp.iv == 0.35 and not sp.iv_measured
    assert sp.short_delta is not None          # still usable for the estimate

    assert _fill(led, sp, settings, live_session).iv_at_open is None


def test_the_short_legs_own_quote_is_recorded(settings, led, live_session):
    sp = build_spreads(make_chain(iv=0.37), 100.0, settings, live_session)[0][0]
    assert sp.iv_measured
    assert _fill(led, sp, settings, live_session).iv_at_open == 0.37


def test_a_ticket_written_before_the_flag_records_nothing(settings, led, live_session):
    """`proposals.json` can hold tickets sized by the old code, whose IV may be
    the placeholder. With no `iv_measured` on them there is no telling, so
    they record None rather than guess."""
    from pcs.optimizer import Spread
    blob = build_spreads(make_chain(iv=0.37), 100.0, settings, live_session)[0][0].as_dict()
    del blob["iv_measured"]
    pos = _fill(led, Spread(**blob), settings, live_session)
    assert pos.iv_at_open is None


def test_the_ticket_says_unquoted_rather_than_zero(settings, live_session):
    from dataclasses import replace
    sp = replace(build_spreads(make_chain(iv=0.37), 100.0, settings, live_session)[0][0],
                 iv=0.0, iv_measured=False, short_delta=None, pop_est=None,
                 pop_source="unavailable")
    p = Proposal(id="P1", created_at="", symbol="TST", name="TST Inc", sector="Energy",
                 bucket="primary", spread=sp.as_dict(), contracts=1, rationale="",
                 risk_ok=True)
    text = ticket(p, settings)
    assert "IV not quoted" in text
    assert "IV 0.0%" not in text
