"""The record of what ran, and the alerts that come out of it.

Everything here exists because a flat book and a dead agent look identical.
These tests pin the distinction: that a run leaves a trace, that the trace
survives a corrupt or half-upgraded file, and that each of the five states the
operator asked to be told about actually produces an alert.
"""
import datetime as dt
import json

import pytest

from pcs import health
from pcs.ledger import Ledger, Position

NOW = dt.datetime(2026, 9, 1, 11, 30)


@pytest.fixture
def hpath(tmp_path):
    return tmp_path / "health.json"


@pytest.fixture
def led(settings, tmp_path):
    return Ledger.load(settings, path=tmp_path / "ledger.json")


@pytest.fixture
def open_market():
    from pcs.session import SessionState
    return SessionState(NOW, True, "open", "live", "open")


def _pos(symbol="TST", short=100.0, spot=110.0, credit=1.0, marked_min_ago=5,
         dte=30, contracts=1):
    p = Position(
        id=symbol.lower(), symbol=symbol, sector="Energy",
        expiration=(NOW.date() + dt.timedelta(days=dte)).isoformat(),
        short_strike=short, long_strike=short - 5, width=5.0, contracts=contracts,
        credit_open=credit, credit_dollars=credit * 100 * contracts,
        collateral=(5.0 - credit) * 100 * contracts,
        opened_at=NOW.isoformat(), opened_spot=spot)
    p.mark_spot = spot
    p.mark_cost_to_close = credit * 0.5
    if marked_min_ago is not None:
        p.marked_at = (dt.datetime.now()
                       - dt.timedelta(minutes=marked_min_ago)).isoformat(timespec="seconds")
    return p


# --- persistence -----------------------------------------------------------
def test_a_run_is_recorded_and_read_back(hpath):
    health.record("mark", path=hpath, marked=3, stale=1)
    h = health.load(hpath)
    assert h.last("mark").marked == 3
    assert h.last("mark").stale == 1


def test_the_last_run_of_a_kind_is_the_newest_not_the_newest_overall(hpath):
    health.record("mark", path=hpath, marked=1)
    health.record("propose", path=hpath, detail="later")
    assert health.load(hpath).last("mark").marked == 1


def test_an_absent_file_is_an_empty_record_not_an_error(tmp_path):
    assert health.load(tmp_path / "nope.json").runs == []


def test_a_corrupt_file_does_not_stop_a_mark(hpath):
    """A health file is telemetry. Losing it must never cost a trading run."""
    hpath.write_text("{ not json")
    assert health.load(hpath).runs == []
    health.record("mark", path=hpath, marked=2)      # still writable
    assert health.load(hpath).last("mark").marked == 2


def test_a_row_with_an_unknown_field_is_skipped_not_fatal(hpath):
    """A file written by a newer build must not take the whole record down."""
    hpath.write_text(json.dumps({"runs": [
        {"kind": "mark", "at": NOW.isoformat(), "marked": 1},
        {"kind": "mark", "at": NOW.isoformat(), "invented_later": True},
    ]}))
    assert len(health.load(hpath).runs) == 1


def test_an_unwritable_path_is_swallowed(tmp_path):
    """Same rule, the other direction: a failed WRITE cannot raise either."""
    health.save(health.Health(runs=[]), tmp_path / "no" / "such" / "dir" / "h.json")


def test_the_record_is_capped(hpath):
    h = health.Health(runs=[health.Run("mark", NOW.isoformat()) for _ in range(health.KEEP + 50)])
    health.save(h, hpath)
    assert len(health.load(hpath).runs) == health.KEEP


def test_runs_today_counts_only_today(hpath):
    h = health.Health(runs=[
        health.Run("mark", dt.datetime.now().isoformat(timespec="seconds")),
        health.Run("mark", (dt.datetime.now() - dt.timedelta(days=2)).isoformat(timespec="seconds")),
    ])
    assert h.runs_today("mark") == 1


def test_an_unparseable_timestamp_does_not_crash_the_count(hpath):
    h = health.Health(runs=[health.Run("mark", "not-a-time")])
    assert h.runs_today("mark") == 0
    assert h.last("mark").when is None


# --- alert 3: an exit was due and was NOT taken ----------------------------
def test_a_held_exit_alerts(led, settings):
    h = health.Health(runs=[health.Run("mark", NOW.isoformat(), exits_due=1,
                                       exits_held=1, held_detail=["TST STOP LOSS: x"])])
    a = health.alerts(led, settings, h, now=NOW)
    assert a and a[0].kind == "exit_unactioned"
    assert a[0].severity == health.CRITICAL
    assert "market shut" in a[0].detail


def test_a_skipped_exit_alerts_and_says_the_mark_was_stale(led, settings):
    h = health.Health(runs=[health.Run("mark", NOW.isoformat(), exits_due=1,
                                       exits_skipped=1)])
    assert "stale mark" in health.alerts(led, settings, h, now=NOW)[0].detail


def test_an_exit_that_was_taken_does_not_alert(led, settings):
    """Taken is the good outcome. Alerting on it is how alerts get ignored."""
    h = health.Health(runs=[health.Run("mark", NOW.isoformat(), exits_due=1,
                                       exits_taken=1)])
    assert not [a for a in health.alerts(led, settings, h, now=NOW)
                if a.kind == "exit_unactioned"]


