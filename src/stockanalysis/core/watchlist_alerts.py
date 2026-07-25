"""
watchlist_alerts.py
====================
The "Watchlist Alert" monitor from the master prompt: scan watchlist tickers
and alert only when a condition is actually true, once, until it changes.

Every condition below is a plain boolean over already-computed scan-row
fields (support/resistance/breakout/breakdown/gap/volume from
core.metrics + core.key_levels — no new fetch needed) plus one fresh
lightweight MACD calc (not computed anywhere else in the pipeline). Each
condition's "currently true" state doubles as the alerts.py dedup key: the
engine fires once when a condition turns true and goes quiet again once it
turns false, which is exactly "only notify once until condition changes"
for both a standing state (RSI oversold) and a one-time event (a crossover,
modeled as "currently on the bullish side of the cross").

Deliberately NOT implemented — no free data source exists for these, and a
placeholder would just be more noise:
  - unusual options flow
  - institutional buying
See core/alerts.py's module docstring for the same call.
"""

from __future__ import annotations

import yfinance as yf

from stockanalysis.core import alerts

DEFAULT_WATCHLIST_NAME = "watchlist"

GAP_THRESHOLD_PCT = 3.0
VOLUME_SPIKE_RVOL = 2.0
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
NEAR_LEVEL_PCT = 1.5  # "price reaches support/resistance" = within this % and not yet broken


def _macd_state(ticker: str) -> str | None:
    """Bullish if the MACD line (EMA12-EMA26) is above its signal line
    (EMA9 of MACD) on the latest daily bar, bearish if below, None if there
    isn't enough history. A fresh fetch — MACD isn't computed anywhere else
    in the scan pipeline (core.metrics only has 8/21 EMA and 50/200 SMA)."""
    try:
        closes = yf.Ticker(ticker).history(period="6mo", interval="1d")["Close"].dropna()
        if len(closes) < 35:
            return None
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        return "bullish" if macd_line.iloc[-1] > signal_line.iloc[-1] else "bearish"
    except Exception:
        return None


def _num(row: dict, key: str) -> float | None:
    v = row.get(key)
    return float(v) if isinstance(v, (int, float)) else None


# ─────────────────────────────────────────────────────────────────────────────
# CONDITIONS — each returns an Alert dict if true for this row, else None.
# ─────────────────────────────────────────────────────────────────────────────

def _support_touch(row: dict) -> dict | None:
    d = _num(row, "Dist_to_Support%")
    if d is None or not (0 <= d <= NEAR_LEVEL_PCT):
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:support_touch", category="watchlist", priority="MEDIUM",
        ticker=ticker, headline=f"is testing support ({d:.1f}% away)",
        why_it_matters="Price is approaching a level where buyers have previously stepped in.",
        expected_impact="Possible bounce if support holds, or a breakdown if it fails.",
        suggested_action="Watch for a reversal candle or volume confirmation before acting.",
        confidence=55, time_sensitivity="next 1-2 sessions",
        supporting_data={"dist_to_support_pct": d, "S1": row.get("S1")})


def _resistance_touch(row: dict) -> dict | None:
    d = _num(row, "Dist_to_Resistance%")
    if d is None or not (0 <= d <= NEAR_LEVEL_PCT):
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:resistance_touch", category="watchlist", priority="MEDIUM",
        ticker=ticker, headline=f"is testing resistance ({d:.1f}% away)",
        why_it_matters="Price is approaching a level that has previously capped upside.",
        expected_impact="Possible breakout if resistance breaks, or a pullback if it holds.",
        suggested_action="Watch for a breakout with volume before chasing.",
        confidence=55, time_sensitivity="next 1-2 sessions",
        supporting_data={"dist_to_resistance_pct": d, "R1": row.get("R1")})


def _breakout(row: dict) -> dict | None:
    d = _num(row, "Dist_to_Resistance%")
    if d is None or d >= 0:
        return None
    ticker = row["Ticker"]
    prob = row.get("Breakout_Probability")
    return alerts.make_alert(
        dedup_key=f"{ticker}:breakout", category="watchlist", priority="HIGH",
        ticker=ticker, headline="broke out above resistance",
        why_it_matters="Price cleared a level that previously capped upside.",
        expected_impact="Follow-through continuation is more likely while it holds above the level.",
        suggested_action="Consider an entry on a pullback-and-hold, respecting a stop back below the level.",
        confidence=int(prob) if isinstance(prob, (int, float)) else 60,
        time_sensitivity="today", supporting_data={"dist_to_resistance_pct": d, "R1": row.get("R1")})


