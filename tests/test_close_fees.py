"""What a close costs must not depend on who closed it.

Buying a vertical back is two legs, exactly like selling it, and the opening
side already charged both legs plus commission. The two closing paths each
made up their own number: `apply_exits` charged ONE leg's regulatory fee and
no commission, and `close` re-charged whatever the opening had cost. So the
same spread closed at the same debit booked a different P&L depending on
whether a stop or a human took it -- and with a commission set, the automated
path understated every exit by most of a round trip.

Both now ask `Settings.spread_fees`, the one place the optimizer's fee comes
from too.
"""
import argparse

import pytest
from conftest import make_chain

from pcs import exits
from pcs import ledger as ledger_mod
from pcs.ledger import Ledger
from pcs.optimizer import build_spreads
from pcs.paper_broker import apply_exits, open_approved

DEBIT = 2.40


@pytest.fixture
def fees_on(settings):
    """A commission, so a path that drops it shows."""
    settings.starting_cash = 3000.0
    settings.paper_trading = True
    settings.commission_per_contract = 0.65
    return settings


def _open(led, settings, sess, contracts=2):
    sp = build_spreads(make_chain(spot=100.0), 100.0, settings, sess)[0][0]
    return open_approved(led, sp, "Energy", contracts, settings, "P1", "human", sess=sess)


def _auto_close(fees_on, tmp_path, live_session):
    led = Ledger.load(fees_on, path=tmp_path / "auto.json")
    pos = _open(led, fees_on, live_session)
    opened = pos.fees_paid
    pos.mark_cost_to_close = DEBIT
    assert exits.decide(pos, fees_on).action == exits.STOP_LOSS
    apply_exits(led, fees_on, fresh={pos.id}, sess=live_session)
    assert pos.status == "closed" and pos.close_debit == DEBIT
    return opened, round(pos.fees_paid - opened, 2), pos.realized_pl


def _manual_close(fees_on, tmp_path, live_session, monkeypatch):
    import run as cli
    from pcs import chains, dashboard, proposer

    path = tmp_path / "manual.json"
    monkeypatch.setattr(ledger_mod, "LEDGER_JSON", path)
    monkeypatch.setattr(chains, "get_chain", lambda *a, **k: None)
    monkeypatch.setattr(dashboard, "render", lambda *a, **k: None)
    monkeypatch.setattr(proposer, "load", lambda path=None: [])

    led = Ledger.load(fees_on, path=path)
    pos = _open(led, fees_on, live_session)
    opened = pos.fees_paid
    led.save()

    args = argparse.Namespace(position_id=pos.id, reason="manual close", debit=DEBIT)
    assert cli.cmd_close(args, fees_on) == 0
    after = Ledger.load(fees_on, path=path).by_id(pos.id)
    return opened, round(after.fees_paid - opened, 2), after.realized_pl


def test_an_automated_exit_pays_for_both_legs(fees_on, tmp_path, live_session):
    opened, closing, _ = _auto_close(fees_on, tmp_path, live_session)
    assert closing == fees_on.spread_fees(2) == opened


def test_a_manual_close_pays_for_both_legs(fees_on, tmp_path, live_session, monkeypatch):
    opened, closing, _ = _manual_close(fees_on, tmp_path, live_session, monkeypatch)
    assert closing == fees_on.spread_fees(2) == opened


def test_the_same_close_books_the_same_result_either_way(fees_on, tmp_path,
                                                         live_session, monkeypatch):
    *_, auto_pl = _auto_close(fees_on, tmp_path, live_session)
    *_, manual_pl = _manual_close(fees_on, tmp_path, live_session, monkeypatch)
    assert auto_pl == manual_pl


def test_the_closing_fee_follows_the_settings_not_the_fill(fees_on, tmp_path,
                                                          live_session, monkeypatch):
    """`close` used to re-charge `fees_paid`, so a commission change after the
    fill was ignored on the way out: the buy-back was priced at the rate in
    force on the day it was SOLD."""
    import run as cli
    from pcs import chains, dashboard, proposer

    path = tmp_path / "manual.json"
    monkeypatch.setattr(ledger_mod, "LEDGER_JSON", path)
    monkeypatch.setattr(chains, "get_chain", lambda *a, **k: None)
    monkeypatch.setattr(dashboard, "render", lambda *a, **k: None)
    monkeypatch.setattr(proposer, "load", lambda path=None: [])

    led = Ledger.load(fees_on, path=path)
    pos = _open(led, fees_on, live_session)
    opened = pos.fees_paid
    led.save()

    fees_on.commission_per_contract = 0.0
    args = argparse.Namespace(position_id=pos.id, reason="manual close", debit=DEBIT)
    assert cli.cmd_close(args, fees_on) == 0
    after = Ledger.load(fees_on, path=path).by_id(pos.id)
    assert round(after.fees_paid - opened, 2) == fees_on.spread_fees(2)
