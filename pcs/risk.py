"""Step 6 -- portfolio-level risk manager (section 1.8).

Per-trade limits live in the optimizer. This module answers the separate
question: given what is already open, may this proposal be added at all?

Section 1.8 leaves the numbers to the user, so all four caps are settings, not
constants -- but every rejection names the cap it hit, so the user can see
which limit is binding rather than just seeing a shorter list.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .optimizer import Spread


@dataclass
class RiskVerdict:
    ok: bool
    reasons: list[str]
    warnings: list[str]

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class PortfolioView:
    open_collateral: float
    open_count: int
    sector_counts: dict[str, int]
    symbols: set[str]
    cash: float
    buying_power: float


def check(spread: Spread, sector: str, pv: PortfolioView, settings: Settings,
          pending: list[tuple[str, str, float]] | None = None) -> RiskVerdict:
    """`pending` = (symbol, sector, collateral) already accepted this run, so a
    single batch cannot blow through a cap by being evaluated one at a time."""
    pending = pending or []
    reasons: list[str] = []
    warnings: list[str] = []

    used = pv.open_collateral + sum(c for _, _, c in pending)
    count = pv.open_count + len(pending)
    sectors = dict(pv.sector_counts)
    symbols = set(pv.symbols)
    for sym, sec, _ in pending:
        sectors[sec] = sectors.get(sec, 0) + 1
        symbols.add(sym)

    if used + spread.collateral > settings.max_total_collateral:
        reasons.append(
            f"portfolio collateral cap: ${used:,.0f} open + ${spread.collateral:,.0f} "
            f"would exceed the ${settings.max_total_collateral:,.0f} limit")
    if count >= settings.max_open_positions:
        reasons.append(f"max open positions ({settings.max_open_positions}) already reached")
    if sectors.get(sector, 0) >= settings.max_positions_per_sector:
        reasons.append(
            f"sector concentration: already {sectors.get(sector, 0)} position(s) in "
            f"{sector}, cap is {settings.max_positions_per_sector}")
    if spread.symbol in symbols:
        reasons.append(f"already holding a spread on {spread.symbol} "
                       f"(cap {settings.max_positions_per_ticker} per ticker)")
    if spread.collateral > pv.buying_power:
        reasons.append(f"insufficient buying power: ${pv.buying_power:,.0f} available, "
                       f"${spread.collateral:,.0f} required")

    # Correlation is a judgement call, not a hard stop -- section 1.8 asks for
    # it to be flagged to the user, not silently blocked.
    if sectors.get(sector, 0) >= 1:
        warnings.append(f"correlation: this would be position #{sectors.get(sector, 0) + 1} "
                        f"in {sector}")
    if spread.fill_risk:
        warnings.append(f"natural credit is only ${spread.credit_nat_dollars:.0f} -- "
                        f"may not fill at the ${spread.credit_dollars:.0f} shown")
    if spread.thin_oi:
        warnings.append(f"thin open interest (short {spread.short_oi} / long {spread.long_oi})")
    if spread.quote_quality != "live":
        warnings.append(f"quotes are a {spread.quote_quality.replace('_', ' ')}, not a live market")
    return RiskVerdict(not reasons, reasons, warnings)
