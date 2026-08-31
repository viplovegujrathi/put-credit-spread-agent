"""The screen -> size -> risk -> propose pipeline (architecture steps 1-7).

Kept separate from the CLI so the same sequence can be driven by a scheduled
task, a test, or a notebook. The screen (steps 1-3) and the sizing (steps 4-5)
stay decoupled on purpose -- section 3's design note -- so the 50-day-low
variant can be swapped into the screen without touching the optimizer.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import chains, marketdata, optimizer, proposer, risk, screener, session, universe
from .config import STRATEGY, Settings
from .ledger import Ledger
from .marketdata import Snapshot
from .optimizer import Spread
from .proposer import Proposal
from .screener import Candidate


@dataclass
class ScreenResult:
    session: session.SessionState
    universe: universe.Universe
    candidates: list[Candidate]
    snapshots: dict[str, Snapshot]
    counts: dict[str, int]
    warnings: list[str] = field(default_factory=list)

    def bucket(self, name: str) -> list[Candidate]:
        out = [c for c in self.candidates if c.bucket == name]
        return sorted(out, key=lambda c: c.pct_from_dma50)


def run_screen(settings: Settings, symbols: list[str] | None = None,
               progress=None, cache_max_age_min: float = 0.0) -> ScreenResult:
    sess = session.state_for(settings)
    uni = universe.load(source=settings.universe_source)
    warnings: list[str] = []
    if uni.staleness_days() > 45:
        warnings.append(
            f"S&P 500 constituent list is {uni.staleness_days()} days old "
            f"(built {uni.as_of}); membership changes several times a year - "
            f"refresh with `run.py universe --refresh`.")
    if sess.quote_quality != "live":
        warnings.append(sess.banner)

    syms = symbols or uni.symbols
    snaps: dict[str, Snapshot] = {}
    if cache_max_age_min > 0:
        hit = marketdata.load_cache(cache_max_age_min)
        if hit and all(s in hit[0] for s in syms):
            snaps, note = {s: hit[0][s] for s in syms if s in hit[0]}, hit[1]
            warnings.append(note)
    if not snaps:
        snaps = marketdata.fetch_snapshots(syms, period=settings.history_period,
                                           batch_size=settings.screen_batch_size,
                                           progress=progress)
        if not symbols:
            marketdata.save_cache(snaps)
    if symbols:
        uni = universe.Universe(uni.frame[uni.frame.symbol.isin(symbols)], uni.as_of, uni.source)
    cands = screener.screen(snaps, uni, settings)
    missing = len(syms) - sum(1 for c in cands if c.bucket != screener.NO_DATA)
    if missing > len(syms) * 0.1:
        warnings.append(f"{missing}/{len(syms)} tickers returned no usable history.")
    return ScreenResult(sess, uni, cands, snaps, screener.summary(cands), warnings)


def shortlist(res: ScreenResult, include_tight: bool = False) -> list[Candidate]:
    buckets = set(screener.TRADEABLE) | (set(screener.REVIEW) if include_tight else set())
    return sorted((c for c in res.candidates if c.bucket in buckets),
                  key=lambda c: -c.pct_off_high)


def resolve_expiration(symbol: str, settings: Settings) -> str | None:
    """Confirm the target expiration against the live chain (section 1.5)."""
    if settings.chain_source == "robinhood":
        listed = sorted(p.name.split("_")[1].removesuffix(".json")
                        for p in chains.RH_CHAIN_DIR.glob(f"{symbol}_*.json"))
        return chains.pick_expiration(listed)
    ch = chains.fetch_yfinance(symbol)
    return ch.expiration or None


def resolve_batch_expiration(cands: list[Candidate], settings: Settings,
                             probe: int = 5) -> str | None:
    """The expiration most of the shortlist shares.

    Only for display and for the Robinhood chain-request list -- sizing still
    resolves per symbol, because not every S&P name lists weeklies. PODD, for
    instance, jumps straight from 18 DTE to 46 DTE, so a batch-wide date would
    either exclude it wrongly or drag everyone else off the 32-day target.
    """
    seen: dict[str, int] = {}
    for c in cands[:probe]:
        exp = resolve_expiration(c.symbol, settings)
        if exp:
            seen[exp] = seen.get(exp, 0) + 1
    return max(seen, key=seen.get) if seen else None


@dataclass
class SizedCandidate:
    candidate: Candidate
    spreads: list[Spread]
    rejects: dict[str, int]
    chain_error: str | None = None


def size_candidates(cands: list[Candidate], res: ScreenResult, settings: Settings,
                    expiration: str | None = None) -> list[SizedCandidate]:
    out: list[SizedCandidate] = []
    for c in cands:
        exp = expiration
        ch = chains.get_chain(c.symbol, settings.chain_source, expiration=exp,
                              target_dte=STRATEGY.target_dte, spot=c.spot)
        if not ch.puts:
            out.append(SizedCandidate(c, [], {}, ch.error or "empty chain"))
            continue
        sps, rej = optimizer.build_spreads(ch, c.spot, settings, res.session)
        out.append(SizedCandidate(c, optimizer.best_per_symbol(sps, keep=3), rej, ch.error))
    return out


def apply_earnings(sized: list[SizedCandidate], res: ScreenResult,
                   settings: Settings) -> None:
    """Check each name against *its own* expiration, not a batch-wide date.

    Run after sizing so the (slow, per-ticker) earnings lookup only happens for
    names that actually produced a tradeable spread.
    """
    live = [sc for sc in sized if sc.spreads]
    marketdata.attach_earnings(res.snapshots, [sc.candidate.symbol for sc in live])
    for sc in live:
        expiry = dt.date.fromisoformat(sc.spreads[0].expiration)
        screener.apply_earnings_filter([sc.candidate], res.snapshots, expiry, settings)


def _earnings_note(c: Candidate, expiry: str) -> tuple[bool, str]:
    """(blocked, note). Unknown is never treated as a clean pass."""
    if c.earnings_in_window is True:
        return True, (f"BLOCKED - reports {c.earnings_date}, inside the {expiry} "
                      f"expiration window ({c.earnings_source})")
    if c.earnings_in_window is None:
        return True, ("BLOCKED - next earnings date unknown; the screen cannot confirm "
                      "the report falls outside the window. Verify manually to override.")
    return False, f"clear - next report {c.earnings_date}, after {expiry} ({c.earnings_source})"


def build_proposals(sized: list[SizedCandidate], res: ScreenResult, ledger: Ledger,
                    settings: Settings, contracts: int = 1,
                    allow_unknown_earnings: bool = False) -> tuple[list[Proposal], list[str]]:
    """Rank across all candidates, then apply portfolio risk in that order."""
    ranked: list[tuple[Spread, Candidate]] = []
    skipped: list[str] = []
    for sc in sized:
        c = sc.candidate
        if not sc.spreads:
            top = sorted(sc.rejects.items(), key=lambda kv: -kv[1])[:2]
            why = "; ".join(f"{k}" for k, _ in top) or (sc.chain_error or "no qualifying spread")
            skipped.append(f"{c.symbol}: no width/strike combination qualified - {why}")
            continue
        ranked.append((sc.spreads[0], c))
    ranked.sort(key=lambda t: -t[0].roc)

    pv = risk.PortfolioView(
        open_collateral=ledger.collateral_held, open_count=len(ledger.open_positions),
        sector_counts=ledger.sector_counts(),
        symbols={p.symbol for p in ledger.open_positions},
        cash=ledger.cash, buying_power=ledger.buying_power)

    pending: list[tuple[str, str, float]] = []
    proposals: list[Proposal] = []
    seq = 1
    for sp, c in ranked:
        blocked, note = _earnings_note(c, sp.expiration)
        if blocked and not allow_unknown_earnings:
            skipped.append(f"{c.symbol}: {note}")
            continue
        if contracts * sp.collateral > STRATEGY.max_collateral_per_trade:
            skipped.append(f"{c.symbol}: {contracts} contracts would put collateral "
                           f"${contracts * sp.collateral:,.0f} over the per-trade "
                           f"${STRATEGY.max_collateral_per_trade:,.0f} cap")
            continue
        verdict = risk.check(sp, c.sector, pv, settings, pending, res.session)
        p = Proposal(
            id=f"P{dt.date.today():%y%m%d}-{seq:02d}",
            created_at=dt.datetime.now().isoformat(timespec="seconds"),
            symbol=c.symbol, name=c.name, sector=c.sector, bucket=c.bucket,
            spread=sp.as_dict(), contracts=contracts,
            rationale=proposer.rationale_for(sp, c.pct_off_high, c.pct_from_dma50, c.dma50),
            risk_ok=verdict.ok, risk_reasons=verdict.reasons,
            risk_warnings=verdict.warnings, earnings_date=c.earnings_date,
            earnings_note=note,
        )
        proposals.append(p)
        seq += 1
        if verdict.ok:
            pending.append((c.symbol, c.sector, sp.collateral * contracts))
    return proposals, skipped
