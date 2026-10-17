"""The portfolio collateral cap and the credit floor, at the price the paper fills.

Both were checked on the ticket only. The paper fill is worse than the ticket by
design, and a smaller credit is a larger collateral, so a ticket inside a cap can
fill outside it. 2026-10-16 did both at once: five tickets that `risk.check` had
summed to $2,396.64 with ORCL already open filled at $2,412.37 against the
$2,400 cap, and DAL among them was sized at a $100.32 credit and filled at
$98.87, under the $100 floor. The per-trade cap already had this re-check
(TTWO, 2026-10-08); these are the other two.

Both are checked twice, as the per-trade cap is. The screen holds the ticket to
them at the EXPECTED fill, so a ticket the fill would refuse is never proposed.
The fill holds the position to them at the ACTUAL fill, which can be worse
still: a ticket sized in a live market and approved by hand once the quotes
have gone stale fills further from the mid.
"""
import pytest
from conftest import make_chain

from pcs import pipeline
from pcs.config import STRATEGY
from pcs.ledger import Ledger
from pcs.optimizer import build_spreads, fill_collateral
from pcs.paper_broker import (
    BelowCreditFloor,
    InsufficientFunds,
    open_approved,
    simulated_fill_credit,
)
from pcs.risk import PortfolioView, check
from pcs.screener import Candidate
from pcs.session import SessionState

# A manual approve after the quotes went stale: the fill sits 0.60 of the way
# from the mid to the natural, against 0.25 in a live market.
STALE = SessionState(None, True, "open", "stale", "")


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 10_000.0     # isolate the caps from the balance floor
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def _spread(symbol, settings, sess, mid_short=2.50):
    """A 95/90 put spread on a $100 stock: package mid `mid_short - 1.00`, legs
    $0.30 wide, so the natural is the mid less $0.30. Live, the ticket and the
    fill are $3 of collateral apart; stale, the fill is $10.50 further."""
    chain = make_chain(symbol=symbol, spot=100.0, strikes=[95.0, 90.0], spread=0.15,
                       price_at=lambda k: mid_short if k == 95.0 else 1.00)
    found = [s for s in build_spreads(chain, 100.0, settings, sess)[0]
             if (s.short_strike, s.long_strike) == (95.0, 90.0)]
    return found[0] if found else None


def _pv(led):
    return PortfolioView(led.collateral_held, len(led.open_positions),
                         led.sector_counts(), led.issuer_counts(), led.cash,
                         led.buying_power)


# -- the portfolio collateral cap, at the screen ----------------------------
def test_the_screen_sizes_the_portfolio_cap_at_the_expected_fill(led, settings,
                                                                 live_session):
    first = open_approved(led, _spread("AAA", settings, live_session), "Energy", 1,
                          settings, "P1", "human", sess=live_session)
    sp = _spread("BBB", settings, live_session)
    filled = fill_collateral(sp, live_session, settings)
    assert filled > sp.collateral

    settings.max_total_collateral = round(first.collateral + sp.collateral, 2)
    v = check(sp, "Materials", _pv(led), settings, sess=live_session)
    assert not v.ok
    assert any("portfolio collateral cap" in r and "at the expected fill" in r
               for r in v.reasons)
    # With no session there is no fill to predict, and it is the ticket's figure.
    assert check(sp, "Materials", _pv(led), settings).ok

    settings.max_total_collateral = round(first.collateral + filled, 2)
    assert check(sp, "Materials", _pv(led), settings, sess=live_session).ok


def test_what_the_screen_passes_the_fill_opens_to_the_cent(led, settings, live_session):
    """One formula: the collateral the screen checked is the collateral written."""
    for i, mid in enumerate((2.40, 2.50, 2.60, 2.75)):
        sp = _spread(f"S{i}", settings, live_session, mid_short=mid)
        pos = open_approved(led, sp, f"Sector{i}", 1, settings, f"P{i}", "human",
                            sess=live_session)
        assert pos.collateral == fill_collateral(sp, live_session, settings)


def test_the_screen_sizes_the_balance_at_the_expected_fill(settings, live_session):
    sp = _spread("AAA", settings, live_session)
    filled = fill_collateral(sp, live_session, settings)
    for bal, ok in ((round(sp.collateral + sp.fees, 2), False),
                    (round(filled + sp.fees, 2), True)):
        pv = PortfolioView(0, 0, {}, {}, bal, bal)
        v = check(sp, "Energy", pv, settings, sess=live_session)
        assert v.ok is ok
        if not ok:
            assert any("available balance" in r and "at the expected fill" in r
                       for r in v.reasons)


def _candidate(symbol, sector):
    return Candidate(symbol=symbol, name=f"{symbol} Inc", sector=sector, bucket="tight",
                     note="", spot=100.0, dma50=110.0, pct_from_dma50=-0.09,
                     pct_off_high=0.25, high_52w=130.0, low_52w=90.0,
                     avg_vol_30d=1e6, last_bar="2026-10-16",
                     earnings_date="2026-12-01", earnings_source="test",
                     earnings_in_window=False)


