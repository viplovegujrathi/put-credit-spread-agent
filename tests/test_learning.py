"""The self-learning journal.

Most of these are boundary tests rather than behaviour tests, because the
interesting property of this module is what it *cannot* do. It reads the
ledger, writes one JSON file, and benches symbols. If a change ever lets it
write settings, open a position, or draw a conclusion from four trades, one of
these should go red.
"""
import datetime as dt
import pathlib

import pytest

from pcs import learning
from pcs.config import Settings
from pcs.ledger import CLOSED, EXPIRED, OPEN, Ledger, Position


def _pos(pid="p1", symbol="TST", pl=50.0, reason="take_profit: 58% of max credit",
         status=CLOSED, credit=0.60, opened="2026-08-01T11:00:00",
         closed="2026-08-20T11:00:00", spot=100.0, short=95.0, quality="live"):
    return Position(
        id=pid, symbol=symbol, sector="Energy", expiration="2026-09-04",
        short_strike=short, long_strike=short - 5, width=5.0, contracts=1,
        credit_open=credit, credit_dollars=credit * 100, collateral=500 - credit * 100,
        opened_at=opened, opened_spot=spot, status=status,
        mark_cost_to_close=0.1, mark_spot=spot, marked_at=closed,
        closed_at=closed, close_debit=0.1, realized_pl=pl, close_reason=reason,
        quote_quality=quality)


@pytest.fixture
def led(settings, tmp_path):
    return Ledger.load(settings, path=tmp_path / "ledger.json")


# --- ingest ----------------------------------------------------------------
def test_sync_ingests_closed_positions_and_is_idempotent(led):
    led.positions = [_pos("a"), _pos("b"), _pos("c", status=OPEN)]
    j = learning.Journal()
    assert learning.sync(j, led) == 2
    assert learning.sync(j, led) == 0          # re-running must not double-count
    assert {o.id for o in j.outcomes} == {"a", "b"}


def test_an_open_position_is_never_ingested(led):
    led.positions = [_pos("a", status=OPEN)]
    j = learning.Journal()
    assert learning.sync(j, led) == 0


@pytest.mark.parametrize("reason,kind", [
    ("take_profit: 58% of max credit", "take_profit"),
    ("stop_loss: buyback is 2.1x the credit", "stop_loss"),
    ("defend: short strike tested with 5 DTE", "defend"),
    ("closed by hand because I felt like it", "manual"),
    ("", "manual"),
])
def test_the_exit_kind_comes_from_the_recorded_reason(reason, kind):
    assert learning.to_outcome(_pos(reason=reason)).exit_kind == kind


def test_an_expired_position_is_its_own_kind_whatever_the_prose_says():
    p = _pos(reason="expired worthless (max profit)", status=EXPIRED)
    assert learning.to_outcome(p).exit_kind == "expired"


def test_a_fee_only_close_is_a_scratch_not_a_loss():
    """A -$0.12 close is the fee, not evidence about the strategy. Counting it
    as a loss moves the win rate without moving the record."""
    assert learning.to_outcome(_pos(pl=-0.12)).result == learning.SCRATCH
    assert learning.to_outcome(_pos(pl=-40.0)).result == learning.LOSS
    assert learning.to_outcome(_pos(pl=40.0)).result == learning.WIN


def test_derived_features_are_computed_from_what_was_stored():
    o = learning.to_outcome(_pos(spot=100.0, short=95.0, credit=1.00))
    assert o.otm_cushion == pytest.approx(0.05)
    assert o.credit_per_width == pytest.approx(0.20)
    assert o.capture == pytest.approx(0.50)       # 50 realized of 100 max credit
    assert o.days_held == 19


# --- the sample floor ------------------------------------------------------
def test_no_lesson_is_drawn_under_the_minimum_sample(settings):
    j = learning.Journal(outcomes=[learning.to_outcome(_pos(f"p{i}"))
                                   for i in range(settings.learning_min_sample - 1)])
    out = learning.lessons(j, settings)
    assert [x.confidence for x in out] == [learning.INSUFFICIENT]
    assert not any(x.suggestion for x in out)


