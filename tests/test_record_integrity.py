"""What the record says has to be what happened.

Four paths could each write, or render, a fact the agent never established --
and in every case the lesson was already written down in LEARNING.md and obeyed
somewhere else in the tree:

  * `--approver` took any string, so the ledger's only evidence that a human
    looked at a trade reads `viplove (blanket paper approval)` -- a standing
    permission in a per-trade field. `Settings.auto_approver` states the rule
    ("never a person's name") one method above the branch that ignored it.
  * Expiry settlement fell back to `pos.mark_spot`, a stored price of unknown
    age, to book max profit or max loss. LEARNING.md 38: every guard on a
    decision is worth exactly what the measurement under it is worth.
  * The go-live gate proved "a stop has fired" with a substring search over
    free text that `close --reason` writes verbatim. LEARNING.md 24: a machine
    check must not depend on prose.
  * A position the mark run refused to price rendered as decided, because the
    row re-derived staleness from `marked_at` -- which cb6e8a1 deliberately
    leaves alone on a refusal. LEARNING.md 21 says to record the fact instead,
    and `health.Run` was already carrying it by symbol.
"""
import datetime as dt

import pytest

from pcs import exits, health
from pcs.config import Settings
from pcs.dashboard import _exit_pill, _heartbeat, _mark_state
from pcs.ledger import CLOSE_EXPIRED, CLOSE_MANUAL, Ledger, Position
from pcs.paper_broker import mark_positions
from pcs.readiness import assess
from pcs.session import SessionState


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def a_position(**kw) -> Position:
    base = {
        "id": "p1", "symbol": "TST", "sector": "Energy",
        "expiration": "2026-10-02", "short_strike": 95.0, "long_strike": 90.0,
        "width": 5.0, "contracts": 1, "credit_open": 1.16,
        "credit_dollars": 115.88, "collateral": 384.12,
        "opened_at": "2026-09-01T14:39:25", "opened_spot": 100.0}
    return Position(**{**base, **kw})


def marked(minutes_ago: float) -> str:
    return (dt.datetime.now() - dt.timedelta(minutes=minutes_ago)).isoformat(
        timespec="seconds")


def session(phase="open") -> SessionState:
    return SessionState(dt.datetime(2026, 9, 16, 11, 0), phase != "weekend",
                        phase, "live", "open")


def criterion(r, needle):
    return next(c for c in r.criteria if needle in c.label)


# --- the approver is a person, not a permission ----------------------------
def test_a_standing_permission_is_refused_as_an_approver():
    """The exact string in the ledger. `approved_by` is the only record that
    the human gate was honoured; a standing permission recorded there claims a
    human reviewed this fill, when what a human reviewed was the idea of fills."""
    bad = Settings().validate_approver("viplove (blanket paper approval)")
    assert bad
    assert "standing permission" in bad


def test_the_refusal_names_the_mechanism_the_writer_actually_wanted():
    """Wanting unattended paper fills is `auto_approve`, which exists and is on.
    A refusal that only says no sends the reader back to invent another string."""
    s = Settings()
    s.auto_approve = True
    bad = s.validate_approver("standing approval for all trades")
    assert "auto_approve (currently on)" in bad
    assert s.auto_approver() in bad


def test_a_plain_name_is_accepted():
    assert Settings().validate_approver("Viplove Gujrathi") == ""
    assert Settings().validate_approver("  vg  ") == ""


def test_an_empty_approver_is_refused():
    assert Settings().validate_approver("   ")


def test_the_auto_marker_cannot_be_typed_by_hand():
    """An unattended fill and a human sign-off have to stay tellable apart, and
    they are only tellable apart by this string."""
    s = Settings()
    assert s.validate_approver(s.auto_approver())


def test_validation_does_not_block_the_path_that_generates_the_marker():
    """The auto marker contains 'auto-approv', which is on the word list. If the
    check were applied to the value the agent generates rather than only to the
    value a human types, auto-approve would refuse its own fills."""
    s = Settings()
    s.auto_approve, s.mode = True, "paper"
    assert s.require_approval() is False
    assert s.auto_approver() == "agent (auto-approve, paper)"


# --- settlement will not book a number off a stale spot --------------------
def test_an_expired_position_is_not_settled_without_a_spot_this_run(settings, led):
    """Settlement is max profit or max loss off one comparison, and an expired
    row is never re-priced -- so booking it wrong is permanent. Refusing only
    delays: it settles on the next run that has a real spot."""
    p = a_position(expiration="2026-09-10", mark_spot=97.0, marked_at=marked(60))
    led.positions = [p]
    notes, fresh = mark_positions(led, settings, {})
    assert p.status == "open"
    assert p.id not in fresh
    assert "could not settle" in notes[0][1]


