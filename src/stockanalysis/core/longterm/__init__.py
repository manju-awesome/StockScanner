"""
core.longterm — the Long-Term Buy Engine
========================================
A separate engine for a separate question. The scanner, the swing scores and
the decision engine all answer "is there a trade here"; this answers "does
this business deserve to be owned, and is the price currently offering a
reasonable entry" — two decisions that are kept independent on purpose.

The whole package exists to enforce one hierarchy:

    business quality > valuation > long-term trend > support > entry trigger

That ordering is structural, not a weighting. A company that fails the
quality gate cannot reach a buy action however good its chart scores,
because no chart pattern compensates for a mediocre business. Weights alone
would let a 95 technical score drag a 62-quality name over the line, which
is the exact failure this engine is built to refuse.

    quality.py      LQuality 0-100 — what deserves to be owned
    valuation.py    fair value + discount band — what it is worth
    technicals.py   trend gate, pullback zone, support confluence — when
    thesis.py       has the business actually deteriorated — whether to keep
    engine.py       the four gates in order, one action per ticker

Opening a position and adding to one are different questions
-------------------------------------------------------------
The hierarchy above answers the first. `thesis.py` exists because the second
cannot use it: measured across the live library, valuation returns the same
verdict at the 8/21 EMA and at the 200 MA for 55 of 56 quality names, so it
grades a company rather than a price and cannot size a tranche. Accumulation
gates on quality and an intact thesis, prices its rungs off support, and
lets valuation modify how much rather than whether. See thesis.py's docstring
for the measurements.

Naming
------
Everything here is prefixed `LQuality` / `LT_` rather than reusing "quality".
Four other modules already compute a company-quality number
(company_scores.compute_business_quality, conviction.score_quality,
strategy_scores.score_investment, decision_engine.investment_score) and they
do not agree, because they were each calibrated for a different question.
Adding a fifth under a shared name would make the disagreement invisible at
the call site; under its own name it stays legible.

Missing data
------------
The rule inherited from decision_engine holds throughout: a missing input
never becomes a score. Every score renormalises over the weight it actually
measured and reports `coverage`, and callers refuse tier labels and buy
actions below the module's MIN_COVERAGE. A 91 built from half its inputs is
not an Elite company, it is an unknown one.
"""

from __future__ import annotations

from stockanalysis.core.longterm.quality import compute_lquality
from stockanalysis.core.longterm.valuation import compute_valuation
from stockanalysis.core.longterm.technicals import (
    compute_trend, compute_pullback, compute_support_confluence,
    compute_pullback_volume, score_support_level,
)
from stockanalysis.core.longterm.thesis import compute_thesis
from stockanalysis.core.longterm.buy_zones import (
    zone_proximity, NEAR_ZONE_PCT,
)
from stockanalysis.core.longterm.engine import evaluate, evaluate_universe

__all__ = [
    "compute_lquality", "compute_valuation", "compute_trend",
    "compute_pullback", "compute_support_confluence",
    "compute_pullback_volume", "score_support_level", "compute_thesis",
    "zone_proximity", "NEAR_ZONE_PCT",
    "evaluate", "evaluate_universe",
]