# --- alert 2: short strike breached, at any DTE ----------------------------
def test_a_breached_short_strike_alerts_far_from_expiration(led, settings):
    """exits.decide() only defends at 7 DTE. This has to fire before that --
    the whole gap between "tested" and "the agent acts" is the drawdown."""
    led.positions = [_pos(spot=99.0, short=100.0, dte=30)]
    a = [x for x in health.alerts(led, settings, health.Health(), now=NOW)
         if x.kind == "strike_breached"]
    assert a and a[0].severity == health.CRITICAL


def test_a_comfortable_position_does_not_alert(led, settings):
    led.positions = [_pos(spot=130.0, short=100.0)]
    assert not [x for x in health.alerts(led, settings, health.Health(), now=NOW)
                if x.kind == "strike_breached"]


def test_a_position_with_no_spot_does_not_alert_as_breached(led, settings):
    """spot 0.0 means unknown, not zero. Reading it as a breach would fire an
    alert on every position that has never marked."""
    p = _pos(spot=110.0, short=100.0)
    p.mark_spot = 0.0
    led.positions = [p]
    assert not [x for x in health.alerts(led, settings, health.Health(), now=NOW)
                if x.kind == "strike_breached"]


# --- alert 4: the mark loop --------------------------------------------------
def test_a_never_run_mark_loop_alerts_when_positions_exist(led, settings, open_market):
    led.positions = [_pos(marked_min_ago=None)]          # no mark, ever
    a = [x for x in health.alerts(led, settings, health.Health(), open_market, NOW)
         if x.kind == "mark_never_ran"]
    assert a and "./run.py mark" in a[0].detail


def test_an_empty_record_does_not_claim_never_run_when_the_ledger_says_otherwise(
        led, settings, open_market):
    """health.json starts empty the day this module is deployed. On a box that
    has been marking for weeks that would put a CRITICAL falsehood at the top
    of the page on the very first view, which is how a reader learns to skip
    the panel. A position carrying `marked_at` is proof the loop ran."""
    led.positions = [_pos(marked_min_ago=5)]
    a = health.alerts(led, settings, health.Health(), open_market, NOW)
    assert not [x for x in a if x.kind == "mark_never_ran"]
    assert not [x for x in a if x.kind == "mark_stalled"]     # 5 min old, fine


def test_the_ledger_fallback_still_reports_a_genuinely_stale_mark(
        led, settings, open_market):
    """Falling back must not become a way to go quiet: an old `marked_at` is
    still an old mark."""
    led.positions = [_pos(marked_min_ago=health.MARK_MISSING_AFTER_MIN + 30)]
    a = [x for x in health.alerts(led, settings, health.Health(), open_market,
                                  dt.datetime.now())
         if x.kind == "mark_stalled"]
    assert a and a[0].severity == health.CRITICAL


def test_ledger_evidence_reads_propose_from_the_positions_themselves(led, settings):
    """A position cannot exist unless propose ran."""
    led.positions = [_pos()]
    assert health.prior_evidence(led, "propose")
    assert health.prior_evidence(led, "watch") == ""


def test_an_empty_book_does_not_alert_about_marks(led, settings, open_market):
    """Nothing to mark is not a fault, and an alert nobody needs trains the
    reader to skip the panel that will matter later."""
    assert not [x for x in health.alerts(led, settings, health.Health(), open_market, NOW)
                if x.kind.startswith("mark_")]


def test_a_stalled_mark_loop_alerts_during_market_hours(led, settings, open_market):
    led.positions = [_pos()]
    old = (NOW - dt.timedelta(minutes=health.MARK_MISSING_AFTER_MIN + 5))
    h = health.Health(runs=[health.Run("mark", old.isoformat())])
    a = [x for x in health.alerts(led, settings, h, open_market, NOW)
         if x.kind == "mark_stalled"]
    assert a and "pcs-mark.timer" in a[0].detail


def test_a_stalled_mark_loop_is_silent_when_the_market_is_shut(led, settings):
    """Overnight there is nothing to re-price. Alerting every night is how a
    real stall gets scrolled past in the morning."""
    from pcs.session import SessionState
    shut = SessionState(NOW, True, "closed", "stale", "closed")
    led.positions = [_pos()]
    old = NOW - dt.timedelta(hours=14)
    h = health.Health(runs=[health.Run("mark", old.isoformat())])
    assert not [x for x in health.alerts(led, settings, h, shut, NOW)
                if x.kind == "mark_stalled"]


def test_a_position_that_failed_to_reprice_alerts_and_names_it(led, settings, open_market):
    led.positions = [_pos("MRVL")]
    h = health.Health(runs=[health.Run("mark", NOW.isoformat(), stale=1,
                                       stale_symbols=["MRVL"])])
    a = [x for x in health.alerts(led, settings, h, open_market, NOW)
         if x.kind == "mark_failed"]
    assert a and "MRVL" in a[0].detail