def test_it_does_not_fall_back_to_the_stored_mark_spot(settings, led):
    """`mark_spot` is a price of unknown age. Under the old fallback this
    position settled at max profit on a number from before expiration."""
    p = a_position(expiration="2026-09-10", mark_spot=99.0, marked_at=marked(60))
    led.positions = [p]
    mark_positions(led, settings, {})
    assert p.realized_pl == 0.0 and p.close_reason == ""


def test_a_real_spot_settles_and_records_how(settings, led):
    p = a_position(expiration="2026-09-10", mark_spot=99.0, marked_at=marked(60))
    led.positions = [p]
    mark_positions(led, settings, {"TST": 99.0})
    assert p.status == "expired"
    assert p.close_action == CLOSE_EXPIRED
    assert "max profit" in p.close_reason


# --- the go-live gate reads fields, not sentences --------------------------
def closed_row(pl, action="", reason="", **kw) -> Position:
    return a_position(status="closed", closed_at="2026-09-10T20:00:00",
                      realized_pl=pl, close_action=action, close_reason=reason,
                      **kw)


def test_a_fee_only_scratch_is_not_a_loss_taken(settings, led):
    """`LOSS_FLOOR` is the ledger's own line and `learning._result` already
    draws it. The criterion exists to prove the downside path was exercised;
    twelve cents of fees exercises nothing."""
    led.positions = [closed_row(-0.12)]
    c = criterion(assess(led, settings), "A loss has actually been taken")
    assert c.ok is False
    assert "fees are not a loss" in c.detail


def test_a_real_loss_is(settings, led):
    led.positions = [closed_row(-209.31)]
    assert criterion(assess(led, settings), "A loss has actually been taken").ok


def test_a_typed_reason_cannot_satisfy_the_stop_criterion(settings, led):
    """This is the whole point of the field. `close --reason` writes free text
    straight into `close_reason`, so the old substring search over it could be
    satisfied by mentioning the words -- including by denying that a stop
    fired."""
    led.positions = [closed_row(-209.31, action=CLOSE_MANUAL,
                                reason="closed by hand, no stop_loss involved")]
    assert criterion(assess(led, settings), "A stop or defend").ok is False


def test_an_actual_stop_does(settings, led):
    led.positions = [closed_row(-209.31, action=exits.STOP_LOSS,
                                reason="stop_loss: 2.1x the credit")]
    assert criterion(assess(led, settings), "A stop or defend").ok


def test_a_defend_counts_too(settings, led):
    led.positions = [closed_row(-120.0, action=exits.DEFEND, reason="defend: breached")]
    assert criterion(assess(led, settings), "A stop or defend").ok


def test_a_row_that_predates_the_field_cannot_be_checked_and_says_so(settings, led):
    """A criterion that cannot be evaluated counts as NOT met -- the rule this
    module's docstring already states, applied to its own new field."""
    led.positions = [closed_row(-209.31, reason="stop_loss: test")]
    c = criterion(assess(led, settings), "A stop or defend")
    assert c.ok is False
    assert "predate the field" in c.detail


# --- a row the mark run refused is not a decided row -----------------------
def test_a_refused_position_does_not_render_as_freshly_marked():
    """Its previous mark is ten minutes old and perfectly good, which is exactly
    why `marked_at` cannot answer this question."""
    p = a_position(marked_at=marked(10))
    assert _mark_state(p, session(), frozenset())[0] == "fresh"
    assert _mark_state(p, session(), frozenset({"p1"}))[0] == "refused"


def test_the_refusal_is_said_on_the_row_not_only_in_the_banner():
    p = a_position(marked_at=marked(10))
    _, txt = _mark_state(p, session(), frozenset({"p1"}))
    assert "would not re-price" in txt


def test_a_refused_row_promises_no_exit(settings):
    """The pill used to read 'the agent closes this on the next mark' for a
    position the agent had already declined to decide about."""
    p = a_position(marked_at=marked(10), mark_cost_to_close=3.0, mark_spot=88.0)
    d = exits.decide(p, settings)
    assert d.act, "fixture must be one the exit engine would act on"
    pill = _exit_pill(p, d, "refused", session(), settings)
    assert "NOT DECIDED" in pill
    assert "closes this on the next mark" not in pill


