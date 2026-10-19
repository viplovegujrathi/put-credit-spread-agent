"""The short strike sits further out: 5% below spot at least, a delta ceiling,
and spreads ranked by the chance of profit before the return.

Ranked by credit over collateral alone, the agent took the strike nearest spot
that the rules allowed on every trade: all 30 positions to 2026-10-17 opened
3.1-5.9% out at a short delta of 0.27-0.38, including ORCL 133/130 at 3.1% and
0.38 on 2026-09-30. See LEARNING.md 55.
"""
from dataclasses import replace

from conftest import make_chain

from pcs import optimizer
from pcs.config import STRATEGY
from pcs.optimizer import build_spreads, rank_key


def test_the_baseline_is_five_percent_and_a_delta_ceiling():
    assert STRATEGY.min_otm_cushion == 0.05
    assert STRATEGY.max_short_delta == 0.30


def test_no_spread_is_taken_closer_than_five_percent(settings, live_session):
    spreads, _ = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)
    assert spreads
    assert all(s.cushion >= 0.05 for s in spreads)


def test_the_delta_ceiling_rejects_a_short_put_that_is_too_likely_to_finish_in_the_money(
        settings, live_session):
    # At 90% vol a put 5% out has a delta near 0.4, past the ceiling.
    spreads, rejects = build_spreads(make_chain(spot=100.0, iv=0.90), 100.0,
                                     settings, live_session)
    assert any(r.startswith("short delta") and "ceiling" in r for r in rejects)
    assert all(abs(s.short_delta) <= STRATEGY.max_short_delta for s in spreads)


def test_every_spread_on_a_normal_chain_is_inside_the_ceiling(settings, live_session):
    spreads, _ = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)
    assert all(abs(s.short_delta) <= STRATEGY.max_short_delta for s in spreads)


def test_the_chance_of_profit_ranks_before_the_return(settings, live_session):
    # Two copies of a real spread, changing only what ranks.
    real = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)[0][0]
    near = replace(real, pop_est=0.62, roc=0.85, cushion=0.05)
    far = replace(real, pop_est=0.83, roc=0.22, cushion=0.13)
    assert sorted([near, far], key=rank_key)[0] is far


def test_return_still_picks_the_narrowest_width_at_equal_odds(settings, live_session):
    spreads, _ = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)
    best = spreads[0]
    same_strike = [s for s in spreads if s.short_strike == best.short_strike]
    assert best.roc == max(s.roc for s in same_strike)


def test_the_across_names_ranking_uses_the_same_key():
    import inspect

    from pcs import pipeline
    assert "optimizer.rank_key" in inspect.getsource(pipeline.build_proposals)
    assert optimizer.rank_key is rank_key
