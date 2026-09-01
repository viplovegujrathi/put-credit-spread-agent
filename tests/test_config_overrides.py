"""The configurable rules (`./run.py config`).

Two things must hold no matter what is configured: a live ledger always needs a
human, and any loosened rule is *reported* rather than quietly replacing the
mandate. Everything else here is ordinary plumbing.
"""
from dataclasses import asdict
from pathlib import Path

import pytest
from conftest import make_chain

ROOT = Path(__file__).resolve().parent.parent

from pcs import learning, paper_broker
from pcs.config import STRATEGY, Settings
from pcs.exits import TAKE_PROFIT
from pcs.ledger import Ledger
from pcs.optimizer import build_spreads
from pcs.proposer import Proposal


@pytest.fixture
def led(settings, tmp_path):
    settings.starting_cash = 3000.0
    return Ledger.load(settings, path=tmp_path / "ledger.json")


def _load_cli():
    """run.py is a script, not a package -- import it by path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pcs_cli", ROOT / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pending_proposal(symbol="TST"):
    import datetime as dt

    from pcs.config import Settings as S
    from pcs.session import SessionState
    sess = SessionState(dt.datetime(2026, 8, 31, 11, 0), True, "open", "live", "open")
    sp = build_spreads(make_chain(symbol=symbol, spot=100.0), 100.0, S(), sess)[0][0]
    return Proposal(id="P-TEST", created_at="2026-08-31T11:00:00", symbol=symbol,
                    name=symbol, sector="Energy", bucket="PRIMARY", contracts=1,
                    spread=asdict(sp), rationale="test", earnings_note="none",
                    risk_ok=True, risk_reasons=[], risk_warnings=[], status="pending")


@pytest.fixture
def spread(settings, live_session):
    return build_spreads(make_chain(spot=100.0), 100.0, settings, live_session)[0][0]


# --- resolution ------------------------------------------------------------
def test_an_untouched_account_runs_the_skills_numbers(settings):
    assert settings.strategy() is STRATEGY
    assert settings.deviations() == []


@pytest.mark.parametrize("field,value", [
    ("max_collateral_per_trade", 750.0),
    ("min_credit_per_trade", 150.0),
    ("max_credit_per_trade", 600.0),
    ("min_otm_cushion", 0.05),
    ("take_profit_pct", 0.40),
])
def test_each_override_reaches_the_resolved_strategy_and_is_reported(settings, field, value):
    setattr(settings, field, value)
    assert getattr(settings.strategy(), field) == value
    assert any(field.replace("_", " ") in d for d in settings.deviations())


def test_overriding_does_not_mutate_the_shared_baseline(settings):
    settings.max_collateral_per_trade = 500.0
    assert settings.strategy().max_collateral_per_trade == 500.0
    assert STRATEGY.max_collateral_per_trade == 1000.0
    assert Settings().strategy().max_collateral_per_trade == 1000.0


def test_max_loss_and_max_collateral_are_the_same_cap_and_the_tighter_one_binds(settings):
    """For a credit spread max loss IS the collateral. Setting both must not
    let the looser number win."""
    settings.max_collateral_per_trade = 900.0
    settings.max_loss_per_trade = 400.0
    assert settings.strategy().max_collateral_per_trade == 400.0

    settings.max_loss_per_trade = 950.0          # looser than the collateral cap
    assert settings.strategy().max_collateral_per_trade == 900.0


def test_an_override_can_only_be_read_through_deviations(settings):
    """Consumers see a plain frozen Strategy -- they must not need to know."""
    settings.min_credit_per_trade = 150.0
    eff = settings.strategy()
    assert type(eff) is type(STRATEGY)
    assert eff.min_credit_per_trade == 150.0
    assert len(settings.deviations()) == 1


# --- the approval lock -----------------------------------------------------
def test_approval_is_required_by_default(settings):
    assert settings.require_approval()


def test_auto_approve_lifts_it_for_paper(settings):
    settings.auto_approve = True
    assert not settings.require_approval()
    assert "human approval" in " ".join(settings.deviations())


@pytest.mark.parametrize("mode", ["live", "LIVE", "real", ""])
def test_no_setting_can_lift_approval_off_a_non_paper_ledger(settings, mode):
    """The lock the whole design rests on."""
    settings.auto_approve = True
    settings.mode = mode
    assert settings.require_approval() is True


def test_the_auto_approver_is_never_a_persons_name(settings):
    who = settings.auto_approver()
    assert "agent" in who and "auto" in who
    assert "paper" in who


def test_auto_approve_does_not_reach_the_live_adapter(settings, led, spread, live_session):
    """require_approval() is policy; open_approved is the wall. Both hold."""
    settings.auto_approve = True
    led.mode = "live"
    with pytest.raises(paper_broker.ApprovalRequired):
        paper_broker.open_approved(led, spread, "Energy", 1, settings,
                                   proposal_id="P1", approved_by=settings.auto_approver(),
                                   sess=live_session)
    assert not led.open_positions


# --- the master switch -----------------------------------------------------
def test_paper_trading_off_refuses_every_fill(settings, led, spread, live_session):
    settings.paper_trading = False
    with pytest.raises(paper_broker.TradingDisabled):
        paper_broker.open_approved(led, spread, "Energy", 1, settings,
                                   proposal_id="P1", approved_by="a human",
                                   sess=live_session)
    assert not led.open_positions
    assert "paper trading" in " ".join(settings.deviations())


def test_the_master_switch_does_not_stop_exits(settings, led, spread, live_session):
    """Pausing new risk is not a reason to stop managing the risk already on."""
    paper_broker.open_approved(led, spread, "Energy", 1, settings,
                               proposal_id="P1", approved_by="a human", sess=live_session)
    settings.paper_trading = False
    pos = led.open_positions[0]
    pos.mark_cost_to_close = round(pos.credit_open * 0.20, 4)     # 80% captured
    acted = paper_broker.apply_exits(led, settings, fresh={pos.id}, sess=live_session)
    assert [d.action for _, d in acted] == [TAKE_PROFIT]


# --- every refusal is catchable as one thing -------------------------------
def test_every_open_refusal_shares_a_base_so_a_batch_can_continue():
    for exc in (paper_broker.ApprovalRequired, paper_broker.MarketNotReady,
                paper_broker.InsufficientFunds, paper_broker.TradingDisabled):
        assert issubclass(exc, paper_broker.OpenBlocked)


# --- what replaces the human --------------------------------------------
def test_auto_open_refuses_a_shut_market(settings, led, tmp_path, monkeypatch):
    """A human approving outside RTH has read the stale-quote banner and made a
    call. Auto-open has nobody to read it, so it holds -- the same standard
    apply_exits holds itself to."""
    import datetime as dt

    from pcs.session import SessionState
    run = _load_cli()
    settings.auto_approve = True
    shut = SessionState(dt.datetime(2026, 8, 31, 22, 0), True, "closed", "stale", "closed")
    assert shut.can_open_positions          # the opening-range gate lets this through
    assert not shut.is_open                 # ... and this is what must stop it

    props = [_pending_proposal()]
    assert run._auto_open(props, led, settings, shut, learning.Journal()) == 0
    assert not led.open_positions
    assert props[0].status == "pending"


def test_auto_open_opens_when_the_market_is_live(settings, led, live_session):
    run = _load_cli()
    settings.auto_approve = True
    props = [_pending_proposal()]
    assert run._auto_open(props, led, settings, live_session, learning.Journal()) == 1
    assert len(led.open_positions) == 1
    assert props[0].status == "approved"
    assert props[0].approved_by == settings.auto_approver()
    assert "agent" in led.open_positions[0].approved_by
