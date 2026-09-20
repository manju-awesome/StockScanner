"""Institutional long option positions from SEC Form 13F.

Public entry points live in `engine`: `prepare()` once per quarter, then
`for_ticker()` / `leaderboard()` against the context it returns.
"""
from .engine import for_ticker, leaderboard, prepare      # noqa: F401
