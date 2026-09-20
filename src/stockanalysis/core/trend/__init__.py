"""
core.trend — the trend regime classification engine.

Answers one question per name, in the order the FINAL RULE demands:

    REGIME (200 SMA) → TREND (50 SMA) → MOMENTUM (21 EMA) → TRIGGER (8 EMA)
    → PULLBACK/REVERSAL → ENTRY

Module map:

    indicators.py   chart facts from a daily frame — MAs, slopes, alignment,
                    price structure, ATR-normalised extension. Pure.
    regime.py       the four sub-verdicts (long-term / alignment / momentum /
                    pullback / reversal) and the single primary regime label.
    score.py        the 0-100 Trend Quality score.
    entry.py        ENTRY_STATUS — deliberately separate from the regime.
    filters.py      the five scanner filters (A-E).
    engine.py       orchestration: bars in, classified rows out, snapshot on
                    disk for the /trend page.

Nothing here fetches anything except engine.py. Everything else is a pure
function of a daily OHLCV frame, so a backtest can classify a historical
date by slicing the frame and calling the same code the live page runs.
"""