def _breakdown(row: dict) -> dict | None:
    d = _num(row, "Dist_to_Support%")
    if d is None or d >= 0:
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:breakdown", category="watchlist", priority="HIGH",
        ticker=ticker, headline="broke down below support",
        why_it_matters="Price gave up a level that previously held as a floor.",
        expected_impact="Further downside is more likely while price stays below the level.",
        suggested_action="Reassess the thesis; consider reducing or tightening a stop on existing exposure.",
        confidence=60, time_sensitivity="today",
        supporting_data={"dist_to_support_pct": d, "S1": row.get("S1")})


def _gap(row: dict) -> dict | None:
    g = _num(row, "Gap_Now%")
    if g is None or abs(g) < GAP_THRESHOLD_PCT:
        return None
    ticker = row["Ticker"]
    direction = "up" if g > 0 else "down"
    return alerts.make_alert(
        dedup_key=f"{ticker}:gap", category="watchlist", priority="HIGH",
        ticker=ticker, headline=f"gapped {direction} {abs(g):.1f}%",
        why_it_matters="A gap this size usually means new information hit the stock overnight.",
        expected_impact="Elevated volatility for the session; gaps often partially fill.",
        suggested_action="Check for a news catalyst before trading the gap in either direction.",
        confidence=70, time_sensitivity="today", supporting_data={"gap_pct": g})


def _volume_spike(row: dict) -> dict | None:
    rvol = _num(row, "RVOL")
    if rvol is None or rvol < VOLUME_SPIKE_RVOL:
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:volume_spike", category="watchlist", priority="MEDIUM",
        ticker=ticker, headline=f"volume is {rvol:.1f}x average",
        why_it_matters="Unusually high volume often accompanies a real move, not noise.",
        expected_impact="Whatever move is happening today is more likely to be meaningful.",
        suggested_action="Check news/price action before treating this as a standalone signal.",
        confidence=55, time_sensitivity="today", supporting_data={"rvol": rvol})


def _rsi_oversold(row: dict) -> dict | None:
    rsi = _num(row, "RSI_14")
    if rsi is None or rsi > RSI_OVERSOLD:
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:rsi_oversold", category="watchlist", priority="MEDIUM",
        ticker=ticker, headline=f"RSI oversold at {rsi:.0f}",
        why_it_matters="RSI this low often marks a stretched short-term move.",
        expected_impact="Elevated odds of a short-term bounce, not a trend reversal by itself.",
        suggested_action="Watch for a reversal signal rather than buying oversold alone.",
        confidence=50, time_sensitivity="next 1-2 sessions", supporting_data={"rsi_14": rsi})


def _rsi_overbought(row: dict) -> dict | None:
    rsi = _num(row, "RSI_14")
    if rsi is None or rsi < RSI_OVERBOUGHT:
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:rsi_overbought", category="watchlist", priority="MEDIUM",
        ticker=ticker, headline=f"RSI overbought at {rsi:.0f}",
        why_it_matters="RSI this high often marks a stretched short-term move.",
        expected_impact="Elevated odds of a short-term pullback, not a trend reversal by itself.",
        suggested_action="Consider taking partial profits or tightening a stop if you're long.",
        confidence=50, time_sensitivity="next 1-2 sessions", supporting_data={"rsi_14": rsi})


def _ema_crossover(row: dict) -> dict | None:
    """Proxy for "EMA crossover": price currently above its 50-day MA. The
    scan pipeline only computes 50/200 SMA (not a 20/50 EMA pair), so this
    is the closest available signal, not a literal EMA cross."""
    pct = _num(row, "Price_vs_50MA%")
    if pct is None or pct <= 0:
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:above_50ma", category="watchlist", priority="LOW",
        ticker=ticker, headline=f"is above its 50-day average ({pct:+.1f}%)",
        why_it_matters="Trend-following systems generally favor longs while price holds above this average.",
        expected_impact="Mildly bullish backdrop while this holds.",
        suggested_action="No action by itself — context for other signals.",
        confidence=40, time_sensitivity="ongoing", supporting_data={"price_vs_50ma_pct": pct})


