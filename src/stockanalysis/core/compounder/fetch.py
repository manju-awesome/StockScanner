"""
fetch.py — one network pass per company, producing a flat measurement dict
==========================================================================
Everything downstream in this package is PURE: it takes the dict this module
produces and returns scores. That split is what makes the engine testable
against fixtures instead of against Yahoo's mood, and it is the same
contract core.longterm.fundamentals holds.

What is fetched, and what each step needs it for
------------------------------------------------
    .info                   cap, sector, employees, ownership, analysts
    income_stmt (4y)        revenue, gross, operating, R&D — the trend
    quarterly_income_stmt   the latest quarter and its year-ago compare
    balance_sheet (4y)      shares outstanding (dilution), cash, debt
    cashflow (4y)           FCF, capex, operating cash flow
    revenue_estimate        forward growth — the only forward-looking input
    recommendations         analyst count and its 3-month drift
    insider_transactions    open-market buying vs selling

Never raises. Every block is independently guarded, because a partial
company is normal — recent IPOs have two years of statements, and a name
with no analyst coverage is a *finding* for Step 10, not an error. Missing
comes back as None and `blend()` renormalises over what was measured.

What CANNOT be fetched
----------------------
Backlog, customer counts, guidance-versus-actual history and patent
portfolios have no source here. They are not silently approximated. Each
consuming module leaves them None and names them in `unmeasured`, and the
page prints that list — because a reader who does not know backlog was
never checked will assume it was.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MAX_YEARS = 4

# The measurement keys every consumer can rely on existing.
EMPTY = {
    "ticker": None, "name": None, "sector": None, "industry": None,
    "market_cap": None, "employees": None, "price": None,
    "shares_outstanding": None,
    "revenue_annual": [], "revenue_quarterly": [], "quarter_dates": [],
    "gross_annual": [], "operating_annual": [], "rnd_annual": [],
    "opex_annual": [], "net_income_annual": [],
    "gross_quarterly": [], "rnd_quarterly": [], "operating_quarterly": [],
    "fcf_annual": [], "ocf_annual": [], "capex_annual": [],
    "shares_annual": [], "cash": None, "debt": None, "equity": None,
    "fiscal_dates": [],
    "inst_own": None, "insider_own": None, "analysts": None,
    "analyst_trend": None, "fwd_rev_growth_cy": None,
    "fwd_rev_growth_ny": None, "fwd_estimate_analysts": None,
    "insider_buys": None, "insider_sells": None, "insider_net_value": None,
    "rs_3m": None, "rs_12m": None, "dist_52w_high": None,
    "vol_expansion": None, "above_200ma": None,
    "errors": [],
}


def _num(v):
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _series(frame, *names) -> list:
    """A statement row, newest first, trimmed to MAX_YEARS.

    First matching name wins — yfinance renames rows between versions and
    across company types, so every caller passes the aliases it knows.
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    wanted = [n.strip().lower() for n in names]
    index = {str(k).strip().lower(): k for k in frame.index}
    for want in wanted:
        if want in index:
            return [_num(v) for v in frame.loc[index[want]].values[:MAX_YEARS]]
    return []


def _qseries(frame, *names, limit=8) -> list:
    if frame is None or getattr(frame, "empty", True):
        return []
    wanted = [n.strip().lower() for n in names]
    index = {str(k).strip().lower(): k for k in frame.index}
    for want in wanted:
        if want in index:
            return [_num(v) for v in frame.loc[index[want]].values[:limit]]
    return []


def _dates(frame, limit=8) -> list:
    cols = getattr(frame, "columns", None)
    if cols is None:
        return []
    out = []
    for c in list(cols)[:limit]:
        try:
            out.append(str(c.date()))
        except AttributeError:
            out.append(str(c))
    return out


