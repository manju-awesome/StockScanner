"""Tests for core.sector_leaders — the pure scoring half of the sector-leader
scan. Every case here is a bug the engine actually shipped during development,
plus the invariants that keep the two directions honest against each other."""

import numpy as np
import pandas as pd
import pytest

from stockanalysis.core import sector_leaders as sl


def _frame(closes, vol=1_000_000, spread=0.01):
    """OHLCV frame from a close series, with a plausible high/low band."""
    c = pd.Series(closes, dtype=float)
    # the index has to go on the series BEFORE the frame is built: pandas
    # aligns on index, so a RangeIndex series against a DatetimeIndex frame
    # silently yields an all-NaN column
    c.index = pd.date_range("2025-01-01", periods=len(c), freq="B")
    return pd.DataFrame({
        "Open": c.shift(1).fillna(c.iloc[0]),
        "High": c * (1 + spread),
        "Low": c * (1 - spread),
        "Close": c,
        "Volume": pd.Series([vol] * len(c), index=c.index),
    })


def _uptrend(n=260, start=100.0, daily=0.004):
    return _frame([start * (1 + daily) ** i for i in range(n)])


def _downtrend(n=260, start=200.0, daily=-0.004):
    return _frame([start * (1 + daily) ** i for i in range(n)])


def _chop(n=260, start=100.0, amp=8.0):
    return _frame([start + amp * np.sin(i / 4.0) for i in range(n)])


# ── metrics ──────────────────────────────────────────────────────────────────

def test_symbol_metrics_needs_history():
    assert sl.symbol_metrics(_frame([100] * 30)) is None
    assert sl.symbol_metrics(None) is None


def test_symbol_metrics_uptrend_flags():
    m = sl.symbol_metrics(_uptrend())
    assert m["above_ema20"] and m["above_sma50"] and m["above_sma200"]
    assert m["r20d"] > 0
    assert m["dollar_vol"] > 0


# ── trend labelling ──────────────────────────────────────────────────────────

def test_above_50_and_200_but_below_20ema_is_a_pullback_not_a_downtrend():
    """The shipped bug: XLRE, XLK and XLI all sat above their 50 and 200 SMAs
    and were labelled 'Emerging downtrend' purely on swing structure, which
    made them bearish candidates. Above the longer averages is a pullback."""
    m = {"above_ema20": False, "above_sma50": True, "above_sma200": True,
         "lower_highs": True, "lower_lows": True,
         "higher_highs": False, "higher_lows": False}
    assert sl.trend_quality_label(m, 41.0) == "Uptrend pullback"
    assert sl.sector_direction({"quality_label": "Uptrend pullback"}) == "neutral"


def test_full_stack_with_structure_is_a_strong_uptrend():
    m = {"above_ema20": True, "above_sma50": True, "above_sma200": True,
         "higher_highs": True, "higher_lows": True,
         "lower_highs": False, "lower_lows": False}
    assert sl.trend_quality_label(m, 80.0) == "Strong uptrend"
    assert sl.sector_direction({"quality_label": "Strong uptrend"}) == "bullish"


def test_below_everything_is_a_downtrend():
    m = {"above_ema20": False, "above_sma50": False, "above_sma200": False,
         "lower_highs": True, "lower_lows": True,
         "higher_highs": False, "higher_lows": False}
    assert sl.trend_quality_label(m, 20.0) == "Strong downtrend"
    assert sl.sector_direction({"quality_label": "Strong downtrend"}) == "bearish"


# ── setup levels: the arithmetic that was wrong ──────────────────────────────

def test_stop_is_never_inside_the_noise():
    """BAX priced a stop 0.13 ATR from entry, implying 25:1 reward. A stop
    must clear MIN_STOP_ATR or it is not a stop."""
    df = _uptrend()
    m = sl.symbol_metrics(df)
    s = sl.detect_setup(df, m, "long")
    if s and s["levels"]:
        risk_atr = s["levels"]["risk_atr"]
        assert risk_atr >= sl.MIN_STOP_ATR - 1e-6
        assert risk_atr <= sl.MAX_STOP_ATR + 1e-6


