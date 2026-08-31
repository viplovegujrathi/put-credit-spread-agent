"""Earnings exclusion and the Robinhood ingest normalisation."""

import sys
from pathlib import Path

from pcs.pipeline import _earnings_note
from pcs.screener import PRIMARY, Candidate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from rh_ingest import normalise  # noqa: E402


def a_candidate(**kw) -> Candidate:
    base = {"symbol": "T", "name": "T Inc", "sector": "S", "bucket": PRIMARY, "note": "",
            "spot": 100.0, "dma50": 105.0, "pct_from_dma50": -0.05, "pct_off_high": 0.20,
            "high_52w": 125.0, "low_52w": 80.0, "avg_vol_30d": 1e6, "last_bar": "2026-08-31"}
    base.update(kw)
    return Candidate(**base)


def test_earnings_inside_the_window_blocks():
    c = a_candidate(earnings_date="2026-09-25", earnings_in_window=True)
    blocked, note = _earnings_note(c, "2026-10-02")
    assert blocked and "inside" in note


def test_unknown_earnings_date_is_not_treated_as_a_pass():
    c = a_candidate(earnings_date=None, earnings_in_window=None)
    blocked, note = _earnings_note(c, "2026-10-02")
    assert blocked and "unknown" in note


def test_earnings_after_the_window_clears():
    c = a_candidate(earnings_date="2026-11-05", earnings_in_window=False)
    blocked, note = _earnings_note(c, "2026-10-02")
    assert not blocked and "clear" in note


def test_rh_ingest_joins_instruments_to_quotes():
    raw = {
        "symbol": "MCD", "expiration": "2026-10-02", "spot": 263.54,
        "instruments": [{"id": "abc", "strike_price": "255.0000"},
                        {"id": "def", "strike_price": "250.0000"}],
        "quotes": [
            {"instrument_id": "abc", "bid_price": "2.350000", "ask_price": "3.250000",
             "mark_price": "2.800000", "adjusted_mark_price": "2.800000",
             "implied_volatility": "0.211373", "delta": "-0.269776",
             "open_interest": 257, "volume": 40,
             "chance_of_profit_short": "0.767440",
             "high_fill_rate_sell_price": "2.524000",
             "high_fill_rate_buy_price": "3.075000"},
            {"instrument_id": "def", "bid_price": "1.320000", "ask_price": "2.060000",
             "mark_price": "1.690000", "open_interest": 205, "volume": 19},
        ],
    }
    snap = normalise(raw)
    assert [p["strike"] for p in snap["puts"]] == [250.0, 255.0]
    short = snap["puts"][1]
    assert short["mark"] == 2.80 and short["pop_short"] == 0.76744
    assert short["open_interest"] == 257
    # the mark-based package credit is what the doc's reference table used
    assert round(snap["puts"][1]["mark"] - snap["puts"][0]["mark"], 2) == 1.11


def test_rh_ingest_drops_quotes_with_no_matching_instrument():
    raw = {"symbol": "X", "expiration": "2026-10-02", "spot": 10.0,
           "instruments": [{"id": "a", "strike_price": "9.0"}],
           "quotes": [{"instrument_id": "zzz", "bid_price": "1", "ask_price": "2"}]}
    assert normalise(raw)["puts"] == []
