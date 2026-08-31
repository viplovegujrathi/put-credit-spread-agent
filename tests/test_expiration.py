"""Section 1.5: pick a real listed Friday near 32 DTE -- never assume one."""
import datetime as dt

from pcs.chains import pick_expiration

TODAY = dt.date(2026, 8, 31)          # a Monday
LISTED = ["2026-09-04", "2026-09-11", "2026-09-18", "2026-09-25",
          "2026-10-02", "2026-10-09", "2026-10-16", "2026-11-20"]


def test_picks_the_listed_friday_nearest_the_target():
    pick = pick_expiration(LISTED, today=TODAY)
    assert pick == "2026-10-02"
    assert dt.date.fromisoformat(pick).weekday() == 4
    assert (dt.date.fromisoformat(pick) - TODAY).days == 32


def test_returns_none_rather_than_a_date_outside_the_window():
    assert pick_expiration(["2026-09-04", "2026-12-18"], today=TODAY) is None


def test_prefers_a_friday_over_a_nearer_non_friday():
    # 2026-09-30 is a Wednesday at 30 DTE; 10-02 is a Friday at 32.
    assert pick_expiration(["2026-09-30", "2026-10-02"], today=TODAY) == "2026-10-02"


def test_takes_a_non_friday_when_it_is_the_only_listing_in_the_window():
    assert pick_expiration(["2026-09-30"], today=TODAY) == "2026-09-30"
