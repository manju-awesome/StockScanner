"""
technical_analysis.py
======================
AI multi-timeframe technical analysis for one ticker — the "professional
swing trader / institutional technical analyst" brief, answered with real
computed data rather than the model's guesses about price history.

Two layers:
  1. build_data_pack(ticker) — pure yfinance + pandas. Monthly / Weekly /
     Daily / 4H frames, each with EMA 20/50/100/200 posture, RSI-14, MACD
     (12/26/9), ATR-14, the last swing highs/lows with an HH/HL market-
     structure read, and recent candles. Plus daily-anchored extras:
     Fibonacci retracement of the 1y range, a volume-profile approximation
     (volume-weighted price bins), and a 3-month anchored VWAP.
  2. analyze_ticker(ticker) — sends the pack to Claude (claude-opus-4-8,
     structured output) with the swing-trader brief and returns the
     10-point analysis as validated JSON for the webapp modal.

Honesty notes, also disclosed to the model and in the output:
  - Institutional demand/supply zone *data* (order flow, dark pools) isn't
    available from yfinance — zones are inferred from price/volume
    structure only.
  - The 4H frame is resampled from 60 days of hourly bars (yfinance has no
    native 4H) — enough for structure, not for long lookbacks.
  - Volume profile and VWAP are daily-bar approximations.
  - Degrades gracefully without ANTHROPIC_API_KEY: returns a clear error
    dict, never raises (same convention as the Journal AI coach).

This module produces ANALYSIS, not advice — the webapp displays it under
the user's own research workflow.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"

TIMEFRAMES = (
    # (label, yfinance period, interval, resample-to-4h)
    ("monthly", "10y", "1mo", False),
    ("weekly", "5y", "1wk", False),
    ("daily", "1y", "1d", False),
    ("four_hour", "60d", "1h", True),
)


# ── Indicator math (pandas only, no network) ─────────────────────────────────

def _ema(closes, span: int):
    return closes.ewm(span=span, adjust=False).mean()


def _rsi(closes, period: int = 14):
    delta = closes.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.where(loss != 0))
    rsi = rsi.where(loss != 0, 100.0)                    # zero losses -> 100
    return rsi.where((loss != 0) | (gain != 0), 50.0)    # flat tape -> neutral


def _macd(closes):
    macd_line = _ema(closes, 12) - _ema(closes, 26)
    signal = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal


def _atr(df, period: int = 14):
    import pandas as pd
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _swings(df, window: int = 5, keep: int = 4):
    """Last `keep` swing highs and lows (local extremes over ±window bars)."""
    highs, lows = [], []
    h, l = df["High"], df["Low"]
    for i in range(window, len(df) - window):
        seg_h = h.iloc[i - window:i + window + 1]
        seg_l = l.iloc[i - window:i + window + 1]
        if h.iloc[i] == seg_h.max():
            highs.append(round(float(h.iloc[i]), 2))
        if l.iloc[i] == seg_l.min():
            lows.append(round(float(l.iloc[i]), 2))
    return highs[-keep:], lows[-keep:]


def _structure(swing_highs: list, swing_lows: list) -> str:
    """Crude market-structure read from the last two swings of each kind."""
    def trend(seq):
        if len(seq) < 2:
            return None
        return "higher" if seq[-1] > seq[-2] else "lower"
    hh, ll = trend(swing_highs), trend(swing_lows)
    if hh == "higher" and ll == "higher":
        return "uptrend (HH + HL)"
    if hh == "lower" and ll == "lower":
        return "downtrend (LH + LL)"
    if hh and ll:
        return f"mixed ({'HH' if hh == 'higher' else 'LH'} + {'HL' if ll == 'higher' else 'LL'})"
    return "insufficient swings"


def _tf_summary(df, price: float) -> dict:
    closes = df["Close"].dropna()
    out = {"bars": len(closes)}
    for span in (20, 50, 100, 200):
        if len(closes) >= span:
            ema_val = float(_ema(closes, span).iloc[-1])
            out[f"ema{span}"] = round(ema_val, 2)
            out[f"above_ema{span}"] = price > ema_val
    rsi = _rsi(closes).dropna()
    if not rsi.empty:
        out["rsi14"] = round(float(rsi.iloc[-1]), 1)
    macd_line, signal = _macd(closes)
    if len(closes) >= 26:
        out["macd"] = round(float(macd_line.iloc[-1]), 3)
        out["macd_signal"] = round(float(signal.iloc[-1]), 3)
        out["macd_bullish"] = float(macd_line.iloc[-1]) > float(signal.iloc[-1])
    atr = _atr(df).dropna()
    if not atr.empty:
        out["atr14"] = round(float(atr.iloc[-1]), 2)
    swing_highs, swing_lows = _swings(df)
    out["swing_highs"] = swing_highs
    out["swing_lows"] = swing_lows
    out["structure"] = _structure(swing_highs, swing_lows)
    out["recent_candles"] = [
        {"o": round(float(r["Open"]), 2), "h": round(float(r["High"]), 2),
         "l": round(float(r["Low"]), 2), "c": round(float(r["Close"]), 2),
         "v": int(r["Volume"])}
        for _, r in df.tail(8).iterrows()
    ]
    return out


def _fib_levels(daily) -> dict:
    lo, hi = float(daily["Low"].min()), float(daily["High"].max())
    span = hi - lo
    return {"range_low": round(lo, 2), "range_high": round(hi, 2),
            **{f"fib_{r}": round(hi - span * r, 2)
               for r in (0.236, 0.382, 0.5, 0.618, 0.786)}}


def _volume_profile(daily, bins: int = 12) -> list[dict]:
    """Volume-weighted price bins over the daily frame; top 3 nodes."""
    import pandas as pd
    closes, vols = daily["Close"], daily["Volume"]
    cut = pd.cut(closes, bins=bins)
    grouped = vols.groupby(cut, observed=True).sum().sort_values(ascending=False)
    nodes = []
    for interval, vol in grouped.head(3).items():
        nodes.append({"price_low": round(float(interval.left), 2),
                      "price_high": round(float(interval.right), 2),
                      "volume": int(vol)})
    return nodes


def _anchored_vwap(daily, lookback: int = 63) -> float | None:
    tail = daily.tail(lookback)
    typical = (tail["High"] + tail["Low"] + tail["Close"]) / 3
    denom = float(tail["Volume"].sum())
    return round(float((typical * tail["Volume"]).sum() / denom), 2) if denom else None


# ── Data pack ────────────────────────────────────────────────────────────────

def build_data_pack(ticker: str) -> dict:
    """All computed inputs for the analysis. {"error": ...} on total failure."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    frames = {}
    for label, period, interval, resample in TIMEFRAMES:
        try:
            df = t.history(period=period, interval=interval, auto_adjust=True)
            if df is None or df.empty:
                continue
            if resample:
                df = (df.resample("4h").agg({"Open": "first", "High": "max",
                                             "Low": "min", "Close": "last",
                                             "Volume": "sum"}).dropna())
            frames[label] = df
        except Exception as e:
            log.warning("%s: %s frame fetch failed (%s)", ticker, label, e)

    daily = frames.get("daily")
    if daily is None or daily.empty:
        return {"error": f"no price history available for {ticker}"}

    price = round(float(daily["Close"].iloc[-1]), 2)
    pack = {
        "ticker": ticker,
        "current_price": price,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "timeframes": {label: _tf_summary(df, price) for label, df in frames.items()},
        "fibonacci_1y": _fib_levels(daily),
        "volume_profile_1y_top_nodes": _volume_profile(daily),
        "anchored_vwap_3mo": _anchored_vwap(daily),
        "data_notes": [
            "4H frame resampled from 60d of hourly bars",
            "volume profile and VWAP are daily-bar approximations",
            "no institutional order-flow data — infer zones from price/volume structure only",
        ],
    }
    return pack


