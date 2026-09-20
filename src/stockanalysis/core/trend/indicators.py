"""
indicators.py — the chart facts the trend engine reasons over
=============================================================
Pure function of one daily OHLCV frame. No network, no clock: hand it bars
sliced to any historical date and every number is point-in-time correct,
the same contract core/metrics.compute_daily_metrics() and
core/key_levels.py already keep.

Six averages, and why these six
-------------------------------
    SMA 200   REGIME    — is this a bull or a bear market in this name
    SMA 50    TREND     — the intermediate swing
    EMA 21    MOMENTUM  — what a pullback in an uptrend reverts to
    EMA 8     TRIGGER   — the short-term line price rides in a strong move
    SMA 20    reference — the band midline most chart readers watch
    SMA 5     very short — where price closed this week, smoothed

Slope is the whole point
------------------------
Price above a 200-day average that is FALLING is a different animal from
price above one that is RISING, and no amount of price-vs-MA logic can tell
them apart. So every average carries its direction, not just its level.

Slope is measured two ways and they are not interchangeable:

    slope_pct   the average's own percentage change over the window. This
                is for READING — it is comparable between a $12 stock and
                a $900 one, and it is what gets displayed.

    slope_atr5  the same move divided by ATR20 and rescaled to "ATR per 5
                sessions". This is for DECIDING. A 200-day average drifting
                0.3% in a week is a real turn for a 2%-ATR utility and pure
                noise for a 6%-ATR software name; a percentage threshold
                calls those two identical and is wrong about one of them.

The rising/flat/falling verdict therefore comes from slope_atr5 against a
per-average threshold. The thresholds differ by average because they must:
an 8 EMA moves roughly forty times as far as a 200 SMA for the same trend,
so one shared number would read every 200 SMA as flat forever.

Extension is measured in ATR, never percent, for the same reason — see
core/shortside/extension.py, which made this argument first and whose
thresholds these are deliberately consistent with.
"""

from __future__ import annotations

import pandas as pd

from stockanalysis.core.longterm._common import f

# name -> (kind, length). Order is the bullish hierarchy, fastest first;
# alignment checks walk this list, so the order here IS the definition of
# "properly ordered".
MA_SPECS = (
    ("SMA5",   "sma",   5),
    ("EMA8",   "ema",   8),
    ("SMA20",  "sma",  20),
    ("EMA21",  "ema",  21),
    ("SMA50",  "sma",  50),
    ("SMA200", "sma", 200),
)

# The four averages the regime logic actually gates on. SMA5/SMA20 are
# reference columns: they are reported, never decisive, because adding two
# more gates to a ladder that already reads 8/21/50/200 would reject names
# on noise without changing what the structure says.
CORE_MAS = ("EMA8", "EMA21", "SMA50", "SMA200")

SLOPE_WINDOWS = (5, 10)

# ATR-per-5-sessions above which an average counts as rising (below the
# negative of which, falling). Scaled by how far each average can physically
# move: an N-period average absorbs a price move at roughly 1/N per bar, so
# the fast lines clear a far higher bar than the slow ones.
RISING_ATR5 = {
    "SMA5":   0.45,
    "EMA8":   0.32,
    "SMA20":  0.20,
    "EMA21":  0.18,
    "SMA50":  0.09,
    "SMA200": 0.025,
}

# A 200 SMA falling faster than this is "strongly declining" — the one
# condition section 10 says must forbid a high Trend Quality score no
# matter how good everything else looks.
STRONG_DECLINE_ATR5 = -0.06

# ATR multiples above an average at which price is EXTENDED from it, and
# the multiple at which the move is parabolic. Distances differ by average
# because their normal distance from price differs: 2 ATR above the 8 EMA
# is stretched, 2 ATR above the 200 MA is an ordinary Tuesday in any uptrend.
EXTENDED_ATR = {"EMA8": 1.5, "EMA21": 2.5, "SMA50": 4.0, "SMA200": 7.0}
PARABOLIC_ATR = {"EMA8": 3.0, "EMA21": 4.5, "SMA50": 6.5, "SMA200": 10.0}