def test_insufficient_is_reported_rather_than_returning_nothing(settings):
    """An empty list reads as 'nothing is wrong'. 'We do not know yet' is a
    different statement and the one that is true."""
    out = learning.lessons(learning.Journal(), settings)
    assert len(out) == 1 and out[0].sample == 0


def test_a_comparison_needs_both_sides_populated(settings):
    """Nine wins on wide cushions and one trade on tight ones is not evidence
    about cushions."""
    rows = [learning.to_outcome(_pos(f"w{i}", short=90.0, pl=40.0)) for i in range(9)]
    rows += [learning.to_outcome(_pos("t0", short=98.0, pl=-40.0))]
    out = learning.lessons(learning.Journal(outcomes=rows), settings)
    assert not any(x.dimension == "cushion" for x in out)


def test_a_real_split_produces_a_suggestion_that_is_only_a_suggestion(settings):
    rows = [learning.to_outcome(_pos(f"w{i}", short=88.0, pl=40.0)) for i in range(6)]
    rows += [learning.to_outcome(_pos(f"t{i}", short=98.0, pl=-40.0)) for i in range(6)]
    out = learning.lessons(learning.Journal(outcomes=rows), settings)
    cushion = next(x for x in out if x.dimension == "cushion")
    assert cushion.suggestion.startswith("./run.py config --set")
    # ... and the settings object it was derived from is untouched.
    assert settings.min_otm_cushion is None


def test_nothing_in_lessons_mutates_settings(settings):
    before = vars(settings).copy()
    rows = [learning.to_outcome(_pos(f"w{i}", short=88.0, pl=40.0)) for i in range(6)]
    rows += [learning.to_outcome(_pos(f"t{i}", short=98.0, pl=-40.0)) for i in range(6)]
    learning.lessons(learning.Journal(outcomes=rows), settings)
    assert vars(settings) == before


# --- faults and self-repair ------------------------------------------------
def _faults(j, sym, kind, n, day):
    for i in range(n):
        j.faults.append(learning.Fault(at=f"{day}T10:{i:02d}:00", kind=kind,
                                       symbol=sym, detail="x"))


def test_repeated_data_failures_bench_a_symbol(settings):
    j = learning.Journal()
    today = dt.date(2026, 9, 1)
    _faults(j, "TST", learning.MARK_FAILED, settings.learning_fault_threshold,
            "2026-08-30")
    acts = learning.self_repair(j, settings, today=today)
    assert any("TST" in a for a in acts)
    assert learning.blocked_symbols(j, today) == {"TST"}


def test_one_failure_short_of_the_threshold_does_not_bench(settings):
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, settings.learning_fault_threshold - 1,
            "2026-08-30")
    learning.self_repair(j, settings, today=dt.date(2026, 9, 1))
    assert not j.quarantines


def test_a_blocked_fill_is_recorded_but_never_benches_a_symbol(settings):
    """A refused open is usually the account being full -- the risk caps doing
    their job. Benching the ticker for it would punish the wrong thing."""
    j = learning.Journal()
    _faults(j, "TST", learning.OPEN_BLOCKED, 12, "2026-08-30")
    learning.self_repair(j, settings, today=dt.date(2026, 9, 1))
    assert not j.quarantines


def test_faults_outside_the_window_do_not_count(settings):
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, 9, "2026-07-01")
    learning.self_repair(j, settings, today=dt.date(2026, 9, 1))
    assert not j.quarantines


def test_a_quarantine_expires_on_its_own(settings):
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, settings.learning_fault_threshold,
            "2026-08-30")
    today = dt.date(2026, 9, 1)
    learning.self_repair(j, settings, today=today)
    assert learning.blocked_symbols(j, today) == {"TST"}

    later = today + dt.timedelta(days=settings.learning_quarantine_days + 1)
    j.faults.clear()                      # the provider recovered
    acts = learning.self_repair(j, settings, today=later)
    assert not j.quarantines
    assert any("expired" in a for a in acts)


