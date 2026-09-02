"""Paper accounting and the approval gate."""
import datetime as dt

import pytest
from conftest import make_chain

from pcs.ledger import EXPIRED, Ledger, Position
from pcs.optimizer import build_spreads
from pcs.paper_broker import (
    ApprovalRequired,
    cost_to_close,
    management_note,
    open_approved,
    settle_expired,
    simulated_fill_credit,
)


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def a_spread(settings, live_session):
    return build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)[0][0]


def test_opening_requires_an_approver(led, settings, live_session):
    with pytest.raises(ApprovalRequired):
        open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                      settings, "P1", approved_by="")


def test_live_mode_refuses_to_fill(led, settings, live_session):
    led.mode = "live"
    with pytest.raises(ApprovalRequired):
        open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                      settings, "P1", approved_by="human")


@pytest.mark.parametrize("quality", ["live", "closing_snapshot", "stale"])
def test_paper_fill_is_never_better_than_the_sizing_basis(settings, live_session, quality):
    """Regression: session-scaled sizing slippage once exceeded the fixed paper
    haircut, so a stale-quote proposal filled BETTER on paper than its ticket."""
    from pcs.session import SessionState
    sess = SessionState(None, True, "open", quality, "")
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, sess)[0][0]
    fill = simulated_fill_credit(sp, settings, sess)
    assert sp.credit_nat <= fill <= sp.credit


def test_open_updates_cash_collateral_and_buying_power(led, settings, live_session):
    sp = a_spread(settings, live_session)
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human")
    assert led.cash == round(3000.0 + pos.credit_dollars, 2)
    assert led.collateral_held == pos.collateral
    # buying power nets off the full width at risk, not the credit-adjusted
    # collateral -- see tests/test_balance.py for why.
    assert led.buying_power == round(led.cash - pos.width * 100 * pos.contracts, 2)
    assert any(e["kind"] == "position_opened" for e in led.events)


def test_round_trip_realized_pl(led, settings, live_session):
    pos = open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                        settings, "P1", "human")
    led.close_position(pos, debit=pos.credit_open * 0.45, reason="took profit")
    assert pos.realized_pl > 0
    assert led.collateral_held == 0
    assert led.buying_power == led.cash
    assert led.realized_pl == pos.realized_pl


def test_max_loss_is_capped_by_the_long_leg(led, settings, live_session):
    """The defining property of a credit spread: loss can never exceed collateral."""
    pos = open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                        settings, "P1", "human")
    debit, _ = settle_expired(pos, spot=0.01)          # catastrophic gap down
    led.close_position(pos, debit, "expired", status=EXPIRED)
    assert pos.realized_pl >= -pos.collateral - pos.fees_paid
    assert led.net_liq >= 3000.0 - pos.collateral - pos.fees_paid


@pytest.mark.parametrize("spot,expected", [(105.0, 0.0), (97.0, 0.0), (94.0, 3.0), (80.0, 5.0)])
def test_expiration_settlement(spot, expected):
    pos = Position(id="x", symbol="T", sector="S", expiration="2026-10-02",
                   short_strike=97.0, long_strike=92.0, width=5.0, contracts=1,
                   credit_open=1.2, credit_dollars=120, collateral=380,
                   opened_at="", opened_spot=100.0)
    assert settle_expired(pos, spot)[0] == expected


def _held(pct_captured: float) -> Position:
    return Position(id="x", symbol="T", sector="S", expiration="2099-01-01",
                    short_strike=97.0, long_strike=92.0, width=5.0, contracts=1,
                    credit_open=1.20, credit_dollars=120, collateral=380,
                    opened_at="", opened_spot=100.0, mark_spot=101.0,
                    mark_cost_to_close=round(1.20 * (1 - pct_captured), 2))


def test_take_profit_advice_shouts_above_the_top_of_the_band():
    """Past the upper edge the position should already have been closed, so the
    note stops being on-plan and starts being a flag."""
    assert "TAKE PROFIT" in management_note(_held(0.80))


def test_inside_the_band_the_advice_says_it_is_on_plan():
    note = management_note(_held(0.60))
    assert "TAKE PROFIT" not in note and "on-plan" in note


