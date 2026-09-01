"""The operator council's findings, pinned to the rendered page.

COUNCIL.md seat 1 found six things the page did not say. Each one is a test
here, named for the failure it prevents rather than for the markup it checks --
if a future edit drops the column, the test name says what that costs.
"""
import datetime as dt

import pytest

from pcs import dashboard, health
from pcs.ledger import Ledger, Position
from pcs.session import SessionState

NOW = dt.datetime(2026, 9, 1, 11, 30)


@pytest.fixture
def led(settings, tmp_path):
    ledger = Ledger.load(settings, path=tmp_path / "ledger.json")
    ledger.cash = 4046.40
    return ledger


@pytest.fixture
def open_market():
    return SessionState(NOW, True, "open", "live", "open")


@pytest.fixture
def shut_market():
    return SessionState(NOW, True, "closed", "stale", "closed")


def _pos(symbol="TST", short=100.0, spot=110.0, credit=1.0, marked_min_ago=5,
         dte=30, cost_to_close=0.5):
    p = Position(
        id=symbol.lower(), symbol=symbol, sector="Energy",
        expiration=(dt.date.today() + dt.timedelta(days=dte)).isoformat(),
        short_strike=short, long_strike=short - 5, width=5.0, contracts=1,
        credit_open=credit, credit_dollars=credit * 100,
        collateral=(5.0 - credit) * 100,
        opened_at=NOW.isoformat(), opened_spot=spot)
    p.mark_spot = spot
    p.mark_cost_to_close = cost_to_close
    if marked_min_ago is not None:
        p.marked_at = (dt.datetime.now()
                       - dt.timedelta(minutes=marked_min_ago)).isoformat(timespec="seconds")
    return p


def _render(led, settings, sess, tmp_path, monkeypatch, runs=()):
    monkeypatch.setattr(health, "HEALTH_JSON", tmp_path / "health.json")
    monkeypatch.setattr(dashboard, "WEB_INDEX", None)
    if runs:
        health.save(health.Health(runs=list(runs)), tmp_path / "health.json")
    out = tmp_path / "dash.html"
    dashboard.render(led, [], settings, sess, path=out)
    return out.read_text()


# --- finding 1: spot, distance to short strike, breakeven ------------------
def test_the_spot_price_is_shown(led, settings, open_market, tmp_path, monkeypatch):
    """A 205/195p opened at 211 that now prints 206 must not render the same as
    one still at 211. Without spot on the row there is no gradient at all."""
    led.positions = [_pos(spot=214.90, short=200.0)]
    assert "214.90" in _render(led, settings, open_market, tmp_path, monkeypatch)