def test_a_benched_symbol_is_not_benched_twice(settings):
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, 8, "2026-08-30")
    today = dt.date(2026, 9, 1)
    learning.self_repair(j, settings, today=today)
    learning.self_repair(j, settings, today=today)
    assert len(j.quarantines) == 1


def test_self_repair_off_still_expires_what_it_already_benched(settings):
    """Turning the switch off must not strand a name in quarantine forever."""
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, 8, "2026-08-30")
    today = dt.date(2026, 9, 1)
    learning.self_repair(j, settings, today=today)
    settings.self_repair = False
    later = today + dt.timedelta(days=settings.learning_quarantine_days + 1)
    learning.self_repair(j, settings, today=later)
    assert not j.quarantines


def test_self_repair_off_benches_nothing_new(settings):
    settings.self_repair = False
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, 8, "2026-08-30")
    learning.self_repair(j, settings, today=dt.date(2026, 9, 1))
    assert not j.quarantines


def test_self_repair_never_touches_settings(settings):
    """The one thing it must not learn to do."""
    before = vars(settings).copy()
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, 8, "2026-08-30")
    learning.self_repair(j, settings, today=dt.date(2026, 9, 1))
    assert vars(settings) == before


def test_self_repair_never_opens_or_closes_anything(settings, led):
    led.positions = [_pos("a", status=OPEN)]
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, 8, "2026-08-30")
    learning.self_repair(j, settings, today=dt.date(2026, 9, 1))
    assert len(led.open_positions) == 1
    assert led.cash == 3000.0


def test_a_bench_is_always_in_the_future(settings):
    j = learning.Journal()
    _faults(j, "TST", learning.MARK_FAILED, 8, "2026-08-30")
    today = dt.date(2026, 9, 1)
    learning.self_repair(j, settings, today=today)
    assert dt.date.fromisoformat(j.quarantines[0].until) > today


def test_the_fault_log_is_bounded(settings):
    j = learning.Journal()
    for i in range(learning.MAX_FAULTS + 50):
        learning.record_fault(j, learning.MARK_FAILED, "TST", f"n {i}")
    assert len(j.faults) == learning.MAX_FAULTS
    assert j.faults[-1].detail == f"n {learning.MAX_FAULTS + 49}"   # newest kept


def test_a_malformed_fault_timestamp_is_skipped_not_fatal(settings):
    j = learning.Journal()
    j.faults.append(learning.Fault(at="not-a-date", kind=learning.MARK_FAILED,
                                   symbol="TST", detail=""))
    assert learning.self_repair(j, settings, today=dt.date(2026, 9, 1)) == []


# --- persistence -----------------------------------------------------------
def test_a_missing_journal_is_a_new_account_not_an_error(tmp_path):
    j = learning.load(tmp_path / "nope.json")
    assert j.outcomes == [] and j.quarantines == []


def test_round_trip_preserves_every_record(settings, tmp_path):
    j = learning.Journal(outcomes=[learning.to_outcome(_pos("a"))])
    learning.record_fault(j, learning.MARK_FAILED, "TST", "chain empty")
    _faults(j, "TST", learning.MARK_FAILED, 8, "2026-08-30")
    learning.self_repair(j, settings, today=dt.date(2026, 9, 1))
    path = learning.save(j, tmp_path / "journal.json")
    back = learning.load(path)
    assert back.outcomes == j.outcomes
    assert back.quarantines == j.quarantines
    assert back.repairs == j.repairs


# --- honesty ---------------------------------------------------------------
def test_the_unrecorded_features_are_named_rather_than_silently_absent():
    gaps = " ".join(learning.feature_gaps())
    assert "52-week high" in gaps and "50-day average" in gaps


