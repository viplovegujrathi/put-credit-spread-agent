"""GOOG and GOOGL are one company, and no cap could see it.

Both were open 2026-09-09 to 2026-09-14 -- $703.74 of a $2,400 collateral
budget on Alphabet, a fifth of the account's risk in one name, one earnings
date and one gap. Two caps existed to stop exactly that and neither did:
`max_positions_per_ticker` counts symbols, and `max_positions_per_sector`
counts GICS labels, of which there are 23 and none of them separates a company
from itself.

The cap was not wrong about what it wanted. Its own documentation reads
"positions in one NAME, not contracts" -- the same shape as every other defect
this record has turned up: a rule stated correctly in one place and measured
wrongly in another. So the unit changed rather than the number, and the count
is now per issuer. The pair happened to win. The exposure was single-name and
invisible.
"""
from dataclasses import dataclass

import pandas as pd
import pytest
from conftest import make_chain

from pcs import risk, watchlist
from pcs.config import SP500_CSV
from pcs.ledger import Ledger, Position
from pcs.optimizer import build_spreads
from pcs.paper_broker import Concentrated, open_approved
from pcs.screener import PRIMARY, Candidate
from pcs.universe import issuer_key

ALPHA_A = "Alphabet Inc. (Class A)"
ALPHA_C = "Alphabet Inc. (Class C)"


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 10_000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def a_spread(settings, live_session, symbol="GOOGL"):
    return build_spreads(make_chain(symbol=symbol, spot=100.0), 100.0,
                         settings, live_session)[0][0]


# --- what counts as one company -------------------------------------------
def test_the_two_alphabet_classes_are_one_key():
    assert issuer_key(ALPHA_A, "GOOGL") == issuer_key(ALPHA_C, "GOOG")


def test_the_index_merges_exactly_the_three_dual_class_pairs():
    """A false merge costs a trade the account was entitled to take, so this
    runs the derivation over the real constituent table rather than over three
    strings chosen to make it pass. 503 rows, 500 companies."""
    frame = pd.read_csv(SP500_CSV)
    groups: dict[str, list[str]] = {}
    for _, row in frame.iterrows():
        groups.setdefault(issuer_key(row["name"], row["symbol"]), []).append(row["symbol"])
    merged = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    assert sorted(merged.values()) == [["FOX", "FOXA"], ["GOOG", "GOOGL"], ["NWS", "NWSA"]]


def test_an_unknown_name_is_only_ever_itself():
    """`screener` writes "Unknown" when the universe lookup misses. Two misses
    are two different companies; merging them would refuse a real trade on the
    strength of a lookup failure."""
    assert issuer_key("Unknown", "RDDT") != issuer_key("Unknown", "STX")
    assert issuer_key("", "XYZ") == issuer_key("Unknown", "XYZ") == "XYZ"


def test_the_symbol_table_stands_in_when_the_name_is_gone():
    """The name derivation is the self-maintaining half -- a new dual-class
    listing is caught without anyone editing a list. The table is for rows that
    reach the cap with no usable name at all."""
    assert issuer_key("", "GOOG") == issuer_key("", "GOOGL")
    assert issuer_key("Unknown", "FOX") == issuer_key("Unknown", "FOXA")


# --- the ledger counts companies ------------------------------------------
def test_two_share_classes_count_once(settings, led, live_session):
    for sym, name in (("GOOGL", ALPHA_A), ("GOOG", ALPHA_C)):
        settings.max_positions_per_ticker = 5
        open_approved(led, a_spread(settings, live_session, sym), "Communication Services",
                      1, settings, f"P-{sym}", "human", sess=live_session, name=name)
    assert led.issuer_counts() == {"ALPHABET": 2}


def test_a_row_that_predates_the_field_falls_back_to_its_symbol(settings, led):
    """Every position already in the live ledger has an empty `issuer`. They
    have to keep counting as themselves -- an empty key shared by all of them
    would read as one enormous position in a company called nothing."""
    led.positions = [
        Position(id="a", symbol="BA", sector="Industrials", expiration="2026-10-02",
                 short_strike=95.0, long_strike=90.0, width=5.0, contracts=1,
                 credit_open=1.16, credit_dollars=115.88, collateral=384.12,
                 opened_at="2026-09-01T14:39:25", opened_spot=100.0),
        Position(id="b", symbol="CLX", sector="Consumer Staples", expiration="2026-10-02",
                 short_strike=95.0, long_strike=90.0, width=5.0, contracts=1,
                 credit_open=1.16, credit_dollars=115.88, collateral=384.12,
                 opened_at="2026-09-01T14:39:25", opened_spot=100.0)]
    assert led.issuer_counts() == {"BA": 1, "CLX": 1}


# --- the gate at proposal time --------------------------------------------
def test_the_second_class_is_refused_at_the_cap(settings, live_session):
    settings.max_positions_per_ticker = 1
    sp = a_spread(settings, live_session, "GOOG")
    pv = risk.PortfolioView(0, 1, {}, {"ALPHABET": 1}, 10_000, 10_000)
    v = risk.check(sp, "Communication Services", pv, settings,
                   issuer=issuer_key(ALPHA_C, "GOOG"))
    assert not v.ok
    assert any("ticker concentration" in r for r in v.reasons)


