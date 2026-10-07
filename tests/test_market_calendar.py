"""Half days, and the edge of the calendar.

The clock knew holidays and assumed every other weekday closed at 16:00. NYSE
closes at 13:00 the day after Thanksgiving and on most Christmas Eves, so on
2026-11-27 the 13:05 through 15:50 marks would each have graded a book that
had stopped trading as "live" -- and `apply_exits`, whose only market gate is
`is_open`, would have fired stops on it. The first of those is a Friday with
the account holding positions over a holiday week.

Dates are NYSE's own (ICE press release, "NYSE Group Announces 2025, 2026 and
2027 Holiday and Early Closings Calendar").
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from pcs import doctor, exits, session
from pcs.ledger import Ledger, Position
from pcs.paper_broker import MarketNotReady, apply_exits

ET = ZoneInfo("America/New_York")
HALF_DAY = dt.date(2026, 11, 27)


def at(day: dt.date, hour: int, minute: int) -> session.SessionState:
    return session.state(dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET))


# --- the half day ---------------------------------------------------------------
@pytest.mark.parametrize("hour,minute", [(13, 5), (13, 30), (15, 50)])
def test_a_half_day_is_closed_after_one(hour, minute):
    st = at(HALF_DAY, hour, minute)
    assert st.phase == "closed" and not st.is_open
    assert st.quote_quality == "closing_snapshot"
    assert "13:00 ET closing snapshot" in st.banner


def test_the_morning_of_a_half_day_trades_and_says_when_it_stops():
    st = at(HALF_DAY, 12, 59)
    assert st.phase == "open" and st.quote_quality == "live"
    assert "Early close today at 13:00 ET" in st.banner


def test_an_ordinary_afternoon_is_untouched():
    st = at(dt.date(2026, 11, 24), 15, 50)
    assert st.phase == "open" and st.quote_quality == "live"
    assert "Early close" not in st.banner


def test_no_stop_fires_on_a_half_day_afternoon(settings, tmp_path):
    """The reason this matters. A position deep enough to stop out at 14:00
    on the half day is held until the next session, exactly as it would be at
    17:00 on any other day."""
    settings.paper_trading = True
    led = Ledger.load(settings, path=tmp_path / "ledger.json")
    pos = Position(id="p", symbol="TST", sector="Energy", expiration="2026-12-18",
                   short_strike=95.0, long_strike=90.0, width=5.0, contracts=1,
                   credit_open=1.0, credit_dollars=99.88, collateral=400.12,
                   opened_at="2026-11-20T11:00:00", opened_spot=100.0,
                   mark_cost_to_close=3.5)
    led.positions = [pos]
    assert exits.decide(pos, settings).action == exits.STOP_LOSS

    with pytest.raises(MarketNotReady):
        apply_exits(led, settings, fresh={"p"}, sess=at(HALF_DAY, 14, 0))
    assert pos.status == "open"


# --- the tables ---------------------------------------------------------------
def _thanksgiving(year: int) -> dt.date:
    nov1 = dt.date(year, 11, 1)
    return nov1 + dt.timedelta(days=(3 - nov1.weekday()) % 7 + 21)


def test_every_year_in_the_table_has_its_thanksgiving_pair():
    """The day after Thanksgiving is a half day every year. A year added to
    HOLIDAYS without its early closes is the gap this file exists for."""
    years = {int(d[:4]) for d in session.HOLIDAYS}
    for y in years:
        tg = _thanksgiving(y)
        assert tg.isoformat() in session.HOLIDAYS, y
        assert (tg + dt.timedelta(days=1)).isoformat() in session.EARLY_CLOSES, y


def test_a_half_day_is_a_weekday_and_never_a_holiday():
    for d in session.EARLY_CLOSES:
        assert d not in session.HOLIDAYS
        assert dt.date.fromisoformat(d).weekday() < 5


# --- the edge of the calendar -------------------------------------------------
def _calendar_warning(settings, tmp_path, now: dt.datetime):
    led = Ledger.load(settings, path=tmp_path / "ledger.json")
    sess = session.SessionState(now, True, "open", "live", "open")
    return [c for c in doctor.diagnose(led, settings, sess) if c.label == "market calendar"]


def test_doctor_says_when_the_tables_are_about_to_run_out(settings, tmp_path):
    ends = session.calendar_ends()
    near = dt.datetime.combine(ends - dt.timedelta(days=30), dt.time(11, 0))
    (c,) = _calendar_warning(settings, tmp_path, near)
    assert c.state == doctor.WARN
    assert str(ends) in c.detail and "pcs/session.py" in c.fix


def test_doctor_is_quiet_while_the_tables_have_room(settings, tmp_path):
    far = dt.datetime.combine(session.calendar_ends() - dt.timedelta(days=200),
                              dt.time(11, 0))
    assert _calendar_warning(settings, tmp_path, far) == []
