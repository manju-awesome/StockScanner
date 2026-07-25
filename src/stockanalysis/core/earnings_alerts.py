"""
earnings_alerts.py
===================
The "Earnings Alert" module from the master prompt: notify on watchlist
tickers with earnings coming up, gated by size so it doesn't fire for
every name reporting this week — most report with routine, in-line
results that don't move the stock.

Three tiers instead of the spec's literal "24 hours before / 1 hour before":
yfinance's earnings_dates carries a DATE, not a reliable release TIME
(before-market vs after-market isn't consistently populated), so hour-
precision timing isn't something this can honestly claim. Tiers are
day-granularity instead:
  - "T-5": days_to_earnings in 2..EARLY_ALERT_DAYS (early heads-up, fires
    once anywhere in that window — a single dedup key covers the whole
    span, so a ticker first seen at day 3 still gets its heads-up)
  - "T-1": days_to_earnings == 1 (heads-up the day before)
  - "T-0": days_to_earnings == 0 (day-of)
T-1 and T-0 are HIGH priority (both email), matching the spec's "email me
both times" — this isn't a graduated-urgency pair, it's the same important
event flagged twice at different lead times. T-5 is MEDIUM (webapp Alerts
feed only, no email): it exists for planning lead time, not urgency.

Gate — a ticker must pass ALL of:
  - market cap > $2B (MIN_MARKET_CAP)
  - expected move >= 5% OR historical avg move >= 6% (either is enough;
    requiring both would drop names with no listed options entirely,
    since expected_move_pct would be None for them)

NOT implemented — no free data source for these:
  - IV Rank: needs a historical IV percentile/distribution; nothing in
    this pipeline computes or stores one.
  - "Strong options activity" as a gate: there's no historical baseline
    volume/OI to judge "unusual" against. Raw options data (avg_iv,
    put/call OI ratio, total volume) is surfaced in supporting_data for
    your own judgment instead of used as a filter.

"Analyst revisions increased" is approximated by net rating actions
(upgrades - downgrades) over the trailing 90 days from
core.earnings_sentiment.fetch_analyst_snapshot — a rating-action count,
not a formal EPS-estimate-revision series (yfinance doesn't expose one
reliably), but the closest available signal. Surfaced in the alert
content, not used as a hard gate (spec lists it as one of several
either/or signals, and it's the weakest-grounded one here).
"""

from __future__ import annotations

from stockanalysis.core import alerts

MIN_MARKET_CAP = 2_000_000_000
EARLY_ALERT_DAYS = 5
MIN_EXPECTED_MOVE_PCT = 5.0
MIN_HISTORICAL_MOVE_PCT = 6.0


def _market_cap(ticker: str) -> float | None:
    import yfinance as yf
    try:
        cap = yf.Ticker(ticker).fast_info["market_cap"]
        return float(cap) if cap else None
    except Exception:
        return None


def _passes_gate(market_cap: float | None, expected_move_pct: float | None,
                 avg_abs_move_pct: float | None) -> bool:
    if market_cap is None or market_cap <= MIN_MARKET_CAP:
        return False
    big_expected = expected_move_pct is not None and expected_move_pct >= MIN_EXPECTED_MOVE_PCT
    big_historical = avg_abs_move_pct is not None and avg_abs_move_pct >= MIN_HISTORICAL_MOVE_PCT
    return big_expected or big_historical


def _build_alert(ticker: str, tier: str, hist: dict, sentiment: dict,
                 analyst: dict, market_cap: float) -> dict:
    expected_move = sentiment.get("expected_move_pct")
    avg_move = hist.get("avg_abs_move_pct")
    bias = sentiment.get("trading_bias") or "Neutral"
    risks = sentiment.get("risk_factors") or []
    if tier == "T-0":
        tier_label = "today"
    elif tier == "T-1":
        tier_label = "tomorrow"
    else:
        tier_label = f"in {hist.get('days_to_earnings')} days"

    move_text = f"±{expected_move:.1f}% expected" if expected_move is not None else (
        f"±{avg_move:.1f}% historical average" if avg_move is not None else "size unclear")
    headline = f"reports earnings {tier_label} ({move_text})"

    suggested_action = f"Review the plan before the release — bias reads {bias.lower()}."
    if risks:
        suggested_action += f" Key risk: {risks[0]}"

    revisions = analyst.get("net_actions")
    revisions_note = (f"{revisions:+d} net analyst rating action(s) over the trailing 90 days"
                      if revisions is not None else "no recent analyst rating actions on file")

    return alerts.make_alert(
        dedup_key=f"{ticker}:earnings_{tier}", category="earnings",
        priority="MEDIUM" if tier == "T-5" else "HIGH",
        ticker=ticker, headline=headline,
        why_it_matters=(f"Market cap ${market_cap / 1e9:.1f}B with a historical average move "
                        f"of ±{avg_move:.1f}%" if avg_move is not None else
                        f"Market cap ${market_cap / 1e9:.1f}B") + " — large enough to matter to a portfolio.",
        expected_impact=f"Expected move {move_text}; {revisions_note}.",
        suggested_action=suggested_action,
        confidence=int(round((sentiment.get("confidence") or 5) * 10)),
        time_sensitivity={"T-0": "today", "T-1": "next session"}.get(tier, "this week"),
        supporting_data={
            "next_earnings_date": hist.get("next_earnings_date"),
            "consensus_eps": hist.get("consensus_eps"),
            "market_cap": market_cap,
            "expected_move_pct": expected_move,
            "avg_abs_move_pct": avg_move,
            "beat_rate": hist.get("beat_rate"),
            "streak": hist.get("streak"),
            "trading_bias": bias,
            "risk_factors": risks,
            "analyst_net_actions": revisions,
        })


def scan_earnings_for_alerts(tickers: list[str]) -> list[dict]:
    """Cheap pre-filter (fetch_earnings_history, one lightweight call) picks
    out only tickers within the T-5/T-1/T-0 window; the heavier
    analyze_earnings_sentiment pipeline (8 sub-fetches) only runs for that
    small subset, not the whole watchlist every cycle."""
    from stockanalysis.core.earnings_sentiment import (
        fetch_earnings_history, fetch_analyst_snapshot, analyze_earnings_sentiment)

    current, checked_keys = [], set()
    for ticker in tickers:
        checked_keys.add(f"{ticker}:earnings_T-5")
        checked_keys.add(f"{ticker}:earnings_T-1")
        checked_keys.add(f"{ticker}:earnings_T-0")
        try:
            hist = fetch_earnings_history(ticker)
        except Exception:
            continue
        days = hist.get("days_to_earnings")
        if days is None or not (0 <= days <= EARLY_ALERT_DAYS):
            continue

        market_cap = _market_cap(ticker)
        try:
            sentiment = analyze_earnings_sentiment(ticker)
        except Exception:
            sentiment = {}
        try:
            analyst = fetch_analyst_snapshot(ticker)
        except Exception:
            analyst = {}

        if not _passes_gate(market_cap, sentiment.get("expected_move_pct"), hist.get("avg_abs_move_pct")):
            continue

        if days == 0:
            tier = "T-0"
        elif days == 1:
            tier = "T-1"
        else:
            tier = "T-5"
        current.append(_build_alert(ticker, tier, hist, sentiment, analyst, market_cap))

    return alerts.raise_alerts(current, checked_keys)