def test_an_unrelated_name_is_not_caught_by_it(settings, live_session):
    settings.max_positions_per_ticker = 1
    sp = a_spread(settings, live_session, "BA")
    pv = risk.PortfolioView(0, 1, {}, {"ALPHABET": 1}, 10_000, 10_000)
    assert risk.check(sp, "Industrials", pv, settings, issuer=issuer_key("Boeing", "BA")).ok


def test_one_batch_cannot_open_both_classes(settings, live_session):
    """The pair would arrive in the same screen -- same sector, same drawdown,
    adjacent in the ranking. Evaluated one at a time against a ledger that has
    neither yet, both pass unless `pending` carries the issuer too."""
    settings.max_positions_per_ticker = 1
    sp = a_spread(settings, live_session, "GOOG")
    pending = [risk.Pending("GOOGL", "Communication Services", sp.collateral,
                            issuer_key(ALPHA_A, "GOOGL"))]
    v = risk.check(sp, "Communication Services",
                   risk.PortfolioView(0, 0, {}, {}, 10_000, 10_000), settings,
                   pending, issuer=issuer_key(ALPHA_C, "GOOG"))
    assert not v.ok and any("ticker concentration" in r for r in v.reasons)


# --- and at the fill, which is the one that sees the present --------------
def test_the_fill_path_refuses_the_sibling_class(settings, led, live_session):
    """A ticket carries the verdict it was given when it was written. GOOG can
    be proposed at 13:00 against an empty book and approved at 15:00 after
    GOOGL filled, and proposal-time checking alone would let it through."""
    settings.max_positions_per_ticker = 1
    open_approved(led, a_spread(settings, live_session, "GOOGL"),
                  "Communication Services", 1, settings, "P1", "human",
                  sess=live_session, name=ALPHA_A)
    with pytest.raises(Concentrated) as exc:
        open_approved(led, a_spread(settings, live_session, "GOOG"),
                      "Communication Services", 1, settings, "P2", "human",
                      sess=live_session, name=ALPHA_C)
    assert "max_positions_per_ticker" in str(exc.value)   # says how to change it


def test_the_fill_path_still_allows_a_permitted_ladder(settings, led, live_session):
    """The cap is a number, not a switch. At 2 the account may hold two spreads
    on one company, and refusing the second would be the mirror-image bug."""
    settings.max_positions_per_ticker = 2
    for i, (sym, name) in enumerate((("GOOGL", ALPHA_A), ("GOOG", ALPHA_C))):
        open_approved(led, a_spread(settings, live_session, sym),
                      "Communication Services", 1, settings, f"P{i}", "human",
                      sess=live_session, name=name)
    assert len(led.open_positions) == 2


def test_a_cap_can_never_hold_a_losing_position_open(settings, led, live_session):
    """Every gate added to the opening path has to be checked against the exit
    path. `apply_exits` does not route through `open_approved`, and this pins
    that a full book cannot turn a stop into an unbounded loss."""
    from pcs import exits
    from pcs.paper_broker import apply_exits
    settings.max_positions_per_ticker = 1
    pos = open_approved(led, a_spread(settings, live_session, "GOOGL"),
                        "Communication Services", 1, settings, "P1", "human",
                        sess=live_session, name=ALPHA_A)
    pos.mark_cost_to_close = pos.credit_open * settings.stop_loss_credit_multiple + 0.5
    assert exits.decide(pos, settings).action == exits.STOP_LOSS
    assert [p.id for p, _ in apply_exits(led, settings, fresh={pos.id},
                                         sess=live_session)] == [pos.id]


# --- and the page agrees with the gate ------------------------------------
@dataclass
class FakeResult:
    session: object
    candidates: list


@dataclass
class FakeSized:
    candidate: object
    spreads: list
    rejects: dict
    chain_error: str | None = None


def test_the_watchlist_says_holding_for_the_sibling(settings, led, live_session):
    """A watchlist keyed by symbol would show GOOG as READY while the risk gate
    refuses it -- a page promising a fill that cannot happen."""
    settings.max_positions_per_ticker = 1
    open_approved(led, a_spread(settings, live_session, "GOOGL"),
                  "Communication Services", 1, settings, "P1", "human",
                  sess=live_session, name=ALPHA_A)
    c = Candidate(symbol="GOOG", name=ALPHA_C, sector="Communication Services",
                  bucket=PRIMARY, note="", spot=100.0, dma50=105.0,
                  pct_from_dma50=-0.05, pct_off_high=0.22, high_52w=128.0,
                  low_52w=90.0, avg_vol_30d=2_000_000, last_bar="2026-08-31",
                  earnings_in_window=False)
    wl = watchlist.build(FakeResult(live_session, [c]),
                         [FakeSized(c, [a_spread(settings, live_session, "GOOG")], {})],
                         led, settings)
    assert wl.entries[0].held == 1
    assert wl.entries[0].signal == watchlist.HOLDING
