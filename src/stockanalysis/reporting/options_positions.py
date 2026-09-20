"""
options_positions.py
====================
The options half of the portfolio, stored separately from portfolio.csv in
data/options_positions.csv.

Why a second file rather than an Asset_Type column in portfolio.csv: that
file's whole contract is "one row = one equity ticker", and
portfolio.build_portfolio_view() joins rows to the day's scan by ticker and
values them as `price × Shares`. An option row run through that math gets no
scan match, no ×100 contract multiplier, and no expiry — it would land in
portfolio_totals() as a silently wrong number. Options need their own schema
(strike/expiration/type/contracts) and their own arithmetic, so they get
their own file and the equity math stays correct.

options_positions.csv format
----------------------------
    Underlying,Option_Symbol,Type,Strike,Expiration,Contracts,Side,
    Avg_Premium,Current_Premium,Quote_At,Opened_Date,Strategy,Notes

  Underlying      required — the stock the contract is on (SPY, NVDA)
  Option_ID       broker's instrument UUID — the sync's identity key, and what
                  the next run re-quotes the contract with
  Option_Symbol   broker's contract symbol, when it gives one
  Type            call | put
  Strike          strike price
  Expiration      YYYY-MM-DD — drives Days_To_Expiry and the expiry alerts
  Contracts       number of contracts (not shares — ×100 is applied here)
  Side            long | short — flips the P&L sign and the value sign
  Avg_Premium     per-share premium paid (long) or received (short)
  Current_Premium last quoted per-share premium, from the last sync
  Quote_At        when Current_Premium was quoted — it is a snapshot, not live
  Opened_Date     YYYY-MM-DD, optional
  Strategy        free text (e.g. "0DTE", "covered call", "leaps")
  Notes           free text

Current_Premium is stored rather than fetched because option quotes need a
broker session and this app has none — the webapp renders the last synced
snapshot and shows Quote_At next to it so a stale number can't read as live.

Like portfolio.csv this is user-owned state; the scanner never writes it.
The get-portfolio skill writes it through core.broker_sync.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
OPTIONS_PATH = DATA_DIR / "options_positions.csv"

# Contract multiplier — one standard US equity option covers 100 shares.
CONTRACT_MULTIPLIER = 100

# Warn when an open contract is this close to expiry (calendar days).
EXPIRY_WARN_DAYS = 7

OPTION_FIELDS = ("Underlying", "Option_ID", "Option_Symbol", "Type", "Strike",
                 "Expiration", "Contracts", "Side", "Avg_Premium",
                 "Current_Premium", "Quote_At", "Opened_Date", "Strategy", "Notes")

VALID_TYPES = {"call", "put"}
VALID_SIDES = {"long", "short"}


def _num(val) -> float | None:
    try:
        v = float(str(val).replace("$", "").replace(",", "").strip())
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _date(val) -> date | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def load_options(path: str | Path | None = None) -> list[dict]:
    """Read options_positions.csv → list of contract dicts. Missing file → [].

    Rows without an Underlying are skipped. Unknown Type/Side fall back to
    "call"/"long" rather than raising, matching portfolio.load_positions()'s
    posture: a typo should degrade the display, not crash the page that shows
    someone their real money. Columns beyond OPTION_FIELDS round-trip under
    "_extra" so the sync's bookkeeping columns survive an edit.
    """
    p = Path(path) if path else OPTIONS_PATH
    if not p.exists():
        return []
    out = []
    with open(p, newline="") as fh:
        for raw in csv.DictReader(fh):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            underlying = row.get("Underlying", "").upper()
            if not underlying:
                continue
            otype = row.get("Type", "").lower()
            side = row.get("Side", "").lower()
            extra = {k: v for k, v in row.items()
                     if k and k not in OPTION_FIELDS}
            out.append({
                "Underlying":      underlying,
                "Option_ID":       row.get("Option_ID", ""),
                "Option_Symbol":   row.get("Option_Symbol", ""),
                "Type":            otype if otype in VALID_TYPES else "call",
                "Strike":          _num(row.get("Strike")),
                "Expiration":      _date(row.get("Expiration")),
                "Contracts":       _num(row.get("Contracts")) or 0.0,
                "Side":            side if side in VALID_SIDES else "long",
                "Avg_Premium":     _num(row.get("Avg_Premium")),
                "Current_Premium": _num(row.get("Current_Premium")),
                "Quote_At":        row.get("Quote_At", ""),
                "Opened_Date":     _date(row.get("Opened_Date")),
                "Strategy":        row.get("Strategy", ""),
                "Notes":           row.get("Notes", ""),
                **({"_extra": extra} if extra else {}),
            })
    return out


def save_options(options: list[dict], path: str | Path | None = None) -> None:
    """Write contracts back in the format load_options() reads, preserving
    list order and any "_extra" columns carried from the original file."""
    p = Path(path) if path else OPTIONS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    extra_cols: list[str] = []
    for opt in options:
        for k in (opt.get("_extra") or {}):
            if k not in extra_cols:
                extra_cols.append(k)

    def _d(v):
        return v.isoformat() if isinstance(v, date) else (v or "")

    def _v(v):
        return "" if v is None else v

    with open(p, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OPTION_FIELDS) + extra_cols)
        writer.writeheader()
        for opt in options:
            writer.writerow({
                "Underlying":      opt["Underlying"],
                "Option_ID":       opt.get("Option_ID") or "",
                "Option_Symbol":   opt.get("Option_Symbol") or "",
                "Type":            opt.get("Type") or "call",
                "Strike":          _v(opt.get("Strike")),
                "Expiration":      _d(opt.get("Expiration")),
                "Contracts":       _v(opt.get("Contracts")),
                "Side":            opt.get("Side") or "long",
                "Avg_Premium":     _v(opt.get("Avg_Premium")),
                "Current_Premium": _v(opt.get("Current_Premium")),
                "Quote_At":        opt.get("Quote_At") or "",
                "Opened_Date":     _d(opt.get("Opened_Date")),
                "Strategy":        opt.get("Strategy") or "",
                "Notes":           opt.get("Notes") or "",
                **(opt.get("_extra") or {}),
            })


def contract_label(opt: dict) -> str:
    """Human-readable contract, e.g. "SPY 601C 07/25"."""
    strike = opt.get("Strike")
    strike_s = (f"{strike:g}" if strike is not None else "?")
    letter = "C" if (opt.get("Type") or "call") == "call" else "P"
    exp = opt.get("Expiration")
    exp_s = exp.strftime("%m/%d") if isinstance(exp, date) else "?"
    return f'{opt["Underlying"]} {strike_s}{letter} {exp_s}'


def build_options_view(options: list[dict],
                       today: date | None = None) -> list[dict]:
    """
    Add the derived columns: Label, Days_To_Expiry, Cost_Basis, Market_Value,
    Gain_Dollars, Gain_Pct, Alerts.

    Sign conventions, which are the whole reason this isn't inlined into the
    page: a long contract is an asset (positive value) that gains when the
    premium rises; a short contract is a liability (negative value) that
    gains when the premium falls. Getting this backwards would report a
    losing short as a winner.
    """
    today = today or date.today()
    view = []
    for opt in options:
        contracts = opt.get("Contracts") or 0.0
        avg = opt.get("Avg_Premium")
        cur = opt.get("Current_Premium")
        is_short = (opt.get("Side") or "long") == "short"
        shares = contracts * CONTRACT_MULTIPLIER

        cost_basis = round(avg * shares, 2) if (avg is not None and contracts) else None
        market_value = None
        if cur is not None and contracts:
            market_value = round(cur * shares * (-1 if is_short else 1), 2)

        gain = gain_pct = None
        if avg is not None and cur is not None and contracts:
            per_share = (avg - cur) if is_short else (cur - avg)
            gain = round(per_share * shares, 2)
            if avg:
                gain_pct = round(per_share / avg * 100, 2)

        exp = opt.get("Expiration")
        dte = (exp - today).days if isinstance(exp, date) else None

        alerts = []
        if dte is not None:
            if dte < 0:
                alerts.append("⛔ expired — reconcile, this should have closed")
            elif dte == 0:
                alerts.append("⚠ expires today (0DTE) — decide before the close")
            elif dte <= EXPIRY_WARN_DAYS:
                alerts.append(f"⚠ expires in {dte}d — theta bleed accelerating")
        if cur is None and contracts:
            alerts.append("no quote yet — re-run the sync for live P&L")
        if gain_pct is not None and gain_pct <= -35:
            alerts.append(f"⛔ premium {gain_pct:.0f}% — at/through a −35% stop band")

        view.append({
            **opt,
            "Label":          contract_label(opt),
            "Days_To_Expiry": dte,
            "Cost_Basis":     cost_basis,
            "Market_Value":   market_value,
            "Gain_Dollars":   gain,
            "Gain_Pct":       gain_pct,
            "Alerts":         alerts,
        })

    # Soonest expiry first — for options, time is the risk that can't be
    # waited out, so the row that needs a decision today sorts to the top.
    view.sort(key=lambda o: (o["Days_To_Expiry"] is None,
                             o["Days_To_Expiry"] if o["Days_To_Expiry"] is not None else 0))
    return view


def options_totals(view: list[dict]) -> dict:
    """Aggregate cost basis, market value and P&L across open contracts."""
    open_rows = [o for o in view if (o.get("Contracts") or 0)]
    values = [o["Market_Value"] for o in open_rows if o["Market_Value"] is not None]
    gains = [o["Gain_Dollars"] for o in open_rows if o["Gain_Dollars"] is not None]
    costs = [o["Cost_Basis"] for o in open_rows if o["Cost_Basis"] is not None]
    total_cost = round(sum(costs), 2) if costs else None
    total_gain = round(sum(gains), 2) if gains else None
    return {
        "contracts":    len(open_rows),
        "total_cost":   total_cost,
        "total_value":  round(sum(values), 2) if values else None,
        "total_gain":   total_gain,
        "total_gain_pct": (round(total_gain / total_cost * 100, 2)
                           if (total_gain is not None and total_cost) else None),
        "expiring_soon": sum(1 for o in open_rows
                             if o["Days_To_Expiry"] is not None
                             and 0 <= o["Days_To_Expiry"] <= EXPIRY_WARN_DAYS),
        "alerts": sum(len(o["Alerts"]) for o in open_rows),
    }
