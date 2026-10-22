"""The earnings blockout is off, and a name reporting inside the window trades.

Through 2026-10-21 any name with a report on or before expiry + 2 days was
dropped, an unknown date included. On 10-19 and 10-20 that took 16 of the 21 and
13 of the 17 names that had a spread; the 5 and 4 left were stopped by capital.
`Settings.earnings_blockout` is False now. The date is still looked up and
printed on the ticket, so the risk is visible rather than removed.
"""
import pytest
from conftest import make_chain

from pcs import pipeline
from pcs.config import Settings
from pcs.ledger import Ledger
from pcs.optimizer import build_spreads
from pcs.pipeline import _earnings_note
from pcs.screener import PRIMARY, Candidate
from pcs.watchlist import EARNINGS, READY, build

EXPIRY = "2026-11-20"


def a_candidate(earnings_in_window, earnings_date="2026-11-04", symbol="TST") -> Candidate:
    return Candidate(symbol=symbol, name=f"{symbol} Inc", sector="Energy", bucket=PRIMARY,
                     note="", spot=100.0, dma50=110.0, pct_from_dma50=-0.09,
                     pct_off_high=0.25, high_52w=130.0, low_52w=90.0, avg_vol_30d=1e6,
                     last_bar="2026-10-21", earnings_date=earnings_date,
                     earnings_source="test", earnings_in_window=earnings_in_window)


def a_spread(symbol, settings, sess):
    chain = make_chain(symbol=symbol, spot=100.0, strikes=[95.0, 90.0], spread=0.15,
                       price_at=lambda k: 2.50 if k == 95.0 else 1.00)
    return [s for s in build_spreads(chain, 100.0, settings, sess)[0]
            if (s.short_strike, s.long_strike) == (95.0, 90.0)][0]


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 10_000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def test_the_blockout_is_off_by_default():
    assert Settings().earnings_blockout is False


def test_a_report_inside_the_window_is_noted_not_blocked():
    blocked, note = _earnings_note(a_candidate(True), EXPIRY, blockout=False)
    assert not blocked
    assert "2026-11-04" in note and "inside" in note


def test_an_unknown_date_is_noted_not_blocked():
    blocked, note = _earnings_note(a_candidate(None, earnings_date=None), EXPIRY,
                                   blockout=False)
    assert not blocked and "unknown" in note


def test_a_report_after_the_window_reads_the_same_either_way():
    c = a_candidate(False, earnings_date="2027-01-14")
    assert _earnings_note(c, EXPIRY, blockout=False) == _earnings_note(c, EXPIRY, blockout=True)


@pytest.mark.parametrize("in_window", [True, None])
def test_the_screen_proposes_it_with_the_blockout_off_and_skips_it_with_it_on(
        led, settings, live_session, in_window):
    sized = [pipeline.SizedCandidate(a_candidate(in_window), [a_spread("TST", settings, live_session)], {})]
    res = pipeline.ScreenResult(live_session, None, [], {}, {})

    settings.earnings_blockout = False
    props, skipped = pipeline.build_proposals(sized, res, led, settings)
    assert [p.symbol for p in props] == ["TST"] and not skipped
    assert props[0].risk_ok

    settings.earnings_blockout = True
    props, skipped = pipeline.build_proposals(sized, res, led, settings)
    assert props == [] and "BLOCKED" in skipped[0]


@pytest.mark.parametrize("in_window", [True, None])
def test_the_watchlist_reads_ready_with_the_blockout_off(led, settings, live_session, in_window):
    sized = [pipeline.SizedCandidate(a_candidate(in_window), [a_spread("TST", settings, live_session)], {})]
    res = pipeline.ScreenResult(live_session, None, [a_candidate(in_window)], {}, {})

    settings.earnings_blockout = False
    assert build(res, sized, led, settings).entries[0].signal == READY
    settings.earnings_blockout = True
    assert build(res, sized, led, settings).entries[0].signal == EARNINGS
