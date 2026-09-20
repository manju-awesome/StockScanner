"""
fundamentals.py — the multi-year statements the long-term engine needs
======================================================================
Two extra yfinance fetches per ticker (annual cash-flow statement and annual
income statement), producing the history that a single `.info` snapshot
cannot contain.

Why this exists at all
----------------------
Two separate problems, one fix.

1. `.info["freeCashflow"]` is not usable. Measured against the statements it
   is wrong by 4-10x on large caps — Microsoft came back at $16.5B against a
   reported $67.0B, Alphabet at $22.7B against $186B of operating cash flow,
   Coca-Cola at $5.2B implying $11.1B of capital expenditure against a real
   $2.1B. Whatever that field is subtracting, it is not capex, and a DCF fed
   from it prices companies at a tenth of their worth. The statement's own
   `Free Cash Flow` line is used instead, and where absent it is computed
   the standard way: operating cash flow minus capital expenditure.

2. Level is not trend. The framework asks for free cash flow that is
   "positive AND growing" and operating margins that are "stable or
   increasing", and neither is answerable from one snapshot. A company whose
   25% margin is down from 40% and one on its way up read identically. With
   four fiscal years the direction is a measurement rather than an
   assumption.

Everything here is annual, not trailing-twelve-month, and the fiscal dates
are returned so a caller can say how stale the newest figure is. Annual is
the right granularity for a framework operating on 6-24 month horizons, and
it avoids the seasonality that makes quarter-on-quarter margin comparisons
meaningless for retailers.

Never raises. A ticker whose statements are unavailable — recent listings,
funds, ADRs with thin filings — comes back as FUNDAMENTAL_DEFAULTS, and
every consumer reads None as "not measured".
"""

from __future__ import annotations

FUNDAMENTAL_KEYS = (
    "FreeCashFlow", "FCF_History", "FCF_CAGR%", "FCF_Positive_Years",
    "FCF_Years", "FCF_Margin%", "FCF_Positive", "NetIncome", "Revenue_CAGR%",
    "OperatingMargin_History", "OperatingMargin_Trend_pp",
    "GrossMargin_History", "GrossMargin_Trend_pp",
    "Fundamentals_As_Of",
)
FUNDAMENTAL_DEFAULTS = {k: None for k in FUNDAMENTAL_KEYS}

MAX_YEARS = 4
# A CAGR across two points that includes a sign change is not a growth rate.
MIN_CAGR_YEARS = 2


def _num(v):
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _series(frame, *names) -> list:
    """Row `names` (first match wins) out of a yfinance statement frame,
    newest first, as a list of floats-or-None trimmed to MAX_YEARS."""
    if frame is None or getattr(frame, "empty", True):
        return []
    wanted = {n.strip().lower() for n in names}
    for key in frame.index:
        if str(key).strip().lower() in wanted:
            return [_num(v) for v in frame.loc[key].values[:MAX_YEARS]]
    return []


def _cagr(values) -> float | None:
    """Compound annual growth from oldest to newest, in percent.

    Returns None rather than a number whenever the arithmetic would be
    meaningless: fewer than MIN_CAGR_YEARS points, or a series that starts
    at or below zero. A company going from -$2B to +$5B has not grown at a
    rate; it has changed state, and reporting "350% CAGR" would put an
    accounting artifact at the top of a growth ranking.
    """
    clean = [v for v in values if v is not None]
    if len(clean) < MIN_CAGR_YEARS:
        return None
    newest, oldest = clean[0], clean[-1]
    years = len(clean) - 1
    if oldest is None or oldest <= 0 or newest <= 0 or years <= 0:
        return None
    return round(((newest / oldest) ** (1 / years) - 1) * 100, 1)


def _margins(numerator, revenue) -> list:
    """Per-year margin percentages, newest first."""
    out = []
    for i, rev in enumerate(revenue):
        num = numerator[i] if i < len(numerator) else None
        if num is None or rev is None or rev <= 0:
            out.append(None)
        else:
            out.append(round(num / rev * 100, 1))
    return out