def test_the_strategy_baseline_is_never_a_suggestion_target(settings):
    """Lessons may suggest changing Settings. Suggesting a change to STRATEGY
    would be suggesting the agent leave its own mandate."""
    rows = [learning.to_outcome(_pos(f"w{i}", short=88.0, pl=40.0)) for i in range(6)]
    rows += [learning.to_outcome(_pos(f"t{i}", short=98.0, pl=-40.0)) for i in range(6)]
    for les in learning.lessons(learning.Journal(outcomes=rows), settings):
        if les.suggestion:
            key = les.suggestion.split("--set", 1)[1].split("=")[0].strip()
            assert key in Settings.__dataclass_fields__


# --- defects found in review ------------------------------------------------
def test_a_group_of_scratches_is_not_a_sample(settings):
    """The size floor excluded scratches from the win RATE but not from the
    group COUNT, so six fee-only closes passed the floor and were then reported
    as having won 0% -- a finding manufactured out of trades that said nothing."""
    rows = [learning.to_outcome(_pos(f"s{i}", short=98.0, pl=-0.10)) for i in range(6)]
    rows += [learning.to_outcome(_pos(f"w{i}", short=88.0, pl=40.0)) for i in range(6)]
    out = learning.lessons(learning.Journal(outcomes=rows), settings)
    assert not any(x.dimension == "cushion" for x in out)


def test_the_stale_quote_advice_only_appears_when_stale_quotes_did_worst(settings):
    """It used to key off the rendered sentence, which always began the same
    way -- so it advised a bigger stale-quote haircut when LIVE fills did worst."""
    rows = [learning.to_outcome(_pos(f"l{i}", pl=-40.0, quality="live"))
            for i in range(6)]
    rows += [learning.to_outcome(_pos(f"s{i}", pl=40.0, quality="stale"))
             for i in range(6)]
    out = learning.lessons(learning.Journal(outcomes=rows), settings)
    quotes = next((x for x in out if x.dimension == "quotes"), None)
    assert quotes is not None                      # live-worst is still worth saying
    assert "paper_slippage_frac" not in quotes.finding
    assert not quotes.suggestion


def test_the_stale_quote_advice_does_appear_the_other_way_round(settings):
    rows = [learning.to_outcome(_pos(f"l{i}", pl=40.0, quality="live"))
            for i in range(6)]
    rows += [learning.to_outcome(_pos(f"s{i}", pl=-40.0, quality="stale"))
             for i in range(6)]
    out = learning.lessons(learning.Journal(outcomes=rows), settings)
    quotes = next(x for x in out if x.dimension == "quotes")
    assert "paper_slippage_frac" in quotes.finding
    # ... and it points at the file, because config --set does not expose it.
    assert not quotes.suggestion


def test_no_suggestion_names_a_key_the_cli_will_refuse(settings):
    """A suggestion the user cannot run is worse than no suggestion."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pcs_cli", pathlib.Path(__file__).resolve().parent.parent / "run.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    rows = [learning.to_outcome(_pos(f"w{i}", short=88.0, pl=40.0, credit=1.5))
            for i in range(6)]
    rows += [learning.to_outcome(_pos(f"t{i}", short=98.0, pl=-40.0, credit=0.3))
             for i in range(6)]
    for les in learning.lessons(learning.Journal(outcomes=rows), settings):
        if les.suggestion:
            key = les.suggestion.split("--set", 1)[1].split("=")[0].strip()
            assert key in cli._CONFIG_KEYS, f"{key} is not settable from the CLI"


def test_the_journal_is_written_atomically(settings, tmp_path):
    """`mark` rewrites this every 15 minutes and the dashboard reads it on every
    render. A truncate-then-write leaves a window where the one page that
    explains what is going on fails to parse it."""
    path = tmp_path / "journal.json"
    j = learning.Journal(outcomes=[learning.to_outcome(_pos("a"))])
    learning.save(j, path)
    j.outcomes.append(learning.to_outcome(_pos("b")))
    learning.save(j, path)
    assert len(learning.load(path).outcomes) == 2
    assert not list(tmp_path.glob("*.tmp"))        # no debris left behind