# ── Claude analysis ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a professional swing trader and institutional \
technical analyst. You are given COMPUTED multi-timeframe data (Monthly, \
Weekly, Daily, 4H) for one stock: trend structure and swing points, EMAs \
(20/50/100/200), RSI and MACD, ATR, Fibonacci retracement of the 1-year \
range, a volume-profile approximation, anchored VWAP, and recent candles.

Identify the highest-probability BUY ZONE from this data. Ground every level \
you give in the numbers provided — do not invent prices that aren't anchored \
to a supplied level (EMA, swing, Fib, volume node, VWAP). Note that \
institutional demand/supply zones must be inferred from price/volume \
structure; you have no order-flow data, so present such zones as inferences.

Do NOT recommend buying immediately unless the confirmations genuinely \
align. If the stock is extended or the data is bearish, your verdict is \
WAIT with the better entry price. Be conservative with the probability \
score: above 70 should be rare and require multi-timeframe alignment."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "current_trend": {
            "type": "object",
            "properties": {
                "monthly": {"type": "string"},
                "weekly": {"type": "string"},
                "daily": {"type": "string"},
                "four_hour": {"type": "string"},
                "overall": {"type": "string", "enum": ["Bullish", "Bearish", "Sideways"]},
            },
            "required": ["monthly", "weekly", "daily", "four_hour", "overall"],
            "additionalProperties": False,
        },
        "buy_zone": {
            "type": "object",
            "properties": {"low": {"type": "number"}, "high": {"type": "number"}},
            "required": ["low", "high"], "additionalProperties": False,
        },
        "ideal_entry": {"type": "number"},
        "confirmations_required": {"type": "array", "items": {"type": "string"}},
        "stop_loss": {"type": "number"},
        "targets": {
            "type": "object",
            "properties": {"t1": {"type": "number"}, "t2": {"type": "number"},
                           "t3": {"type": "number"}},
            "required": ["t1", "t2", "t3"], "additionalProperties": False,
        },
        "risk_reward": {"type": "string"},
        "probability_score": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["BUY_ZONE_VALID", "WAIT"]},
        "wait_reason": {"type": "string"},
        "better_entry_price": {"type": ["number", "null"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "invalidations": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["current_trend", "buy_zone", "ideal_entry",
                 "confirmations_required", "stop_loss", "targets",
                 "risk_reward", "probability_score", "verdict", "wait_reason",
                 "better_entry_price", "reasons", "invalidations", "summary"],
    "additionalProperties": False,
}


def analyze_ticker(ticker: str) -> dict:
    """Full pipeline: data pack -> Claude -> validated 10-point analysis.
    Never raises; {"error": ...} on any failure."""
    ticker = ticker.upper().strip()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("sk-ant-your"):   # unset OR .env.example placeholder
        return {"error": "ANTHROPIC_API_KEY is not set — add a real key to .env "
                         "to enable AI technical analysis (see .env.example)."}

    pack = build_data_pack(ticker)
    if pack.get("error"):
        return pack

    try:
        import anthropic
        client = anthropic.Anthropic()
        # streaming: the analysis + adaptive thinking can run long enough to
        # risk request timeouts on a plain create()
        with client.messages.stream(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema",
                                      "schema": OUTPUT_SCHEMA}},
            messages=[{
                "role": "user",
                "content": ("Analyze this stock and produce the buy-zone "
                            "assessment.\n\nDATA:\n" + json.dumps(pack)),
            }],
        ) as stream:
            msg = stream.get_final_message()

        if msg.stop_reason == "refusal":
            return {"error": "the analysis request was declined"}
        text = next(b.text for b in msg.content if b.type == "text")
        result = json.loads(text)
        result["ticker"] = ticker
        result["current_price"] = pack["current_price"]
        result["generated_at"] = pack["as_of"]
        result["model"] = MODEL
        result["data_notes"] = pack["data_notes"]
        return result
    except Exception as e:
        log.warning("%s: AI technical analysis failed (%s)", ticker, e)
        return {"error": f"analysis failed: {e}"}
