"""The "why has nothing opened?" diagnostic.

The value of this command is entirely in ordering: it must name the FIRST gate
that binds, because a later one is moot. If the master switch is off it does
not matter that the book has room. These tests pin that order, and pin that a
laptop with no systemd never reports a missing timer as a fault.
"""
import datetime as dt

import pytest

from pcs import doctor
from pcs.ledger import Ledger, Position
from pcs.session import SessionState


@pytest.fixture
def led(settings, tmp_path):
    return Ledger.load(settings, path=tmp_path / "ledger.json")


@pytest.fixture
def open_market():
    return SessionState(dt.datetime(2026, 9, 1, 11, 0), True, "open", "live", "open")


@pytest.fixture
def clear(settings):
    """Settings that block nothing, so each test turns exactly one thing off."""
    settings.auto_approve = True
    return settings


def _pos(pid, symbol="TST", sector="Energy", collateral=500.0, width=5.0):
    return Position(id=pid, symbol=symbol, sector=sector, expiration="2026-10-02",
                    short_strike=95.0, long_strike=90.0, width=width, contracts=1,
                    credit_open=0.6, credit_dollars=60.0, collateral=collateral,
                    opened_at="2026-09-01T11:00:00", opened_spot=100.0)


def _blocks(checks):
    return [c.label for c in checks if c.state == doctor.BLOCK]


def _first(checks):
    return next((c.label for c in checks if c.state == doctor.BLOCK), None)


# --- nothing wrong ---------------------------------------------------------
def test_a_clean_account_blocks_on_nothing(led, clear, open_market):
    checks = doctor.diagnose(led, clear, open_market)
    assert _blocks(checks) == []
    assert "Nothing is blocking" in doctor.verdict(checks)


def test_the_verdict_does_not_claim_a_trade_will_happen(led, clear, open_market):
    """No blocker is not the same as a candidate existing. Saying "it will
    trade" and then not trading is how a dashboard loses its reader."""
    v = doctor.verdict(doctor.diagnose(led, clear, open_market))
    assert "found no name that cleared" in v


# --- each gate, alone ------------------------------------------------------
def test_the_master_switch_is_reported_first(led, clear, open_market):
    clear.paper_trading = False
    checks = doctor.diagnose(led, clear, open_market)
    assert _first(checks) == "master switch"
    assert "config --set paper_trading=on" in doctor.verdict(checks)


def test_a_live_ledger_is_reported_as_a_block_with_no_setting_to_lift_it(
        led, clear, open_market):
    led.mode = "live"
    checks = doctor.diagnose(led, clear, open_market)
    assert "ledger mode" in _blocks(checks)
    fix = next(c.fix for c in checks if c.label == "ledger mode")
    assert "no setting" in fix


def test_required_approval_is_a_block_and_names_the_command(led, settings, open_market):
    settings.auto_approve = False
    checks = doctor.diagnose(led, settings, open_market)
    assert "approval" in _blocks(checks)
    assert "run.py approve" in next(c.fix for c in checks if c.label == "approval")


def test_a_shut_market_blocks(led, clear):
    shut = SessionState(dt.datetime(2026, 9, 1, 22, 0), True, "closed", "stale", "closed")
    checks = doctor.diagnose(led, clear, shut)
    assert "market hours" in _blocks(checks)


def test_a_non_trading_day_blocks(led, clear):
    sat = SessionState(dt.datetime(2026, 9, 5, 11, 0), False, "closed", "stale", "closed")
    assert "trading day" in _blocks(doctor.diagnose(led, clear, sat))


def test_the_opening_range_blocks_and_says_when_it_lifts(led, clear):
    early = SessionState(dt.datetime(2026, 9, 1, 9, 40), True, "opening_range", "live",
                         "opening range", settle_until=dt.datetime(2026, 9, 1, 10, 0))
    checks = doctor.diagnose(led, clear, early)
    assert "opening range" in _blocks(checks)
    assert "10:00" in next(c.fix for c in checks if c.label == "opening range")


