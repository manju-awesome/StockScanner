"""
Turning an allocation into an expected return and a volatility.

The point of this module is that the return assumption should be a
CONSEQUENCE of how the money is invested, not a free parameter. A plan
that assumes 7% real while holding 40% bonds and 15% cash is not
optimistic — it is arithmetically impossible, and the impossibility is
invisible unless the two are connected somewhere.

Volatility combines with correlation rather than by weighted average,
because that is how variance works and because the weighted-average
version understates diversification badly enough to change conclusions.
"""

from __future__ import annotations

import math

DEFAULT_MIX = {"equity": 0.80, "bond": 0.15, "cash": 0.05}


def normalise(alloc: dict | None) -> dict:
    """Weights that sum to 1, over the three asset classes the model has.

    Anything the user calls something else (real estate, gold, crypto,
    private equity) has to be mapped to one of these three by whoever
    enters it. That is a real limitation and it is stated on the page
    rather than papered over with a fourth made-up asset class.
    """
    if not alloc:
        return dict(DEFAULT_MIX)
    out = {}
    for k in ("equity", "bond", "cash"):
        try:
            out[k] = max(float(alloc.get(k) or 0.0), 0.0)
        except (TypeError, ValueError):
            out[k] = 0.0
    total = sum(out.values())
    if total <= 0:
        return dict(DEFAULT_MIX)
    return {k: v / total for k, v in out.items()}


def expected_real_return(alloc: dict, a) -> float:
    """Weighted real return, in percent."""
    from . import assumptions as A
    w = normalise(alloc)
    return (w["equity"] * A.value(a, "equity_real_return")
            + w["bond"] * A.value(a, "bond_real_return")
            + w["cash"] * A.value(a, "cash_real_return"))


def volatility(alloc: dict, a) -> float:
    """Portfolio standard deviation, in percent.

    Cash is treated as zero-volatility in real terms, which is not quite
    true — inflation surprises move it — but the error is small next to
    the equity term and stating it is better than modelling it badly.
    """
    from . import assumptions as A
    w = normalise(alloc)
    se = A.value(a, "equity_volatility") / 100.0
    sb = A.value(a, "bond_volatility") / 100.0
    rho = A.value(a, "equity_bond_correlation")
    var = ((w["equity"] * se) ** 2 + (w["bond"] * sb) ** 2
           + 2 * w["equity"] * w["bond"] * se * sb * rho)
    return 100.0 * math.sqrt(max(var, 0.0))


def describe(alloc: dict, a) -> dict:
    w = normalise(alloc)
    return {
        "weights": {k: round(v * 100, 1) for k, v in w.items()},
        "expected_real_return": expected_real_return(alloc, a),
        "volatility": volatility(alloc, a),
        "note": ("Return and volatility are derived from the allocation, "
                 "not chosen independently of it."),
    }
