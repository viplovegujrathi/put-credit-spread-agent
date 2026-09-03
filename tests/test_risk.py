"""Section 1.8 portfolio caps. These are separate from the per-trade rules and
must bind across a batch, not just one proposal at a time."""
from conftest import make_chain

from pcs.optimizer import build_spreads
from pcs.risk import PortfolioView, check


def one_spread(settings, live_session):
    chain = make_chain(spot=100.0)
    return build_spreads(chain, 100.0, settings, live_session)[0][0]


def empty_view(cash=3000.0):
    return PortfolioView(0.0, 0, {}, {}, cash, cash)


def test_clean_portfolio_accepts(settings, live_session):
    assert check(one_spread(settings, live_session), "Industrials", empty_view(), settings)


def test_total_collateral_cap_binds(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(settings.max_total_collateral - 10, 1, {}, {}, 3000, 3000)
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok and any("collateral cap" in r for r in v.reasons)


def test_max_open_positions_binds(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, settings.max_open_positions, {}, {}, 3000, 3000)
    assert not check(sp, "Industrials", pv, settings).ok


def test_sector_concentration_binds_and_warns(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, 1, {"Industrials": settings.max_positions_per_sector},
                       {}, 3000, 3000)
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok and any("sector concentration" in r for r in v.reasons)

    pv1 = PortfolioView(0, 1, {"Industrials": 1}, {}, 3000, 3000)
    v1 = check(sp, "Industrials", pv1, settings)
    assert v1.ok and any("correlation" in w for w in v1.warnings)


def test_pending_batch_cannot_slip_past_a_cap(settings, live_session):
    """Three proposals evaluated one at a time must not collectively exceed the
    portfolio cap."""
    sp = one_spread(settings, live_session)
    settings.max_total_collateral = sp.collateral * 2 + 1
    pending = [("AAA", "Energy", sp.collateral), ("BBB", "Utilities", sp.collateral)]
    assert not check(sp, "Materials", empty_view(), settings, pending).ok


def test_the_per_ticker_cap_counts_instead_of_asking_whether_we_hold_any(
        settings, live_session):
    """The regression this replaces. `PortfolioView` held a SET of symbols and
    the check was `symbol in symbols`, so the cap behaved as 1 whatever
    `max_positions_per_ticker` was set to -- while the refusal message quoted
    the setting's real value back at the reader. Setting it to 5 changed the
    sentence and nothing else."""
    sp = one_spread(settings, live_session)
    settings.max_positions_per_ticker = 3

    for held in (1, 2):
        pv = PortfolioView(0, held, {}, {sp.symbol: held}, 3000, 3000)
        v = check(sp, "Industrials", pv, settings)
        assert v.ok, f"{held} of 3 should not block"
        assert any("single-name concentration" in w for w in v.warnings)

    pv = PortfolioView(0, 3, {}, {sp.symbol: 3}, 3000, 3000)
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok and any("ticker concentration" in r for r in v.reasons)


def test_a_ladder_cannot_be_built_past_the_cap_one_proposal_at_a_time(
        settings, live_session):
    """Same batch, same symbol. `pending` has to count per ticker or two
    proposals on one name both pass a cap of 1."""
    sp = one_spread(settings, live_session)
    settings.max_positions_per_ticker = 1
    pending = [(sp.symbol, "Industrials", sp.collateral)]
    v = check(sp, "Industrials", empty_view(), settings, pending)
    assert not v.ok and any("ticker concentration" in r for r in v.reasons)


def test_the_sector_cap_is_the_real_ceiling_on_a_ladder(settings, live_session):
    """A ticker has exactly one sector, so a per-ticker cap above the sector cap
    is unreachable. Worth pinning: raising only the per-ticker number looks like
    it should allow a deeper ladder and does not."""
    sp = one_spread(settings, live_session)
    settings.max_positions_per_ticker = 5
    settings.max_positions_per_sector = 2
    pv = PortfolioView(0, 2, {"Industrials": 2}, {sp.symbol: 2}, 3000, 3000)
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok
    assert any("sector concentration" in r for r in v.reasons)
    assert not any("ticker concentration" in r for r in v.reasons)


# --- the re-entry cooldown -------------------------------------------------
def test_a_name_inside_the_cooldown_is_refused(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, 0, {}, {}, 3000, 3000,
                       cooldowns={sp.symbol: "2026-09-08"})
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok
    assert any("re-entry cooldown" in r and "2026-09-08" in r for r in v.reasons)


def test_the_cooldown_only_touches_the_name_that_lost(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, 0, {}, {}, 3000, 3000, cooldowns={"OTHER": "2026-09-08"})
    assert check(sp, "Industrials", pv, settings).ok


def test_available_balance_is_enforced(settings, live_session):
    sp = one_spread(settings, live_session)
    pv = PortfolioView(0, 0, {}, {}, 100, 100)
    v = check(sp, "Industrials", pv, settings)
    assert not v.ok and any("available balance" in r for r in v.reasons)
