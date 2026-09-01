"""The account balance is the floor beneath every other cap.

A position can never commit more max loss than the account has free cash to
pay. These tests exist because `buying power` was originally defined as
`cash - collateral_held`, which double-counts the credit: cash already includes
the premium and collateral is measured net of it. That overstated the free
balance by exactly the credit received, so the account could be talked into
committing more than it held.
"""
import pytest
from conftest import make_chain

from pcs.config import STRATEGY
from pcs.ledger import Ledger
from pcs.optimizer import build_spreads
from pcs.paper_broker import InsufficientFunds, open_approved, simulated_fill_credit
from pcs.risk import PortfolioView, check


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def a_spread(settings, live_session, spot=100.0):
    return build_spreads(make_chain(spot=spot), spot, settings, live_session)[0][0]


# -- the accounting itself -------------------------------------------------
def test_buying_power_is_cash_less_the_full_width_at_risk(led, settings, live_session):
    pos = open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                        settings, "P1", "human", sess=live_session)
    at_risk = pos.width * 100 * pos.contracts
    assert led.capital_at_risk == at_risk
    assert led.buying_power == round(led.cash - at_risk, 2)
    # and the intuitive identity: what you started with, less what you committed
    assert led.buying_power == pytest.approx(
        3000.0 - pos.collateral - pos.fees_paid, abs=0.02)


def test_buying_power_does_not_double_count_the_credit(led, settings, live_session):
    """Regression: the old `cash - collateral_held` reported MORE free balance
    than the account had, by the exact size of the premium taken in."""
    pos = open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                        settings, "P1", "human", sess=live_session)
    old = round(led.cash - led.collateral_held, 2)
    assert old > led.buying_power
    assert round(old - led.buying_power, 2) == round(pos.credit_open * 100 * pos.contracts, 2)


def test_balance_is_restored_when_the_position_closes(led, settings, live_session):
    pos = open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                        settings, "P1", "human", sess=live_session)
    led.close_position(pos, debit=pos.credit_open * 0.45, reason="took profit")
    assert led.capital_at_risk == 0
    assert led.buying_power == led.cash


# -- the gate at fill time -------------------------------------------------
def test_a_position_larger_than_the_balance_is_refused(led, settings, live_session):
    led.cash = 200.0                       # less than any spread's max loss
    with pytest.raises(InsufficientFunds):
        open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                      settings, "P1", "human", sess=live_session)


def test_a_refused_open_leaves_the_ledger_completely_untouched(led, settings, live_session):
    led.cash = 200.0
    before = (led.cash, len(led.positions), len(led.events))
    with pytest.raises(InsufficientFunds):
        open_approved(led, a_spread(settings, live_session), "Industrials", 1,
                      settings, "P1", "human", sess=live_session)
    assert (led.cash, len(led.positions), len(led.events)) == before


def test_the_balance_can_never_be_driven_negative(led, settings, live_session):
    """Open until the account refuses, then assert it is still solvent."""
    opened = 0
    for i in range(40):
        sp = a_spread(settings, live_session, spot=100.0 + i)
        try:
            open_approved(led, sp, "Industrials", 1, settings, f"P{i}", "human", sess=live_session)
        except InsufficientFunds:
            break
        opened += 1
    assert opened > 0, "fixture should allow at least one position"
    assert led.buying_power >= 0
    assert led.cash >= led.capital_at_risk


def test_the_boundary_is_inclusive(led, settings, live_session):
    """A position costing exactly the free balance is allowed; a cent more is not."""
    sp = a_spread(settings, live_session)
    fill = simulated_fill_credit(sp, settings, live_session)
    cost = round((sp.width * 100 - fill * 100) + sp.fees, 2)

    led.cash = cost
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human", sess=live_session)
    assert pos.id and led.buying_power == 0.0

    short = Ledger.load(settings, path=led.path.parent / "short.json")
    short.cash = round(cost - 0.01, 2)
    with pytest.raises(InsufficientFunds):
        open_approved(short, sp, "Industrials", 1, settings, "P2", "human",
                      sess=live_session)


def test_the_gate_uses_the_filled_collateral_not_the_sized_one(settings, live_session,
                                                               tmp_path):
    """A worse fill means less credit, which means MORE collateral. The balance
    check has to see the number the account actually ends up holding."""
    from pcs.session import SessionState
    stale = SessionState(None, True, "open", "stale", "")
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, stale)[0][0]
    fill = simulated_fill_credit(sp, settings, stale)
    filled_collateral = round(sp.width * 100 - fill * 100, 2)
    assert filled_collateral >= sp.collateral

    settings.starting_cash = round(sp.collateral + sp.fees, 2)   # covers the ticket only
    led = Ledger.load(settings, path=tmp_path / "tight.json")
    if filled_collateral > sp.collateral:
        with pytest.raises(InsufficientFunds):
            open_approved(led, sp, "Industrials", 1, settings, "P1", "human", sess=stale)


def test_filled_collateral_cannot_breach_the_per_trade_cap(led, settings, live_session):
    """The $1,000 per-trade cap is asserted on the ticket; the fill can drift
    past it, and the position that gets written must not."""
    for i in range(40):
        sp = a_spread(settings, live_session, spot=100.0 + i)
        try:
            pos = open_approved(led, sp, "Industrials", 1, settings, f"P{i}", "human", sess=live_session)
        except InsufficientFunds:
            continue
        assert pos.collateral <= STRATEGY.max_collateral_per_trade


# -- the gate at proposal time ---------------------------------------------
def test_risk_check_sizes_the_balance_by_contract_count(settings, live_session):
    """A 2-lot must be checked as a 2-lot, not silently as a 1-lot."""
    sp = a_spread(settings, live_session)
    bal = sp.collateral * 1.5                     # room for one, not for two
    pv = PortfolioView(0, 0, {}, set(), bal, bal)
    assert check(sp, "Industrials", pv, settings, contracts=1).ok
    v = check(sp, "Industrials", pv, settings, contracts=2)
    assert not v.ok and any("available balance" in r for r in v.reasons)


def test_a_batch_cannot_collectively_outspend_the_balance(settings, live_session):
    """Two proposals that each fit alone must not both pass when only one fits."""
    sp = a_spread(settings, live_session)
    bal = sp.collateral * 1.5
    pv = PortfolioView(0, 0, {}, set(), bal, bal)
    settings.max_total_collateral = 10_000        # isolate the balance rule
    assert check(sp, "Industrials", pv, settings).ok
    pending = [("AAA", "Energy", sp.collateral)]
    v = check(sp, "Materials", pv, settings, pending)
    assert not v.ok and any("available balance" in r for r in v.reasons)
    assert any("already committed" in r for r in v.reasons)
