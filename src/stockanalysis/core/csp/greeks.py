"""
Black-Scholes greeks for the cash-secured put engine.

yfinance option chains carry bid/ask/IV/OI/volume but no greeks, so the
engine computes them. Pure stdlib `math` — no scipy — because the only
special function needed is the normal CDF and `math.erf` provides it
exactly. Nothing here reaches the network or reads a file, so every rule
in the CSP score is reproducible from the chain snapshot alone.

Conventions
-----------
- All rates and vols are decimals per year (0.28, not 28).
- `t` is time to expiry in YEARS.
- Put delta is NEGATIVE, as quoted. The engine compares |delta| against
  the 0.15-0.30 band, and does the abs() at the call site so the sign
  survives into anything that needs it.
"""

from __future__ import annotations

import math

# Below roughly a day to expiry, or with vol/spot at zero, the lognormal
# model stops describing the contract: d1 explodes and delta snaps to a
# step function. Returning None beats returning a confident 0.999.
MIN_T = 1.0 / 365.0 / 4.0        # ~6 hours
MIN_SIGMA = 0.005                # 0.5% annualised


def norm_cdf(x: float) -> float:
    """Φ(x). erf is exact to double precision, so this is not an
    approximation the way a polynomial fit would be."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(s, k, t, r, sigma, q=0.0):
    """None when the inputs cannot support a lognormal model."""
    if not all(isinstance(v, (int, float)) for v in (s, k, t, r, sigma, q)):
        return None
    if s <= 0 or k <= 0 or t < MIN_T or sigma < MIN_SIGMA:
        return None
    vt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / vt
    return d1, d1 - vt


def put_greeks(s, k, t, r, sigma, q=0.0) -> dict:
    """Every greek for one put, plus the probabilities the engine ranks on.

    Returns a dict whose values are all None when the model does not
    apply — callers treat a None greek as a missing datum and say so,
    rather than substituting a default that would score.

    `theta` is per CALENDAR DAY and `vega` per ONE VOLATILITY POINT
    (1%), which is how both are quoted on a broker screen. The raw
    per-year / per-1.0-vol figures are what the formulas produce, so the
    division happens here once instead of at every call site.
    """
    blank = {"delta": None, "gamma": None, "theta": None, "vega": None,
             "rho": None, "prob_itm": None, "prob_otm": None,
             "price": None, "d1": None, "d2": None}

    dd = _d1_d2(s, k, t, r, sigma, q)
    if dd is None:
        return blank
    d1, d2 = dd

    disc_r = math.exp(-r * t)
    disc_q = math.exp(-q * t)
    nd1, nd2 = norm_cdf(-d1), norm_cdf(-d2)
    pdf1 = norm_pdf(d1)

    price = k * disc_r * nd2 - s * disc_q * nd1

    theta_yr = (-(s * pdf1 * sigma * disc_q) / (2.0 * math.sqrt(t))
                + r * k * disc_r * nd2
                - q * s * disc_q * nd1)

    return {
        # Negative, as quoted for a put.
        "delta": -disc_q * nd1,
        "gamma": disc_q * pdf1 / (s * sigma * math.sqrt(t)),
        "theta": theta_yr / 365.0,
        "vega": s * disc_q * pdf1 * math.sqrt(t) / 100.0,
        "rho": -k * t * disc_r * nd2 / 100.0,
        # Risk-neutral P(S_T < K). Not a real-world probability — it
        # carries the model's drift assumption, not the stock's. The page
        # labels it "model" for exactly that reason.
        "prob_itm": nd2,
        "prob_otm": 1.0 - nd2,
        "price": price,
        "d1": d1, "d2": d2,
    }


def prob_above(s, level, t, r, sigma, q=0.0):
    """Risk-neutral P(S_T > level). For a CSP the number that matters is
    P(finish above BREAKEVEN), not P(finish above strike) — the premium
    is yours either way, so probability of profit is measured from the
    effective cost basis. Passing breakeven as `level` gives exactly that.
    """
    dd = _d1_d2(s, level, t, r, sigma, q)
    if dd is None:
        return None
    return norm_cdf(dd[1])


def expected_move(s, t, sigma):
    """One-standard-deviation move over `t` years, in dollars.

    The engine reports this beside the strike so a strike inside the
    expected move is visibly inside it. No drift term: over 20-45 days
    the drift is a rounding error against sigma*sqrt(t), and pretending
    to know it would be false precision.
    """
    if s is None or t is None or sigma is None:
        return None
    if s <= 0 or t < MIN_T or sigma < MIN_SIGMA:
        return None
    return s * sigma * math.sqrt(t)


def implied_vol(target, s, k, t, r, q=0.0, lo=0.005, hi=5.0):
    """Backsolve IV from a put price by bisection.

    Used only when the chain's own `impliedVolatility` is missing or
    obviously broken (yfinance returns 0.0 and occasionally 1e-5 on
    illiquid strikes). Bisection rather than Newton: it cannot diverge,
    and 60 iterations on a monotone function is microseconds.
    """
    if target is None or target <= 0:
        return None
    # No volatility can produce a price below intrinsic.
    intrinsic = max(0.0, k * math.exp(-r * t) - s * math.exp(-q * t))
    if target < intrinsic - 1e-6:
        return None

    for _ in range(60):
        mid = 0.5 * (lo + hi)
        px = put_greeks(s, k, t, r, mid, q)["price"]
        if px is None:
            return None
        if px > target:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            break
    out = 0.5 * (lo + hi)
    # Pinned at a bound means the price is outside what the model can
    # produce; that is a data problem, not a 500% vol.
    return None if out <= 0.006 or out >= 4.99 else out
