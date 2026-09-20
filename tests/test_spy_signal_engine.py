"""
Golden-case tests for signal_engine.py — this is a from-scratch translation
of a Pine Script, so it needs to be provably correct against hand-computed
values, not just "runs without error."
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Daytrades.core import signal_engine  # noqa: E402


def _bars(closes: list[float], start: str = "2026-07-20 09:30", freq: str = "5min") -> pd.DataFrame:
    """Builds a High=Low=Close=given-value bar series so True Range reduces
    to |close - prev_close|, keeping the hand-computed math tractable."""
    idx = pd.date_range(start=start, periods=len(closes), freq=freq, tz="America/New_York")
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [1000] * len(closes)},
        index=idx,
    )


def test_ut_bot_stop_matches_hand_computed_series():
    # Hand-derived in the plan: key_value=2, atr_period=1 (=> nLoss = 2*TR,
    # TR = |close - prev_close| since High=Low=Close here).
    closes = [100, 101, 102, 103, 95, 94]
    df = _bars(closes)

    frame = signal_engine.compute_signal_frame(df, key_value=2, atr_period=1)

    expected_stop = [100.0, 99.0, 100.0, 101.0, 111.0, 96.0]
    assert frame["ut_stop"].tolist() == pytest.approx(expected_stop)

    # No buy signals in this series (price is already above the stop from
    # bar 1 onward); exactly one sell, on the bar where the big drop crosses
    # under the trailing stop.
    assert frame["buy"].tolist() == [False] * 6
    assert frame["sell"].tolist() == [False, False, False, False, True, False]


def test_ut_bot_sell_does_not_refire_while_still_below():
    # Bar 5 (close=94) stays below its stop (96) but did NOT newly cross
    # under it (it was already below at bar 4) — sell must not re-fire.
    closes = [100, 101, 102, 103, 95, 94]
    frame = signal_engine.compute_signal_frame(_bars(closes), key_value=2, atr_period=1)
    assert frame["sell"].iloc[4] is True or bool(frame["sell"].iloc[4]) is True
    assert bool(frame["sell"].iloc[5]) is False


def test_ut_bot_buy_signal_fires_on_upward_cross():
    # Sharp drop then a sharp recovery: the recovery bar should cross back
    # above the (now elevated) stop and fire a fresh buy.
    closes = [100, 99, 98, 97, 110, 111]
    frame = signal_engine.compute_signal_frame(_bars(closes), key_value=2, atr_period=1)
    assert bool(frame["buy"].iloc[4]) is True
    assert frame["sell"].tolist().count(True) == 0 or bool(frame["sell"].iloc[4]) is False


def test_hma_trend_reflects_slope_on_monotonic_series():
    # Strictly increasing closes -> HMA should be rising (bullish) once
    # warmed up; strictly decreasing -> bearish.
    up_closes = list(range(100, 130))
    frame_up = signal_engine.compute_signal_frame(_bars(up_closes), hma_period=5, ma200_period=5)
    assert frame_up["hma_trend"].iloc[-1] == "bullish"
    assert frame_up["ma200_trend"].iloc[-1] == "bullish"

    down_closes = list(range(130, 100, -1))
    frame_down = signal_engine.compute_signal_frame(_bars(down_closes), hma_period=5, ma200_period=5)
    assert frame_down["hma_trend"].iloc[-1] == "bearish"
    assert frame_down["ma200_trend"].iloc[-1] == "bearish"


def test_orb_freezes_after_window_and_flags_breakout():
    # 09:30 and 09:33 fall inside a 5-minute (09:30-09:35) opening range;
    # 09:40 and 09:45 are after it and should see the frozen high/low, with
    # a breakout flag once price clears it.
    idx = pd.to_datetime([
        "2026-07-20 09:30", "2026-07-20 09:33", "2026-07-20 09:40", "2026-07-20 09:45",
    ]).tz_localize("America/New_York")
    df = pd.DataFrame(
        {
            "Open": [500, 501, 502, 506],
            "High": [500.5, 502, 502.5, 507],
            "Low": [499.5, 500.5, 501.5, 505.5],
            "Close": [500, 501.5, 502, 506.5],
            "Volume": [1000] * 4,
        },
        index=idx,
    )

    frame = signal_engine.compute_signal_frame(df, orb_session_start="09:30", orb_minutes=5)

    # Opening range = high/low of the 09:30 and 09:33 bars.
    assert frame["orb_high"].iloc[2] == pytest.approx(502.0)
    assert frame["orb_low"].iloc[2] == pytest.approx(499.5)
    assert frame["orb_high"].iloc[3] == pytest.approx(502.0)

    # 09:45 close (506.5) clears the frozen ORB high (502) -> long breakout.
    assert frame["orb_breakout"].iloc[3] == "long"
    # 09:40 close (502) does not clear it -> no breakout flag.
    assert frame["orb_breakout"].iloc[2] is None


def test_orb_breakout_uses_none_not_nan_for_no_breakout():
    """Regression: np.select(..., default=None) yields None on pandas 2.x but
    nan on 3.x, which silently flips `is None` checks between versions. The
    no-breakout marker must be None on every supported pandas."""
    closes = [100, 101, 102]
    frame = signal_engine.compute_signal_frame(_bars(closes))

    for value in frame["orb_breakout"]:
        assert value is None or value in ("long", "short")
        assert not (isinstance(value, float) and pd.isna(value)), "nan leaked in as the no-breakout marker"


def test_latest_signal_returns_none_when_no_signal_on_last_bar():
    closes = [100, 101, 102, 103, 104, 105]
    frame = signal_engine.compute_signal_frame(_bars(closes), key_value=2, atr_period=1)
    assert signal_engine.latest_signal(frame) is None


def test_latest_signal_shape_when_sell_fires():
    closes = [100, 101, 102, 103, 95]
    frame = signal_engine.compute_signal_frame(_bars(closes), key_value=2, atr_period=1)
    sig = signal_engine.latest_signal(frame, ticker="SPY", vix=18.5)
    assert sig is not None
    assert sig["ticker"] == "SPY"
    assert sig["side"] == "short"
    assert sig["spy_price"] == pytest.approx(95.0)
    assert sig["vix"] == pytest.approx(18.5)
    assert isinstance(sig["orb_breakout_aligned"], bool)
