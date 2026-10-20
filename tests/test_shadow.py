"""Early exits against holding to expiration.

The point of the module is a comparison nobody could make: by 2026-10-15 every
win was a take-profit and every loss a stop, and no spread had ever been left to
run, so the record could not say whether either exit paid. These pin that the
"held" number is the one a real expiry would have booked, that it only ever
appears after the expiration date has really passed, and that a missing price
leaves a row unresolved instead of settling it wrongly.
"""
import datetime as dt
import json

import pandas as pd

from pcs import exits, shadow
from pcs.ledger import CLOSED, EXPIRED, OPEN, Ledger, Position

TODAY = dt.date(2026, 10, 18)


def _pos(pid="a", status=CLOSED, expiration="2026-10-16", action="", reason="",
         realized=100.0, credit=120.0, short=100.0, long=95.0, contracts=1):
    return Position(id=pid, symbol="TST", sector="Energy", expiration=expiration,
                    short_strike=short, long_strike=long, width=short - long,
                    contracts=contracts, credit_open=credit / 100 / contracts,
                    credit_dollars=credit, collateral=500.0,
                    opened_at="2026-09-14T11:00:00", opened_spot=105.0,
                    status=status, realized_pl=realized, close_action=action,
                    close_reason=reason)


def _fetch(price, calls=None):
    def f(symbol, expiration):
        if calls is not None:
            calls.append((symbol, expiration))
        return price
    return f


# --- the held number is what a real expiry books ----------------------------
def test_held_to_expiry_above_the_short_strike_keeps_the_whole_credit():
    assert shadow.held_pl(_pos(credit=120.0), 110.0) == 120.0


def test_held_through_both_strikes_loses_the_width_less_the_credit():
    # width 5 = $500 against $120 banked
    assert shadow.held_pl(_pos(credit=120.0), 90.0) == -380.0


def test_held_between_the_strikes_loses_the_intrinsic_value():
    # short 100, close 97 -> $3 of intrinsic = $300 against $120 banked
    assert shadow.held_pl(_pos(credit=120.0), 97.0) == -180.0


def test_held_scales_with_contracts():
    p = _pos(credit=240.0, contracts=2)
    assert shadow.held_pl(p, 90.0) == 240.0 - 500.0 * 2


# --- what is due ------------------------------------------------------------
def test_only_an_early_close_past_its_expiration_is_due():
    assert shadow.due(_pos(), TODAY)
    assert not shadow.due(_pos(expiration="2026-10-18"), TODAY)   # same day: bar not final
    assert not shadow.due(_pos(expiration="2026-10-30"), TODAY)
    assert not shadow.due(_pos(status=OPEN), TODAY)
    assert not shadow.due(_pos(status=EXPIRED), TODAY)            # it WAS held, for real


def test_a_resolved_row_is_not_due_again():
    p = _pos()
    p.shadow_settle_spot = 101.0
    assert not shadow.due(p, TODAY)


# --- resolving --------------------------------------------------------------
def _ledger(settings, tmp_path, *positions):
    led = Ledger.load(settings, path=tmp_path / "ledger.json")
    led.positions = list(positions)
    return led


def test_resolve_fills_the_shadow_fields(settings, tmp_path):
    p = _pos(action=exits.STOP_LOSS, realized=-150.0, credit=120.0)
    led = _ledger(settings, tmp_path, p)
    done = shadow.resolve(led, fetch=_fetch(108.0), today=TODAY)
    assert done == [p]
    assert p.shadow_settle_spot == 108.0
    assert p.shadow_pl == 120.0                  # it would have expired worthless
    assert any(e["kind"] == "shadow_settled" for e in led.events)


def test_resolve_does_not_download_for_a_position_that_is_not_due(settings, tmp_path):
    calls = []
    led = _ledger(settings, tmp_path, _pos(expiration="2026-10-30"), _pos("b", status=OPEN),
                  _pos("c", status=EXPIRED))
    assert shadow.resolve(led, fetch=_fetch(100.0, calls), today=TODAY) == []
    assert calls == []


def test_a_missing_price_leaves_the_row_unsettled_not_settled_at_zero(settings, tmp_path):
    p = _pos()
    led = _ledger(settings, tmp_path, p)
    assert shadow.resolve(led, fetch=_fetch(None), today=TODAY) == []
    assert p.shadow_settle_spot is None and p.shadow_pl is None