def _trend_pp(margins) -> float | None:
    """Change in margin from the oldest available year to the newest, in
    percentage points — the direction, not the level."""
    clean = [m for m in margins if m is not None]
    if len(clean) < 2:
        return None
    return round(clean[0] - clean[-1], 1)


def compute_fundamentals(cashflow=None, income=None) -> dict:
    """
    cashflow / income: yfinance annual statement frames (`Ticker.cashflow`
    and `Ticker.income_stmt`). Either may be None or empty.

    Pure — the fetching is the caller's job, which keeps this testable
    against fixture frames and reusable from a backtest.
    """
    out = dict(FUNDAMENTAL_DEFAULTS)

    fcf = _series(cashflow, "Free Cash Flow")
    if not any(v is not None for v in fcf):
        # Compute it the standard way. Capital Expenditure is reported as a
        # negative number in these statements, so it is ADDED.
        ocf = _series(cashflow, "Operating Cash Flow",
                      "Total Cash From Operating Activities")
        capex = _series(cashflow, "Capital Expenditure", "Capital Expenditures")
        fcf = [None if (o is None or c is None) else o + c
               for o, c in zip(ocf, capex)] if ocf and capex else []

    if fcf:
        clean = [v for v in fcf if v is not None]
        out["FCF_History"] = fcf
        out["FreeCashFlow"] = clean[0] if clean else None
        out["FCF_CAGR%"] = _cagr(fcf)
        out["FCF_Years"] = len(clean)
        out["FCF_Positive_Years"] = sum(1 for v in clean if v > 0)

    revenue = _series(income, "Total Revenue")

    # Margin from the STATEMENTS, both sides. Computed from
    # .info["freeCashflow"] it inherits that field's 4-10x error: Alphabet
    # showed a 5.1% FCF margin against a real 16.4%, because the numerator
    # was $22.7B rather than the filed $73.3B. Read beside "compounding
    # +7%/yr, positive in 4 of 4 years" the two lines contradicted each
    # other, which is how the error surfaced.
    if out["FreeCashFlow"] is not None:
        out["FCF_Positive"] = out["FreeCashFlow"] > 0
        rev_now = next((v for v in revenue if v), None)
        if rev_now and rev_now > 0:
            out["FCF_Margin%"] = round(out["FreeCashFlow"] / rev_now * 100, 1)

    op_income = _series(income, "Operating Income")
    gross = _series(income, "Gross Profit")
    net = _series(income, "Net Income", "Net Income Common Stockholders")

    if net:
        clean_net = [v for v in net if v is not None]
        out["NetIncome"] = clean_net[0] if clean_net else None
    if revenue:
        out["Revenue_CAGR%"] = _cagr(revenue)
        if op_income:
            om = _margins(op_income, revenue)
            out["OperatingMargin_History"] = om
            out["OperatingMargin_Trend_pp"] = _trend_pp(om)
        if gross:
            gm = _margins(gross, revenue)
            out["GrossMargin_History"] = gm
            out["GrossMargin_Trend_pp"] = _trend_pp(gm)

    for frame in (cashflow, income):
        cols = getattr(frame, "columns", None)
        if cols is not None and len(cols):
            try:
                out["Fundamentals_As_Of"] = str(cols[0].date())
            except AttributeError:
                out["Fundamentals_As_Of"] = str(cols[0])
            break

    return out


def fetch_fundamentals(ticker_obj) -> dict:
    """Live wrapper: pull both statements off a yfinance Ticker and compute.

    Never raises — a ticker with no filings (recently listed, fund, thin
    ADR) degrades to FUNDAMENTAL_DEFAULTS rather than husking the scan row,
    the same contract get_metrics() holds for every other block.
    """
    cashflow = income = None
    try:
        cashflow = ticker_obj.cashflow
    except Exception:
        pass
    try:
        income = ticker_obj.income_stmt
    except Exception:
        pass
    try:
        return compute_fundamentals(cashflow, income)
    except Exception:
        return dict(FUNDAMENTAL_DEFAULTS)