SWING_WINDOW = 3          # bars each side for a fractal pivot
STRUCTURE_LOOKBACK = 120  # ≈6 months — the swing structure that matters
RECENT_CROSS = 10         # sessions inside which an MA cross is "recent"

# Price structure, from the last two swing highs and the last two swing lows.
HH_HL = "HH/HL"           # bullish
HH_LL = "HH/LL"           # volatile / transition
LH_LL = "LH/LL"           # bearish
LH_HL = "LH/HL"           # compression / transition
UNKNOWN = "unknown"


def _series(close: pd.Series, kind: str, length: int) -> pd.Series:
    if kind == "ema":
        return close.ewm(span=length, adjust=False).mean()
    return close.rolling(length).mean()


def _at(series: pd.Series, offset: int):
    """Value `offset` bars back, or None if the series is too short."""
    if series is None or len(series) <= offset:
        return None
    return f(series.iloc[-1 - offset])


def slope_pct(series: pd.Series, lookback: int):
    """The average's own % change over `lookback` sessions.

    A percentage of its own level, not a raw price slope, so the number
    means the same thing on a $12 stock and a $900 one.
    """
    now, then = _at(series, 0), _at(series, lookback)
    if now is None or then is None or then == 0:
        return None
    return round((now / then - 1) * 100, 2)


def slope_atr5(series: pd.Series, lookback: int, atr):
    """The same move in ATR units, rescaled to ATR per 5 sessions.

    Rescaling is what lets the 5-day and 10-day windows share one threshold
    table. Without it, a 10-day reading would clear a 5-day bar just for
    covering twice the ground at the same speed.
    """
    now, then, a = _at(series, 0), _at(series, lookback), f(atr)
    if now is None or then is None or a is None or a <= 0 or lookback <= 0:
        return None
    return round((now - then) / a * (5.0 / lookback), 3)


def slope_state(atr5, key: str) -> str:
    """rising / flat / falling — or 'unknown' when the slope is not measured.

    'unknown' is never silently read as 'flat': a name with 180 bars of
    history has no 200 SMA slope, and treating that absence as "the trend
    is flat" would let it pass gates it has not earned.
    """
    if atr5 is None:
        return "unknown"
    bar = RISING_ATR5.get(key, 0.1)
    if atr5 >= bar:
        return "rising"
    if atr5 <= -bar:
        return "falling"
    return "flat"


def swing_points(daily: pd.DataFrame, window: int = SWING_WINDOW,
                 lookback: int = STRUCTURE_LOOKBACK):
    """Fractal swing highs and lows: a bar higher (lower) than `window` bars
    on both sides. Returns ([highs], [lows]) as (index, price), oldest first.

    The last `window` bars can never be confirmed pivots — there is not yet
    a right shoulder — so the newest structure is always a few days behind
    price. That lag is correct: a high is not a swing high until price has
    turned away from it.
    """
    frame = daily.tail(lookback)
    highs, lows = [], []
    h, l = frame["High"].to_numpy(), frame["Low"].to_numpy()
    for i in range(window, len(frame) - window):
        left_h, right_h = h[i - window:i], h[i + 1:i + 1 + window]
        if h[i] > left_h.max() and h[i] > right_h.max():
            highs.append((i, float(h[i])))
        left_l, right_l = l[i - window:i], l[i + 1:i + 1 + window]
        if l[i] < left_l.min() and l[i] < right_l.min():
            lows.append((i, float(l[i])))
    return highs, lows


def price_structure(daily: pd.DataFrame) -> dict:
    """Higher highs / lower lows, from confirmed swing pivots.

    Moving averages describe the average of price; this describes what price
    itself did. When the two agree the read is high confidence, and when
    they disagree that disagreement is the signal — a bullish MA stack
    printing lower highs is a trend running out of buyers.
    """
    highs, lows = swing_points(daily)
    out = {"structure": UNKNOWN, "swing_highs": len(highs),
           "swing_lows": len(lows), "last_swing_high": None,
           "last_swing_low": None, "higher_highs": None, "higher_lows": None}
    if len(highs) >= 2:
        out["higher_highs"] = highs[-1][1] > highs[-2][1]
        out["last_swing_high"] = round(highs[-1][1], 2)
    if len(lows) >= 2:
        out["higher_lows"] = lows[-1][1] > lows[-2][1]
        out["last_swing_low"] = round(lows[-1][1], 2)
    hh, hl = out["higher_highs"], out["higher_lows"]
    if hh is None or hl is None:
        return out
    out["structure"] = (HH_HL if (hh and hl) else
                        HH_LL if (hh and not hl) else
                        LH_HL if (not hh and hl) else LH_LL)
    return out


