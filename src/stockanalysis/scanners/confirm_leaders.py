"""
confirm_leaders.py
==================
Third-layer confirmation for the sector-leader scan: once the technicals have
named a leader, ask whether the *fundamentals* and the *news* agree with the
chart before treating the setup as high conviction.

The scan that feeds this (scanners.scan_sector_leaders) is pure price and
volume. Price is the fastest of the three signals and the least explanatory —
a name can be the strongest chart in the strongest sector because of a
takeover rumour that caps its upside, or because of an approval that changes
the multi-year earnings path. Those two look identical in the ATR.

What this module does and does not do
-------------------------------------
It FETCHES, it does not judge: headlines, fundamentals, next earnings date and
analyst positioning come back as structured data for a reader (human or model)
to weigh. The one number it computes on its own is a fundamentals score, via
core.company_scores, which is an existing pure function over yfinance `.info`
fields.

Headline *sentiment* is deliberately not scored here. The project already has
a keyword-lexicon scorer in core.earnings_sentiment and that module's own
docstring calls it "not real NLP" — running a bag of positive/negative words
over "Merck halts trial after independent monitoring board recommendation"
produces a number with no relationship to what the sentence means. Headlines
are returned verbatim, with dates and publishers, and read rather than counted.

Earnings proximity is fetched because it silently invalidates swing setups:
a clean pullback entry four days before a print is an earnings bet wearing a
technical costume, and the scan cannot see that from the bars.

    python3 -m stockanalysis.scanners.confirm_leaders --tickers MRK,AMGN \\
        --json confirm.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

# .info fields worth carrying. freeCashflow is deliberately absent: that field
# is wrong by a factor of 4-10x for many names, and the cash-flow statement is
# read directly instead (see fetch_cashflow).
_FUND_FIELDS = (
    "longName", "sector", "industry", "marketCap", "trailingPE", "forwardPE",
    "pegRatio", "priceToSalesTrailing12Months", "priceToBook",
    "profitMargins", "operatingMargins", "grossMargins",
    "returnOnEquity", "returnOnAssets", "revenueGrowth", "earningsGrowth",
    "earningsQuarterlyGrowth", "debtToEquity", "currentRatio", "quickRatio",
    "totalCash", "totalDebt", "ebitda", "totalRevenue",
    "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "recommendationMean", "recommendationKey", "numberOfAnalystOpinions",
    "heldPercentInstitutions", "dividendYield", "beta",
)


def fetch_fundamentals(ticker: str) -> dict:
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        return {"error": str(e)[:120]}
    return {k: info.get(k) for k in _FUND_FIELDS if info.get(k) is not None}


def fetch_cashflow(ticker: str) -> dict:
    """Free cash flow from the cash-flow statement.

    `.info["freeCashflow"]` is not usable — it has been observed wrong by 4-10x
    against the filed statements — so operating cash flow and capex are read
    off Ticker.cashflow and differenced.
    """
    import yfinance as yf
    try:
        cf = yf.Ticker(ticker).cashflow
        if cf is None or cf.empty:
            return {"error": "no cashflow statement"}
        col = cf.columns[0]

        def row(*names):
            for n in names:
                if n in cf.index:
                    v = cf.loc[n, col]
                    if v == v:      # not NaN
                        return float(v)
            return None

        ocf = row("Operating Cash Flow", "Total Cash From Operating Activities")
        capex = row("Capital Expenditure", "Capital Expenditures")
        fcf = (ocf + capex) if (ocf is not None and capex is not None) else None
        return {"period": str(col)[:10], "operating_cash_flow": ocf,
                "capex": capex, "free_cash_flow": fcf}
    except Exception as e:
        return {"error": str(e)[:120]}


def fetch_next_earnings(ticker: str) -> dict:
    """Next scheduled earnings date and how many calendar days out.

    A swing setup inside the earnings window is an earnings bet, not a
    technical trade, and nothing in the price data says so.
    """
    import yfinance as yf
    try:
        cal = yf.Ticker(ticker).calendar
        dt = None
        if isinstance(cal, dict):
            v = cal.get("Earnings Date")
            if isinstance(v, (list, tuple)) and v:
                dt = v[0]
            elif v:
                dt = v
        if dt is None:
            return {"next_earnings": None, "days_away": None,
                    "note": "no scheduled date from the feed"}
        d = dt if isinstance(dt, date) else datetime.fromisoformat(str(dt)[:10]).date()
        return {"next_earnings": d.isoformat(), "days_away": (d - date.today()).days}
    except Exception as e:
        return {"next_earnings": None, "days_away": None, "error": str(e)[:120]}


def fetch_headlines(ticker: str, limit: int = 10) -> list[dict]:
    """Recent headlines, verbatim. Reuses the research page's fetcher so both
    surfaces show the same feed."""
    try:
        from stockanalysis.reporting.research import _fetch_ticker_news
        return _fetch_ticker_news(ticker, limit=limit)
    except Exception as e:
        log.debug("%s: news fetch failed: %s", ticker, e)
        return [{"error": str(e)[:120]}]


def fundamental_scores(fund: dict, cashflow: dict | None = None) -> dict:
    """core.company_scores over the fetched fields.

    Those functions read a scan row, whose field names are not the .info keys
    (`GrossMargin%`, not `grossMargins`), so the mapping has to be exact —
    a near-miss silently returns "only 0 of 8 inputs available" and every
    name scores None. FCF margin comes from the cash-flow statement rather
    than .info["freeCashflow"], which is unreliable.
    """
    from stockanalysis.core.company_scores import (
        compute_business_quality, compute_economic_moat, compute_financial_health)
    fcf = (cashflow or {}).get("free_cash_flow")
    rev = fund.get("totalRevenue")
    row = {
        "GrossMargin%": _pct(fund.get("grossMargins")),
        "OperatingMargin%": _pct(fund.get("operatingMargins")),
        "ReturnOnEquity%": _pct(fund.get("returnOnEquity")),
        "Revenue": _pct(fund.get("revenueGrowth")),
        "EPS_Growth%": _pct(fund.get("earningsGrowth")),
        "DebtToEquity": fund.get("debtToEquity"),
        "Inst_Own%": _pct(fund.get("heldPercentInstitutions")),
        "CurrentRatio": fund.get("currentRatio"),
        "QuickRatio": fund.get("quickRatio"),
        "TotalCash": fund.get("totalCash"),
        "TotalDebt": fund.get("totalDebt"),
        "FCF_Margin%": (round(fcf / rev * 100, 2)
                        if (fcf is not None and rev) else None),
        "FCF_Positive": (fcf > 0) if fcf is not None else None,
    }
    try:
        return {"business_quality": compute_business_quality(row),
                "financial_health": compute_financial_health(row),
                "moat_checklist": compute_economic_moat(row),
                "inputs": {k: v for k, v in row.items() if v is not None}}
    except Exception as e:
        return {"error": str(e)[:160]}


def _pct(v):
    """yfinance returns margins/growth as fractions; company_scores wants percent."""
    return round(v * 100, 2) if isinstance(v, (int, float)) else None


def confirm(tickers: list[str]) -> dict:
    out = {}
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {t}", file=sys.stderr)
        fund = fetch_fundamentals(t)
        cf = fetch_cashflow(t)
        out[t] = {
            "fundamentals": fund,
            "cashflow": cf,
            "scores": fundamental_scores(fund, cf) if "error" not in fund else {},
            "earnings": fetch_next_earnings(t),
            "headlines": fetch_headlines(t),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--json", required=True)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    res = confirm(tickers)
    Path(args.json).write_text(json.dumps(res, indent=1, default=str))
    print(f"wrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()


def confirm_and_store(tickers: list[str], progress_cb=None) -> dict:
    """Fetch the confirmation layer for these names and merge it into the
    stored snapshot. Merged rather than replaced so confirming three names
    does not blank the other twenty."""
    from stockanalysis.core import leaders_store

    out = {}
    for i, t in enumerate(tickers, 1):
        if progress_cb:
            progress_cb(f"confirming — {t}", i, len(tickers))
        fund = fetch_fundamentals(t)
        cf = fetch_cashflow(t)
        out[t] = {
            "fundamentals": fund,
            "cashflow": cf,
            "scores": fundamental_scores(fund, cf) if "error" not in fund else {},
            "earnings": fetch_next_earnings(t),
            "headlines": fetch_headlines(t),
        }
    leaders_store.save_confirmations(out)
    return out