def test_only_the_refused_position_is_flagged(led):
    """A ladder on one name can have a near strike that prices and a far strike
    that does not, which is why the join is on id and not on symbol."""
    a, b = a_position(id="a", marked_at=marked(10)), a_position(id="b", marked_at=marked(10))
    refused = frozenset({"b"})
    assert _mark_state(a, session(), refused)[0] == "fresh"
    assert _mark_state(b, session(), refused)[0] == "refused"


def test_a_run_written_before_the_field_existed_flags_nothing():
    """Old rows carry no `stale_ids`, so the join degrades to the previous
    behaviour rather than vouching for rows it knows nothing about."""
    assert health.Run("mark", "2026-09-16T12:00:00").stale_ids == []


# --- the heartbeat can go red at the hour the page is read -----------------
def _hb(led, minutes_ago, phase):
    """Just the `marks` item. `propose` and `watch` have no run in these
    fixtures and correctly render as never-run, which would swamp the check."""
    hb = health.Health(runs=[health.Run("mark", marked(minutes_ago))])
    html = _heartbeat(led, hb, session(phase))
    return next(s for s in html.split('<span class="hb-item">')
                if "<b>marks</b>" in s)


def test_a_dead_mark_loop_is_flagged_at_a_weekend(led):
    """Requiring `sess.is_open` made the alarm impossible to raise outside RTH,
    so a loop last seen five days ago printed calm at exactly the hour a
    once-a-day reader opens the page."""
    led.positions = [a_position(marked_at=marked(60 * 24 * 5))]
    assert "hb-bad" in _hb(led, 60 * 24 * 5, "weekend")


def test_an_overnight_gap_inside_a_day_is_still_calm(led):
    """The reason the session gate existed. The timer only fires 09:35-16:00, so
    a forty-minute-old mark at 22:00 is normal and alerting on it would train
    the reader to skip the panel (LEARNING.md 22)."""
    led.positions = [a_position(marked_at=marked(40))]
    assert "hb-bad" not in _hb(led, 40, "closed")


def test_the_market_hours_rule_is_unchanged(led):
    led.positions = [a_position(marked_at=marked(40))]
    assert "hb-bad" in _hb(led, 40, "open")
    assert "hb-bad" not in _hb(led, 10, "open")


def test_an_empty_book_is_never_a_dead_loop(led):
    """Nothing to mark is not a failure to mark."""
    led.positions = []
    assert "hb-bad" not in _hb(led, 60 * 24 * 5, "weekend")


# --- a stop on an untouched strike is a different trade --------------------
def test_the_closed_row_says_where_the_stock_was(settings, led):
    """`close_reason` says `stop_loss` for a spread the stock went through and
    for one it never came near. Those are different failures with different
    fixes -- one is the thesis being wrong, the other is the mark or the vol --
    and the page reported them identically. `mark_spot` is written on every
    mark, so on a closed row it is the reading the exit acted on."""
    from pcs.dashboard import _closed_table
    through = closed_row(-170.17, reason="stop_loss: 2.1x the credit")
    through.mark_spot, through.short_strike = 90.0, 95.0
    clear = closed_row(-303.93, reason="stop_loss: 52% of max loss")
    clear.mark_spot, clear.short_strike = 100.0, 95.0
    html = _closed_table([through, clear])
    assert "<b>5.6%</b> through" in html
    assert "<b>5.0%</b> clear" in html


def test_a_row_with_no_spot_does_not_claim_the_strike_was_clear(settings, led):
    """`cushion` is None without a spot, and None must not render as 0% clear.
    An unknown cushion and a comfortable one are not the same fact."""
    from pcs.dashboard import _closed_table
    p = closed_row(-100.0)
    p.mark_spot = 0.0
    assert "clear" not in _closed_table([p])


def test_the_history_note_counts_stops_that_never_touched_the_strike(settings, led):
    """The live record's headline number: 3 of 5. Counted off losses rather
    than `close_action`, because that field shipped after these rows and every
    one of them would otherwise be uncountable."""
    from pcs.dashboard import _history_panel
    rows = []
    for i, (pl, spot) in enumerate([(-209.31, 100.0), (-255.05, 96.0),
                                    (-303.93, 101.0), (-136.69, 94.0),
                                    (180.0, 110.0)]):
        p = closed_row(pl, id=f"p{i}")
        p.mark_spot, p.short_strike = spot, 95.0
        rows.append(p)
    led.positions = rows
    html = _history_panel(led, settings)
    assert "<b>3 of 4</b> losing trade(s) closed with the short strike never breached" in html
