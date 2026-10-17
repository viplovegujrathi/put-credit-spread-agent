"""The portfolio collateral cap and the credit floor, at the price the paper fills.

Both were checked on the ticket only. The paper fill is worse than the ticket by
design, and a smaller credit is a larger collateral, so a ticket inside a cap can
fill outside it. 2026-10-16 did both at once: five tickets that `risk.check` had
summed to $2,396.64 with ORCL already open filled at $2,412.37 against the
$2,400 cap, and DAL among them was sized at a $100.32 credit and filled at
$98.87, under the $100 floor. The per-trade cap already had this re-check
(TTWO, 2026-10-08); these are the other two.
"""
import pytest
from conftest import make_chain

from pcs.config import STRATEGY
from pcs.ledger import Ledger
from pcs.optimizer import build_spreads
from pcs.paper_broker import (
    BelowCreditFloor,
    InsufficientFunds,
    open_approved,
    simulated_fill_credit,
)
from pcs.risk import PortfolioView, check


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 10_000.0     # isolate the caps from the balance floor
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def _spread(symbol, settings, sess, mid_short=7.30):
    """A 95/80 put spread on a $100 stock with $1.00-wide legs, so the ticket
    and the fill are $10 of collateral apart."""
    chain = make_chain(symbol=symbol, spot=100.0, strikes=[95.0, 80.0], spread=0.50,
                       price_at=lambda k: mid_short if k == 95.0 else 2.00)
    (sp,) = [s for s in build_spreads(chain, 100.0, settings, sess)[0]
             if (s.short_strike, s.long_strike) == (95.0, 80.0)]
    return sp


def _filled_collateral(sp, settings, sess):
    return round(sp.width * 100 - simulated_fill_credit(sp, settings, sess) * 100, 2)


# -- the portfolio collateral cap ------------------------------------------
def test_a_ticket_inside_the_portfolio_cap_cannot_fill_outside_it(led, settings,
                                                                  live_session):
    first = open_approved(led, _spread("AAA", settings, live_session), "Energy", 1,
                          settings, "P1", "human", sess=live_session)
    sp = _spread("BBB", settings, live_session)
    assert _filled_collateral(sp, settings, live_session) > sp.collateral
    settings.max_total_collateral = round(first.collateral + sp.collateral, 2)

    # The screen passes it: the ticket fits the cap exactly.
    pv = PortfolioView(led.collateral_held, 1, {"Energy": 1}, {}, led.cash,
                       led.buying_power)
    assert check(sp, "Materials", pv, settings, sess=live_session).ok

    cash, positions = led.cash, len(led.positions)
    with pytest.raises(InsufficientFunds, match="portfolio collateral") as exc:
        open_approved(led, sp, "Materials", 1, settings, "P2", "human",
                      sess=live_session)
    assert f"${settings.max_total_collateral:,.0f} limit" in str(exc.value)
    assert (led.cash, len(led.positions)) == (cash, positions)
    assert led.collateral_held == first.collateral


def test_a_fill_that_lands_exactly_on_the_portfolio_cap_opens(led, settings,
                                                              live_session):
    first = open_approved(led, _spread("AAA", settings, live_session), "Energy", 1,
                          settings, "P1", "human", sess=live_session)
    sp = _spread("BBB", settings, live_session)
    settings.max_total_collateral = round(
        first.collateral + _filled_collateral(sp, settings, live_session), 2)
    open_approved(led, sp, "Materials", 1, settings, "P2", "human", sess=live_session)
    assert led.collateral_held == settings.max_total_collateral


def test_the_portfolio_cap_counts_fills_made_earlier_in_the_same_batch(
        led, settings, live_session):
    """The 10-16 shape: a batch whose tickets sum to just under the cap. Each
    fill is checked against the fills before it, so only the last one is held."""
    tickets = [_spread(s, settings, live_session) for s in ("AAA", "BBB", "CCC")]
    settings.max_total_collateral = round(sum(t.collateral for t in tickets), 2)
    sectors = ("Energy", "Materials", "Utilities")
    for i in range(2):
        open_approved(led, tickets[i], sectors[i], 1, settings, f"P{i}", "human",
                      sess=live_session)
    with pytest.raises(InsufficientFunds, match="portfolio collateral"):
        open_approved(led, tickets[2], sectors[2], 1, settings, "P2", "human",
                      sess=live_session)
    assert len(led.open_positions) == 2
    assert led.collateral_held <= settings.max_total_collateral


# -- the credit floor -----------------------------------------------------
def _near_floor(mid_short: float):
    """One 95/90 put spread with $0.30-wide legs: package mid `mid_short - 1.00`,
    natural mid - 0.30. A ticket sized at 0.15 of the gap and a fill at 0.25 of
    it are three cents a share apart, $3 of credit."""
    return make_chain(spot=100.0, strikes=[95.0, 90.0], spread=0.15,
                      price_at=lambda k: mid_short if k == 95.0 else 1.00)


def _floor_spread(settings, sess, mid_short):
    (sp,) = [s for s in build_spreads(_near_floor(mid_short), 100.0, settings, sess)[0]
             if (s.short_strike, s.long_strike) == (95.0, 90.0)]
    return sp


def test_a_ticket_over_the_credit_floor_cannot_fill_under_it(led, settings,
                                                            live_session):
    sp = _floor_spread(settings, live_session, 2.06)     # ticket $101.50, fill $98.50
    fill = simulated_fill_credit(sp, settings, live_session)
    assert sp.credit_dollars >= STRATEGY.min_credit_per_trade > fill * 100

    cash = led.cash
    with pytest.raises(BelowCreditFloor) as exc:
        open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                      sess=live_session)
    assert f"${STRATEGY.min_credit_per_trade:,.0f} per-trade floor" in str(exc.value)
    assert f"sized at ${sp.credit_dollars:,.2f}" in str(exc.value)
    assert led.cash == cash and not led.positions


def test_a_fill_with_room_over_the_credit_floor_still_opens(led, settings,
                                                           live_session):
    sp = _floor_spread(settings, live_session, 2.08)     # ticket $103.50, fill $100.50
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                        sess=live_session)
    assert STRATEGY.min_credit_per_trade <= pos.credit_open * 100 <= sp.credit_dollars


def test_the_credit_floor_follows_the_settings_override(led, settings, live_session):
    """`settings.strategy()` is what the screen sized against, so it is what the
    fill is held to -- not the frozen default."""
    sp = _floor_spread(settings, live_session, 2.20)     # fill $110.50
    settings.min_credit_per_trade = 115.0
    with pytest.raises(BelowCreditFloor, match=r"\$115 per-trade floor"):
        open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                      sess=live_session)
