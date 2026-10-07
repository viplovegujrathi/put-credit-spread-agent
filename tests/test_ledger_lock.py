"""Two timers write one ledger, and the later save used to win.

`propose` fires at 10:15 ET and `mark` at 10:05, 10:20, 10:35... `propose`
loaded the ledger after its screen, then spent minutes pulling option chains
and earnings dates before it opened anything and saved. The 10:20 mark lands
inside that window. A stop taken there was written to disk, and then `propose`
saved the copy it had loaded before the stop: the position back open, the debit
back in cash, the close event gone -- while the journal, written by the mark,
had already booked the loss. Nothing raised.

`ledger.locked` is the fix: every writer loads inside an exclusive lock and
saves before releasing it, and `save` is atomic so the unlocked readers
(`status`, `watch`, the dashboard) never see half a file.
"""
import argparse
import pathlib
import threading
import types

import pytest
from conftest import make_chain

from pcs import ledger as ledger_mod
from pcs.ledger import Ledger, LedgerBusy, Position, locked
from pcs.optimizer import build_spreads
from pcs.proposer import Proposal


def an_open(symbol="AAA", pid="aaa") -> Position:
    return Position(
        id=pid, symbol=symbol, sector="Energy", expiration="2026-10-30",
        short_strike=95.0, long_strike=90.0, width=5.0, contracts=1,
        credit_open=1.16, credit_dollars=115.88, collateral=384.12,
        opened_at="2026-09-01T14:39:25", opened_spot=100.0, issuer=symbol)


# --- the lock itself ---------------------------------------------------------
def test_a_second_writer_waits_and_then_gives_up_loudly(settings, tmp_path):
    path = tmp_path / "ledger.json"
    held, release = threading.Event(), threading.Event()

    def first():
        with locked(settings, path):
            held.set()
            release.wait(5)

    t = threading.Thread(target=first)
    t.start()
    try:
        assert held.wait(5)
        with pytest.raises(LedgerBusy), locked(settings, path, wait=0.3):
            pass
    finally:
        release.set()
        t.join()
    # Released: the next writer gets straight in.
    with locked(settings, path, wait=0.3) as led:
        assert isinstance(led, Ledger)


def test_the_lock_reads_the_file_as_it_is_now(settings, tmp_path):
    """Loading inside the lock is the whole point -- a copy from earlier is
    the lost update with extra steps."""
    path = tmp_path / "ledger.json"
    led = Ledger.load(settings, path=path)
    led.positions = [an_open()]
    led.save()
    with locked(settings, path) as fresh:
        assert [p.id for p in fresh.open_positions] == ["aaa"]


def test_a_failed_save_leaves_the_previous_ledger_whole(settings, tmp_path, monkeypatch):
    """A save that dies part-way -- disk full, the unit killed at its timeout
    -- used to leave a truncated ledger.json, and every command after it
    failed to parse the one file that holds the account."""
    path = tmp_path / "ledger.json"
    led = Ledger.load(settings, path=path)
    led.positions = [an_open()]
    led.save()
    before = path.read_text()

    real = pathlib.Path.write_text

    def dies_halfway(self, data, *a, **kw):
        real(self, data[:20], *a, **kw)
        raise OSError("No space left on device")

    monkeypatch.setattr(pathlib.Path, "write_text", dies_halfway)
    led.cash -= 100
    with pytest.raises(OSError):
        led.save()
    monkeypatch.undo()

    assert path.read_text() == before
    assert [p.id for p in Ledger.load(settings, path=path).open_positions] == ["aaa"]


# --- the race that actually happens --------------------------------------
def test_a_stop_taken_while_propose_is_sizing_survives_propose(
        settings, tmp_path, monkeypatch, live_session):
    """Reconstructs 10:15-10:22: propose has loaded the ledger, the mark timer
    closes a position while propose is pulling chains, then propose opens a
    new spread and saves. Both the close and the open must be on disk."""
    import run as cli
    from pcs import dashboard, health, learning, pipeline, proposer

    path = tmp_path / "ledger.json"
    monkeypatch.setattr(ledger_mod, "LEDGER_JSON", path)
    monkeypatch.setattr(health, "HEALTH_JSON", tmp_path / "health.json")
    monkeypatch.setattr(learning, "load", lambda path=None: learning.Journal())
    monkeypatch.setattr(learning, "save", lambda journal, path=None: None)
    monkeypatch.setattr(proposer, "save", lambda props, path=None: tmp_path / "p.json")
    monkeypatch.setattr(dashboard, "render", lambda *a, **k: None)
    settings.starting_cash = 3000.0
    settings.auto_approve = True

    seed = Ledger.load(settings, path=path)
    seed.positions = [an_open()]
    seed.cash = 3115.88
    seed.save()

    sp = build_spreads(make_chain(symbol="NEW", spot=100.0), 100.0,
                       settings, live_session)[0][0]
    prop = Proposal(id="P1", created_at="", symbol="NEW", name="New Co",
                    sector="Utilities", bucket="primary", spread=sp.as_dict(),
                    contracts=1, rationale="", risk_ok=True)

    def mark_fires_meanwhile(*a, **k):
        # What `mark` does at 10:20, in its own process.
        other = Ledger.load(settings, path=path)
        other.close_position(other.by_id("aaa"), 2.40, "stop_loss: test",
                             fees=0.12, action="stop_loss")
        other.save()
        return []

    class Res:
        session = live_session
        warnings: list = []

    monkeypatch.setattr(pipeline, "run_screen", lambda *a, **k: Res())
    monkeypatch.setattr(pipeline, "shortlist",
                        lambda *a, **k: [types.SimpleNamespace(symbol="NEW")])
    monkeypatch.setattr(pipeline, "resolve_batch_expiration", lambda *a, **k: sp.expiration)
    monkeypatch.setattr(pipeline, "size_candidates", mark_fires_meanwhile)
    monkeypatch.setattr(pipeline, "apply_earnings", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "build_proposals", lambda *a, **k: ([prop], []))
    monkeypatch.setattr(learning, "blocked_symbols", lambda *a, **k: set())

    args = argparse.Namespace(symbols=None, max_cache_age=60, include_tight=False,
                              expiration=None, contracts=1,
                              allow_unknown_earnings=False, no_auto_open=False)
    assert cli.cmd_propose(args, settings) == 0

    after = Ledger.load(settings, path=path)
    old = after.by_id("aaa")
    assert old.status == "closed", "propose saved over the stop the mark had taken"
    assert old.close_action == "stop_loss"
    assert [p.symbol for p in after.open_positions] == ["NEW"]
    assert any(e["kind"] == "position_closed" for e in after.events)
