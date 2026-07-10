"""
Historical backtesting for the scanner's swing signals.

Modules:
  data.py    – bulk daily-bar download with a local cache
  resolve.py – trade outcome resolution (shared with the live signal tracker)
  engine.py  – walk-forward loop replaying the live pipeline point-in-time
"""