def test_a_failed_fetch_is_not_retried_the_same_day_but_is_the_next(settings, tmp_path):
    """26 marks a day must not turn one missing bar into 26 downloads."""
    calls = []
    p = _pos()
    led = _ledger(settings, tmp_path, p)
    shadow.resolve(led, fetch=_fetch(None, calls), today=TODAY)
    shadow.resolve(led, fetch=_fetch(None, calls), today=TODAY)
    assert len(calls) == 1
    shadow.resolve(led, fetch=_fetch(None, calls), today=TODAY + dt.timedelta(days=1))
    assert len(calls) == 2


def test_one_bad_fetch_does_not_stop_the_rest(settings, tmp_path):
    a, b = _pos("a"), _pos("b")

    def f(symbol, expiration):
        if f.n == 0:
            f.n += 1
            raise RuntimeError("network down")
        return 110.0
    f.n = 0
    led = _ledger(settings, tmp_path, a, b)
    done = shadow.resolve(led, fetch=f, today=TODAY)
    assert done == [b] and a.shadow_pl is None


def test_a_run_settles_at_most_the_cap(settings, tmp_path):
    """The first run after deploy has every closed trade to back-fill and holds
    the ledger lock while it works."""
    ps = [_pos(str(i)) for i in range(10)]
    led = _ledger(settings, tmp_path, *ps)
    assert len(shadow.resolve(led, fetch=_fetch(110.0), today=TODAY, limit=4)) == 4
    assert len(shadow.resolve(led, fetch=_fetch(110.0), today=TODAY, limit=4)) == 4
    assert len(shadow.resolve(led, fetch=_fetch(110.0), today=TODAY, limit=4)) == 2


def test_shadow_fields_never_touch_cash_or_the_realised_figure(settings, tmp_path):
    p = _pos(realized=-150.0)
    led = _ledger(settings, tmp_path, p)
    cash = led.cash
    shadow.resolve(led, fetch=_fetch(90.0), today=TODAY)
    assert led.cash == cash and p.realized_pl == -150.0 and p.status == CLOSED


# --- the settlement price ---------------------------------------------------
def _frame(rows, multi=False):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in rows])
    df = pd.DataFrame({"Close": [c for _, c in rows]}, index=idx)
    if multi:
        df.columns = pd.MultiIndex.from_product([df.columns, ["TST"]])
    return df


def _patch_download(monkeypatch, df):
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda *a, **k: df)


def test_settle_close_returns_the_bar_for_that_exact_date(monkeypatch):
    _patch_download(monkeypatch, _frame([("2026-10-16", 132.0)]))
    assert shadow.settle_close("TST", "2026-10-16") == 132.0


def test_settle_close_reads_the_multiindex_frame_yfinance_returns(monkeypatch):
    _patch_download(monkeypatch, _frame([("2026-10-16", 87.81)], multi=True))
    assert shadow.settle_close("TST", "2026-10-16") == 87.81


def test_settle_close_refuses_a_bar_for_a_different_day(monkeypatch):
    """A holiday-shifted request returning the next session's bar must not be
    booked as the expiration-day settlement."""
    _patch_download(monkeypatch, _frame([("2026-10-19", 99.0)]))
    assert shadow.settle_close("TST", "2026-10-16") is None


def test_settle_close_refuses_empty_nan_and_errors(monkeypatch):
    _patch_download(monkeypatch, _frame([]))
    assert shadow.settle_close("TST", "2026-10-16") is None
    _patch_download(monkeypatch, _frame([("2026-10-16", float("nan"))]))
    assert shadow.settle_close("TST", "2026-10-16") is None
    import yfinance as yf

    def boom(*a, **k):
        raise OSError("no route")
    monkeypatch.setattr(yf, "download", boom)
    assert shadow.settle_close("TST", "2026-10-16") is None


# --- classifying how a trade was closed -------------------------------------
def test_kind_reads_close_action_when_present():
    assert shadow.kind(_pos(action=exits.STOP_LOSS)) == "stop_loss"
    assert shadow.kind(_pos(action="manual")) == "manual"


