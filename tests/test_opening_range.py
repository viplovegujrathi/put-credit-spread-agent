"""No position is opened inside the opening range -- paper included.

The first 30 minutes after the bell are the widest, thinnest book of the day.
A paper fill taken there would overstate what the live account could have
gotten, so the block is a hard gate in the fill path rather than a warning.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from conftest import make_chain

from pcs import session
from pcs.ledger import Ledger
from pcs.optimizer import build_spreads
from pcs.paper_broker import MarketNotReady, open_approved

ET = ZoneInfo("America/New_York")
TRADING_DAY = dt.date(2026, 9, 1)          # a Tuesday, not a holiday


def at(hour: int, minute: int, settle: int | None = None) -> session.SessionState:
    return session.state(dt.datetime(TRADING_DAY.year, TRADING_DAY.month,
                                     TRADING_DAY.day, hour, minute, tzinfo=ET),
                         settle_minutes=settle)


@pytest.mark.parametrize("hour,minute", [(9, 30), (9, 31), (9, 45), (9, 59)])
def test_opening_range_blocks_opening_a_position(hour, minute):
    st = at(hour, minute)
    assert st.phase == "opening_range"
    assert st.is_open, "the market IS open; it is the book that has not settled"
    assert not st.can_open_positions
    assert "opening range" in st.open_block_reason


@pytest.mark.parametrize("hour,minute", [(10, 0), (10, 1), (12, 0), (15, 59)])
def test_settled_session_allows_opening(hour, minute):
    st = at(hour, minute)
    assert st.phase == "open" and st.can_open_positions


def test_the_boundary_is_exactly_thirty_minutes():
    assert not at(9, 59).can_open_positions
    assert at(10, 0).can_open_positions
    assert at(9, 30).settle_until.strftime("%H:%M") == "10:00"


def test_the_window_is_configurable():
    assert at(10, 5, settle=45).phase == "opening_range"
    assert at(10, 5, settle=30).phase == "open"
    assert at(9, 35, settle=0).phase == "open"          # opt out entirely


def test_quotes_are_still_graded_live_inside_the_range():
    """The block is about the book settling, not about quote staleness -- these
    are genuinely live quotes, so sizing must not be penalised as if stale."""
    assert at(9, 45).quote_quality == "live"
    assert session.spread_tolerance_multiplier(at(9, 45)) == 1.0


def test_fill_path_refuses_inside_the_range(settings, tmp_path):
    led = Ledger.load(settings, path=tmp_path / "ledger.json")
    sess = at(9, 45)
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, sess)[0][0]
    with pytest.raises(MarketNotReady, match="opening range"):
        open_approved(led, sp, "Industrials", 1, settings, "P1", "human", sess=sess)
    assert led.open_positions == [], "nothing may be recorded when the gate fires"
    assert led.cash == settings.starting_cash


def test_the_same_approval_succeeds_once_the_range_has_passed(settings, tmp_path):
    led = Ledger.load(settings, path=tmp_path / "ledger.json")
    sess = at(10, 0)
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, sess)[0][0]
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human", sess=sess)
    assert pos.id and led.open_positions == [pos]


def test_settings_window_reaches_the_session(settings):
    settings.opening_settle_minutes = 60
    blocked = session.state_for(settings, dt.datetime(2026, 9, 1, 10, 29, tzinfo=ET))
    assert blocked.phase == "opening_range" and not blocked.can_open_positions
    # the window is half-open: the settle minute itself is already tradeable
    clear = session.state_for(settings, dt.datetime(2026, 9, 1, 10, 30, tzinfo=ET))
    assert clear.phase == "open" and clear.can_open_positions


def test_premarket_and_closed_are_not_blocked_by_this_rule():
    """The rule the user set is about the opening range specifically; other
    phases stay governed by the quote-quality warnings."""
    assert at(9, 0).can_open_positions
    assert at(18, 0).can_open_positions
