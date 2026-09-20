"""The number the dashboard has been describing instead of computing.

Win rate and average capture have sat side by side on the history tab for
weeks, under a note saying that a high win rate at low capture and a few
full-size stops is a losing book that reads as a winning one. That sentence
names the failure and then leaves the arithmetic to the reader.

On 2026-09-17 the live account was the sentence: 61.5% won across 13 closed
trades, +$11.79, while the average win kept 0.548x the credit and the average
loss gave back 1.158x -- a configuration that needs 67.9% to make nothing at
all. `min_win_rate_for_live` was set to 60%, underneath its own break-even, so
the go-live gate would have certified a book that loses money at exactly the
rate it was certified for. See LEARNING.md 44.
"""
import pytest

from pcs import expectancy
from pcs.ledger import Ledger, Position
from pcs.readiness import assess


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def closed(pl, credit=1.00, contracts=1, ident="p1") -> Position:
    """A closed position whose max credit is `credit x 100 x contracts`."""
    return Position(
        id=ident, symbol="TST", sector="Energy", expiration="2026-10-02",
        short_strike=95.0, long_strike=90.0, width=5.0, contracts=contracts,
        credit_open=credit, credit_dollars=credit * 100 * contracts - 0.12,
        collateral=384.12, opened_at="2026-09-01T14:39:25", opened_spot=100.0,
        status="closed", closed_at="2026-09-10T20:00:00", realized_pl=pl)


# --- what the settings alone require --------------------------------------
def test_the_shipped_defaults_need_two_wins_in_three(settings):
    """Book at 50% of the credit, buy back at 2x it: a win is worth 0.50x and
    a loss costs 1.00x, so 1.00 / 1.50 = 66.7%. Nothing on the entry side of
    the system moves this number."""
    p = expectancy.implied(settings)
    assert (p.win, p.loss) == (0.50, 1.00)
    assert p.breakeven == pytest.approx(2 / 3, abs=1e-4)
    assert p.sample == 0          # derived, not measured


def test_a_tighter_stop_lowers_the_bar(settings):
    settings.stop_loss_credit_multiple = 1.5
    assert expectancy.implied(settings).breakeven == pytest.approx(0.50)


def test_a_stop_that_cannot_lose_the_credit_has_no_break_even(settings):
    """At or below 1x the rule stops being expressible in credit multiples --
    at exactly 1x a 'loss' costs nothing, and below it the stop fires in
    profit. Returning a payoff there would invent a break-even of 0%, which
    reads as 'any win rate is fine': the most dangerous possible output."""
    settings.stop_loss_credit_multiple = 1.0
    assert expectancy.implied(settings) is None


def test_expectancy_is_zero_at_the_break_even_rate(settings):
    p = expectancy.implied(settings)
    assert p.expectancy(p.breakeven) == pytest.approx(0.0, abs=1e-9)
    assert p.expectancy(p.breakeven + 0.1) > 0
    assert p.expectancy(p.breakeven - 0.1) < 0


# --- which stop is actually live ------------------------------------------
def test_the_two_stops_cross_at_a_third_of_the_width(settings):
    """`pct / (mult - 1 + pct)` -- 0.50 / 1.50 at the defaults. Below it the
    credit multiple fires first; above it the 50%-of-max-loss backstop does."""
    assert expectancy.crossover_credit_frac(settings) == pytest.approx(1 / 3)


def test_a_thin_credit_is_governed_by_the_credit_multiple(settings):
    """$5 wide for $1.00 is 20% of width, under the crossover: the 2x rule
    fires at 1.00x the credit before the backstop is anywhere near."""
    assert expectancy.governing_loss(5.0, 1.00, settings) == pytest.approx(1.00)


def test_a_fat_credit_is_governed_by_the_max_loss_backstop(settings):
    """STX, 2026-09-08: $2.50 wide sold for $1.05, 42% of the width. The
    backstop fires at 0.5 x (2.50 - 1.05) / 1.05 = 0.69x the credit, well
    inside the 1.00x the credit multiple would have allowed -- so the trade
    stopped out having given back less than the credit, and the shape of the
    loss was decided by the optimizer's width choice, not by the stop setting
    anyone looked at."""
    assert expectancy.governing_loss(2.50, 1.05, settings) == pytest.approx(
        0.5 * 1.45 / 1.05)


def test_a_credit_wider_than_the_spread_is_not_a_spread(settings):
    assert expectancy.governing_loss(5.0, 5.0, settings) == 0.0
    assert expectancy.governing_loss(5.0, 0.0, settings) == 0.0


# --- what the record actually paid ----------------------------------------
def test_one_sided_books_measure_nothing(led):
    """Eight wins and no losses has not yet measured what a loss costs. A
    break-even computed off that would be 0%."""
    led.positions = [closed(50.0, ident="a"), closed(50.0, ident="b")]
    assert expectancy.realised(led.closed_positions) is None
    led.positions = [closed(-100.0, ident="a"), closed(-100.0, ident="b")]
    assert expectancy.realised(led.closed_positions) is None


