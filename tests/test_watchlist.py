"""The watchlist is observation. The tests that matter are the ones that keep it
that way -- and the one that keeps a stale quote from reading as a fill."""
import datetime as dt
from dataclasses import dataclass

import pytest
from conftest import make_chain

from pcs import watchlist
from pcs.ledger import Ledger
from pcs.optimizer import build_spreads
from pcs.screener import NEAR_TIGHT, PRIMARY, STRETCHED, Candidate
from pcs.session import SessionState


@dataclass
class FakeSized:
    candidate: Candidate
    spreads: list
    rejects: dict
    chain_error: str | None = None


@dataclass
class FakeResult:
    session: SessionState
    candidates: list


def a_candidate(symbol="TST", bucket=PRIMARY, sector="Energy", earnings=False):
    return Candidate(symbol=symbol, name=f"{symbol} Inc", sector=sector, bucket=bucket,
                     note="", spot=100.0, dma50=105.0, pct_from_dma50=-0.05,
                     pct_off_high=0.22, high_52w=128.0, low_52w=90.0,
                     avg_vol_30d=2_000_000, last_bar="2026-08-31",
                     earnings_in_window=earnings)


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def build(settings, sess, cands, led, spreads_for=None):
    spreads_for = spreads_for if spreads_for is not None else {c.symbol for c in cands}
    sized = []
    for c in cands:
        sps = (build_spreads(make_chain(symbol=c.symbol, spot=100.0), 100.0, settings, sess)[0]
               if c.symbol in spreads_for else [])
        sized.append(FakeSized(c, sps[:1], {}))
    return watchlist.build(FakeResult(sess, cands), sized, led, settings)


# --- the boundary ---------------------------------------------------------
def test_the_module_cannot_open_a_position():
    """Not a style point. A watchlist that refreshes at 02:00 must not be able
    to reach the fill path, because nothing stops it running out of hours."""
    src = (watchlist.__file__)
    text = open(src).read()
    assert "paper_broker" not in text
    assert "open_approved" not in text
    assert not hasattr(watchlist, "open_approved")


def test_a_stale_session_is_reported_as_untradeable(settings, led):
    shut = SessionState(dt.datetime(2026, 8, 31, 2, 0), True, "premarket", "stale", "x")
    wl = build(settings, shut, [a_candidate()], led)
    assert wl.tradeable is False
    assert wl.quote_quality == "stale"


def test_a_live_session_is_tradeable(settings, led, live_session):
    wl = build(settings, live_session, [a_candidate()], led)
    assert wl.tradeable is True


# --- signals --------------------------------------------------------------
def test_a_clean_primary_name_is_ready(settings, led, live_session):
    wl = build(settings, live_session, [a_candidate()], led)
    e = wl.entries[0]
    assert e.signal == watchlist.READY
    assert e.blockers == []
    assert e.credit_dollars > 0 and e.roc > 0 and e.short_strike > 0


def test_a_portfolio_cap_makes_it_blocked_not_absent(settings, led, live_session):
    """A name the agent wants but cannot take is the single most useful row on
    the list, so it must never be silently dropped."""
    settings.max_open_positions = 0
    wl = build(settings, live_session, [a_candidate()], led)
    e = wl.entries[0]
    assert e.signal == watchlist.BLOCKED
    assert any("max open positions" in b for b in e.blockers)
    assert e.credit_dollars > 0          # still priced, so you can see the cost


@pytest.mark.parametrize("earnings,expected", [
    (True, watchlist.EARNINGS),
    (None, watchlist.EARNINGS),          # unknown is not clean
    (False, watchlist.READY),
])
def test_earnings_gate(settings, led, live_session, earnings, expected):
    wl = build(settings, live_session, [a_candidate(earnings=earnings)], led)
    assert wl.entries[0].signal == expected


def test_an_unknown_earnings_date_says_so(settings, led, live_session):
    wl = build(settings, live_session, [a_candidate(earnings=None)], led)
    assert "unknown" in wl.entries[0].reason


def test_a_name_with_no_qualifying_spread_is_no_fit(settings, led, live_session):
    wl = build(settings, live_session, [a_candidate()], led, spreads_for=set())
    e = wl.entries[0]
    assert e.signal == watchlist.NO_FIT
    assert not e.has_spread
    assert e.reason


