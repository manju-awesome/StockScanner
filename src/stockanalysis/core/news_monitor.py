"""
news_monitor.py
================
The "Breaking News Monitor" from the master prompt: scan each watchlist
ticker's recent headlines and alert only on categories that plausibly move
a stock >3% — everything else (routine coverage, analyst re-ratings, macro
commentary) is deliberately ignored, per "ignore low-impact articles."

Reuses market_movers.CATALYST_PATTERNS / _classify_headline (already built
for the Dashboard's mover catalyst tags) rather than a second classifier,
and reporting.research._fetch_ticker_news for the per-ticker headline
fetch (also already built, used by the Research page's news card).

Not real-time: this runs on the same 10-minute cadence as the Watchlist
Alert monitor (see scheduler.py's job_watchlist_alerts), which is as close
to "real time" as a polling scan over free yfinance headlines gets — there
is no push/streaming news feed wired in.

Dedup note: a news headline doesn't "resolve" the way a technical
condition does (RSI can't un-fire). Instead, alerts.py's active-state
lifecycle is repurposed here as a recency window: yfinance's `.news`
returns roughly the last 10 headlines per ticker, so once an alerted
headline scrolls out of that window it's no longer in `current_alerts`,
and reconcile() clears it — a reasonable proxy for "no longer fresh news."
"""

from __future__ import annotations

import hashlib

from stockanalysis.core import alerts

# label -> priority. Categories not listed here (General News, Analyst
# Upgrade/Downgrade, New Coverage, Fed/Rate Decision, Economic Data,
# Trade/Tariff, Short Squeeze, Insider Buying) are intentionally excluded:
# either too routine/frequent to be "breaking" (analyst actions happen
# constantly) or macro rather than company-specific (better suited to a
# future Macro Alert module). Earnings Beat/Miss are also excluded here —
# the dedicated Earnings Alert module (core.earnings_alerts) already covers
# the earnings event itself with richer, structured data; alerting on the
# same event twice through two different code paths would be exactly the
# noise this feature exists to cut down on.
ALERT_WORTHY_CATALYSTS = {
    "FDA Approval": "HIGH",
    "M&A / Acquisition": "HIGH",
    "Executive Departure": "HIGH",
    "Legal / Regulatory": "HIGH",
    "Credit Downgrade": "HIGH",
    "Guidance Cut": "HIGH",
    "Restructuring": "MEDIUM",
    "Guidance Raised": "MEDIUM",
    "Capital Return": "MEDIUM",
    "New Deal / Contract": "MEDIUM",
    "Product Launch": "MEDIUM",
    "Divestiture": "MEDIUM",
}

_WHY_IT_MATTERS = {
    "FDA Approval": "Regulatory approvals/rejections are binary, often-large catalysts for the names they affect.",
    "M&A / Acquisition": "M&A activity typically re-prices a stock immediately toward (or away from) deal terms.",
    "Executive Departure": "A CEO/executive exit often signals unresolved problems or a strategy shift.",
    "Legal / Regulatory": "Investigations and litigation carry open-ended financial and reputational risk.",
    "Credit Downgrade": "A rating agency downgrade raises borrowing costs and can trigger forced selling by mandate-constrained funds.",
    "Guidance Cut": "Management lowering its own forecast is a direct signal of weakening fundamentals.",
    "Restructuring": "Layoffs/restructuring can mean either overdue cost discipline or a deeper slowdown.",
    "Guidance Raised": "Management raising its own forecast is a direct signal of strengthening fundamentals.",
    "Capital Return": "Buybacks/dividend changes signal management's confidence in cash flow.",
    "New Deal / Contract": "A large new contract can move the revenue outlook meaningfully for smaller names.",
    "Product Launch": "A major product launch can shift the competitive/revenue picture.",
    "Divestiture": "Divesting a unit reshapes the business mix and can unlock or destroy value depending on terms.",
}


def _headline_key(title: str) -> str:
    return hashlib.sha1(title.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _build_alert(ticker: str, label: str, item: dict) -> dict:
    priority = ALERT_WORTHY_CATALYSTS[label]
    return alerts.make_alert(
        dedup_key=f"{ticker}:news:{_headline_key(item['title'])}",
        category="news", priority=priority, ticker=ticker,
        headline=f"{label}: {item['title']}",
        why_it_matters=_WHY_IT_MATTERS.get(label, f"Classified as {label}."),
        expected_impact="Historically a market-moving category — magnitude depends on the specifics of this article.",
        suggested_action="Read the full article before trading; a headline alone isn't enough to size a position.",
        confidence=60, time_sensitivity="today",
        supporting_data={"catalyst": label, "publisher": item.get("publisher"),
                        "url": item.get("url"), "published": item.get("when")})


def scan_news_for_alerts(tickers: list[str]) -> list[dict]:
    """Runs over every ticker's recent headlines and raises alerts through
    core.alerts (dedup + email for HIGH). Returns only the alerts newly
    firing this cycle."""
    from stockanalysis.reporting.research import _fetch_ticker_news
    from stockanalysis.scanners.market_movers import _classify_headline

    current, checked_keys = [], set()
    active = alerts.load_active()
    for ticker in tickers:
        # every previously-active news key for this ticker must be in
        # checked_keys so reconcile() can clear it once the headline ages
        # out of the recent-news window (see module docstring)
        checked_keys.update(k for k in active if k.startswith(f"{ticker}:news:"))
        try:
            items = _fetch_ticker_news(ticker, limit=10)
        except Exception:
            continue
        for item in items:
            title = item.get("title")
            if not title:
                continue
            label = _classify_headline(title)
            if label not in ALERT_WORTHY_CATALYSTS:
                continue
            alert = _build_alert(ticker, label, item)
            current.append(alert)
            checked_keys.add(alert["dedup_key"])

    return alerts.raise_alerts(current, checked_keys)