def test_tested_strike_advice_never_suggests_removing_the_hedge():
    pos = Position(id="x", symbol="T", sector="S",
                   expiration=(dt.date.today() + dt.timedelta(days=3)).isoformat(),
                   short_strike=97.0, long_strike=92.0, width=5.0, contracts=1,
                   credit_open=1.20, credit_dollars=120, collateral=380,
                   opened_at="", opened_spot=100.0, mark_spot=95.0,
                   mark_cost_to_close=2.40)
    note = management_note(pos)
    assert "roll down-and-out" in note and "Never remove the long leg" in note


def test_cost_to_close_is_pessimistic(settings):
    chain = make_chain(spot=100.0)
    pos = Position(id="x", symbol="TST", sector="S", expiration=chain.expiration,
                   short_strike=97.0, long_strike=92.0, width=5.0, contracts=1,
                   credit_open=1.0, credit_dollars=100, collateral=400,
                   opened_at="", opened_spot=100.0)
    debit = cost_to_close(chain, pos, settings)
    sq, lq = chain.at(97.0), chain.at(92.0)
    assert debit >= sq.mid - lq.mid          # never better than the mid


# --- the two totals that used to disagree ------------------------------------
def test_unrealized_pl_reconciles_with_net_liq(settings, tmp_path):
    """The dashboard showed unrealised +$53.63 and a net liq implying +$53.15.
    `credit_dollars` is banked net of fill fees; `open_pl` used to recompute
    from the gross quote, so it overstated every position by its own fees."""
    led = Ledger.load(settings, path=tmp_path / "l.json")
    led.cash = led.starting_cash
    for sym, credit in (("A", 4.16), ("B", 3.67), ("C", 1.47), ("D", 1.82)):
        p = Position(id=sym, symbol=sym, sector="S", expiration="2099-01-01",
                     short_strike=100.0, long_strike=95.0, width=5.0, contracts=1,
                     credit_open=credit, credit_dollars=round(credit * 100 - 0.12, 2),
                     collateral=500 - credit * 100, opened_at="", opened_spot=110.0)
        p.mark_spot = 110.0
        p.mark_cost_to_close = round(credit * 0.87, 2)
        led.positions.append(p)
        led.cash = round(led.cash + p.credit_dollars, 2)
    assert led.fees_on_open == 0.48
    assert led.cash == round(led.starting_cash + led.premium_collected, 2)
    assert round(led.net_liq - led.starting_cash, 2) == led.unrealized_pl


def test_pct_of_max_credit_is_measured_on_the_credit_actually_banked(settings):
    """Same basis as open_pl, so the exit rules fire on money kept."""
    p = Position(id="x", symbol="T", sector="S", expiration="2099-01-01",
                 short_strike=97.0, long_strike=92.0, width=5.0, contracts=1,
                 credit_open=1.20, credit_dollars=119.88, collateral=380,
                 opened_at="", opened_spot=100.0, mark_spot=101.0,
                 mark_cost_to_close=0.60)
    assert p.open_fees == 0.12
    assert p.gross_credit == 120.0
    assert p.open_pl == round(119.88 - 60.0, 2)
    assert p.pct_of_max_credit == round(p.open_pl / 119.88, 4)


def test_cost_to_close_is_the_liability_net_liq_subtracts(settings, tmp_path):
    """`net_liq` computed this inline and discarded it, so the page could say
    what the account was worth but never what stood between the premium and
    the profit. One property, used by both."""
    led = Ledger.load(settings, path=tmp_path / "ledger.json")
    led.cash = 4046.40
    for sym, credit, debit in (("MRVL", 4.16, 3.61), ("META", 3.67, 3.10)):
        p = _held(0.0)
        p.symbol, p.credit_open = sym, credit
        p.credit_dollars = round(credit * 100 - 0.12, 2)
        p.mark_cost_to_close = debit
        led.positions.append(p)
    assert led.cost_to_close == round(
        sum(p.mark_cost_to_close * 100 * p.contracts for p in led.open_positions), 2)
    assert led.net_liq == round(led.cash - led.cost_to_close, 2)
    assert round(led.premium_collected - led.cost_to_close, 2) == led.unrealized_pl