def test_an_open_position_shows_as_holding(settings, led, live_session, monkeypatch):
    from pcs.paper_broker import open_approved
    sp = build_spreads(make_chain(symbol="TST", spot=100.0), 100.0, settings,
                       live_session)[0][0]
    open_approved(led, sp, "Energy", 1, settings, "P1", "human", sess=live_session)
    wl = build(settings, live_session, [a_candidate("TST")], led)
    assert wl.entries[0].signal == watchlist.HOLDING


@pytest.mark.parametrize("bucket,expected", [
    (NEAR_TIGHT, watchlist.NEAR),
    (STRETCHED, watchlist.STRETCHED),
])
def test_the_near_buckets_are_watched_but_never_ready(settings, led, live_session,
                                                      bucket, expected):
    """These are not the setup. They belong on a watchlist and nowhere near a
    proposal."""
    wl = build(settings, live_session, [a_candidate(bucket=bucket)], led)
    assert wl.entries[0].signal == expected


def test_broken_and_above_names_are_not_watched(settings, led, live_session):
    from pcs.screener import ABOVE, BROKEN
    cands = [a_candidate("AAA", bucket=BROKEN), a_candidate("BBB", bucket=ABOVE)]
    wl = build(settings, live_session, cands, led)
    assert wl.entries == []


# --- ordering and persistence --------------------------------------------
def test_ready_sorts_above_everything_it_is_not(settings, led, live_session):
    cands = [a_candidate("AAA", bucket=NEAR_TIGHT), a_candidate("BBB", bucket=PRIMARY)]
    wl = build(settings, live_session, cands, led)
    assert [e.symbol for e in wl.entries] == ["BBB", "AAA"]
    assert wl.entries[0].signal == watchlist.READY


def test_it_round_trips_through_disk(settings, led, live_session, tmp_path):
    wl = build(settings, live_session, [a_candidate()], led)
    path = watchlist.save(wl, tmp_path / "wl.json")
    back = watchlist.load(path)
    assert back.tradeable == wl.tradeable
    assert [e.symbol for e in back.entries] == [e.symbol for e in wl.entries]
    assert back.entries[0].roc == wl.entries[0].roc


def test_loading_a_missing_file_is_not_an_error(tmp_path):
    assert watchlist.load(tmp_path / "nope.json") is None


# --- the dashboard must say WHY a name is not ready -------------------------
def _entry(**kw):
    base = {"symbol": "TST", "name": "Test Co", "sector": "Energy",
            "signal": watchlist.BLOCKED, "reason": "", "bucket": PRIMARY,
            "spot": 100.0, "dma50": 104.0, "pct_from_dma50": -0.04,
            "pct_off_high": -0.22}
    return watchlist.Entry(**{**base, **kw})


def _panel(monkeypatch, entry):
    from pcs import dashboard, learning
    wl = watchlist.Watchlist(generated_at="2026-09-01T14:00:00", quote_quality="live",
                             phase="open", tradeable=True, entries=[entry])
    monkeypatch.setattr(dashboard.watchlist, "load", lambda: wl)
    monkeypatch.setattr(learning, "load", lambda path=None: learning.Journal())
    return dashboard._watchlist_panel()


def test_a_blocked_name_shows_the_risk_reasons_on_the_row(monkeypatch):
    """These come from risk.check() and were computed and then discarded. A
    name sitting BLOCKED for a week with no reason cannot tell you whether
    closing one winner would unblock it or whether nothing would."""
    html = _panel(monkeypatch, _entry(blockers=["sector cap: Energy already has 2",
                                                "balance floor: $180 free"]))
    assert "sector cap: Energy already has 2" in html
    assert "balance floor: $180 free" in html


def test_the_reason_is_shown_when_there_are_no_risk_blockers(monkeypatch):
    """NO_FIT and EARNINGS never reach risk.check(), so `blockers` is empty and
    `reason` is the only account of why the name is not ready."""
    html = _panel(monkeypatch, _entry(signal=watchlist.NO_FIT,
                                      reason="no width clears $100 credit"))
    assert "no width clears $100 credit" in html


def test_the_reason_is_escaped(monkeypatch):
    html = _panel(monkeypatch, _entry(blockers=["<script>x</script>"]))
    assert "<script>" not in html
