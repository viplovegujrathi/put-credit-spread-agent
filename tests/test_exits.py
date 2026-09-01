"""Section 1.7 exits, as decisions rather than advice.

The agent books profit and cuts losses on its own. These tests pin the two
things that matter about that: the triggers fire on the right numbers, and the
autonomy stays inside its box -- closing only, paper only, fresh marks only.
"""
import pytest
from conftest import make_chain

from pcs.config import STRATEGY
from pcs.exits import DEFEND, HOLD, ROLL, STOP_LOSS, TAKE_PROFIT, decide, review, ticket
from pcs.ledger import Ledger, Position
from pcs.optimizer import build_spreads
from pcs.paper_broker import (
    ApprovalRequired,
    MarketNotReady,
    apply_exits,
    open_approved,
)


def a_position(credit=1.00, width=5.0, contracts=1, debit=None, spot=100.0,
               short=97.0, dte=25) -> Position:
    import datetime as dt
    return Position(
        id="p1", symbol="TST", sector="Industrials",
        expiration=(dt.date.today() + dt.timedelta(days=dte)).isoformat(),
        short_strike=short, long_strike=short - width, width=width,
        contracts=contracts, credit_open=credit,
        credit_dollars=credit * 100 * contracts,
        collateral=(width * 100 - credit * 100) * contracts,
        opened_at="", opened_spot=spot,
        mark_cost_to_close=credit if debit is None else debit, mark_spot=spot)


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


# -- profit booking --------------------------------------------------------
def test_books_profit_at_the_strategy_target(settings):
    """The trigger is the LOWER edge of the band, so the first mark at or above
    it books. Derived from STRATEGY so moving the target moves the test."""
    debit = round(1.00 - STRATEGY.take_profit_pct, 2)   # exactly on the target
    pos = a_position(credit=1.00, debit=debit)
    d = decide(pos, settings)
    assert d.action == TAKE_PROFIT and d.act
    assert f"{STRATEGY.take_profit_pct:.0%}" in d.reason


def test_does_not_book_a_cent_early(settings):
    debit = round(1.01 - STRATEGY.take_profit_pct, 2)  # one cent short of it
    pos = a_position(credit=1.00, debit=debit)
    d = decide(pos, settings)
    assert not d.act and d.action == HOLD


def test_the_trigger_is_the_bottom_of_the_band_not_the_middle(settings):
    """Booking at the lower edge is the point: everything above it is time the
    account did not need to spend carrying gamma."""
    lo, hi = STRATEGY.take_profit_band
    assert STRATEGY.take_profit_pct == lo < hi


def test_the_profit_target_defaults_to_the_skill_and_reports_any_override(settings):
    """The target is overridable, but never silently: an untouched account uses
    the skill number, and a changed one shows up in deviations()."""
    assert STRATEGY.take_profit_pct == 0.50
    assert STRATEGY.take_profit_band == (0.50, 0.75)
    assert settings.take_profit_pct is None          # untouched
    assert settings.strategy().take_profit_pct == 0.50
    assert settings.deviations() == []

    settings.take_profit_pct = 0.30
    assert settings.strategy().take_profit_pct == 0.30
    assert any("take profit" in d for d in settings.deviations())
    pos = a_position(credit=1.00, debit=0.68)        # 32% captured
    assert decide(pos, settings).action == TAKE_PROFIT


# -- stop loss -------------------------------------------------------------
def test_stops_out_at_twice_the_credit(settings):
    """The classic credit-spread stop: buyback costs 2x what came in."""
    pos = a_position(credit=1.00, debit=2.00)
    d = decide(pos, settings)
    assert d.action == STOP_LOSS and d.act
    assert pos.open_pl == -100.0                        # loss equals the credit
    assert "2.0x" in d.reason


def test_does_not_stop_just_under_the_multiple(settings):
    pos = a_position(credit=1.00, debit=1.99)
    assert not decide(pos, settings).act


def test_the_multiple_is_configurable(settings):
    pos = a_position(credit=1.00, debit=1.50)
    assert not decide(pos, settings).act
    settings.stop_loss_credit_multiple = 1.5
    assert decide(pos, settings).action == STOP_LOSS


def test_max_loss_backstop_catches_rich_credit_spreads(settings):
    """When credit is large relative to width, 2x the credit is unreachable --
    the loss can still run to the max, so a second stop has to exist."""
    pos = a_position(credit=4.00, width=5.0, debit=4.60)   # collateral $100
    assert pos.collateral == 100.0
    assert pos.mark_cost_to_close * 100 < 2 * pos.credit_open * 100   # multiple can't fire
    d = decide(pos, settings)
    assert d.action == STOP_LOSS and d.act
    assert "max loss" in d.reason


