"""The wait after a loss, and the four numbers now recorded at entry.

Both come out of the same incident. GOOGL 325/320 was sold on 2026-09-01 and
stopped out at 13:50 the next morning for -$209 while the stock had moved
-0.41% and the short strike, 3.1% below spot, was never touched. The mark was
wrong; the stop acted correctly on it.

The agent then re-proposed GOOGL in the same run, hours after the stop. It was
blocked, but only because the master trading switch happened to be off -- no
rule stood between a stop firing and the same name being re-opened at a worse
price. `max_positions_per_ticker` was not that rule: it stops the account
HOLDING two spreads on a name at once and says nothing about re-entering after
a close.

And nothing in the record could have told you whether that trade was a bad
setup or a bad price, because the two conditions the screen selects on -- how
far off the 52-week high, how far below the 50dma -- did not survive the fill.
"""
import datetime as dt

import pytest
from conftest import make_chain

from pcs import exits
from pcs.ledger import Ledger, Position
from pcs.optimizer import build_spreads
from pcs.paper_broker import CoolingOff, apply_exits, open_approved


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def a_spread(settings, live_session, symbol="TST"):
    return build_spreads(make_chain(symbol=symbol, spot=100.0), 100.0,
                         settings, live_session)[0][0]


def closed(symbol="TST", pl=-209.31, at="2026-09-02T13:50:11") -> Position:
    return Position(
        id=symbol.lower(), symbol=symbol, sector="Energy", expiration="2026-10-02",
        short_strike=95.0, long_strike=90.0, width=5.0, contracts=1,
        credit_open=1.16, credit_dollars=115.88, collateral=384.12,
        opened_at="2026-09-01T14:39:25", opened_spot=100.0, status="closed",
        closed_at=at, realized_pl=pl, close_reason="stop_loss: test")


TODAY = dt.date(2026, 9, 3)


# --- what counts as needing a cooldown ------------------------------------
def test_a_loss_benches_the_name_and_names_the_date(settings, led):
    settings.reentry_cooldown_days = 5
    led.positions = [closed(pl=-209.31)]
    assert led.cooling_off(settings, today=TODAY) == {"TST": "2026-09-07"}


def test_a_win_does_not(settings, led):
    """Selling premium again in a name that just paid out is the strategy
    working, not churn. There is nothing to cool off from."""
    settings.reentry_cooldown_days = 5
    led.positions = [closed(pl=180.0)]
    assert led.cooling_off(settings, today=TODAY) == {}


def test_a_fee_only_close_does_not(settings, led):
    """Inside a dollar either way is fees, not a result -- the same line
    `learning._result` draws between LOSS and SCRATCH. A $0.12 close is not the
    loss this exists for, and benching a name for it would be an accident."""
    settings.reentry_cooldown_days = 5
    led.positions = [closed(pl=-0.12)]
    assert led.cooling_off(settings, today=TODAY) == {}


def test_it_expires_on_its_own(settings, led):
    settings.reentry_cooldown_days = 5
    led.positions = [closed(at="2026-08-20T13:50:11")]
    assert led.cooling_off(settings, today=TODAY) == {}


def test_zero_switches_it_off(settings, led):
    settings.reentry_cooldown_days = 0
    led.positions = [closed()]
    assert led.cooling_off(settings, today=TODAY) == {}


def test_the_most_recent_loss_sets_the_date(settings, led):
    """Two losses on one name: the later close is the one that has to bind, or
    an old loss makes a fresh one look already served."""
    settings.reentry_cooldown_days = 5
    led.positions = [closed(at="2026-09-02T13:50:11"), closed(at="2026-08-30T13:50:11")]
    assert led.cooling_off(settings, today=TODAY) == {"TST": "2026-09-07"}


def test_an_unparseable_timestamp_does_not_bench_forever(settings, led):
    """A close stamp this cannot read must fail open. Refusing to enter a name
    permanently because of a malformed string is a worse failure than missing
    one cooldown."""
    settings.reentry_cooldown_days = 5
    led.positions = [closed(at="not-a-date")]
    assert led.cooling_off(settings, today=TODAY) == {}