def test_a_full_book_blocks(led, clear, open_market):
    clear.max_open_positions = 2
    led.positions = [_pos("a"), _pos("b", symbol="TS2")]
    assert "position count" in _blocks(doctor.diagnose(led, clear, open_market))


def test_the_collateral_cap_blocks(led, clear, open_market):
    clear.max_total_collateral = 400.0
    led.positions = [_pos("a", collateral=500.0)]
    assert "collateral cap" in _blocks(doctor.diagnose(led, clear, open_market))


def test_an_exhausted_balance_blocks(led, clear, open_market):
    """The floor under every other cap: an account cannot commit more max loss
    than it has free cash to pay."""
    led.cash = 300.0
    led.positions = [_pos("a", width=5.0)]        # $500 capital at risk
    checks = doctor.diagnose(led, clear, open_market)
    assert "available balance" in _blocks(checks)


# --- ordering --------------------------------------------------------------
def test_the_switch_outranks_every_downstream_gate(led, clear, open_market):
    """With the master switch off it does not matter that the book is also
    full -- reporting the full book first would send someone to close a
    position that would not have helped."""
    clear.paper_trading = False
    clear.max_open_positions = 1
    led.positions = [_pos("a")]
    assert _first(doctor.diagnose(led, clear, open_market)) == "master switch"


def test_the_clock_outranks_the_caps(led, clear):
    shut = SessionState(dt.datetime(2026, 9, 1, 22, 0), True, "closed", "stale", "closed")
    clear.max_open_positions = 1
    led.positions = [_pos("a")]
    assert _first(doctor.diagnose(led, clear, shut)) == "market hours"


# --- warnings are not blocks ----------------------------------------------
def test_a_sector_at_its_cap_warns_but_does_not_block(led, clear, open_market):
    """Other sectors are still open, so this is not a reason nothing traded."""
    clear.max_positions_per_sector = 1
    led.positions = [_pos("a", sector="Energy")]
    checks = doctor.diagnose(led, clear, open_market)
    assert _blocks(checks) == []
    assert any(c.label == "sector caps" and c.state == doctor.WARN for c in checks)


def test_a_benched_symbol_warns_but_does_not_block(led, clear, open_market):
    from pcs import learning
    j = learning.Journal(quarantines=[learning.Quarantine(
        symbol="BAD", until="2099-01-01", reason="x", faults=3, since="2026-09-01")])
    checks = doctor.diagnose(led, clear, open_market, journal=j)
    assert _blocks(checks) == []
    assert any("BAD" in c.detail for c in checks)


# --- environment -----------------------------------------------------------
def test_no_systemd_is_not_a_fault(led, clear, open_market, monkeypatch):
    """Run on a laptop, this must not report every timer as missing."""
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    checks = doctor.diagnose(led, clear, open_market)
    assert not any(".service" in c.label for c in checks)


def test_a_failed_unit_is_a_block_naming_the_journalctl_line(
        led, clear, open_market, monkeypatch):
    monkeypatch.setattr(doctor, "_svc",
                        lambda u: (("failed", "Tue 2026-09-01 10:15:00 EDT")
                                   if "propose" in u else ("", "")))
    checks = doctor.diagnose(led, clear, open_market)
    unit = next(c for c in checks if c.label == "pcs-propose.service")
    assert unit.state == doctor.BLOCK
    assert "journalctl -u pcs-propose.service" in unit.fix


def test_an_unreadable_proposals_file_does_not_crash(led, clear, open_market,
                                                     tmp_path, monkeypatch):
    bad = tmp_path / "proposals.json"
    bad.write_text("{ this is not json")
    monkeypatch.setattr(doctor, "PROPOSALS_JSON", bad)
    assert doctor.diagnose(led, clear, open_market)      # no exception
