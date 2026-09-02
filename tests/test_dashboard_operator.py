"""The operator council's findings, pinned to the rendered page.

COUNCIL.md seat 1 found six things the page did not say. Each one is a test
here, named for the failure it prevents rather than for the markup it checks --
if a future edit drops the column, the test name says what that costs.
"""
import datetime as dt

import pytest

from pcs import dashboard, health, watchlist
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


def _render(led, settings, sess, tmp_path, monkeypatch, runs=(), watch_age_h=0.5):
    monkeypatch.setattr(health, "HEALTH_JSON", tmp_path / "health.json")
    monkeypatch.setattr(dashboard, "WEB_INDEX", None)
    # Isolate from the repo's own data/watchlist.json, which is real and old:
    # without this every render here inherits a months-stale watchlist and the
    # page under test grows an alert that has nothing to do with the test.
    monkeypatch.setattr(watchlist, "load", lambda *a, **k: None if watch_age_h is None
                        else watchlist.Watchlist(
                            generated_at=(dt.datetime.now()
                                          - dt.timedelta(hours=watch_age_h)
                                          ).isoformat(timespec="seconds"),
                            quote_quality="live", phase="open", tradeable=True,
                            entries=[]))
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
    assert "of the account" in doc
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


@pytest.fixture
def money(led, settings, shut_market, tmp_path, monkeypatch):
    """A book shaped like the live one: premium banked net of fill fees, so
    cash, net liq and unrealised P&L all have to agree."""
    for sym, credit in (("MRVL", 4.16), ("META", 3.67)):
        p = _pos(sym, credit=credit, cost_to_close=credit * 0.87)
        p.credit_dollars = round(credit * 100 - 0.12, 2)      # fees at fill
        led.positions.append(p)
    led.cash = round(led.starting_cash + led.premium_collected, 2)
    return led, _render(led, settings, shut_market, tmp_path, monkeypatch)



# --- the money cards have to add up -----------------------------------------
def test_the_cards_reconcile_start_plus_premium_into_cash(money):
    led, doc = money
    """The complaint that started this: nine cards, three different bases, and
    no way to get from one to the next. Cash IS start plus premium."""
    assert "premium taken in" in doc
    assert (f"${led.starting_cash:,.2f} you put in + the "
            f"${led.premium_collected:,.2f} premium taken in") in doc
    assert round(led.starting_cash + led.premium_collected, 2) == led.cash


def test_both_risk_numbers_name_their_own_basis(money):
    led, doc = money
    """Collateral is net of the credit; the buying-power hold is the gross
    width. Subtracting one from cash does not give the other, so each card has
    to say which it is or the page reads as an arithmetic error."""
    doc = " ".join(doc.split())
    # Each card prints its own subtraction, so the pair reads as two
    # derivations rather than as one number contradicting itself.
    assert (f"the ${led.capital_at_risk:,.2f} you would pay out less the "
            f"${led.gross_premium:,.2f} they sold for") in doc
    assert (f"${led.cash:,.2f} cash less the "
            f"${led.capital_at_risk:,.2f} tied up") in doc


def test_the_split_is_dropped_when_there_is_nothing_booked(money):
    led, doc = money
    """open +$140 next to a +$139.52 headline is two numbers for one fact."""
    cards = doc.split('<div class="cards">')[1].split("<h2>")[0]
    assert f"on the ${led.starting_cash:,.0f} you put in" in cards
    assert f"${led.gross_premium:,.2f} they sold for" in cards
    assert "booked" not in cards and "open +" not in cards


# --- what moved -------------------------------------------------------------
def test_portfolio_limits_is_not_in_the_positions_panel(money):
    led, doc = money
    now_panel = doc.split('id="p-now"')[1].split("</section>")[0]
    assert "Portfolio limits" not in now_panel
    assert "Portfolio limits" in doc.split('id="p-rules"')[1].split("</section>")[0]


def test_the_position_cap_is_editable_from_the_header_not_the_prose(money):
    led, doc = money
    assert 'id="gearpop"' in doc and 'id="gearer"' in doc
    head = doc.split('class="tabs"')[0]
    assert 'action="/settings"' in head          # the form lives in the header
    assert doc.count('action="/settings"') == 1  # and only there


