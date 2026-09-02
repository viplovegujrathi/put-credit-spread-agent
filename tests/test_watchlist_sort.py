"""Sorting the watchlist.

The page is a static file behind nginx, so there is no server to ask for a
different ordering -- and no need for one, because the keys that produced each
cell are already known at render time. These tests are mostly about the keys
being EMITTED rather than parsed back out of "$849" and "17.8%", and about the
rows that have nothing to rank on.
"""
import re

import pytest

from pcs import dashboard, watchlist

HEADERS = ["ticker", "signal", "spread", "collateral", "premium",
           "return", "cushion", "earnings", "off high"]


def _entry(symbol, signal="READY", **kw):
    base = {"symbol": symbol, "name": symbol, "sector": "Information Technology",
            "signal": signal, "reason": "", "bucket": "primary", "spot": 100.0,
            "dma50": 103.0, "pct_from_dma50": -0.03, "pct_off_high": -0.25}
    base.update(kw)
    return watchlist.Entry(**base)


def _priced(symbol, credit, collateral, roc, cushion, pop, signal="READY", off=-0.25):
    return _entry(symbol, signal, expiration="2099-01-15", dte=31,
                  short_strike=100.0, long_strike=95.0, width=5.0,
                  credit_dollars=credit, credit_nat_dollars=credit * 0.8,
                  collateral=collateral, roc=roc, cushion=cushion, pop_est=pop,
                  pct_off_high=off, earnings_date="2099-03-01")


@pytest.fixture
def doc(monkeypatch):
    wl = watchlist.Watchlist(
        generated_at="2026-09-01T19:00:00", quote_quality="closing_snapshot",
        phase="closed", tradeable=False,
        entries=[_priced("AAA", 322, 678, 0.475, 0.032, 0.66, off=-0.27),
                 _priced("BBB", 154, 346, 0.444, 0.049, 0.66, "BLOCKED", off=-0.36),
                 _priced("CCC", 249, 751, 0.331, 0.040, 0.65, off=-0.18),
                 _entry("ZZZ", "NO_FIT")])          # no spread: nothing to rank
    monkeypatch.setattr(watchlist, "load", lambda *a, **k: wl)
    return dashboard._watchlist_panel()


def _cells(doc, label):
    """Every (key-or-None) for one column, in document order."""
    out = []
    for td in re.findall(rf'<td data-l="{label}"[^>]*>', doc):
        m = re.search(r'data-s="([^"]*)"', td)
        out.append(m.group(1) if m else None)
    return out


def test_the_key_is_emitted_not_parsed_back_out_of_the_cell(doc):
    """"$678", "47.5%" and "2099-01-15" are three different parses and one of
    them is wrong. The value that produced the text is already in hand."""
    assert _cells(doc, "collateral")[:3] == ["678", "346", "751"]
    assert _cells(doc, "premium")[:3] == ["322", "154", "249"]
    assert "$" not in "".join(x or "" for x in _cells(doc, "collateral"))


def test_a_name_with_no_spread_carries_no_key(doc):
    """It has no premium to be ranked on. Without a key the row sinks either
    way round, so reversing never buries the rows being read."""
    assert _cells(doc, "premium")[-1] is None
    assert _cells(doc, "collateral")[-1] is None
    assert _cells(doc, "ticker")[-1] == "ZZZ"      # still sortable by name


def test_columns_declare_their_own_type(doc):
    types = dict(re.findall(r'data-i="(\d+)" data-t="([ns])"', doc))
    assert types["0"] == "s"          # ticker
    assert types["4"] == "n"          # premium
    assert types["7"] == "s"          # earnings -- ISO dates sort as text


def test_the_first_click_shows_the_useful_end_first(doc):
    """Biggest premium, widest cushion, best estimated win. Cheapest collateral
    and best signal read forwards."""
    dirs = {name: d for d, name in
            re.findall(r'data-d="(-?1)"[^>]*title="Sort by ([a-z ]+)"', doc)}
    assert dirs["premium"] == "-1" and dirs["cushion"] == "-1"
    assert dirs["collateral"] == "1" and dirs["signal"] == "1"


def test_signal_sorts_by_urgency_not_by_alphabet(doc):
    """READY before BLOCKED before NO FIT. Alphabetically that is backwards."""
    keys = [int(k) for k in _cells(doc, "signal")]
    assert keys[0] < keys[1] < keys[3]        # READY < BLOCKED < NO_FIT


def test_there_is_a_control_that_works_where_the_header_is_hidden(doc):
    """`thead{display:none}` at phone widths -- a header that is not on screen
    cannot be clicked, so the select is not a nicety."""
    assert 'id="sort-watch"' in doc and 'class="sortdir"' in doc
    for h in HEADERS:
        assert f">{h}</option>" in doc


def test_a_table_without_keys_is_left_alone():
    """Only the watchlist asked for this. Every other table keeps its plain
    markup rather than growing attributes nothing reads."""
    plain = dashboard._table(["a", "b"], [["1", "2"]])
    assert "sortable" not in plain and "data-s=" not in plain
    assert "sortbar" not in plain and "<th>a</th>" in plain