def _macd_crossover(row: dict) -> dict | None:
    ticker = row["Ticker"]
    state = _macd_state(ticker)
    if state is None:
        return None
    return alerts.make_alert(
        dedup_key=f"{ticker}:macd_{state}", category="watchlist",
        priority="MEDIUM" if state == "bullish" else "LOW",
        ticker=ticker, headline=f"MACD is {state}",
        why_it_matters=f"MACD line is {'above' if state == 'bullish' else 'below'} its signal line.",
        expected_impact="Momentum backdrop is " + ("supportive of upside." if state == "bullish" else "supportive of downside."),
        suggested_action="Combine with price action before acting — MACD lags.",
        confidence=45, time_sensitivity="ongoing", supporting_data={"macd_state": state})


WEEK52_TOUCH_PCT = 0.3  # within this % of the recorded 52-week extreme counts as "hitting" it


def _52w_high(row: dict) -> dict | None:
    """Dist_52W_High% >= 0 means the live price is at/above the highest daily
    High seen over the trailing year (core.metrics computes both from the
    same fetch) — i.e. the stock just made a fresh 52-week high."""
    dist = _num(row, "Dist_52W_High%")
    if dist is None or dist < -WEEK52_TOUCH_PCT:
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:52w_high", category="watchlist", priority="HIGH",
        ticker=ticker, headline="hit a new 52-week high",
        why_it_matters="Price is trading at or above its highest level in the past year — "
                       "no overhead supply left to absorb.",
        expected_impact="Momentum names often continue trending once they clear the prior "
                        "52-week ceiling.",
        suggested_action="Consider a breakout entry with a stop below the prior high, "
                         "or trail a stop on an existing position.",
        confidence=65, time_sensitivity="today",
        supporting_data={"dist_52w_high_pct": dist, "52w_high": row.get("52W High"),
                         "price": row.get("Current Price")})


def _52w_low(row: dict) -> dict | None:
    """Pct_From_52W_Low% <= 0 means the live price is at/below the lowest
    daily Low seen over the trailing year — a fresh 52-week low."""
    pct_low = _num(row, "Pct_From_52W_Low%")
    if pct_low is None or pct_low > WEEK52_TOUCH_PCT:
        return None
    ticker = row["Ticker"]
    return alerts.make_alert(
        dedup_key=f"{ticker}:52w_low", category="watchlist", priority="HIGH",
        ticker=ticker, headline="hit a new 52-week low",
        why_it_matters="Price is trading at or below its lowest level in the past year — "
                       "the prior floor has given way.",
        expected_impact="Downside can accelerate once a 52-week low breaks, as stops and "
                        "technical selling often cluster there.",
        suggested_action="Reassess the thesis on any long position; a short/put idea should "
                         "confirm with volume before acting.",
        confidence=60, time_sensitivity="today",
        supporting_data={"pct_from_52w_low_pct": pct_low, "52w_low": row.get("52W Low"),
                         "price": row.get("Current Price")})


PUT_SCORE_ALERT_MIN = 8      # exclusive — Put_Score must EXCEED this to alert


def _put_score_high(row: dict) -> dict | None:
    """Put_Score > PUT_SCORE_ALERT_MIN: a rare, high-conviction bearish
    setup (raw additive score, observed range ≈ -1..10; disqualified names
    are forced to 0 upstream, so a high score is never a disqualified one).
    HIGH priority — emails and lands in the ALERT_TICKERS watchlist."""
    score = _num(row, "Put_Score")
    if score is None or score <= PUT_SCORE_ALERT_MIN:
        return None
    ticker = row["Ticker"]
    reason = row.get("Put_Reason") or "multiple bearish factors aligned"
    return alerts.make_alert(
        dedup_key=f"{ticker}:put_score_high", category="put_setup", priority="HIGH",
        ticker=ticker, headline=f"put setup score {score:.0f} — high-conviction bearish",
        why_it_matters=f"Put screen factors stacking up: {reason}",
        expected_impact="Downside continuation is the base case while the score holds this high.",
        suggested_action="Review the put candidate (strike/expiry per the scan report); "
                         "if holding the stock, reassess or hedge.",
        confidence=min(95, 50 + int(score) * 5), time_sensitivity="today",
        supporting_data={"put_score": score, "put_reason": row.get("Put_Reason"),
                         "put_candidate": row.get("Put_Candidate"),
                         "price": row.get("Current Price")})