# --- the class collision that mashed the watchlist together -----------------
def test_no_table_cell_borrows_the_page_subtitle_class(money):
    led, doc = money
    """`.sub` is the page subtitle: a span with no display rule and a 16px
    bottom margin. Borrowed inside a cell it rendered "METACommunication
    Services" and "$322nat" -- inline, with the margin doing nothing."""
    body = doc.split('class="tabs"')[1]
    assert 'class="sub"' not in body


# --- the bridge from premium to profit --------------------------------------
def test_the_cost_to_close_the_whole_book_is_on_the_page(money):
    """The gap that made the cards unreadable: $1,046 of premium beside $53 of
    profit, and the number that separates them computed inside `net_liq` and
    never shown. Premium less cost-to-close IS the unrealised P&L."""
    led, doc = money
    assert "cost to close now" in doc
    assert f"${led.cost_to_close:,.2f}" in doc
    assert round(led.premium_collected - led.cost_to_close, 2) == led.unrealized_pl


def test_the_page_opens_with_the_arithmetic_in_words(money):
    """Numbers do not explain themselves. Anyone should be able to read the
    account without knowing what "net liquidation" means."""
    led, doc = money
    story = doc.split('class="story"')[1].split("</div>")[0]
    assert doc.index('class="story"') < doc.index('class="cards"')
    for figure in (f"${led.starting_cash:,.2f}", f"${led.premium_collected:,.2f}",
                   f"${led.cash:,.2f}", f"${led.cost_to_close:,.2f}",
                   f"${led.net_liq:,.2f}", f"${led.collateral_held:,.2f}"):
        assert figure in story, figure


def test_the_story_explains_why_the_two_risk_numbers_differ(money):
    """`worst case $1,953` and `$3,000 held` are both true and look like a
    contradiction. The width less the premium already in hand is the whole
    explanation, and it is the one sentence the page never said."""
    led, doc = money
    story = " ".join(doc.split()).split('class="story"')[1].split("<h2>")[0]
    assert f"${led.capital_at_risk:,.2f}" in story
    assert f"${led.collateral_held:,.2f}" in story
    assert "they sold for" in story


def test_an_empty_book_does_not_narrate_four_sentences_about_nothing(
        led, settings, shut_market, tmp_path, monkeypatch):
    doc = _render(led, settings, shut_market, tmp_path, monkeypatch)
    story = doc.split('class="story"')[1].split("</div>")[0]
    assert "Nothing is open" in story
    assert "Worst case" not in story


def test_a_losing_account_reads_as_down_not_as_up_a_negative(
        led, settings, shut_market, tmp_path, monkeypatch):
    """"up $-53.15" is how a number reads when the sign is doing the work of a
    word. The story says the word."""
    p = _pos(credit=1.0, cost_to_close=3.0)          # bought back for more
    led.positions = [p]
    led.cash = round(led.starting_cash + led.premium_collected, 2)
    doc = _render(led, settings, shut_market, tmp_path, monkeypatch)
    story = doc.split('class="story"')[1].split("<h2>")[0]
    assert "down $" in story and "up $-" not in story


def test_every_subtraction_on_the_page_actually_comes_out(money):
    """The complaint in one test. Collateral is netted against the QUOTED
    credit, `premium_collected` is net of fill fees, and a page that says
    "pay out $3,000, keep $1,146.52 of premium, lose $1,853.00" is wrong by
    the $0.48 in between -- which is exactly the kind of near-miss that makes
    a correct page unreadable."""
    led, doc = money
    assert round(led.starting_cash + led.premium_collected, 2) == led.cash
    assert round(led.cash - led.cost_to_close, 2) == led.net_liq
    assert round(led.capital_at_risk - led.gross_premium, 2) == led.collateral_held
    assert round(led.cash - led.capital_at_risk, 2) == led.buying_power
    assert round(led.gross_premium - led.premium_collected, 2) == led.fees_on_open
    story = " ".join(doc.split()).split('class="story"')[1].split("<h2>")[0]
    assert f"${led.fees_on_open:,.2f} of that premium went to fees" in story
