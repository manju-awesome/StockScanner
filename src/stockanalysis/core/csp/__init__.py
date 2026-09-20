"""
Cash-Secured Put Opportunity Engine.

Finds the names worth being assigned into, then the strike, expiry and
limit price to get paid for waiting at. The objective is NOT premium: it
is buying a high-quality, undervalued company at an attractive effective
cost basis while being paid to wait.

The company verdict is not re-derived here. `core.longterm` already
scores quality, runs the reverse-DCF valuation, and maps the support
structure; this package consumes those results and adds the options
layer — chains, greeks, volatility context, strike selection against
support, assignment analysis and the CSP score.

Layout
------
    greeks.py       Black-Scholes deltas and probabilities (stdlib only)
    chain.py        yfinance chain retrieval, liquidity, limit pricing
    volatility.py   realised vol, IV vs HV, and the accumulating IV Rank
    eligibility.py  Step 1 — the company gate, blind to option prices
    strike.py       Steps 2 & 5 — support levels and strike selection
    score.py        Steps 6, 7 & 11 — returns, assignment, CSP score
    engine.py       orchestration and the portfolio view
"""

from .engine import (evaluate, evaluate_universe, portfolio_view,
                     DEFAULT_MIN_DTE, DEFAULT_MAX_DTE)

__all__ = ["evaluate", "evaluate_universe", "portfolio_view",
           "DEFAULT_MIN_DTE", "DEFAULT_MAX_DTE"]