def test_kind_falls_back_to_the_reason_on_rows_that_predate_close_action():
    """Blank is "not recorded", not "not a stop": 13 of the first 23 trades have
    no close_action and every one of them has its exit in the prose reason."""
    assert shadow.kind(_pos(reason="take_profit: at 55% of max credit")) == "take_profit"
    assert shadow.kind(_pos(reason="stop_loss: down $304, which is 52% ...")) == "stop_loss"
    assert shadow.kind(_pos(reason="something else")) == "other"
    assert shadow.kind(_pos()) == "other"


# --- the comparison ---------------------------------------------------------
def _settled(p, held):
    p.shadow_settle_spot = 100.0
    p.shadow_pl = held
    return p


def test_comparison_groups_by_exit_and_counts_where_holding_was_better():
    tp1 = _settled(_pos("t1", action=exits.TAKE_PROFIT, realized=60.0), 120.0)
    tp2 = _settled(_pos("t2", action=exits.TAKE_PROFIT, realized=60.0), -380.0)
    st = _settled(_pos("s1", action=exits.STOP_LOSS, realized=-150.0), -380.0)
    rows, pending = shadow.comparison([tp1, tp2, st, _pos("w", status=OPEN),
                                       _pos("x", status=EXPIRED, realized=100.0)])
    by = {r.kind: r for r in rows}
    assert pending == 0
    assert (by["take_profit"].n, by["take_profit"].actual, by["take_profit"].held) == (2, 120.0, -260.0)
    assert by["take_profit"].delta == -380.0 and by["take_profit"].held_better == 1
    assert (by["stop_loss"].n, by["stop_loss"].delta, by["stop_loss"].held_better) == (1, -230.0, 0)


def test_comparison_counts_early_closes_still_waiting():
    rows, pending = shadow.comparison([_pos("a"), _pos("b", expiration="2026-10-30")])
    assert rows == [] and pending == 2


def test_the_report_says_when_nothing_has_settled_yet():
    assert "no early close has passed its expiration yet (1 waiting)" in \
        shadow.report_lines([_pos()])[0]


def test_the_report_totals_across_exit_kinds():
    ps = [_settled(_pos("t", action=exits.TAKE_PROFIT, realized=60.0), 120.0),
          _settled(_pos("s", action=exits.STOP_LOSS, realized=-150.0), -380.0)]
    text = "\n".join(shadow.report_lines(ps))
    assert "take_profit" in text and "stop_loss" in text and "all" in text


# --- the ledger carries it ---------------------------------------------------
def test_an_old_ledger_without_shadow_fields_still_loads(settings, tmp_path):
    """The box's ledger predates these fields. Position(**row) must default them."""
    led = _ledger(settings, tmp_path, _pos())
    led.save()
    raw = json.loads(led.path.read_text())
    for k in ("shadow_settle_spot", "shadow_pl", "shadow_tried_on"):
        del raw["positions"][0][k]
    led.path.write_text(json.dumps(raw))
    back = Ledger.load(settings, path=led.path).positions[0]
    assert back.shadow_pl is None and back.shadow_settle_spot is None
    assert back.shadow_tried_on == ""


def test_shadow_fields_survive_a_save_and_load(settings, tmp_path):
    p = _pos()
    led = _ledger(settings, tmp_path, p)
    shadow.resolve(led, fetch=_fetch(110.0), today=TODAY)
    led.save()
    back = Ledger.load(settings, path=led.path).positions[0]
    assert (back.shadow_settle_spot, back.shadow_pl) == (110.0, 120.0)


# --- the mark job ------------------------------------------------------------
def test_a_failure_in_shadow_cannot_break_the_job_that_takes_stops(
        settings, tmp_path, monkeypatch, capsys):
    import run as cli
    led = _ledger(settings, tmp_path, _pos())

    def boom(*a, **k):
        raise RuntimeError("yfinance exploded")
    monkeypatch.setattr(shadow, "resolve", boom)
    cli._shadow_pass(led)                       # must not raise
    assert "shadow: skipped" in capsys.readouterr().out


def test_the_mark_pass_saves_what_it_settled(settings, tmp_path, monkeypatch, capsys):
    import run as cli
    p = _pos(action=exits.STOP_LOSS, realized=-150.0, expiration="2020-01-03")
    led = _ledger(settings, tmp_path, p)
    monkeypatch.setattr(shadow, "settle_close", lambda s, e: 108.0)
    cli._shadow_pass(led)
    assert Ledger.load(settings, path=led.path).positions[0].shadow_pl == 120.0
    assert "SHADOW" in capsys.readouterr().out