def _cross_age(fast: pd.Series, slow: pd.Series, lookback: int = 60):
    """Sessions since fast last crossed slow, and which way. (None, None)
    if they have not crossed inside `lookback` — a cross older than that is
    trend, not an event.
    """
    if fast is None or slow is None:
        return None, None
    n = min(len(fast), len(slow), lookback + 1)
    if n < 2:
        return None, None
    fa, sl = fast.iloc[-n:].to_numpy(), slow.iloc[-n:].to_numpy()
    above = fa > sl
    for age in range(0, n - 1):
        i = len(above) - 1 - age
        if above[i] != above[i - 1]:
            return age, ("up" if above[i] else "down")
    return None, None


def compute(daily: pd.DataFrame, current_price: float | None = None,
            atr: float | None = None, rvol: float | None = None) -> dict:
    """Every trend fact derivable from `daily`.

    daily         : yfinance-layout OHLCV, ≥1y so the 200 SMA and its slope
                    exist. Shorter frames are not an error — the averages
                    that cannot be computed come back None and every
                    consumer reads None as "not measured".
    current_price : live quote; falls back to the last close.
    atr / rvol    : pass the scan's own values to keep one number in the
                    system; both are computed here when omitted.

    Returns {} on an empty frame. Never raises on a short one.
    """
    if daily is None or daily.empty:
        return {}

    daily = daily.copy()
    for col in ("Open", "High", "Low", "Close"):
        daily[col] = daily[col].ffill()
    daily["Volume"] = daily["Volume"].fillna(0)
    close = daily["Close"]

    price = f(current_price) or f(close.iloc[-1])
    out: dict = {"price": round(price, 2) if price else None,
                 "bars": len(daily)}

    # ── ATR first: every slope and distance below is normalised by it ─────
    if atr is None:
        from stockanalysis.core.metrics import calculate_atr
        atr = f(calculate_atr(daily).iloc[-1]) if len(daily) >= 20 else None
    atr = f(atr)
    out["atr"] = round(atr, 2) if atr else None
    out["atr_pct"] = round(atr / price * 100, 2) if (atr and price) else None

    # ── The six averages, their levels, slopes and distances ─────────────
    series: dict[str, pd.Series] = {}
    levels, slopes, dists, dist_atr, states = {}, {}, {}, {}, {}
    for key, kind, length in MA_SPECS:
        if len(daily) < length:
            series[key] = None
            levels[key] = None
            states[key] = "unknown"
            for w in SLOPE_WINDOWS:
                slopes[f"{key}_{w}d_pct"] = None
                slopes[f"{key}_{w}d_atr5"] = None
            dists[key] = dist_atr[key] = None
            continue
        s = _series(close, kind, length)
        series[key] = s
        lvl = f(s.iloc[-1])
        levels[key] = round(lvl, 2) if lvl is not None else None
        for w in SLOPE_WINDOWS:
            slopes[f"{key}_{w}d_pct"] = slope_pct(s, w)
            slopes[f"{key}_{w}d_atr5"] = slope_atr5(s, w, atr)
        # The 5-day window sets the verdict; 10-day is context. Five sessions
        # is the shortest window that is not one bad day, and waiting ten to
        # call a turn means calling it after the move.
        states[key] = slope_state(slopes[f"{key}_5d_atr5"], key)
        if lvl and price:
            dists[key] = round((price / lvl - 1) * 100, 2)
            dist_atr[key] = round((price - lvl) / atr, 2) if atr else None

    out["ma"] = levels
    out["slope"] = slopes
    out["slope_state"] = states
    out["dist_pct"] = dists
    out["dist_atr"] = dist_atr
    out["series"] = series          # dropped before serialisation

    # Closes below the 200 SMA in the last ten sessions. A break of the
    # 200 is only a breakdown once price has stayed there — one wick
    # through it and back is noise, and the count is what tells them apart.
    s200 = series.get("SMA200")
    if s200 is not None and len(daily) >= 210:
        tail = min(10, len(daily))
        below = (close.iloc[-tail:] < s200.iloc[-tail:])
        out["closes_below_200_10d"] = int(below.sum())
    else:
        out["closes_below_200_10d"] = None

    # Was there anything to reverse FROM? A "confirmed bullish reversal" on
    # a name that has been above its 200 SMA all year is not a reversal, it
    # is an uptrend being described twice — and the reversal columns are
    # worthless if they fire on every healthy trend. Sixty sessions is the
    # window: a quarter is long enough to contain the weakness a real
    # reversal turns away from, short enough that last year's bear market
    # does not still count as context.
    # Counted, not a yes/no: NVDA printed exactly one close under its 200
    # SMA in this window and a boolean read that single wick as "this name
    # was in a downtrend", which is how a year-old uptrend ends up labelled
    # a confirmed reversal. The consumer applies its own threshold.
    if s200 is not None and len(daily) >= 260:
        w = min(60, len(daily))
        below60 = (close.iloc[-w:] < s200.iloc[-w:])
        out["closes_below_200_60d"] = int(below60.sum())
        out["closes_above_200_60d"] = int(w - below60.sum())
    else:
        # Unknown, not zero. A short frame must not silently assert that a
        # name has never been weak — that would let reversals fire freely on
        # exactly the names with the least evidence behind them.
        out["closes_below_200_60d"] = None
        out["closes_above_200_60d"] = None

    # Sessions under the 50 SMA in the last thirty. "Recovering above the
    # 50" and "above the 50" are different claims, and only the count can
    # tell them apart — without it an early-reversal filter returns every
    # established leader, which is the opposite of early.
    s50 = series.get("SMA50")
    if s50 is not None and len(daily) >= 80:
        w = min(30, len(daily))
        out["closes_below_50_30d"] = int((close.iloc[-w:] < s50.iloc[-w:]).sum())
    else:
        out["closes_below_50_30d"] = None

    # ── Alignment: is the stack properly ordered ─────────────────────────
    stack = [price] + [levels[k] for k, _, _ in MA_SPECS if k in CORE_MAS]
    known = [v for v in stack if v is not None]
    if len(known) < len(stack):
        out["alignment"] = "MIXED_ALIGNMENT"
        out["alignment_known"] = False
    else:
        out["alignment_known"] = True
        if all(a > b for a, b in zip(stack, stack[1:])):
            out["alignment"] = "FULL_BULLISH_ALIGNMENT"
        elif all(a < b for a, b in zip(stack, stack[1:])):
            out["alignment"] = "FULL_BEARISH_ALIGNMENT"
        else:
            out["alignment"] = "MIXED_ALIGNMENT"

    # How much of the bullish ordering holds, for the score's partial credit.
    pairs = list(zip(stack, stack[1:]))
    good = sum(1 for a, b in pairs if a is not None and b is not None and a > b)
    countable = sum(1 for a, b in pairs if a is not None and b is not None)
    out["alignment_pct"] = round(good / countable * 100) if countable else None

    # ── Crosses: the events, separately from the state ───────────────────
    age_8_21, dir_8_21 = _cross_age(series["EMA8"], series["EMA21"])
    age_50_200, dir_50_200 = _cross_age(series["SMA50"], series["SMA200"])
    out["cross"] = {
        "ema8_ema21_age": age_8_21, "ema8_ema21_dir": dir_8_21,
        "ema8_over_ema21": (levels["EMA8"] > levels["EMA21"]
                            if levels["EMA8"] and levels["EMA21"] else None),
        "sma50_sma200_age": age_50_200, "sma50_sma200_dir": dir_50_200,
        "sma50_over_sma200": (levels["SMA50"] > levels["SMA200"]
                              if levels["SMA50"] and levels["SMA200"] else None),
        "recent_bull_cross": bool(age_8_21 is not None
                                  and age_8_21 <= RECENT_CROSS
                                  and dir_8_21 == "up"),
        "recent_bear_cross": bool(age_8_21 is not None
                                  and age_8_21 <= RECENT_CROSS
                                  and dir_8_21 == "down"),
    }

    # ── Price structure ──────────────────────────────────────────────────
    out.update(price_structure(daily))

    # ── Ranges ───────────────────────────────────────────────────────────
    hi20 = f(daily["High"].tail(20).max())
    lo20 = f(daily["Low"].tail(20).min())
    hi52 = f(daily["High"].max())
    lo52 = f(daily["Low"].min())
    out["high_20d"] = round(hi20, 2) if hi20 else None
    out["low_20d"] = round(lo20, 2) if lo20 else None
    out["high_52w"] = round(hi52, 2) if hi52 else None
    out["low_52w"] = round(lo52, 2) if lo52 else None
    out["pct_below_52w_high"] = (round((price / hi52 - 1) * 100, 1)
                                 if (price and hi52) else None)
    # Strictly above the prior 20 sessions, not this one: including today's
    # own high makes "at a 20-day high" true on every up day.
    prior20 = f(daily["High"].iloc[-21:-1].max()) if len(daily) >= 21 else None
    out["at_20d_high"] = bool(price and prior20 and price >= prior20)
    prior20_lo = f(daily["Low"].iloc[-21:-1].min()) if len(daily) >= 21 else None
    out["at_20d_low"] = bool(price and prior20_lo and price <= prior20_lo)

    # ── Volume ───────────────────────────────────────────────────────────
    avg50 = f(daily["Volume"].rolling(50).mean().iloc[-1]) if len(daily) >= 50 else None
    cur_vol = f(daily["Volume"].iloc[-1])
    out["rvol"] = (round(rvol, 2) if rvol is not None else
                   (round(cur_vol / avg50, 2) if (avg50 and cur_vol) else None))
    vol5 = f(daily["Volume"].tail(5).mean())
    vol20 = f(daily["Volume"].tail(20).mean())
    out["vol_trend"] = (round(vol5 / vol20, 2) if (vol5 and vol20) else None)
    out["vol_expanding"] = (out["vol_trend"] > 1.15
                            if out["vol_trend"] is not None else None)

    # Volume on the down days of the last five sessions, against the 20-day
    # average. This is what separates a healthy pullback from distribution:
    # the same 3% dip is a gift on drying volume and a warning on swelling
    # volume, and no price-only rule can tell them apart.
    last5 = daily.tail(5)
    red = last5[last5["Close"] < last5["Open"]]
    out["down_vol_ratio"] = (round(f(red["Volume"].mean()) / vol20, 2)
                             if (not red.empty and vol20) else None)

    # ── ADX / RSI ────────────────────────────────────────────────────────
    from stockanalysis.core.metrics import calculate_adx, calculate_rsi
    try:
        adx = calculate_adx(daily["High"], daily["Low"], close).dropna()
        out["adx"] = round(f(adx.iloc[-1]), 1) if len(adx) else None
    except Exception:
        out["adx"] = None
    try:
        rsi = calculate_rsi(close).dropna()
        out["rsi"] = round(f(rsi.iloc[-1]), 1) if len(rsi) else None
    except Exception:
        out["rsi"] = None

    # ── Extension, in ATR ────────────────────────────────────────────────
    flags = []
    for key in ("EMA8", "EMA21", "SMA50"):
        mult = dist_atr.get(key)
        if mult is not None and mult >= EXTENDED_ATR[key]:
            flags.append(f"EXTENDED_FROM_{'8EMA' if key == 'EMA8' else '21EMA' if key == 'EMA21' else '50SMA'}")
    out["extension_flags"] = flags
    m21 = dist_atr.get("EMA21")
    out["extension_status"] = (
        "UNKNOWN" if m21 is None else
        "PARABOLIC" if m21 >= PARABOLIC_ATR["EMA21"] else
        "VERY_EXTENDED" if m21 >= EXTENDED_ATR["EMA21"] else
        "EXTENDED" if m21 >= EXTENDED_ATR["EMA21"] * 0.6 else
        "NORMAL" if m21 >= -1.0 else "BELOW_TREND")
    return out