def fetch(ticker: str, benchmark_3m: float | None = None,
          benchmark_12m: float | None = None) -> dict:
    """Every measurement for one company. Never raises.

    `benchmark_*` are QQQ's returns over the same windows; relative strength
    is computed against them so Step 10 measures strength RELATIVE to the
    market rather than the market's own move.
    """
    import yfinance as yf

    out = {k: (list(v) if isinstance(v, list) else v)
           for k, v in EMPTY.items()}
    out["ticker"] = ticker

    try:
        t = yf.Ticker(ticker)
    except Exception as e:                              # pragma: no cover
        out["errors"].append(f"ticker init failed: {e}")
        return out

    # ── .info ────────────────────────────────────────────────────────────
    info = {}
    try:
        info = t.info or {}
    except Exception as e:
        out["errors"].append(f"info: {e}")
    out["name"] = info.get("longName") or ticker
    out["sector"] = info.get("sector")
    out["industry"] = info.get("industry")
    out["market_cap"] = _num(info.get("marketCap"))
    out["employees"] = _num(info.get("fullTimeEmployees"))
    out["shares_outstanding"] = _num(info.get("sharesOutstanding"))
    out["price"] = (_num(info.get("currentPrice"))
                    or _num(info.get("regularMarketPrice")))
    # Percentages arrive as fractions. Institutional ownership above 1.0 is
    # a Yahoo artifact on names with multiple share classes, not a company
    # with 130% institutional ownership, so it is clamped rather than
    # allowed to saturate the discovery score.
    inst = _num(info.get("heldPercentInstitutions"))
    ins = _num(info.get("heldPercentInsiders"))
    out["inst_own"] = None if inst is None else round(min(inst, 1.0) * 100, 1)
    out["insider_own"] = None if ins is None else round(min(ins, 1.0) * 100, 1)
    out["analysts"] = _num(info.get("numberOfAnalystOpinions"))

    if out["market_cap"] is None:
        try:
            cap = t.fast_info["market_cap"]
            out["market_cap"] = _num(cap)
        except Exception:
            pass
    if out["price"] is None:
        try:
            out["price"] = _num(t.fast_info["last_price"])
        except Exception:
            pass

    # ── Annual statements ────────────────────────────────────────────────
    income = cash = bal = None
    try:
        income = t.income_stmt
    except Exception as e:
        out["errors"].append(f"income_stmt: {e}")
    try:
        cash = t.cashflow
    except Exception as e:
        out["errors"].append(f"cashflow: {e}")
    try:
        bal = t.balance_sheet
    except Exception as e:
        out["errors"].append(f"balance_sheet: {e}")

    out["revenue_annual"] = _series(income, "Total Revenue", "Operating Revenue")
    out["gross_annual"] = _series(income, "Gross Profit")
    out["operating_annual"] = _series(income, "Operating Income",
                                      "Total Operating Income As Reported")
    out["rnd_annual"] = _series(income, "Research And Development")
    out["opex_annual"] = _series(income, "Operating Expense")
    out["net_income_annual"] = _series(income, "Net Income",
                                       "Net Income Common Stockholders")
    out["fiscal_dates"] = _dates(income, MAX_YEARS)

    # FCF from the statement line, falling back to OCF + capex. Never from
    # .info["freeCashflow"], which this project has measured as wrong by
    # 4-10x — see core.longterm.fundamentals for the evidence.
    fcf = _series(cash, "Free Cash Flow")
    ocf = _series(cash, "Operating Cash Flow",
                  "Total Cash From Operating Activities")
    capex = _series(cash, "Capital Expenditure", "Capital Expenditures")
    if not any(v is not None for v in fcf) and ocf and capex:
        # Capex is reported negative in these statements, so it is ADDED.
        fcf = [None if (o is None or c is None) else o + c
               for o, c in zip(ocf, capex)]
    out["fcf_annual"] = fcf
    out["ocf_annual"] = ocf
    out["capex_annual"] = capex

    out["shares_annual"] = _series(bal, "Ordinary Shares Number", "Share Issued")
    cash_line = _series(bal, "Cash Cash Equivalents And Short Term Investments",
                        "Cash And Cash Equivalents")
    debt_line = _series(bal, "Total Debt")
    eq_line = _series(bal, "Stockholders Equity", "Common Stock Equity")
    out["cash"] = next((v for v in cash_line if v is not None), None)
    out["debt"] = next((v for v in debt_line if v is not None), None)
    out["equity"] = next((v for v in eq_line if v is not None), None)

    # ── Quarterly ────────────────────────────────────────────────────────
    try:
        q = t.quarterly_income_stmt
        out["revenue_quarterly"] = _qseries(q, "Total Revenue",
                                            "Operating Revenue")
        out["gross_quarterly"] = _qseries(q, "Gross Profit")
        out["rnd_quarterly"] = _qseries(q, "Research And Development")
        out["operating_quarterly"] = _qseries(q, "Operating Income")
        out["quarter_dates"] = _dates(q)
    except Exception as e:
        out["errors"].append(f"quarterly_income_stmt: {e}")

    # ── Forward estimates — the only forward-looking input in the engine ──
    try:
        est = t.revenue_estimate
        if est is not None and not est.empty:
            for period, key in (("0y", "fwd_rev_growth_cy"),
                                ("+1y", "fwd_rev_growth_ny")):
                if period in est.index:
                    g = _num(est.loc[period].get("growth"))
                    out[key] = None if g is None else round(g * 100, 1)
            if "0y" in est.index:
                out["fwd_estimate_analysts"] = _num(
                    est.loc["0y"].get("numberOfAnalysts"))
    except Exception as e:
        out["errors"].append(f"revenue_estimate: {e}")

    # Analyst coverage drift. `recommendations` carries four monthly
    # snapshots; the change in TOTAL analysts covering is the "is the street
    # discovering this" reading Step 10 asks for. The buy/sell mix is
    # deliberately ignored — ratings say what the street thinks, coverage
    # count says whether it is looking at all, and only the second is what
    # discovery means.
    try:
        rec = t.recommendations
        if rec is not None and not rec.empty:
            cols = [c for c in ("strongBuy", "buy", "hold", "sell",
                                "strongSell") if c in rec.columns]
            totals = {}
            for _, r in rec.iterrows():
                p = str(r.get("period", "")).strip()
                totals[p] = sum(_num(r.get(c)) or 0 for c in cols)
            now, old = totals.get("0m"), totals.get("-3m")
            if now is not None and old is not None:
                out["analyst_trend"] = round(now - old, 1)
            if out["analysts"] is None and now:
                out["analysts"] = now
    except Exception as e:
        out["errors"].append(f"recommendations: {e}")

    # ── Insider open-market activity ─────────────────────────────────────
    # Grants and option exercises are excluded: an award is compensation,
    # not a purchase, and counting it as insider buying would make every
    # company with an equity comp plan look like management was accumulating.
    try:
        tx = t.insider_transactions
        if tx is not None and not tx.empty:
            buys = sells = 0
            net = 0.0
            for _, r in tx.head(60).iterrows():
                text = str(r.get("Text", "")).lower()
                val = _num(r.get("Value")) or 0.0
                if "sale" in text:
                    sells += 1
                    net -= val
                elif "purchase" in text or "buy" in text:
                    buys += 1
                    net += val
            out["insider_buys"] = buys
            out["insider_sells"] = sells
            out["insider_net_value"] = round(net, 0)
    except Exception as e:
        out["errors"].append(f"insider_transactions: {e}")

    # ── Price context for Step 10 ────────────────────────────────────────
    try:
        hist = t.history(period="1y", auto_adjust=True)
        if hist is not None and not hist.empty and len(hist) > 30:
            close = hist["Close"].ffill()
            last = _num(close.iloc[-1])
            out["price"] = out["price"] or last
            if last:
                if len(close) > 63:
                    r3 = (last / _num(close.iloc[-63]) - 1) * 100
                    out["rs_3m"] = (round(r3, 1) if benchmark_3m is None
                                    else round(r3 - benchmark_3m, 1))
                first = _num(close.iloc[0])
                if first:
                    r12 = (last / first - 1) * 100
                    out["rs_12m"] = (round(r12, 1) if benchmark_12m is None
                                     else round(r12 - benchmark_12m, 1))
                high = _num(close.max())
                if high:
                    out["dist_52w_high"] = round((last / high - 1) * 100, 1)
                if len(close) >= 200:
                    ma200 = _num(close.rolling(200).mean().iloc[-1])
                    out["above_200ma"] = (None if ma200 is None
                                          else bool(last > ma200))
            vol = hist["Volume"]
            if len(vol) > 60:
                recent = _num(vol.tail(20).mean())
                base = _num(vol.tail(90).mean())
                if recent and base and base > 0:
                    out["vol_expansion"] = round(recent / base, 2)
    except Exception as e:
        out["errors"].append(f"history: {e}")

    return out


def benchmark_returns(symbol: str = "QQQ") -> tuple[float | None, float | None]:
    """3-month and 12-month benchmark returns, for relative strength.

    Fetched once per scan rather than per ticker. Returns (None, None) on
    failure, and every RS reading then degrades to an ABSOLUTE return —
    which the consuming module labels as such, because "up 40%" and "up 40%
    against a market up 35%" are different findings.
    """
    try:
        import yfinance as yf
        h = yf.Ticker(symbol).history(period="1y", auto_adjust=True)
        if h is None or h.empty or len(h) < 70:
            return None, None
        close = h["Close"].ffill()
        last = _num(close.iloc[-1])
        r3 = ((last / _num(close.iloc[-63]) - 1) * 100
              if len(close) > 63 else None)
        r12 = (last / _num(close.iloc[0]) - 1) * 100
        return (None if r3 is None else round(r3, 1), round(r12, 1))
    except Exception as e:
        log.debug("benchmark fetch failed: %s", e)
        return None, None
