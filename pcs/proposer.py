"""Step 7 -- trade proposer.

Formats one reviewable ticket per candidate and persists the batch. This is
the last step before the human gate; it never submits anything, and nothing
downstream can open a position without quoting a proposal id back.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import PROPOSALS_JSON
from .optimizer import Spread


@dataclass
class Proposal:
    id: str
    created_at: str
    symbol: str
    name: str
    sector: str
    bucket: str
    spread: dict
    contracts: int
    rationale: str
    risk_ok: bool
    risk_reasons: list[str] = field(default_factory=list)
    risk_warnings: list[str] = field(default_factory=list)
    earnings_date: str | None = None
    earnings_note: str = ""
    status: str = "pending"          # pending | approved | rejected | expired
    approved_by: str = ""
    approved_at: str = ""
    position_id: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def rationale_for(sp: Spread, pct_off_high: float, pct_from_dma50: float,
                  dma50: float) -> str:
    return (
        f"{sp.symbol} is {pct_off_high:.0%} off its 52-week high and sitting "
        f"{abs(pct_from_dma50):.1%} below its ${dma50:,.2f} 50-day average - the "
        f"pull-back-to-support setup this strategy screens for. Selling the "
        f"${sp.short_strike:g} put ({sp.cushion:.1%} out of the money) against the "
        f"${sp.long_strike:g} put collects ${sp.credit_dollars:.0f} against "
        f"${sp.collateral:.0f} of collateral ({sp.roc:.0%} return on max loss) and "
        f"breaks even at ${sp.breakeven:,.2f}, {sp.pct_to_breakeven:.1%} below spot."
    )


def ticket(p: Proposal, settings=None) -> str:
    """The human-readable block a reviewer actually reads before approving."""
    s = p.spread
    warn = "".join(f"\n  ! {w}" for w in p.risk_warnings)
    block = [
        f"PROPOSAL {p.id}   {p.symbol} bull put credit spread   [{p.status.upper()}]",
        f"  {p.name} - {p.sector} ({p.bucket})",
        f"  SELL {p.contracts}x {s['symbol']} {s['expiration']} ${s['short_strike']:g} put",
        f"  BUY  {p.contracts}x {s['symbol']} {s['expiration']} ${s['long_strike']:g} put",
        f"  width ${s['width']:g}   {s['dte']} DTE   spot ${s['spot']:,.2f}",
        f"  credit  ${s['credit_dollars']:.0f}  (package {s['pkg_bid']:.2f}/{s['pkg_ask']:.2f}, "
        f"natural ${s['credit_nat_dollars']:.0f})",
        f"  collateral / max loss  ${s['collateral']:.0f}",
        f"  return on collateral   {s['roc']:.1%}",
        f"  cushion {s['cushion']:.1%} OTM   breakeven ${s['breakeven']:,.2f}   "
        f"POP {s['pop_est'] if s['pop_est'] is None else format(s['pop_est'], '.0%')} ({s['pop_source']})",
        f"  liquidity  short OI {s['short_oi']} / long OI {s['long_oi']}   IV {s['iv']:.1%}",
        f"  pricing    {s['basis']} via {s['source']} ({s['quote_quality'].replace('_', ' ')})",
        f"  earnings   {p.earnings_note}",
        f"  {p.rationale}",
    ]
    if warn:
        block.append(f"  warnings:{warn}")
    if not p.risk_ok:
        block.append("  BLOCKED: " + "; ".join(p.risk_reasons))

    # A ticket sized under a loosened rule must never read like one that met the
    # standard rule, so the deviations travel with it.
    dev = settings.deviations() if settings is not None else []
    if dev:
        block.append("  sized under NON-STANDARD rules:")
        block.extend(f"    - {d}" for d in dev)
    if settings is not None and not settings.require_approval():
        block.append("  -> human approval is OFF for this paper account; the agent "
                     "opens this itself.")
    else:
        block.append("  -> requires explicit human approval; nothing is placed automatically.")
    return "\n".join(block)


def save(proposals: list[Proposal], path: Path = PROPOSALS_JSON) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "proposals": [p.as_dict() for p in proposals],
    }, indent=2))
    return path


def load(path: Path = PROPOSALS_JSON) -> list[Proposal]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [Proposal(**p) for p in raw.get("proposals", [])]
