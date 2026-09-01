"""Section 1.8 portfolio caps. These are separate from the per-trade rules and
must bind across a batch, not just one proposal at a time."""
from conftest import make_chain

from pcs.optimizer import build_spreads
from pcs.risk import PortfolioView, check


def one_spread(settings, live_session):
    chain = make_chain(spot=100.0)
    return build_spreads(chain, 100.0, settings, live_session)[0][0]


def empty_view(cash=3000.0):
    return PortfolioView(0.0, 0, {}, set(), cash, cash)


def test_clean_portfolio_accepts(settings, live_session):
    assert check(one_spread(settings, live_session), "Industrials", empty_view(), settings)


def test_total_collateral_cap_binds(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(settings.max_total_collateral - 10, 1, {}, set(), 3000, 3000)
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok and any("collateral cap" in r for r in v.reasons)


def test_max_open_positions_binds(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, settings.max_open_positions, {}, set(), 3000, 3000)
    assert not check(sp, "Industrials", pv, settings).ok


def test_sector_concentration_binds_and_warns(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, 1, {"Industrials": settings.max_positions_per_sector},
                       set(), 3000, 3000)
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok and any("sector concentration" in r for r in v.reasons)

    pv1 = PortfolioView(0, 1, {"Industrials": 1}, set(), 3000, 3000)
    v1 = check(sp, "Industrials", pv1, settings)
    assert v1.ok and any("correlation" in w for w in v1.warnings)


def test_pending_batch_cannot_slip_past_a_cap(settings, live_session):
    """Three proposals evaluated one at a time must not collectively exceed the
    portfolio cap."""
    sp = one_spread(settings, live_session)
    settings.max_total_collateral = sp.collateral * 2 + 1
    pending = [("AAA", "Energy", sp.collateral), ("BBB", "Utilities", sp.collateral)]
    assert not check(sp, "Materials", empty_view(), settings, pending).ok


def test_one_position_per_ticker(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, 1, {}, {sp.symbol}, 3000, 3000)
    assert not check(sp, "Industrials", pv, settings).ok


def test_available_balance_is_enforced(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, 0, {}, set(), 100, 100)
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok and any("available balance" in r for r in v.reasons)