CALL_SCORE_ALERT_MIN = 8     # exclusive — a stricter bar than the module's
                             # own STRONG tier (Call_Score >= 7), same cut as puts


def _call_score_high(row: dict) -> dict | None:
    """Call_Score > CALL_SCORE_ALERT_MIN: high-conviction turnaround call
    setup (additive score, STRONG tier starts at 7; disqualified names are
    zeroed upstream). HIGH priority — emails and lands in ALERT_TICKERS."""
    score = _num(row, "Call_Score")
    if score is None or score <= CALL_SCORE_ALERT_MIN:
        return None
    ticker = row["Ticker"]
    reason = row.get("Call_Reason") or "multiple turnaround factors aligned"
    return alerts.make_alert(
        dedup_key=f"{ticker}:call_score_high", category="call_setup", priority="HIGH",
        ticker=ticker, headline=f"call setup score {score:.0f} — high-conviction turnaround",
        why_it_matters=f"Call screen factors stacking up: {reason}",
        expected_impact="Asymmetric upside if the reversal holds — defined risk via premium.",
        suggested_action="Review the call candidate (strike/expiry per the scan report) "
                         "and size as a defined-risk options position.",
        confidence=min(95, 50 + int(score) * 4), time_sensitivity="today",
        supporting_data={"call_score": score, "call_reason": row.get("Call_Reason"),
                         "call_strength": row.get("Call_Strength"),
                         "call_strike_hint": row.get("Call_Strike_Hint"),
                         "price": row.get("Current Price")})


# A+ thresholds mirror the scheduler's GRADE_BANDS (day >= 80, swing >= 95,
# both over the dashboard card-rank formulas in core.strategy_scores); the
# long-term equivalent is all six Investment primary filters green AND
# Investment_Score >= 80 — there is no scheduler band for long-term.
A_PLUS_DAY_MIN = 80
A_PLUS_SWING_MIN = 95
A_PLUS_LT_SCORE_MIN = 80


def _a_plus_setup(row: dict) -> dict | None:
    """Any strategy hitting its A+ band fires ONE alert per ticker naming
    every strategy that qualified. HIGH priority — emails and lands in
    ALERT_TICKERS. Resolves when no strategy holds its band any more."""
    from stockanalysis.core.strategy_scores import day_card_rank, swing_card_rank
    ticker = row.get("Ticker")
    if not ticker:
        return None
    hits = []
    swing = swing_card_rank(row)
    if swing >= A_PLUS_SWING_MIN:
        hits.append(f"swing (rank {swing:.0f})")
    day = day_card_rank(row)
    if day >= A_PLUS_DAY_MIN:
        hits.append(f"day (rank {day:.0f})")
    inv_score = _num(row, "Investment_Score")
    if row.get("Investment_Pass") and inv_score is not None and inv_score >= A_PLUS_LT_SCORE_MIN:
        hits.append(f"long-term (score {inv_score:.0f}, all filters green)")
    if not hits:
        return None
    cat = row.get("Category") or "—"
    return alerts.make_alert(
        dedup_key=f"{ticker}:a_plus_setup", category="a_plus_setup", priority="HIGH",
        ticker=ticker, headline=f"A+ {' + '.join(h.split(' ')[0] for h in hits)} setup ({cat})",
        why_it_matters="Scored in the top band for: " + "; ".join(hits) + ".",
        expected_impact="Highest-conviction tier the scanner produces — these are the "
                        "setups the grade filter emails for.",
        suggested_action="Open the research page and review the entry/stop/target plan "
                         "before the setup ages.",
        confidence=85, time_sensitivity="today",
        supporting_data={"strategies": hits, "category": cat,
                         "swing_rank": swing if swing > -999 else None,
                         "day_rank": day if day > -999 else None,
                         "investment_score": inv_score,
                         "entry": row.get("Entry"), "stop": row.get("Stop"),
                         "target": row.get("Target"), "grade": row.get("Grade"),
                         "price": row.get("Current Price")})