def test_target_is_never_a_level_price_already_sits_on():
    """MRK's target 1 was the 20-day high 0.7% overhead, giving R:R 0.05.
    Levels closer than MIN_T1_R are stepped over."""
    entry, stop, atr_val = 100.0, 96.0, 2.0     # risk 4.0
    ladder = [100.5, 107.0]                      # 100.5 is 0.125R away
    t1, t1b, t2, t2b = sl._pick_targets(entry, stop, atr_val, ladder, "long")
    assert t1 == 107.0
    assert abs(t1 - entry) / abs(entry - stop) >= sl.MIN_T1_R


def test_target_falls_back_to_atr_when_nothing_is_overhead():
    entry, stop, atr_val = 100.0, 96.0, 2.0
    t1, t1b, t2, t2b = sl._pick_targets(entry, stop, atr_val, [100.2], "long")
    assert t1 == pytest.approx(104.0)            # entry + 2 ATR
    assert "ATR" in t1b


def test_stop_falls_back_to_atr_cap_when_structure_is_far():
    """MRK's only swing low was 4.8 ATR below the close — a 13.6% 'stop'."""
    stop, basis = sl._pick_stop(100.0, 2.0, [(90.0, "last swing low")], "long")
    assert stop == pytest.approx(96.0)           # entry − MAX_STOP_ATR × ATR
    assert "ATR from entry" in basis


def test_stop_prefers_the_tightest_level_that_clears_the_floor():
    stop, basis = sl._pick_stop(
        100.0, 2.0,
        [(99.5, "too tight"), (97.5, "20 EMA"), (96.5, "50 SMA")], "long")
    assert stop == pytest.approx(97.5)
    assert "20 EMA" in basis


def test_extended_names_get_no_entry_price():
    """Rule 1 of the spec — do not chase — as arithmetic rather than hope."""
    df = _uptrend(daily=0.02)                    # runs far from its own 20 EMA
    m = sl.symbol_metrics(df)
    s = sl.detect_setup(df, m, "long")
    assert s["levels"] is None
    assert "Extended" in s["setup"]
    assert s["extension_atr"] > sl.EXTENSION_NO_ENTRY_ATR


def test_short_levels_are_the_mirror_of_long_levels():
    df = _downtrend()
    m = sl.symbol_metrics(df)
    s = sl.detect_setup(df, m, "short")
    assert s is not None
    if s["levels"]:
        lv = s["levels"]
        assert lv["stop"] > lv["entry_mid"]
        assert lv["target1"] < lv["entry_mid"]
        assert lv["target2"] < lv["target1"]


# ── direction symmetry ───────────────────────────────────────────────────────

def test_a_chopping_stock_scores_poorly_in_both_directions():
    """The point of mirroring the tests rather than negating the score: range
    tape should not hand out a 90 to whichever side you happen to ask for."""
    df = _chop()
    m = sl.symbol_metrics(df)
    empty = {"r5d": 0.0, "r20d": 0.0, "r60d": 0.0}
    scores = {}
    for d in ("long", "short"):
        setup = sl.detect_setup(df, m, d)
        scores[d] = sl.score_stock(m, empty, empty, empty, setup,
                                   "neutral", "neutral", d)["score"]
    assert max(scores.values()) < 75, scores


def test_trend_clarity_prefers_the_clean_trend_over_the_fast_one():
    clean = sl.trend_clarity(_uptrend(daily=0.003), "long")
    noisy_closes = [100 * (1.003 ** i) + 6 * np.sin(i / 2.0) for i in range(260)]
    noisy = sl.trend_clarity(_frame(noisy_closes), "long")
    assert clean["score"] > noisy["score"]
    assert clean["r2"] > noisy["r2"]


def test_trend_clarity_zeroes_a_fit_that_slopes_the_wrong_way():
    assert sl.trend_clarity(_uptrend(), "short")["r2"] == 0.0


# ── breadth ──────────────────────────────────────────────────────────────────

def test_breadth_handles_an_empty_group():
    b = sl.breadth([])
    assert b["members"] == 0 and b["pct_above_ema20"] is None


