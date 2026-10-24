"""A name with no spread on its nearest expiration is tried on the others in the window.

`pick_expiration` takes the listed expiration nearest 32 days out and sizing
never looked further, so a weekly that was the closest listing but had no open
interest cost the name even when the monthly a week away would have qualified.
Of 3,145 skip lines in 39 sittings, 1,866 were open interest or quotes; on
2026-10-23 the 11-27 weekly produced a spread for 7 names and the 11-20 monthly
for 20. The fallback is off unless `expiry_fallback` is set, and it never looks
outside the DTE window. See TRADING_COUNCIL.md R1 and LEARNING.md 63.
"""
import datetime as dt
import types

import pytest
from conftest import make_chain

from pcs import chains, pipeline
from pcs.ledger import Ledger
from pcs.screener import Candidate

TODAY = dt.date.today()


def _on(days: int) -> str:
    return (TODAY + dt.timedelta(days=days)).isoformat()


# Three expirations inside the window, named in the order the selector prefers
# them. Which of +31/+34/+36 days is best depends on the weekday, because a
# Friday beats a nearer non-Friday, so the order is asked for, not assumed.
E1, E2, E3 = chains.rank_expirations([_on(31), _on(34), _on(36)])
OUTSIDE = _on(45)


def _candidate(symbol="TST"):
    return Candidate(symbol=symbol, name=f"{symbol} Inc", sector="Energy", bucket="tight",
                     note="", spot=100.0, dma50=110.0, pct_from_dma50=-0.09,
                     pct_off_high=0.25, high_52w=130.0, low_52w=90.0,
                     avg_vol_30d=1e6, last_bar="2026-10-16",
                     earnings_date="2026-12-01", earnings_source="test",
                     earnings_in_window=False)


def _thin(expiration, listed):
    """Quotes at every strike, no open interest: a chain that sizes nothing."""
    ch = make_chain(expiration=expiration, oi=0)
    ch.listed = list(listed)
    return ch


def _good(expiration, listed):
    ch = make_chain(expiration=expiration)
    ch.listed = list(listed)
    return ch


def _failed(expiration):
    return chains.PutChain("TST", expiration, 0.0, [], "yfinance", "now", error="boom")


@pytest.fixture
def served(monkeypatch):
    """Replace the provider. `by_exp` maps an expiration to the chain it returns;
    `first` is what the unspecified (nearest-expiration) call returns."""
    calls: list[str | None] = []
    box = types.SimpleNamespace(calls=calls, first=None, by_exp={})

    def fake(symbol, source="yfinance", expiration=None, target_dte=None, spot=0.0,
             iv=0.30):
        calls.append(expiration)
        return box.first if expiration is None else box.by_exp[expiration]

    monkeypatch.setattr(chains, "get_chain", fake)
    return box


def _size(settings, live_session, expiration=None):
    res = pipeline.ScreenResult(live_session, None, [], {}, {})
    return pipeline.size_candidates([_candidate()], res, settings, expiration=expiration)[0]


# --- ranking ----------------------------------------------------------------
MON = dt.date(2026, 8, 31)


def test_every_expiration_in_the_window_is_ranked_best_first():
    # 09-30 Wed (30 DTE, +3), 10-02 Fri (32), 10-06 Tue (36, +3); 09-18 and 11-20 are outside.
    listed = ["2026-09-18", "2026-09-30", "2026-10-02", "2026-10-06", "2026-11-20"]
    assert chains.rank_expirations(listed, today=MON) == [
        "2026-10-02", "2026-09-30", "2026-10-06"]


def test_the_first_choice_is_still_the_head_of_the_ranking():
    listed = ["2026-09-30", "2026-10-02", "2026-10-06"]
    assert chains.pick_expiration(listed, today=MON) == chains.rank_expirations(
        listed, today=MON)[0]
    assert chains.pick_expiration(["2026-09-18", "2026-11-20"], today=MON) is None
    assert chains.rank_expirations(["2026-09-18", "not a date"], today=MON) == []


def test_alternates_leave_out_the_chosen_expiration_and_stay_in_the_window():
    ch = _thin(E1, [E1, E2, OUTSIDE])
    assert chains.alternate_expirations(ch) == [E2]


def test_alternates_are_capped():
    ch = _thin(E1, [E1, E2, E3, _on(29)])
    assert len(chains.alternate_expirations(ch)) == chains.FALLBACK_TRIES == 2


def test_a_provider_that_does_not_say_what_it_lists_has_no_alternates():
    assert chains.alternate_expirations(_thin(E1, [])) == []


def test_the_yfinance_chain_remembers_everything_it_listed(monkeypatch):
    class Tk:
        options = (E1, E2, OUTSIDE)
        fast_info = {"last_price": 100.0}

        def __init__(self, symbol):
            pass

        def option_chain(self, exp):
            import pandas as pd
            return types.SimpleNamespace(puts=pd.DataFrame(
                [{"strike": 95.0, "bid": 1.0, "ask": 1.1, "lastPrice": 1.05,
                  "volume": 10, "openInterest": 100, "impliedVolatility": 0.3}]))

    monkeypatch.setattr(chains.yf, "Ticker", Tk)
    ch = chains.fetch_yfinance("TST")
    assert ch.expiration == E1
    assert ch.listed == [E1, E2, OUTSIDE]