_CONDITIONS = (
    _support_touch, _resistance_touch, _breakout, _breakdown, _gap,
    _volume_spike, _rsi_oversold, _rsi_overbought, _ema_crossover, _macd_crossover,
    _put_score_high, _call_score_high, _a_plus_setup, _52w_high, _52w_low,
)

_OPTION_CONDITIONS = ((_put_score_high, "put_score_high"),
                      (_call_score_high, "call_score_high"),
                      (_a_plus_setup, "a_plus_setup"),
                      (_52w_high, "52w_high"),
                      (_52w_low, "52w_low"))


def scan_rows_for_option_alerts(rows: list[dict]) -> list[dict]:
    """The put/call-score, A+ setup, and 52-week high/low conditions, for
    the main scan pipeline: every scan (any category universe, ad-hoc
    tickers, or day-trade scan) checks its rows on the way through, so a
    high-conviction setup or a fresh 52-week extreme alerts no matter which
    scan found it — not just the 10-minute watchlist monitor. Same dedup
    keys as the monitor's conditions, so both paths share one alert state."""
    current, checked_keys = [], set()
    for row in rows:
        ticker = row.get("Ticker")
        if not ticker:
            continue
        for condition, key in _OPTION_CONDITIONS:
            checked_keys.add(f"{ticker}:{key}")
            found = condition(row)
            if found:
                current.append(found)
    new_alerts = alerts.raise_alerts(current, checked_keys)
    from stockanalysis.core import trade_proposals
    trade_proposals.sync_from_alerts()
    return new_alerts


# Original name of the put-only scanner, kept callable — same behavior,
# now also covering calls.
scan_rows_for_put_alerts = scan_rows_for_option_alerts


def default_alert_tickers() -> list[str]:
    """Tickers the Watchlist Alert monitor scans by default: just the
    "watchlist" category from data/watchlists.json — deliberately NOT a
    union of every saved list. watchlists.json also holds broad reference
    universes built for the Scanner/Research pages' browsing (e.g. "sp500"
    at 494 tickers, several full GICS sector lists) — unioning those in
    would turn a 10-minute alert scan into a 500+ ticker fetch, exactly the
    noise this feature exists to cut down on."""
    from stockanalysis.reporting.research import load_watchlists
    return list(load_watchlists().get(DEFAULT_WATCHLIST_NAME, []))


def scan_rows_for_alerts(rows: list[dict]) -> list[dict]:
    """Runs every condition against every already-scanned row and raises
    alerts through core.alerts (dedup + email for CRITICAL/HIGH). Returns
    only the alerts that are newly firing this cycle."""
    current, checked_keys = [], set()
    for row in rows:
        ticker = row.get("Ticker")
        if not ticker:
            continue
        for condition in _CONDITIONS:
            # every dedup_key this condition COULD produce for this ticker
            # must be in checked_keys so alerts.reconcile can clear it if
            # the condition just turned false
            found = condition(row)
            if found:
                current.append(found)
                checked_keys.add(found["dedup_key"])
            else:
                checked_keys.update(_possible_keys(condition, ticker))
    new_alerts = alerts.raise_alerts(current, checked_keys)
    from stockanalysis.core import trade_proposals
    trade_proposals.sync_from_alerts()
    return new_alerts


def _possible_keys(condition, ticker: str) -> set[str]:
    """The dedup_key(s) `condition` would have produced for `ticker` had it
    fired — needed so a condition that just turned false gets its state
    cleared even though it returned None this cycle."""
    name = condition.__name__.lstrip("_")
    if condition is _macd_crossover:
        return {f"{ticker}:macd_bullish", f"{ticker}:macd_bearish"}
    if condition is _ema_crossover:
        return {f"{ticker}:above_50ma"}
    return {f"{ticker}:{name}"}
