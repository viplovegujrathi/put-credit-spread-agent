"""Step 5 -- spread optimizer (section 1.4).

For one candidate and one expiration, search every (short, long) put pair on
the real strike ladder and keep the ones that independently satisfy:

    credit x 100  >=  $100          (no upper cap -- more premium is strictly better)
    collateral    <=  $1,000        where collateral = (width x 100) - (credit x 100)
    short strike  >=  3% OTM        (never pull the strike to the money to hit the number)
    tradeable package market        (a 20%-wide market is not tradeable at the modeled price)

A vertical fills as one package, so pricing and liquidity are both judged on
the package, not leg by leg:

    nat  = short_bid - long_ask     worst realistic fill
    mid  = short_mid - long_mid     fair value (broker mark when available)
    used = mid - slippage x (mid - nat)

`used` is what sizing qualifies on; `nat` travels with every proposal so the
worst case is always visible, and a spread whose natural credit falls under
$100 is flagged `fill_risk` rather than quietly presented as a clean pass.

Ranking by credit / collateral is what implements "default to the narrowest
width" from section 1.4 -- a $5-wide clearing $110 on $390 scores 28% while a
$10-wide clearing the same $110 on $890 scores 12%, so the capital-efficient
spread wins on its own without a special case. Cushion breaks near-ties, so
equal-return spreads resolve toward the safer strike.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .bs import put_delta
from .chains import PutChain, PutQuote
from .config import STRATEGY, Settings
from .session import SessionState, slippage_frac, spread_tolerance_multiplier

MAX_WIDTH_SEARCH = 15.0     # collateral cap makes anything wider unusable anyway


@dataclass
class Spread:
    symbol: str
    expiration: str
    dte: int
    spot: float
    short_strike: float
    long_strike: float
    width: float
    credit: float               # per share, the sizing basis
    credit_nat: float           # short bid - long ask  (worst realistic fill)
    credit_mid: float           # mid - mid             (fair value)
    collateral: float           # dollars, = max loss
    credit_dollars: float
    credit_nat_dollars: float
    roc: float                  # credit / collateral
    cushion: float              # (spot - short_strike) / spot
    breakeven: float
    pct_to_breakeven: float
    short_delta: float | None
    pop_est: float | None       # broker POP when available, else 1 - |delta|
    pop_source: str
    short_oi: int
    long_oi: int
    short_volume: int
    long_volume: int
    short_bid: float
    short_ask: float
    long_bid: float
    long_ask: float
    pkg_bid: float
    pkg_ask: float
    iv: float
    basis: str                  # "live" | "modeled"
    source: str
    quote_quality: str          # "live" | "closing_snapshot" | "stale"
    efficiency: str = ""        # "efficient" | "needed wider width" | ...
    fill_risk: bool = False     # natural credit does not clear the $100 floor
    thin_oi: bool = False       # below the preferred OI, tradeable but watch it
    fees: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)

    def label(self) -> str:
        return f"{self.symbol} {self.short_strike:g}/{self.long_strike:g}p {self.expiration}"


def _leg_ok(q: PutQuote, s: Settings, mult: float) -> tuple[bool, str]:
    if q.ask <= 0:
        return False, "no offer"
    if q.open_interest < s.min_open_interest:
        return False, f"OI {q.open_interest} < {s.min_open_interest}"
    if q.volume < s.min_leg_volume:
        return False, f"volume {q.volume} < {s.min_leg_volume}"
    tol = max(s.max_leg_spread_abs, s.max_leg_spread_pct * q.mid) * mult
    if q.spread_abs > tol:
        return False, f"leg bid/ask {q.bid:.2f}/{q.ask:.2f} wider than {tol:.2f}"
    return True, ""


def _efficiency(sp: Spread) -> str:
    """The distinction the skill asks to be called out: did this clear $100
    easily on small collateral, or did it need help?"""
    if sp.collateral <= 500 and sp.cushion >= 0.03:
        return "efficient"
    if sp.width >= 8.0:
        return "needed wider width"
    if sp.cushion < 0.04:
        return "needed tighter strike"
    return "acceptable"


def build_spreads(chain: PutChain, spot: float, settings: Settings,
                  sess: SessionState, strategy=STRATEGY
                  ) -> tuple[list[Spread], dict[str, int]]:
    """Return (qualifying spreads ranked best-first, reject-reason counts)."""
    rejects: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejects[reason] = rejects.get(reason, 0) + 1

    if not chain.puts:
        reject(f"chain: {chain.error or 'empty'}")
        return [], rejects
    spot = spot or chain.spot
    if spot <= 0:
        reject("no spot price")
        return [], rejects

    mult = spread_tolerance_multiplier(sess)
    # The package-width gate is deliberately less forgiving than the per-leg
    # one: a wide package is wide however the session is graded.
    pkg_mult = min(mult, 1.4)
    slip = slippage_frac(sess, settings)
    min_nat = strategy.min_credit_per_trade * settings.min_natural_credit_frac
    by_strike = {q.strike: q for q in chain.puts}
    strikes = sorted(by_strike)
    hi = spot * (1 - strategy.min_otm_cushion)     # short strike must sit at/below this
    lo = spot * (1 - strategy.max_otm_cushion)
    shorts = [k for k in strikes if lo <= k <= hi]
    if not shorts:
        reject(f"no listed strike between {lo:.2f} and {hi:.2f} (3-20% OTM)")
        return [], rejects

    dte = chain.dte
    fees = round(2 * settings.per_contract_fees + 2 * settings.commission_per_contract, 2)
    out: list[Spread] = []
    for sk in shorts:
        sq = by_strike[sk]
        ok, why = _leg_ok(sq, settings, mult)
        if not ok:
            reject(f"short leg: {why}")
            continue
        if sq.bid <= 0:
            reject("short leg: no bid (nothing to sell into)")
            continue
        for lk in strikes:
            width = round(sk - lk, 4)
            if width <= 0 or width > MAX_WIDTH_SEARCH:
                continue
            lq = by_strike[lk]
            ok, why = _leg_ok(lq, settings, mult)
            if not ok:
                reject(f"long leg: {why}")
                continue

            nat = round(sq.bid - lq.ask, 4)
            mid = round(sq.mid - lq.mid, 4)
            if mid <= 0:
                reject("non-positive package mid (bad quote)")
                continue
            credit = round(mid - slip * (mid - nat), 4)
            credit_dollars = round(credit * 100, 2)
            collateral = round(width * 100 - credit_dollars, 2)

            if credit_dollars < strategy.min_credit_per_trade:
                reject(f"credit ${credit_dollars:.0f} < ${strategy.min_credit_per_trade:.0f}")
                continue
            if collateral > strategy.max_collateral_per_trade:
                reject(f"collateral ${collateral:.0f} > ${strategy.max_collateral_per_trade:.0f}")
                continue
            if collateral <= 0:
                reject("credit exceeds width (bad quote)")
                continue
            if nat * 100 < min_nat:
                reject(f"natural credit ${nat * 100:.0f} < ${min_nat:.0f} "
                       f"({settings.min_natural_credit_frac:.0%} of the "
                       f"${strategy.min_credit_per_trade:.0f} floor) -- the mid overstates "
                       f"what this would fill at")
                continue

            pkg_bid, pkg_ask = nat, round(sq.ask - lq.bid, 4)
            if (pkg_ask - pkg_bid) > settings.max_package_spread_pct_of_width * width * pkg_mult:
                reject(f"package market {pkg_bid:.2f}/{pkg_ask:.2f} too wide "
                       f"for a ${width:g} spread")
                continue

            iv = sq.iv or lq.iv or 0.30
            delta = sq.delta if sq.delta is not None else (
                put_delta(spot, sk, iv, dte) if iv > 0 else None)
            if sq.pop_short is not None:
                pop, pop_src = round(float(sq.pop_short), 4), "broker"
            elif delta is not None:
                pop, pop_src = round(1 - abs(delta), 4), "1-|delta| estimate"
            else:
                pop, pop_src = None, "unavailable"

            sp = Spread(
                symbol=chain.symbol, expiration=chain.expiration, dte=dte, spot=round(spot, 2),
                short_strike=sk, long_strike=lk, width=width,
                credit=credit, credit_nat=nat, credit_mid=mid,
                collateral=collateral, credit_dollars=credit_dollars,
                credit_nat_dollars=round(nat * 100, 2),
                roc=round(credit_dollars / collateral, 4),
                cushion=round((spot - sk) / spot, 4),
                breakeven=round(sk - credit, 2),
                pct_to_breakeven=round((spot - (sk - credit)) / spot, 4),
                short_delta=round(delta, 4) if delta is not None else None,
                pop_est=pop, pop_source=pop_src,
                short_oi=sq.open_interest, long_oi=lq.open_interest,
                short_volume=sq.volume, long_volume=lq.volume,
                short_bid=sq.bid, short_ask=sq.ask, long_bid=lq.bid, long_ask=lq.ask,
                pkg_bid=pkg_bid, pkg_ask=pkg_ask,
                iv=round(iv, 4), basis=chain.basis, source=chain.source,
                quote_quality=sess.quote_quality, fees=fees,
                fill_risk=(nat * 100) < strategy.min_credit_per_trade,
                thin_oi=min(sq.open_interest, lq.open_interest) < settings.preferred_open_interest,
            )
            sp.efficiency = _efficiency(sp)
            out.append(sp)

    # Return on collateral does the "narrowest width that clears $100" work by
    # construction; cushion breaks near-ties toward the safer strike.
    out.sort(key=lambda s: (-round(s.roc, 3), -s.cushion, s.width))
    return out, rejects


def best_per_symbol(spreads: list[Spread], keep: int = 3) -> list[Spread]:
    """Top `keep` alternates for one symbol, de-duplicated by strike pair."""
    seen: set[tuple[float, float]] = set()
    out: list[Spread] = []
    for sp in spreads:
        key = (sp.short_strike, sp.long_strike)
        if key in seen:
            continue
        seen.add(key)
        out.append(sp)
        if len(out) >= keep:
            break
    return out