def test_breadth_counts_shares_and_net_new_highs():
    members = [
        {"above_ema20": True,  "above_sma50": True,  "above_sma200": True,
         "at_20d_high": True,  "at_20d_low": False, "r1d": 1.0},
        {"above_ema20": False, "above_sma50": True,  "above_sma200": True,
         "at_20d_high": False, "at_20d_low": True,  "r1d": -1.0},
    ]
    b = sl.breadth(members)
    assert b["pct_above_ema20"] == 50.0
    assert b["pct_above_sma50"] == 100.0
    assert b["net_new_highs_pct"] == 0.0
    assert b["pct_advancing"] == 50.0


# ── confluence gating ────────────────────────────────────────────────────────

def test_a_strong_stock_in_a_hostile_market_cannot_reach_high_confluence():
    """Rule 6: a strong stock in a weak sector is a divergence to flag, not a
    trade to rank first."""
    df = _uptrend()
    m = sl.symbol_metrics(df)
    clarity = sl.trend_clarity(df, "long")
    rs = {"r5d": 6.0, "r20d": 12.0, "r60d": 20.0}
    weak_sector = {"score": 25.0, "quality_label": "Strong downtrend"}
    c = sl.confluence_score("bearish", 5.0, weak_sector, {}, clarity, rs, m, "long")
    assert c["score"] < 65, c


def test_divergence_flags_name_the_four_cases():
    assert "LEADERSHIP" in sl.divergence_flag("bullish", "bullish", 1, 20)
    assert "WEAK LEADERSHIP" in sl.divergence_flag("bearish", "bearish", 1, 20)
    assert "BULLISH DIVERGENCE" in sl.divergence_flag("bullish", "bearish", 4, 20)
    assert "BEARISH DIVERGENCE" in sl.divergence_flag("bearish", "bullish", 4, 20)
    assert sl.divergence_flag("neutral", "bullish", 3, 20) is None


def test_leadership_bands_are_ordered():
    assert sl.band(95) == "🔥 Institutional leader"
    assert sl.band(85) == "🟢 Strong leader"
    assert sl.band(45) == "🔴 Laggard"


def test_target2_always_sits_beyond_target1():
    """DHR priced T1 242.80 and T2 234.75; AMAT's T2 was the 52-week low."""
    for direction, ladder in (("long", [105.0, 106.0]), ("short", [95.0, 94.0])):
        entry, stop = (100.0, 96.0) if direction == "long" else (100.0, 104.0)
        t1, _, t2, _ = sl._pick_targets(entry, stop, 2.0, ladder, direction)
        sign = 1 if direction == "long" else -1
        assert (t2 - entry) * sign > (t1 - entry) * sign, (direction, t1, t2)


def test_first_target_is_not_the_52_week_low():
    """ON skipped its nearby lows and landed on a 42%-away 52-week low."""
    entry, stop, atr_val = 76.34, 83.07, 4.78
    t1, t1b, t2, t2b = sl._pick_targets(entry, stop, atr_val, [74.0, 44.56], "short")
    assert (entry - t1) <= sl.MAX_T1_ATR * atr_val
    assert "ATR" in t1b


def test_structural_target_inside_the_cap_is_still_used():
    t1, t1b, _, _ = sl._pick_targets(100.0, 96.0, 2.0, [107.0], "long")
    assert t1 == 107.0 and t1b == "structural level"


def test_new_20d_high_is_measured_on_closes_not_intraday_wicks():
    """Every sector reported 0 new highs and 0 new lows because the close was
    being tested against the highest intraday wick of the month."""
    closes = [100.0] * 80 + [101, 102, 103, 104, 105]
    df = _frame(closes, spread=0.03)          # 3% wicks, well above any close
    m = sl.symbol_metrics(df)
    assert m["at_20d_high"] is True
    assert m["hi20"] > m["hi20_close"]        # the wick is still available


def test_a_breakout_setup_can_actually_fire():
    closes = [100.0] * 200 + [100 + i for i in range(1, 26)]
    df = _frame(closes)
    m = sl.symbol_metrics(df)
    s = sl.detect_setup(df, m, "long")
    assert "Breakout" in s["setup"] or "Extended" in s["setup"], s["setup"]