# --- the setting ------------------------------------------------------------
def test_it_ships_off(settings):
    assert settings.expiry_fallback is False


def test_it_is_settable_from_the_command_line():
    import run as cli
    assert "expiry_fallback" in cli._CONFIG_KEYS
    assert cli._coerce("expiry_fallback", "on") is True
    assert cli._coerce("expiry_fallback", "off") is False


# --- sizing -----------------------------------------------------------------
def test_off_a_thin_nearest_expiration_costs_the_name(settings, live_session, served):
    served.first = _thin(E1, [E1, E2])
    served.by_exp = {E2: _good(E2, [E1, E2])}
    sc = _size(settings, live_session)
    assert sc.spreads == [] and sc.fell_back_from is None
    assert served.calls == [None]


def test_on_a_thin_nearest_expiration_is_replaced_by_the_next_one(settings, live_session,
                                                                  served):
    settings.expiry_fallback = True
    served.first = _thin(E1, [E1, E2])
    served.by_exp = {E2: _good(E2, [E1, E2])}
    sc = _size(settings, live_session)
    assert sc.spreads
    assert {s.expiration for s in sc.spreads} == {E2}
    assert sc.fell_back_from == E1
    assert served.calls == [None, E2]


def test_a_name_that_already_has_a_spread_is_not_looked_at_again(settings, live_session,
                                                                served):
    settings.expiry_fallback = True
    served.first = _good(E1, [E1, E2])
    sc = _size(settings, live_session)
    assert sc.spreads and sc.fell_back_from is None
    assert served.calls == [None]


def test_an_expiration_the_caller_named_is_not_second_guessed(settings, live_session,
                                                              served):
    settings.expiry_fallback = True
    served.by_exp = {E1: _thin(E1, [E1, E2])}
    sc = _size(settings, live_session, expiration=E1)
    assert sc.spreads == [] and sc.fell_back_from is None
    assert served.calls == [E1]


def test_a_chain_that_failed_to_load_is_a_data_fault_not_a_reason_to_look_elsewhere(
        settings, live_session, served):
    settings.expiry_fallback = True
    served.first = _failed(E1)
    sc = _size(settings, live_session)
    assert sc.spreads == [] and sc.chain_error == "boom"
    assert served.calls == [None]


def test_when_every_alternate_is_also_thin_the_name_is_skipped_and_says_what_was_tried(
        settings, live_session, served, tmp_path):
    settings.expiry_fallback = True
    listed = [E1, E2, E3]
    served.first = _thin(E1, listed)
    served.by_exp = {E2: _thin(E2, listed), E3: _thin(E3, listed)}
    sc = _size(settings, live_session)
    assert sc.spreads == [] and sc.fell_back_from is None
    assert sc.also_tried == [E2, E3]
    assert served.calls == [None, E2, E3]

    res = pipeline.ScreenResult(live_session, None, [], {}, {})
    led = Ledger.load(settings, path=tmp_path / "ledger.json")
    _, skipped = pipeline.build_proposals([sc], res, led, settings)
    assert len(skipped) == 1
    assert skipped[0].startswith("TST: no width/strike combination qualified")
    assert f"(also tried {E2}, {E3})" in skipped[0]


def test_it_goes_on_to_the_next_alternate_when_the_first_has_no_chain(settings, live_session,
                                                                      served):
    settings.expiry_fallback = True
    listed = [E1, E2, E3]
    served.first = _thin(E1, listed)
    served.by_exp = {E2: _failed(E2), E3: _good(E3, listed)}
    sc = _size(settings, live_session)
    assert {s.expiration for s in sc.spreads} == {E3}
    assert sc.fell_back_from == E1
    assert sc.also_tried == [E2]


def test_it_stops_at_the_first_alternate_that_works(settings, live_session, served):
    settings.expiry_fallback = True
    listed = [E1, E2, E3]
    served.first = _thin(E1, listed)
    served.by_exp = {E2: _good(E2, listed), E3: _good(E3, listed)}
    _size(settings, live_session)
    assert served.calls == [None, E2]


def test_only_expirations_inside_the_window_are_ever_asked_for(settings, live_session,
                                                               served):
    settings.expiry_fallback = True
    served.first = _thin(E1, [E1, OUTSIDE])
    served.by_exp = {OUTSIDE: _good(OUTSIDE, [E1, OUTSIDE])}
    sc = _size(settings, live_session)
    assert sc.spreads == []
    assert served.calls == [None]


def test_the_run_log_names_each_name_that_moved(settings, live_session, served):
    settings.expiry_fallback = True
    served.first = _thin(E1, [E1, E2])
    served.by_exp = {E2: _good(E2, [E1, E2])}
    moved = _size(settings, live_session)
    unmoved = pipeline.SizedCandidate(_candidate("ZZZ"), [], {})
    assert pipeline.fallback_lines([moved, unmoved]) == [f"TST {E2} ({E1} had no spread)"]