def test_the_distance_to_the_short_strike_is_shown(led, settings, open_market,
                                                   tmp_path, monkeypatch):
    led.positions = [_pos(spot=110.0, short=100.0)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "to short" in doc
    assert "+9.1%" in doc          # (110 - 100) / 110


def test_a_breached_strike_renders_as_breached_not_as_a_small_number(
        led, settings, open_market, tmp_path, monkeypatch):
    led.positions = [_pos(spot=99.0, short=100.0)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "cush breach" in doc
    assert "through it" in doc


def test_an_unknown_cushion_does_not_render_like_a_wide_one(
        led, settings, open_market, tmp_path, monkeypatch):
    p = _pos()
    p.mark_spot = 0.0
    led.positions = [p]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "no spot" in doc
    assert "cush ok" not in doc


def test_the_breakeven_is_shown(led, settings, open_market, tmp_path, monkeypatch):
    """short strike less credit. Between the strike and here the position loses
    money with no exit rule having fired."""
    led.positions = [_pos(short=100.0, credit=1.0)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "breakeven" in doc
    assert "$99.00" in doc


# --- finding 2: mark age ----------------------------------------------------
def test_a_fresh_mark_says_when_it_was_taken(led, settings, open_market,
                                             tmp_path, monkeypatch):
    led.positions = [_pos(marked_min_ago=5)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "agemark fresh" in doc
    assert "marked 5m ago" in doc


def test_a_three_day_old_mark_is_flagged_stale(led, settings, open_market,
                                               tmp_path, monkeypatch):
    """The exact failure: yfinance drops a strike for a week, cost_to_close
    returns None, and the page keeps showing P&L $0 in the same typeface."""
    led.positions = [_pos(marked_min_ago=60 * 24 * 3)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "agemark stale" in doc
    assert "marked 3d ago" in doc


def test_a_position_that_never_marked_says_so(led, settings, open_market,
                                              tmp_path, monkeypatch):
    led.positions = [_pos(marked_min_ago=None)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "never marked" in doc


def test_the_cost_to_close_is_labelled_as_a_model_not_a_quote(
        led, settings, open_market, tmp_path, monkeypatch):
    """cost_to_close() is mid + 0.25 x (nat - mid). Labelling the column `cost
    to close` with no qualifier reads as money you could take right now."""
    led.positions = [_pos()]
    assert "modelled mid" in _render(led, settings, open_market, tmp_path, monkeypatch)


# --- finding 3: due-but-not-taken is not the same as handled ---------------
def test_an_exit_on_a_stale_mark_says_no_rule_was_evaluated(
        led, settings, open_market, tmp_path, monkeypatch):
    """The page used to re-run decide() at render time on marks review() had
    already refused to act on, and show the result as a live decision."""
    led.positions = [_pos(marked_min_ago=60 * 24 * 3, cost_to_close=4.0)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "NOT DECIDED" in doc
    assert "STOP LOSS" not in doc


def test_an_exit_due_with_the_market_shut_says_held(led, settings, shut_market,
                                                    tmp_path, monkeypatch):
    """META gaps down after hours, apply_exits refuses, stdout says HELD. The
    page showed the same amber pill it shows for an exit the agent took."""
    settings.auto_exit = True
    led.positions = [_pos(cost_to_close=4.0)]
    doc = _render(led, settings, shut_market, tmp_path, monkeypatch)
    assert "HELD" in doc
    assert "still open, still moving" in doc.lower()


def test_an_exit_due_with_auto_exit_off_says_it_needs_a_human(
        led, settings, open_market, tmp_path, monkeypatch):
    settings.auto_exit = False
    led.positions = [_pos(cost_to_close=4.0)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "NEEDS YOU" in doc
    assert "will not close this" in doc


def test_an_exit_the_agent_will_take_says_so_plainly(led, settings, open_market,
                                                     tmp_path, monkeypatch):
    settings.auto_exit = True
    led.positions = [_pos(cost_to_close=4.0)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "closes this on the next mark" in doc


# --- finding 4: the heartbeat ----------------------------------------------
def test_a_never_run_timer_says_never_run(led, settings, open_market,
                                          tmp_path, monkeypatch):
    """A dead scheduler and a quiet market render the same flat book."""
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "never run" in doc
    assert "hb-bad" in doc


def test_the_heartbeat_reports_the_last_run_and_the_count_today(
        led, settings, open_market, tmp_path, monkeypatch):
    runs = [health.Run("mark", dt.datetime.now().isoformat(timespec="seconds"))]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch, runs)
    assert "1 today" in doc


def test_a_stalled_mark_loop_is_red_in_the_heartbeat(led, settings, open_market,
                                                     tmp_path, monkeypatch):
    led.positions = [_pos()]
    old = dt.datetime.now() - dt.timedelta(minutes=health.MARK_MISSING_AFTER_MIN + 10)
    doc = _render(led, settings, open_market, tmp_path, monkeypatch,
                  [health.Run("mark", old.isoformat(timespec="seconds"))])
    assert "hb-bad" in doc


# --- the alert panel --------------------------------------------------------
def test_alerts_render_above_the_cards(led, settings, open_market, tmp_path, monkeypatch):
    """They are the reason to open the page. Below the fold is not an alert."""
    led.positions = [_pos(spot=99.0, short=100.0)]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert doc.index('class="alerts"') < doc.index('class="cards"')


def test_a_clean_account_renders_no_alert_block(led, settings, open_market,
                                                tmp_path, monkeypatch):
    led.positions = [_pos()]
    runs = [health.Run("mark", dt.datetime.now().isoformat(timespec="seconds"))]
    assert 'class="alerts"' not in _render(led, settings, open_market, tmp_path,
                                          monkeypatch, runs)


def test_alert_text_is_escaped(led, settings, open_market, tmp_path, monkeypatch):
    led.positions = [_pos()]
    runs = [health.Run("mark", dt.datetime.now().isoformat(timespec="seconds"),
                       exits_held=1, held_detail=["<script>alert(1)</script>"])]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch, runs)
    assert "<script>alert(1)</script>" not in doc
    assert "&lt;script&gt;" in doc


# --- finding 5: concentration as a fraction of the account -----------------
def test_the_worst_case_is_stated_as_a_percentage_of_the_account(
        led, settings, open_market, tmp_path, monkeypatch):
    """`collateral at risk $1,331` and `44% of a $3,000 account` are the same
    number and only one of them is read as a warning."""
    led.positions = [_pos(), _pos("TS2")]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "of net liq" in doc
    assert "went to max loss" in doc


def test_the_sector_cap_is_described_as_counting_labels_not_correlation(
        led, settings, open_market, tmp_path, monkeypatch):
    """Four large-cap tech names in two GICS sectors pass the cap and are still
    one bet. The page should not imply the cap means diversified."""
    led.positions = [_pos()]
    doc = " ".join(_render(led, settings, open_market, tmp_path, monkeypatch).split())
    assert "counts labels, not correlation" in doc


def test_a_book_risking_most_of_the_account_is_coloured_as_a_loss(
        led, settings, open_market, tmp_path, monkeypatch):
    led.cash = 1200.0
    led.positions = [_pos(), _pos("TS2")]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "card c-neg" in doc


# --- nice-to-haves ----------------------------------------------------------
def test_a_negative_amount_puts_the_sign_before_the_currency():
    assert "-$239" in dashboard._sign(-239.0)
    assert "$-239" not in dashboard._sign(-239.0)


def test_average_credit_capture_is_reported_next_to_the_win_rate(
        led, settings, open_market, tmp_path, monkeypatch):
    """An 80% win rate can be eight trades at 20% capture and two full stops --
    a net loser presented as healthy."""
    p = _pos()
    led.open_position(p)
    led.close_position(p, 0.5, "take_profit")
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "avg credit capture" in doc
    assert "How they ended" in doc


def test_routine_mark_events_are_kept_out_of_the_log(led, settings, open_market,
                                                     tmp_path, monkeypatch):
    """~26 a day, unpaginated, newest first: within a week the two rows anyone
    opens this tab for are off the bottom of the screen."""
    for _ in range(40):
        led.log("marked", positions=1)
    led.log("position_opened", symbol="KEEP", id="x")
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert "KEEP" in doc
    assert "routine <code>marked</code> event(s) hidden" in doc


def test_the_hidden_events_are_still_in_the_ledger(led, settings, open_market,
                                                   tmp_path, monkeypatch):
    """Filtered from the view, never from the audit trail."""
    led.log("marked", positions=1)
    _render(led, settings, open_market, tmp_path, monkeypatch)
    assert any(e["kind"] == "marked" for e in led.events)


# --- the editable cap and signing out ---------------------------------------
def test_the_max_positions_control_is_rendered(led, settings, open_market,
                                               tmp_path, monkeypatch):
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert 'action="/settings"' in doc
    assert 'name="key" value="max_open_positions"' in doc


def test_the_control_carries_the_same_bounds_the_server_enforces(
        led, settings, open_market, tmp_path, monkeypatch):
    """A form that offers a value the endpoint rejects is a form that lies."""
    from pcs.config import DASHBOARD_SETTABLE
    lo, hi = DASHBOARD_SETTABLE["max_open_positions"]
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert f'min="{lo}"' in doc and f'max="{hi}"' in doc


def test_the_control_shows_the_value_in_force(led, settings, open_market,
                                              tmp_path, monkeypatch):
    settings.max_open_positions = 7
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    flat = " ".join(doc.split())
    assert 'id="maxpos"' in flat and 'value="7"' in flat


def test_the_settings_form_posts_rather_than_gets(led, settings, open_market,
                                                  tmp_path, monkeypatch):
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert '<form class="setf" method="post"' in doc


def test_signing_out_is_offered(led, settings, open_market, tmp_path, monkeypatch):
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert 'action="/logout"' in doc
    assert "Sign out" in doc


def test_signing_out_is_a_post_not_a_link(led, settings, open_market,
                                          tmp_path, monkeypatch):
    """A GET logout fires from any page that can embed an image, and browsers
    prefetch links."""
    doc = _render(led, settings, open_market, tmp_path, monkeypatch)
    assert '<form class="logoutf" method="post" action="/logout">' in doc
    assert '<a href="/logout"' not in doc
