"""Section 1.5: pick a real listed Friday near 32 DTE -- never assume one."""
import datetime as dt

from pcs.chains import pick_expiration

TODAY = dt.date(2026, 8, 31)          # a Monday
LISTED = ["2026-09-04", "2026-09-11", "2026-09-18", "2026-09-25",
          "2026-10-02", "2026-10-09", "2026-10-16", "2026-11-20"]


def test_picks_the_listed_friday_nearest_the_target():
    pick = pick_expiration(LISTED, today=TODAY)
    assert pick == "2026-10-02"
    assert dt.date.fromisoformat(pick).weekday() == 4
    assert (dt.date.fromisoformat(pick) - TODAY).days == 32


def test_returns_none_rather_than_a_date_outside_the_window():
    assert pick_expiration(["2026-09-04", "2026-12-18"], today=TODAY) is None


def test_prefers_a_friday_over_a_nearer_non_friday():
    # 2026-09-30 is a Wednesday at 30 DTE; 10-02 is a Friday at 32.
    assert pick_expiration(["2026-09-30", "2026-10-02"], today=TODAY) == "2026-10-02"


def test_takes_a_non_friday_when_it_is_the_only_listing_in_the_window():
    assert pick_expiration(["2026-09-30"], today=TODAY) == "2026-09-30"


# --- the batch date is display-only -------------------------------------------
MONTHLY_ONLY = ["CSGP", "PODD", "ZTS", "APTV", "HONA"]     # 2026-10-06's first five


def test_a_monthly_only_head_of_the_shortlist_does_not_end_the_run(
        settings, tmp_path, monkeypatch, live_session):
    """2026-10-06: 112 names passed the screen. The batch date is probed on the
    first five, all five list monthlies only (10-16, then 11-20 at 45 DTE),
    and `propose` returned there -- the other 107, weeklies among them, were
    never priced. LEARNING.md 8 says a name with no listing is skipped "instead
    of taking the whole run down"; the probe is the one place that still did."""
    import argparse
    import types

    import run as cli
    from pcs import dashboard, health, learning, pipeline, proposer
    from pcs import ledger as ledger_mod

    monkeypatch.setattr(ledger_mod, "LEDGER_JSON", tmp_path / "ledger.json")
    monkeypatch.setattr(health, "HEALTH_JSON", tmp_path / "health.json")
    monkeypatch.setattr(learning, "load", lambda path=None: learning.Journal())
    monkeypatch.setattr(proposer, "save", lambda props, path=None: tmp_path / "p.json")
    monkeypatch.setattr(dashboard, "render", lambda *a, **k: None)

    class Res:
        session = live_session
        warnings: list = []

    names = MONTHLY_ONLY + ["ADBE", "UNH"]
    monkeypatch.setattr(pipeline, "run_screen", lambda *a, **k: Res())
    monkeypatch.setattr(pipeline, "shortlist", lambda *a, **k: [
        types.SimpleNamespace(symbol=s) for s in names])
    monkeypatch.setattr(pipeline, "resolve_expiration", lambda sym, s: (
        None if sym in MONTHLY_ONLY else "2026-11-06"))
    priced: list[str] = []

    def size(cands, *a, **k):
        priced.extend(c.symbol for c in cands)
        return []

    monkeypatch.setattr(pipeline, "size_candidates", size)
    monkeypatch.setattr(pipeline, "apply_earnings", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "build_proposals", lambda *a, **k: ([], []))

    args = argparse.Namespace(symbols=None, max_cache_age=60, include_tight=False,
                              expiration=None, contracts=1,
                              allow_unknown_earnings=False, no_auto_open=False)
    assert cli.cmd_propose(args, settings) == 0
    assert priced == names