def test_both_sides_measured_in_credit_multiples(led):
    """Normalised by max credit, not dollars, so a $98 trade and a $420 trade
    are comparable -- the same normalisation `learning.Outcome.capture` uses."""
    led.positions = [closed(55.0, credit=1.00, ident="a"),
                     closed(-232.0, credit=2.00, ident="b")]
    p = expectancy.realised(led.closed_positions)
    assert p.win == pytest.approx(0.55)
    assert p.loss == pytest.approx(1.16)
    assert p.sample == 2
    assert p.breakeven == pytest.approx(1.16 / 1.71, abs=1e-4)


def test_a_fee_only_close_is_in_neither_bucket(led):
    """`LOSS_FLOOR` is the ledger's own line between LOSS and SCRATCH. Twelve
    cents of fees is not a measurement of what a loss costs, and counting it
    as one would drag the average loss toward zero -- flattering the
    break-even in exactly the direction that hurts."""
    led.positions = [closed(55.0, ident="a"), closed(-232.0, credit=2.0, ident="b"),
                     closed(-0.12, ident="c")]
    p = expectancy.realised(led.closed_positions)
    assert p.sample == 2
    rate, n = expectancy.decided_win_rate(led.closed_positions)
    assert (rate, n) == (0.5, 2)          # the scratch is out of the denominator


def test_contracts_scale_the_max_credit(led):
    """Two contracts at $1.00 can make $200, so $110 of it is 0.55x -- not
    1.10x. The bug this pins would report a double-sized winner as having
    beaten its own maximum."""
    led.positions = [closed(110.0, credit=1.00, contracts=2, ident="a"),
                     closed(-100.0, credit=1.00, ident="b")]
    assert expectancy.realised(led.closed_positions).win == pytest.approx(0.55)


# --- the summary the panel and the gate both read -------------------------
def test_a_measured_payoff_beats_the_settings(settings, led):
    """The exits overshoot in both directions: take-profit fires on the first
    mark at or above the target so it books 55% against a 50% rule, and a stop
    set at 2.0x fills at 2.1x. Only one of those drifts is in the reader's
    favour, so once there is a measurement it is the one to judge against."""
    led.positions = [closed(55.0, credit=1.00, ident="a"),
                     closed(-232.0, credit=2.00, ident="b")]
    s = expectancy.summary(led, settings)
    assert s.payoff is s.realised
    assert s.breakeven == pytest.approx(1.16 / 1.71, abs=1e-4)
    assert s.implied.breakeven == pytest.approx(2 / 3, abs=1e-4)   # still reported


def test_it_falls_back_to_the_settings_before_anything_closes(settings, led):
    s = expectancy.summary(led, settings)
    assert s.realised is None
    assert s.breakeven == pytest.approx(2 / 3, abs=1e-4)
    assert s.edge is None        # no trades decided: no rate to judge
    assert s.clears is None


def test_the_edge_is_negative_when_the_rate_is_under_the_line(settings, led):
    """Five wins and five losses at the live payoff: 50% against a 68%
    requirement. Positive realised P&L would not change this -- the point of
    the number is that it reads the shape of the book, not its running total."""
    led.positions = ([closed(55.0, credit=1.00, ident=f"w{i}") for i in range(5)]
                     + [closed(-116.0, credit=1.00, ident=f"l{i}") for i in range(5)])
    s = expectancy.summary(led, settings)
    assert s.win_rate == 0.5
    assert s.clears is False
    assert s.edge == pytest.approx(0.5 * 0.55 - 0.5 * 1.16)
    assert s.edge < 0


# --- the gate that stops a bar being set under its own break-even ---------
def crit(r):
    return next(c for c in r.criteria if "break-even" in c.label)


def test_the_go_live_bar_must_clear_the_break_even(settings, led):
    """60% was the shipped bar and 66.7% is what the shipped exits require, so
    the gate as written would certify a configuration that loses money at
    exactly the rate it was certified for."""
    settings.min_win_rate_for_live = 0.60
    c = crit(assess(led, settings))
    assert c.ok is False
    assert c.blocking
    assert "would still lose money" in c.detail


def test_a_bar_above_the_break_even_passes(settings, led):
    settings.min_win_rate_for_live = 0.70
    assert crit(assess(led, settings)).ok


def test_the_bar_is_checked_against_the_measured_payoff_once_there_is_one(settings, led):
    """A 70% bar clears the 66.7% the settings imply and fails the 67.9% the
    record actually paid. The measured one has to win, or the gate certifies
    the configuration on paper while the book does something else."""
    settings.min_win_rate_for_live = 0.70
    led.positions = [closed(55.0, credit=1.00, ident="a"),
                     closed(-300.0, credit=1.00, ident="b")]      # 3.00x loss
    c = crit(assess(led, settings))
    assert c.ok is False
    assert "85%" in c.detail                                       # 3.00 / 3.55
