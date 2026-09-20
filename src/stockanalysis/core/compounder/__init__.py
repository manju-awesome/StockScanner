"""
core.compounder — the Future Compounder / Emerging Leader engine
================================================================
Finds companies that could become 5-10 year market leaders BEFORE the market
has finished recognising them. Twelve steps, ending in a single
FUTURE COMPOUNDER SCORE and a 20-name 10-year watchlist.

How it differs from the other engines in this project
-----------------------------------------------------
    /longterm    "should I own this quality business, and at what price"
                 — gates on quality, then valuation, then trend
    /shortside   "what is this stock worth on both sides today"
    /csp         "what premium does this company's chain pay"
    THIS ONE     "could this become a major company in ten years"

The difference that shows up everywhere: this engine is forbidden from
rejecting a company for being unprofitable, cash-burning, uncovered,
un-indexed, small, or expensive. Those are the normal conditions of the
population it searches, so each is CLASSIFIED as a risk and the ranking
continues. See engine.py for why weights are right here and gates are right
in /longterm.

Reading order for the code
--------------------------
    themes.py         the curated TAM library — and the universe
    fetch.py          one network pass per company
    growth.py         Step 3 — acceleration, the second derivative
    leverage.py       Step 4 — does scale make the model better
    moat.py           Step 5 — evidence a moat is forming
    reinvestment.py   Step 6 — is the spending buying anything
    position.py       Steps 1, 2, 7 — market, share, standing
    management.py     Step 8 — execution from revealed preference
    survivability.py  Step 9 — can it survive being right
    discovery.py      Step 10 — how far along is recognition
    stage.py          Step 11 — where on the curve
    engine.py         Step 12 — the composite and the watchlist
    narrative.py      the written case, every line traced to a number
"""

from __future__ import annotations

from .engine import (WEIGHTS, cap_band, counts, evaluate, evaluate_universe,
                     stage_counts, tier, watchlist)
from .themes import THEMES, coverage, theme_for, universe

__all__ = ["evaluate", "evaluate_universe", "watchlist", "counts",
           "stage_counts", "tier", "cap_band", "WEIGHTS",
           "universe", "theme_for", "THEMES", "coverage"]
