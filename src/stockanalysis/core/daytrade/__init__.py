"""
core.daytrade — the stock day-trade momentum engine
===================================================
A third engine for a third question. The scanner and decision engine ask
"is there a trade here" on a multi-day horizon; core.longterm asks "does
this business deserve to be owned". This asks only:

    can this stock move far enough, fast enough, for a reason, with
    structure I can define risk against, and can I get out again?

Nothing in this package reads a fundamental. No revenue growth, no ROE, no
fair value, no EPS — not because they are uninformative, but because they
are not informative about the next ninety minutes, and a ranking factor
that does not bear on the holding period is noise wearing the clothes of
rigour. A name can be AVOID in core.longterm and A+ here without either
being wrong. They are not measuring the same thing.

    universe.py     §1 hard filters, §11 tradeability — a gate
    catalyst.py     §2 why it is moving, materiality × freshness
    volatility.py   §3 ATR%, time-of-day RVOL, expected remaining move
    supply.py       §4 float on an inverted-U, short interest as potential
    structure.py    §5 premarket + §6 intraday levels and patterns
    strength.py     §7 relative strength vs SPY/QQQ/IWM/sector
    volume.py       §8 the contraction → expansion → follow-through sequence
    room.py         §9 distance to the next real level — a gate
    regime.py       §15 intraday RISK ON / MIXED / RISK OFF
    plan.py         §13 structural stops, measured-move targets, §14 sizing
    engine.py       §10 confluence, §12 grade, §17 decision
    report.py       §16 ranked table, §17 explanation blocks
    scan.py         orchestration; datafeed.py is the only I/O

What is a gate and what is a weight
------------------------------------
Three things cannot be outscored, because §10, §11 and §9 say they cannot:

    tradeability   an unexitable position is not improved by a better chart
    room           a setup into a wall is not improved by a better catalyst
    confirmations  §18's "several independent factors agree" is a count,
                   not a sum — 85 points from two huge factors and 85 from
                   eight modest ones are the same number and very
                   different trades

Everything else is weighted per §10 and can trade off freely.

Missing data never becomes a score
-----------------------------------
Inherited from core.longterm and load-bearing here, because this engine
runs on data that is frequently absent. Every score renormalises over the
inputs it actually had and reports `coverage`; the engine refuses a grade
below MIN_COVERAGE. Specifically not estimated anywhere:

    borrow fee, shares available to borrow      no source
    offerings / ATM / warrants / convertibles   no source; every candidate
                                                carries an explicit
                                                unverified flag (§1)
    market breadth (advance/decline)            no source (§15)
    bid/ask spread outside market hours         the quotes are stale, and
                                                scoring them rejected every
                                                candidate on a closed market

§2's catalyst engine is the one deliberate exception: absence of news
scores low rather than unknown, because a stock up 40% on nothing is a
finding, not a gap in the data.

Sessions
--------
The engine analyses the most recent session present in the data. Outside
market hours that is the last completed one, `is_live` is False and the
report says so on the first line — every level is that session's close,
which is useful for preparation and is not a live scan. `scan.run(at_time=)`
replays a session as of a wall-clock time, which is the only way to see
the engine's real behaviour outside market hours: read at the close, every
candidate has already made its move and correctly scores a poor R:R.
"""

from __future__ import annotations

from stockanalysis.core.daytrade.engine import evaluate, rank_key
from stockanalysis.core.daytrade.scan import run

__all__ = ["evaluate", "rank_key", "run"]