def test_the_failed_symbol_comes_from_the_run_not_from_marked_at(led, settings, open_market):
    """A position can hold a good mark from yesterday and still have failed to
    price today, so `marked_at` cannot identify it -- only the run can."""
    p = _pos("GOOGL", marked_min_ago=600)      # has a mark, just an old one
    led.positions = [p]
    h = health.Health(runs=[health.Run("mark", NOW.isoformat(), stale=1,
                                       stale_symbols=["GOOGL"])])
    a = [x for x in health.alerts(led, settings, h, open_market, NOW)
         if x.kind == "mark_failed"]
    assert a and "GOOGL" in a[0].detail


# --- alert 5: book-wide drawdown -------------------------------------------
def test_a_book_down_half_its_collateral_alerts_before_any_stop_fires(led, settings):
    p = _pos(credit=1.0, contracts=1)          # collateral $400
    p.mark_cost_to_close = 3.2                 # -$220 unrealised
    led.positions = [p]
    a = [x for x in health.alerts(led, settings, health.Health(), now=NOW)
         if x.kind == "book_drawdown"]
    assert a and a[0].severity == health.CRITICAL


def test_a_book_in_profit_does_not_alert(led, settings):
    led.positions = [_pos()]
    assert not [x for x in health.alerts(led, settings, health.Health(), now=NOW)
                if x.kind == "book_drawdown"]


# --- alert 1: the agent closed something -----------------------------------
def test_a_recent_agent_close_is_reported(led, settings):
    led.log("position_closed", symbol="META", reason="stop_loss", realized_pl=-443.0)
    a = [x for x in health.alerts(led, settings, health.Health(), now=dt.datetime.now())
         if x.kind == "agent_closed"]
    assert a and "META" in a[0].title and a[0].severity == health.INFO


def test_an_old_close_is_not_reported(led, settings):
    led.events.append({"at": (dt.datetime.now() - dt.timedelta(days=4)).isoformat(),
                       "kind": "position_closed", "symbol": "OLD",
                       "reason": "take_profit", "realized_pl": 100.0})
    assert not [x for x in health.alerts(led, settings, health.Health(),
                                         now=dt.datetime.now())
                if x.kind == "agent_closed"]


# --- ordering ---------------------------------------------------------------
def test_critical_alerts_sort_above_informational_ones(led, settings):
    led.positions = [_pos(spot=99.0, short=100.0)]
    led.log("position_closed", symbol="X", reason="take_profit", realized_pl=50.0)
    a = health.alerts(led, settings, health.Health(), now=dt.datetime.now())
    assert [x.severity for x in a] == sorted([x.severity for x in a],
                                             key=lambda s: health._SEV_RANK[s])


# --- the watchlist refresh cadence ------------------------------------------
def test_a_watchlist_that_has_not_refreshed_alerts(led, settings):
    """The one thing on the page with no ledger behind it: if the refresh
    stops, every name keeps its last quote and the tab goes on looking current.
    Age is the honest measure -- not whether the timer fired."""
    old = (NOW - dt.timedelta(hours=health.WATCH_STALE_AFTER_H + 3)).isoformat()
    a = [x for x in health.alerts(led, settings, health.Health(), now=NOW, watch_at=old)
         if x.kind == "watchlist_stale"]
    assert a and "pcs-watch.timer" in a[0].detail


def test_a_fresh_watchlist_is_silent(led, settings):
    fresh = (NOW - dt.timedelta(hours=1)).isoformat()
    assert not [x for x in health.alerts(led, settings, health.Health(), now=NOW,
                                         watch_at=fresh)
                if x.kind.startswith("watch")]


def test_the_stale_threshold_sits_under_the_promised_cadence(led, settings):
    """Four refreshes a day is one every six hours. The alert has to fire
    before the cadence has silently halved, and not on one missed run."""
    assert 6 < health.WATCH_STALE_AFTER_H <= 12


def test_a_watchlist_that_was_never_built_alerts(led, settings):
    a = [x for x in health.alerts(led, settings, health.Health(), now=NOW)
         if x.kind == "watch_never_ran"]
    assert a and "./run.py watch" in a[0].detail


def test_a_failed_refresh_is_reported_even_though_the_file_is_still_there(
        led, settings):
    """A stale file plus a green timer is the trap: the job ran, it raised, and
    the last good watchlist is still on disk looking fine."""
    fresh = (NOW - dt.timedelta(hours=1)).isoformat()
    h = health.Health(runs=[health.Run("watch", NOW.isoformat(), ok=False,
                                       detail="HTTPError: 429 Too Many Requests")])
    a = [x for x in health.alerts(led, settings, h, now=NOW, watch_at=fresh)
         if x.kind == "watch_failed"]
    assert a and "429" in a[0].detail and "journalctl" in a[0].detail


def test_watch_evidence_comes_from_the_file_not_from_the_ledger(led, settings):
    """Nothing in the ledger proves a screen ran -- watching never writes a
    position. watchlist.json stamps itself, so the file is the receipt."""
    assert health.prior_evidence(led, "watch") == ""
    assert health.prior_evidence(led, "watch", "2026-09-01T10:00:00") \
        == "2026-09-01T10:00:00"
