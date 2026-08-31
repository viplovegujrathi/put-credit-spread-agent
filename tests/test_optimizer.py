"""Per-trade sizing rules from section 1.4. These are the numbers real money
would be risked on, so each constraint is asserted independently."""
from conftest import make_chain

from pcs.config import STRATEGY
from pcs.optimizer import build_spreads


def test_every_spread_satisfies_both_caps_independently(settings, live_session):
    chain = make_chain(spot=100.0)
    spreads, _ = build_spreads(chain, 100.0, settings, live_session)
    assert spreads, "expected the synthetic chain to produce candidates"
    for sp in spreads:
        assert sp.credit_dollars >= STRATEGY.min_credit_per_trade
        assert sp.collateral <= STRATEGY.max_collateral_per_trade
        assert sp.cushion >= STRATEGY.min_otm_cushion
        assert sp.long_strike < sp.short_strike
        assert sp.collateral == round(sp.width * 100 - sp.credit_dollars, 2)


def test_no_upper_cap_on_credit(settings, live_session):
    """A fat premium must not be throttled back -- more credit for the same
    capped risk is strictly better (section 1.4)."""
    rich = make_chain(spot=100.0, price_at=lambda k: round(max(0.10, 9.0 * 2.718 ** (-(100 - k) / 9.0)), 2))
    spreads, _ = build_spreads(rich, 100.0, settings, live_session)
    assert spreads
    assert max(s.credit_dollars for s in spreads) > 300


def test_ranking_prefers_capital_efficiency_over_width(settings, live_session):
    chain = make_chain(spot=100.0)
    spreads, _ = build_spreads(chain, 100.0, settings, live_session)
    best = spreads[0]
    same_credit_wider = [s for s in spreads
                         if s.credit_dollars >= best.credit_dollars and s.width > best.width]
    assert all(s.roc <= best.roc for s in same_credit_wider)
    assert best.roc == max(s.roc for s in spreads)


def test_strike_is_never_pulled_to_the_money(settings, live_session):
    """If nothing at >=3% OTM clears $100, skip the name rather than moving in."""
    thin = make_chain(spot=100.0, price_at=lambda k: round(max(0.01, 0.60 * 2.718 ** (-(100 - k) / 4.0)), 2))
    spreads, _ = build_spreads(thin, 100.0, settings, live_session)
    assert spreads == []


def test_illiquid_chain_is_rejected_even_when_the_price_screen_passes(settings, live_session):
    dead = make_chain(spot=100.0, oi=0)
    spreads, rejects = build_spreads(dead, 100.0, settings, live_session)
    assert spreads == []
    assert any("OI" in r for r in rejects)


def test_wide_package_market_is_rejected(settings, live_session):
    wide = make_chain(spot=100.0, spread=1.50)
    spreads, rejects = build_spreads(wide, 100.0, settings, live_session)
    assert spreads == []
    assert any("wide" in r or "package" in r for r in rejects)


def test_natural_credit_below_the_floor_is_flagged_not_hidden(settings, live_session):
    chain = make_chain(spot=100.0, spread=0.35)
    spreads, _ = build_spreads(chain, 100.0, settings, live_session)
    for sp in spreads:
        assert sp.credit_nat_dollars <= sp.credit_dollars
        assert sp.fill_risk == (sp.credit_nat_dollars < STRATEGY.min_credit_per_trade)


def test_broker_pop_is_preferred_over_the_delta_estimate(settings, live_session):
    chain = make_chain(spot=100.0)
    for q in chain.puts:
        q.pop_short = 0.77
    spreads, _ = build_spreads(chain, 100.0, settings, live_session)
    assert spreads and spreads[0].pop_source == "broker"
    assert spreads[0].pop_est == 0.77


def test_a_wide_book_cannot_inflate_the_credit_past_what_it_would_fill(settings, live_session):
    """Sizing on the mid favours the widest markets, because that is where the
    mid overstates most. The natural credit must clear a floor of its own."""
    wide = make_chain(spot=100.0, spread=0.55)
    spreads, rejects = build_spreads(wide, 100.0, settings, live_session)
    floor = STRATEGY.min_credit_per_trade * settings.min_natural_credit_frac
    for sp in spreads:
        assert sp.credit_nat_dollars >= floor
    assert not spreads or all(sp.credit_nat_dollars >= floor for sp in spreads)


def test_sizing_moves_toward_the_natural_credit_when_quotes_are_stale(settings):
    from pcs.session import SessionState
    chain = make_chain(spot=100.0, spread=0.20)
    live = SessionState(None, True, "open", "live", "")
    stale = SessionState(None, True, "closed", "stale", "")
    best_live = build_spreads(chain, 100.0, settings, live)[0]
    best_stale = build_spreads(chain, 100.0, settings, stale)[0]
    same = {(s.short_strike, s.long_strike): s for s in best_stale}
    for sp in best_live:
        twin = same.get((sp.short_strike, sp.long_strike))
        if twin:
            assert twin.credit <= sp.credit
