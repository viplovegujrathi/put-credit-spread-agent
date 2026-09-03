"""Step 6 -- portfolio-level risk manager (section 1.8).

Per-trade limits live in the optimizer. This module answers the separate
question: given what is already open, may this proposal be added at all?

Section 1.8 leaves the numbers to the user, so every cap here is a setting, not
a constant -- but every rejection names the cap it hit, so the user can see
which limit is binding rather than just seeing a shorter list.

One of these is not a cap on size at all. The re-entry cooldown is a cap on
TIMING: it keeps a name out of the book for a few days after it closed at a
loss. `max_positions_per_ticker` stops the account holding two spreads on a
name at once and says nothing about opening one straight after a stop, which
is the churn case -- see `Ledger.cooling_off`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    # Per-symbol COUNTS, not a set of held names. This was a set, and the
    # per-ticker check was `symbol in symbols` -- so the cap behaved as 1 no
    # matter what `max_positions_per_ticker` said, while the refusal message
    # quoted the setting's real value back at the reader.
    ticker_counts: dict[str, int]
    cash: float
    buying_power: float          # unencumbered cash, see Ledger.buying_power
    # symbol -> ISO date it becomes eligible again. See Ledger.cooling_off.
    # Defaulted so a caller that does not track closes still gets a valid view.
    cooldowns: dict[str, str] = field(default_factory=dict)


def check(spread: Spread, sector: str, pv: PortfolioView, settings: Settings,
          pending: list[tuple[str, str, float]] | None = None,
          sess=None, contracts: int = 1) -> RiskVerdict:
    """`pending` = (symbol, sector, collateral) already accepted this run, so a
    single batch cannot blow through a cap by being evaluated one at a time.

    `Spread.collateral` is per contract; every cap here is a portfolio total, so
    the position size has to be multiplied in or a 2-lot is checked as a 1-lot.
    """
    pending = pending or []
    reasons: list[str] = []
    warnings: list[str] = []

    need = round(spread.collateral * contracts, 2)
    committed = sum(c for _, _, c in pending)
    used = pv.open_collateral + committed
    count = pv.open_count + len(pending)
    sectors = dict(pv.sector_counts)
    tickers = dict(pv.ticker_counts)
    for sym, sec, _ in pending:
        sectors[sec] = sectors.get(sec, 0) + 1
        tickers[sym] = tickers.get(sym, 0) + 1

    if used + need > settings.max_total_collateral:
        reasons.append(
            f"portfolio collateral cap: ${used:,.0f} open + ${need:,.0f} "
            f"would exceed the ${settings.max_total_collateral:,.0f} limit")
    if count >= settings.max_open_positions:
        reasons.append(f"max open positions ({settings.max_open_positions}) already reached")
    if sectors.get(sector, 0) >= settings.max_positions_per_sector:
        reasons.append(
            f"sector concentration: already {sectors.get(sector, 0)} position(s) in "
            f"{sector}, cap is {settings.max_positions_per_sector}")
    on_ticker = tickers.get(spread.symbol, 0)
    if on_ticker >= settings.max_positions_per_ticker:
        reasons.append(
            f"ticker concentration: already {on_ticker} position(s) on "
            f"{spread.symbol}, cap is {settings.max_positions_per_ticker} per ticker")
    # A stop fires on a mark, and a mark can be wrong. Re-opening the name the
    # same session re-establishes the risk at a worse price, so a single bad
    # print gets paid for twice -- which is exactly what happened to GOOGL.
    clear_on = pv.cooldowns.get(spread.symbol)
    if clear_on:
        reasons.append(
            f"re-entry cooldown: {spread.symbol} closed at a loss inside the last "
            f"{settings.reentry_cooldown_days} day(s) and is eligible again on "
            f"{clear_on}")
    # The account balance is the floor beneath every other cap: a position can
    # never commit more max loss than there is free cash to pay it with. Earlier
    # proposals in this same batch have already spoken for their share.
    free = round(pv.buying_power - committed, 2)
    cost = round(need + spread.fees * contracts, 2)
    if cost > free:
        reasons.append(
            f"exceeds available balance: ${cost:,.2f} required, ${free:,.2f} free"
            + (f" (${pv.buying_power:,.2f} in the account less ${committed:,.2f} "
               f"already committed to earlier proposals in this batch)" if committed else ""))

    # Correlation is a judgement call, not a hard stop -- section 1.8 asks for
    # it to be flagged to the user, not silently blocked.
    if sectors.get(sector, 0) >= 1:
        warnings.append(f"correlation: this would be position #{sectors.get(sector, 0) + 1} "
                        f"in {sector}")
    # A second spread on the same underlying is a legal ladder above a
    # per-ticker cap of 1, and it is also the most concentrated thing this book
    # can do: two positions, one gap risk. Allowed, and never silent.
    if on_ticker >= 1:
        warnings.append(f"single-name concentration: this would be position "
                        f"#{on_ticker + 1} on {spread.symbol} itself -- one gap in "
                        f"that name hits both")
    if spread.fill_risk:
        warnings.append(f"natural credit is only ${spread.credit_nat_dollars:.0f} -- "
                        f"may not fill at the ${spread.credit_dollars:.0f} shown")
    if spread.thin_oi:
        warnings.append(f"thin open interest (short {spread.short_oi} / long {spread.long_oi})")
    if spread.quote_quality != "live":
        warnings.append(f"quotes are a {spread.quote_quality.replace('_', ' ')}, not a live market")
    if sess is not None and not sess.can_open_positions:
        warnings.append(f"sized {sess.open_block_reason}")
    return RiskVerdict(not reasons, reasons, warnings)