def test_stops_are_checked_before_profit(settings):
    """A future edit must not let a profit rule mask a stop."""
    import inspect

    from pcs import exits
    src = inspect.getsource(exits.decide)
    assert src.index("stop_loss_credit_multiple") < src.index("take_profit_pct")


# -- defending near expiration ---------------------------------------------
def test_defends_a_breached_short_strike_near_expiration(settings):
    pos = a_position(credit=1.00, debit=1.20, spot=96.0, short=97.0, dte=5)
    d = decide(pos, settings)
    assert d.action == DEFEND and d.act
    assert "never remove the long leg" in d.reason.lower()


def test_a_breach_with_time_left_is_watched_not_closed(settings):
    pos = a_position(credit=1.00, debit=1.20, spot=96.0, short=97.0, dte=25)
    d = decide(pos, settings)
    assert not d.act and "watch it" in d.reason


def test_underwater_near_manage_dte_suggests_a_roll(settings):
    pos = a_position(credit=1.00, debit=1.10, spot=105.0, short=97.0, dte=15)
    d = decide(pos, settings)
    assert d.action == ROLL and not d.act


# -- the boundaries of the autonomy ----------------------------------------
def test_auto_exit_refuses_to_run_on_a_live_ledger(led, settings, live_session):
    led.mode = "live"
    with pytest.raises(ApprovalRequired):
        apply_exits(led, settings, sess=live_session)


@pytest.mark.parametrize("phase", ["closed", "premarket", "weekend", "holiday"])
def test_no_exit_is_taken_while_the_market_is_shut(led, settings, live_session, phase):
    """A close at 22:00 against the afternoon's last print is a fill nobody
    could have got. The opening range is different -- see the test below."""
    from pcs.session import SessionState
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)[0][0]
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                        sess=live_session)
    pos.mark_cost_to_close = pos.credit_open * 3          # a screaming stop
    shut = SessionState(None, False, phase, "stale", "")
    with pytest.raises(MarketNotReady):
        apply_exits(led, settings, fresh={pos.id}, sess=shut)
    assert pos.status == "open"


def test_a_stale_mark_decides_nothing(led, settings, live_session):
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)[0][0]
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                        sess=live_session)
    pos.mark_cost_to_close = pos.credit_open * 3        # a screaming stop...
    assert decide(pos, settings).act
    # ...but it did not re-price this run, so it is not in `fresh`
    assert review(led, settings, fresh=set()) == []
    acted = apply_exits(led, settings, fresh=set(), sess=live_session)
    assert acted == [] and pos.status == "open"


def test_exits_only_ever_close(led, settings, live_session):
    """apply_exits must never grow the book or the capital at risk."""
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)[0][0]
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                        sess=live_session)
    before = (len(led.positions), led.capital_at_risk)
    pos.mark_cost_to_close = round(pos.credit_open * 0.4, 2)   # 60% captured
    acted = apply_exits(led, settings, fresh={pos.id}, sess=live_session)
    assert len(acted) == 1
    assert len(led.positions) == before[0]              # nothing new opened
    assert led.capital_at_risk < before[1]              # risk went down
    assert led.buying_power > 0


def test_an_executed_exit_is_logged_as_the_agents_decision(led, settings, live_session):
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)[0][0]
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                        sess=live_session)
    pos.mark_cost_to_close = round(pos.credit_open * 0.4, 2)
    apply_exits(led, settings, fresh={pos.id}, sess=live_session)
    ev = [e for e in led.events if e["kind"] == "auto_exit"]
    assert len(ev) == 1 and ev[0]["decided_by"] == "agent"
    assert pos.status == "closed" and pos.realized_pl > 0
    assert "take_profit" in pos.close_reason


def test_a_live_close_renders_a_ticket_for_a_human(settings):
    pos = a_position(credit=1.00, debit=0.40)
    d = decide(pos, settings)
    t = ticket(pos, d)
    assert "CLOSE" in t and "BUY" in t and "SELL" in t
    assert f"{pos.short_strike:g}" in t


def test_exits_are_not_blocked_by_the_opening_range(led, settings, live_session):
    """The opening gate stops new risk. A stop loss must still be able to fire."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    from pcs.session import state
    opening = state(dt.datetime(2026, 8, 31, 9, 41, tzinfo=ZoneInfo("America/New_York")))
    assert not opening.can_open_positions

    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)[0][0]
    pos = open_approved(led, sp, "Industrials", 1, settings, "P1", "human",
                        sess=live_session)
    pos.mark_cost_to_close = pos.credit_open * 2.5
    acted = apply_exits(led, settings, fresh={pos.id}, sess=opening)
    assert len(acted) == 1 and acted[0][1].action == STOP_LOSS
