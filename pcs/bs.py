"""Black-Scholes put pricing -- FALLBACK ONLY.

Section 5 of the strategy doc is explicit: modeled premium is a prototyping
stand-in, never the number a real trade is sized on. Anything priced here is
tagged `basis="modeled"` so it can never be mistaken for a quote downstream.
"""

from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def put_price(spot: float, strike: float, iv: float, dte_days: float,
              rate: float = 0.04, div_yield: float = 0.0) -> float:
    """European put value. Returns intrinsic if inputs degenerate."""
    t = max(dte_days, 0.0) / 365.0
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(strike - spot, 0.0)
    sig_t = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate - div_yield + 0.5 * iv * iv) * t) / sig_t
    d2 = d1 - sig_t
    return (strike * math.exp(-rate * t) * _norm_cdf(-d2)
            - spot * math.exp(-div_yield * t) * _norm_cdf(-d1))


def put_delta(spot: float, strike: float, iv: float, dte_days: float,
              rate: float = 0.04, div_yield: float = 0.0) -> float:
    t = max(dte_days, 0.0) / 365.0
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return -1.0 if strike > spot else 0.0
    sig_t = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate - div_yield + 0.5 * iv * iv) * t) / sig_t
    return math.exp(-div_yield * t) * (_norm_cdf(d1) - 1.0)
