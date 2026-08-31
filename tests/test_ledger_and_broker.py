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
    assert led.buying_power == round(led.cash - pos.collateral, 2)
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


def test_take_profit_advice_fires_inside_the_band():
    pos = Position(id="x", symbol="T", sector="S", expiration="2099-01-01",
                   short_strike=97.0, long_strike=92.0, width=5.0, contracts=1,
                   credit_open=1.20, credit_dollars=120, collateral=380,
                   opened_at="", opened_spot=100.0, mark_spot=101.0,
                   mark_cost_to_close=0.36)                 # 70% of max credit
    assert "TAKE PROFIT" in management_note(pos)


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