# --- the gate -------------------------------------------------------------
def test_the_fill_path_refuses_a_name_still_cooling_off(settings, led, live_session):
    """Enforced at the fill and not only at proposal time, because this gate is
    the only one that sees the present. A ticket written at 13:00 still says
    `risk_ok` after a stop fires at 13:50 -- which is exactly the sequence."""
    settings.reentry_cooldown_days = 5
    led.positions = [closed(symbol="TST", at=dt.datetime.now().isoformat(timespec="seconds"))]
    with pytest.raises(CoolingOff) as exc:
        open_approved(led, a_spread(settings, live_session), "Energy", 1, settings,
                      "P1", "human", sess=live_session)
    assert "closed at a loss" in str(exc.value)
    assert "reentry_cooldown_days" in str(exc.value)   # says how to change it


def test_only_the_name_that_lost_is_refused(settings, led, live_session):
    settings.reentry_cooldown_days = 5
    led.positions = [closed(symbol="OTHER",
                            at=dt.datetime.now().isoformat(timespec="seconds"))]
    pos = open_approved(led, a_spread(settings, live_session), "Energy", 1,
                        settings, "P1", "human", sess=live_session)
    assert pos.symbol == "TST"


def test_a_cooldown_can_never_hold_a_losing_position_open(settings, led, live_session):
    """The property that makes this safe to run unattended. Every gate added to
    the opening path has to be checked against the exit path, because a rule
    that blocked a close would turn a delay into an unbounded loss. `apply_exits`
    does not go through `open_approved` at all, and this pins that."""
    settings.reentry_cooldown_days = 5
    settings.paper_trading = True
    sp = a_spread(settings, live_session)
    pos = open_approved(led, sp, "Energy", 1, settings, "P1", "human", sess=live_session)

    # Same name, now deep enough underwater to trip the stop.
    pos.mark_cost_to_close = pos.credit_open * settings.stop_loss_credit_multiple + 0.5
    assert exits.decide(pos, settings).action == exits.STOP_LOSS

    acted = apply_exits(led, settings, fresh={pos.id}, sess=live_session)
    assert [p.id for p, _ in acted] == [pos.id]
    assert pos.status == "closed"

    # ... and the close it just took is what starts the cooldown.
    assert "TST" in led.cooling_off(settings)


# --- what the fill now writes down ----------------------------------------
def test_the_screen_conditions_reach_the_position(settings, led, live_session):
    sp = a_spread(settings, live_session)
    pos = open_approved(led, sp, "Energy", 1, settings, "P1", "human",
                        sess=live_session, pct_off_high=0.22, pct_from_dma50=-0.05)
    assert pos.pct_off_high_at_open == 0.22
    assert pos.pct_from_dma50_at_open == -0.05
    # Both of these were already on the sized spread and were dropped at fill.
    assert pos.short_delta_at_open == sp.short_delta
    assert pos.iv_at_open == sp.iv


def test_an_unrecorded_feature_stays_none_rather_than_zero(settings, led, live_session):
    """`0.0` is a real reading -- a stock exactly at its 52-week high. A
    position that was never told has to be distinguishable from one that was."""
    pos = open_approved(led, a_spread(settings, live_session), "Energy", 1,
                        settings, "P1", "human", sess=live_session)
    assert pos.pct_off_high_at_open is None
    assert pos.pct_from_dma50_at_open is None


def test_the_features_survive_a_ledger_round_trip(settings, led, live_session):
    open_approved(led, a_spread(settings, live_session), "Energy", 1, settings,
                  "P1", "human", sess=live_session, pct_off_high=0.22,
                  pct_from_dma50=-0.05)
    back = Ledger.load(settings, path=led.save())
    assert back.open_positions[0].pct_off_high_at_open == 0.22
    assert back.open_positions[0].pct_from_dma50_at_open == -0.05


def test_the_ticker_count_is_a_count(settings, led, live_session):
    settings.max_positions_per_ticker = 3
    for i, sym in enumerate(("TST", "TST", "OTHER")):
        open_approved(led, a_spread(settings, live_session, sym), "Energy", 1,
                      settings, f"P{i}", "human", sess=live_session)
    assert led.ticker_counts() == {"TST": 2, "OTHER": 1}
