"""The record since trade selection last changed, beside the whole record.

Twenty-one trades sit on the History tab as one record, but they were picked
by a system that kept changing underneath them -- 2026-10-10 alone changed
which names can be proposed, which runs price anything, and which tickets clear
the cap. Whether the CURRENT code makes money is a question about the trades
it picked, and the page answered a different one. It also answered it with a
bare percentage: 67% of 21 reads like a measurement, when the true rate behind
it could be anywhere from about 45% to 83%.
"""
import pytest

from pcs import dashboard, expectancy
from pcs.ledger import Ledger, Position

CHANGE = expectancy.SELECTION_CHANGES[-1][0]


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def _pos(ident, pl, opened, closed="2026-11-20T15:00:00", status="closed"):
    return Position(
        id=ident, symbol="TST", sector="Energy", expiration="2026-11-20",
        short_strike=95.0, long_strike=90.0, width=5.0, contracts=1,
        credit_open=1.00, credit_dollars=99.88, collateral=400.12,
        opened_at=opened, opened_spot=100.0, status=status,
        closed_at=closed if status != "open" else "", realized_pl=pl)


BEFORE = "2026-10-08T14:16:00"
AFTER = "2026-10-12T14:16:00"


# --- which trades count ----------------------------------------------------------
def test_the_latest_change_is_the_one_that_shipped_the_fixes():
    day, commit, what = expectancy.SELECTION_CHANGES[-1]
    assert (day, commit) == ("2026-10-10", "db27f29")
    assert what


def test_changes_are_listed_oldest_first():
    days = [d for d, _, _ in expectancy.SELECTION_CHANGES]
    assert days == sorted(days)


def test_a_position_opened_before_the_change_is_left_out_even_if_it_closed_after():
    """The old code picked it. When it closed says nothing about that."""
    old = _pos("old", -300.0, BEFORE, closed="2026-10-14T15:00:00")
    new = _pos("new", 55.0, AFTER)
    assert expectancy.opened_since([old, new], CHANGE) == [new]


def test_the_change_day_itself_counts():
    p = _pos("p", 55.0, f"{CHANGE}T14:16:00")
    assert expectancy.opened_since([p], CHANGE) == [p]


# --- how sure the rate is ---------------------------------------------------------
def test_twenty_one_trades_cannot_place_the_win_rate_within_twenty_points():
    lo, hi = expectancy.win_rate_interval(14, 21)
    assert lo == pytest.approx(0.454, abs=0.002)
    assert hi == pytest.approx(0.828, abs=0.002)


def test_the_interval_always_holds_the_observed_rate_and_stays_in_bounds():
    for wins, n in ((0, 5), (5, 5), (1, 3), (60, 100)):
        lo, hi = expectancy.win_rate_interval(wins, n)
        assert 0.0 <= lo <= wins / n <= hi <= 1.0


def test_no_trades_is_no_interval():
    assert expectancy.win_rate_interval(0, 0) is None


def test_more_trades_narrow_it():
    narrow = expectancy.win_rate_interval(70, 100)
    wide = expectancy.win_rate_interval(7, 10)
    assert narrow[1] - narrow[0] < wide[1] - wide[0]


# --- the panel ----------------------------------------------------------------------
def test_the_panel_counts_only_what_the_current_code_picked(led, settings):
    led.positions = [_pos("old1", -666.43, BEFORE), _pos("old2", 55.0, BEFORE),
                     _pos("new1", 55.0, AFTER), _pos("new2", 60.0, AFTER),
                     _pos("new3", 0.0, AFTER, status="open")]
    html = dashboard._since_change_panel(led, settings)
    assert f"Since trade selection last changed &mdash; {CHANGE}" in html
    assert "db27f29" in html
    assert ">2</div>" in html and "1 still open" in html     # closed since, open since
    assert "+$115.00" in html                                # not the -$666.43
    assert "-$666" not in html


def test_nothing_closed_yet_says_so_instead_of_showing_zeros(led, settings):
    led.positions = [_pos("old", 55.0, BEFORE), _pos("new", 0.0, AFTER, status="open")]
    html = dashboard._since_change_panel(led, settings)
    assert "Nothing the current code picked has closed yet" in html
    assert "1 still open" in html
    assert "0%" not in html


def test_a_small_sample_says_it_cannot_tell(led, settings):
    """Three wins from three is 100%, and proves nothing about a 68% line."""
    led.positions = ([_pos(f"o{i}", 55.0, BEFORE) for i in range(4)]
                     + [_pos("ol", -116.0, BEFORE)]
                     + [_pos(f"n{i}", 55.0, AFTER) for i in range(3)])
    html = dashboard._since_change_panel(led, settings)
    assert "too few trades to tell" in html


def test_it_says_when_the_whole_range_clears_the_break_even(led, settings):
    """The break-even comes from the whole record: the exits did not change, so
    every closed trade measures what a win and a loss cost."""
    led.positions = ([_pos(f"o{i}", 55.0, BEFORE) for i in range(4)]
                     + [_pos("ol", -116.0, BEFORE)]
                     + [_pos(f"n{i}", 55.0, AFTER) for i in range(40)])
    html = dashboard._since_change_panel(led, settings)
    assert "above the break-even even at the low end" in html


def test_the_history_tab_leads_with_it(led, settings):
    led.positions = [_pos("old", 55.0, BEFORE), _pos("new", 55.0, AFTER)]
    html = dashboard._history_panel(led, settings)
    since = html.index("Since trade selection last changed")
    assert since < html.index("Whole record") < html.index("Closed positions")
