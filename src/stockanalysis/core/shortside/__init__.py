"""
Short-side opportunity engine — the other half of the decision.

`core.longterm` answers "is this worth owning?" and stops. This answers
"is there a trade here, in either direction?" — and treats a failed
quality test as evidence for a short rather than the end of the
analysis. Every name the long engine rejects is scored on the short side
before anything is called NO EDGE.

Layout
------
    extension.py  how far price is from its own averages, in ATR
    reversal.py   bearish candles and first-red-day — the trigger
    thesis.py     the seven-component Short Opportunity Score
    engine.py     long / short / avoid, and the four buckets

The two ideas worth keeping in mind when changing any of this:

1.  A high short score is a SETUP, not an instruction. Confirmation is a
    separate axis and must never be blended into the score, or the
    scanner will point hardest at momentum names on the day they are
    least willing to break.
2.  Extension is measured in ATR, never in percent. The same 10% above
    the 8 EMA is a parabolic move on one name and a normal week on
    another.
"""

from .engine import (evaluate, evaluate_universe, counts, plan,
                     BUCKETS, BUCKET_ICONS, SHORT_STRONG)

__all__ = ["evaluate", "evaluate_universe", "counts", "plan",
           "BUCKETS", "BUCKET_ICONS", "SHORT_STRONG"]