def test_a_batch_is_summed_at_its_expected_fills(led, settings, live_session):
    """The 10-16 shape, at the screen: three tickets that sum to exactly the cap.
    Summed as fills they do not fit, so the third is not cleared to open."""
    names = (("AAA", "Energy"), ("BBB", "Materials"), ("CCC", "Utilities"))
    sized = [pipeline.SizedCandidate(_candidate(s, sec),
                                     [_spread(s, settings, live_session)], {})
             for s, sec in names]
    settings.max_total_collateral = round(
        sum(sc.spreads[0].collateral for sc in sized), 2)
    res = pipeline.ScreenResult(live_session, None, [], {}, {})
    props, _ = pipeline.build_proposals(sized, res, led, settings)
    assert [p.risk_ok for p in props] == [True, True, False]
    assert any("portfolio collateral cap" in r for r in props[2].risk_reasons)

    filled = sum(fill_collateral(sc.spreads[0], live_session, settings) for sc in sized)
    settings.max_total_collateral = round(filled, 2)
    props, _ = pipeline.build_proposals(sized, res, led, settings)
    assert all(p.risk_ok for p in props)


# -- the portfolio collateral cap, at the fill ------------------------------
def test_a_ticket_inside_the_portfolio_cap_cannot_fill_outside_it(led, settings,
                                                                  live_session):
    first = open_approved(led, _spread("AAA", settings, live_session), "Energy", 1,
                          settings, "P1", "human", sess=live_session)
    sp = _spread("BBB", settings, live_session)
    settings.max_total_collateral = round(
        first.collateral + fill_collateral(sp, live_session, settings), 2)
    assert check(sp, "Materials", _pv(led), settings, sess=live_session).ok
    assert fill_collateral(sp, STALE, settings) > fill_collateral(sp, live_session, settings)

    cash, positions = led.cash, len(led.positions)
    with pytest.raises(InsufficientFunds, match="portfolio collateral") as exc:
        open_approved(led, sp, "Materials", 1, settings, "P2", "human", sess=STALE)
    assert f"${settings.max_total_collateral:,.0f} limit" in str(exc.value)
    assert (led.cash, len(led.positions)) == (cash, positions)
    assert led.collateral_held == first.collateral


def test_a_fill_that_lands_exactly_on_the_portfolio_cap_opens(led, settings,
                                                              live_session):
    first = open_approved(led, _spread("AAA", settings, live_session), "Energy", 1,
                          settings, "P1", "human", sess=live_session)
    sp = _spread("BBB", settings, live_session)
    settings.max_total_collateral = round(
        first.collateral + fill_collateral(sp, live_session, settings), 2)
    open_approved(led, sp, "Materials", 1, settings, "P2", "human", sess=live_session)
    assert led.collateral_held == settings.max_total_collateral


def test_the_portfolio_cap_counts_fills_made_earlier_in_the_same_batch(
        led, settings, live_session):
    """Each fill is checked against the fills before it, so only the last of a
    batch that does not fit is held."""
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


# -- the credit floor, at the screen ----------------------------------------
def _near_floor(mid_short: float):
    """One 95/90 put spread with $0.30-wide legs: package mid `mid_short - 1.00`,
    natural mid - 0.30. A ticket sized at 0.15 of the gap and a fill at 0.25 of
    it are three cents a share apart, $3 of credit."""
    return make_chain(spot=100.0, strikes=[95.0, 90.0], spread=0.15,
                      price_at=lambda k: mid_short if k == 95.0 else 1.00)


def _floor_spread(settings, sess, mid_short):
    found = [s for s in build_spreads(_near_floor(mid_short), 100.0, settings, sess)[0]
             if (s.short_strike, s.long_strike) == (95.0, 90.0)]
    return found[0] if found else None


def test_a_ticket_that_clears_the_floor_only_at_its_own_price_is_not_proposed(
        settings, live_session):
    """DAL's shape: a ticket over $100 whose fill comes in under it."""
    spreads, rejects = build_spreads(_near_floor(2.06), 100.0, settings, live_session)
    assert not spreads                                   # ticket $101.50, fill $98.50
    assert any(r.startswith("credit $98.50 at the expected fill") for r in rejects)


@pytest.mark.parametrize("mid_short", [2.02, 2.04, 2.06, 2.075, 2.08, 2.10, 2.15])
def test_whatever_the_screen_proposes_the_fill_clears_the_floor(led, settings,
                                                                live_session, mid_short):
    sp = _floor_spread(settings, live_session, mid_short)
    if sp is None:                                       # refused at the screen
        return
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                        sess=live_session)
    assert pos.credit_open * 100 >= STRATEGY.min_credit_per_trade


# -- the credit floor, at the fill ------------------------------------------
def test_a_ticket_over_the_credit_floor_cannot_fill_under_it(led, settings,
                                                            live_session):
    sp = _floor_spread(settings, live_session, 2.10)   # ticket $105.50, live fill $102.50
    assert sp is not None
    fill = simulated_fill_credit(sp, settings, STALE)  # stale, $92.00
    assert sp.credit_dollars >= STRATEGY.min_credit_per_trade > fill * 100

    cash = led.cash
    with pytest.raises(BelowCreditFloor) as exc:
        open_approved(led, sp, "Industrials", 1, settings, "P1", "human", sess=STALE)
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


def test_the_stale_session_used_here_can_open(settings):
    """The fill-side tests lean on a manual approve in a stale session being
    allowed at all; if it stops being allowed they test nothing."""
    assert STALE.can_open_positions
