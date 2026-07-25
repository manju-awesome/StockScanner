"""
portfolio.py
============
Loads the user's positions/watchlist from data/portfolio.csv and joins them
with the current scan rows to produce the dashboard's Portfolio & Watchlist
panel: live P&L per position plus rule-based alerts (stop breach, strategy
filters degrading, earnings inside the blackout window, category flipped to
Avoid).

portfolio.csv format (copy data/portfolio_template.csv to get started)
----------------------------------------------------------------------
    Ticker,Shares,Avg_Cost,Entry_Date,Strategy,Stop,Target,Notes
    NVDA,25,145.50,2026-05-12,longterm,,260,core AI position
    ARM,40,290.00,2026-07-08,swing,285.00,340,pullback add
    HOOD,0,,,watch,,,waiting for base

  Ticker     required
  Shares     0 or blank = watchlist-only row (no P&L, still gets alerts)
  Avg_Cost   cost basis per share (needed for Gain columns)
  Entry_Date YYYY-MM-DD — enables the Days Held column
  Strategy   day | swing | longterm | watch — selects which alerts apply
  Stop       optional hard stop; drives the Risk column + breach alert
  Target     optional take-profit level; drives the target-hit Next Action
  Notes      free text, shown on the panel

The panel computes per position: Gain % / Gain $ (vs Avg_Cost), Days Held
(vs Entry_Date), Risk ($ from price to stop for the share count), and a
Next Action recommendation (EXIT / TRIM / REDUCE / REVIEW / HOLD / ENTER?)
derived from the alerts, target proximity, and the position's strategy
scores in today's scan.

The file is user-owned state — the scanner never writes it. The webapp's
Portfolio page can add/edit/delete rows through save_positions() below (via
the Edit/Delete buttons), but a missing file is still not an error (the
panel shows setup instructions).
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime
from pathlib import Path

# data/ directory at the project root (same one the scan CSVs live in)
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
PORTFOLIO_PATH = DATA_DIR / "portfolio.csv"

EARNINGS_ALERT_DAYS = 7
VALID_STRATEGIES = {"day", "swing", "longterm", "watch"}

# User-defined total portfolio value (static input) — allocation percentages
# are computed against this, not against the sum of positions, so cash is
# implicit. Set PORTFOLIO_VALUE in the environment / .env; falls back to the
# ACCOUNT_SIZE the risk-sizing already uses.
PORTFOLIO_VALUE = float(os.environ.get(
    "PORTFOLIO_VALUE", os.environ.get("ACCOUNT_SIZE", "100000")))
# Warn when small-cap exposure exceeds this share of the portfolio
SMALLCAP_MAX_PCT = float(os.environ.get("SMALLCAP_MAX_PCT", "10"))

_CAP_BUCKETS = (   # (floor, label) — checked in order
    (200e9, "Mega"), (10e9, "Large"), (2e9, "Mid"), (0, "Small"))


def cap_bucket(market_cap) -> str | None:
    """Mega ≥ $200B, Large ≥ $10B, Mid ≥ $2B, Small < $2B."""
    if not market_cap:
        return None
    for floor, label in _CAP_BUCKETS:
        if market_cap >= floor:
            return label
    return "Small"


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


POSITION_FIELDS = ("Ticker", "Shares", "Avg_Cost", "Entry_Date", "Strategy",
                   "Stop", "Target", "Notes")


def load_positions(path: str | Path | None = None) -> list[dict]:
    """
    Read portfolio.csv → list of position dicts. Missing file → []. Rows
    without a ticker are skipped; an unknown Strategy falls back to "watch"
    so a typo can't attach the wrong alert set to real money.

    Columns beyond POSITION_FIELDS (e.g. a hand-added Target_Weight/Theme
    for an allocation plan) are round-tripped verbatim under "_extra" so
    save_positions() doesn't silently discard them on the next webapp edit.
    """
    p = Path(path) if path else PORTFOLIO_PATH
    if not p.exists():
        return []
    positions = []
    with open(p, newline="") as fh:
        for raw in csv.DictReader(fh):
            row = {(k or "").strip(): (v or "").strip()
                   for k, v in raw.items()}
            ticker = row.get("Ticker", "").upper()
            if not ticker:
                continue
            strategy = row.get("Strategy", "").lower() or "watch"
            if strategy not in VALID_STRATEGIES:
                strategy = "watch"
            extra = {k: v for k, v in row.items()
                     if k and k not in POSITION_FIELDS}
            positions.append({
                "Ticker":     ticker,
                "Shares":     _num(row.get("Shares")) or 0.0,
                "Avg_Cost":   _num(row.get("Avg_Cost")),
                "Entry_Date": _date(row.get("Entry_Date")),
                "Strategy":   strategy,
                "Stop":       _num(row.get("Stop")),
                "Target":     _num(row.get("Target")),
                "Notes":      row.get("Notes", ""),
                **({"_extra": extra} if extra else {}),
            })
    return positions


def save_positions(positions: list[dict], path: str | Path | None = None) -> None:
    """Write positions back to portfolio.csv in the format load_positions()
    reads, preserving list order (callers control sort/placement) and any
    "_extra" columns a row carries from the original file."""
    p = Path(path) if path else PORTFOLIO_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    extra_cols = []
    for pos in positions:
        for k in (pos.get("_extra") or {}):
            if k not in extra_cols:
                extra_cols.append(k)
    with open(p, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(POSITION_FIELDS) + extra_cols)
        writer.writeheader()
        for pos in positions:
            entry_date = pos.get("Entry_Date")
            row = {
                "Ticker":     pos["Ticker"],
                "Shares":     pos.get("Shares") or "",
                "Avg_Cost":   pos.get("Avg_Cost") if pos.get("Avg_Cost") is not None else "",
                "Entry_Date": entry_date.isoformat() if isinstance(entry_date, date) else (entry_date or ""),
                "Strategy":   pos.get("Strategy") or "watch",
                "Stop":       pos.get("Stop") if pos.get("Stop") is not None else "",
                "Target":     pos.get("Target") if pos.get("Target") is not None else "",
                "Notes":      pos.get("Notes") or "",
                **(pos.get("_extra") or {}),
            }
            writer.writerow(row)


def upsert_position(fields: dict, original_ticker: str | None = None,
                    path: str | Path | None = None) -> list[dict]:
    """Add a new position or update an existing one (matched by Ticker),
    then save. `original_ticker` renames a row in place when the Edit form's
    ticker was changed — without it, editing a ticker would leave the old
    row behind as an orphan. Returns the full position list after saving.
    Raises ValueError on a blank ticker (never silently drops the row)."""
    ticker = (fields.get("Ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Ticker is required")
    strategy = (fields.get("Strategy") or "").strip().lower() or "watch"
    if strategy not in VALID_STRATEGIES:
        strategy = "watch"
    positions = load_positions(path)
    # Preserve any "_extra" columns (Target_Weight, Theme, ...) belonging to
    # the row this edit targets, so saving through the webapp doesn't erase
    # hand-added metadata the form doesn't know about.
    match_ticker = original_ticker.strip().upper() if original_ticker else ticker
    matched = next((p for p in positions if p["Ticker"] == match_ticker), None)

    parsed = {
        "Ticker":     ticker,
        "Shares":     _num(fields.get("Shares")) or 0.0,
        "Avg_Cost":   _num(fields.get("Avg_Cost")),
        "Entry_Date": _date(fields.get("Entry_Date")),
        "Strategy":   strategy,
        "Stop":       _num(fields.get("Stop")),
        "Target":     _num(fields.get("Target")),
        "Notes":      (fields.get("Notes") or "").strip(),
        **({"_extra": matched["_extra"]} if matched and matched.get("_extra") else {}),
    }

    if original_ticker:
        original_ticker = original_ticker.strip().upper()
        positions = [p for p in positions if p["Ticker"] != original_ticker]
    idx = next((i for i, p in enumerate(positions) if p["Ticker"] == ticker), None)
    if idx is not None:
        positions[idx] = parsed
    else:
        positions.append(parsed)
    save_positions(positions, path)
    return positions


def delete_position(ticker: str, path: str | Path | None = None) -> list[dict]:
    """Remove a position by ticker, then save. Returns the full position
    list after saving; a ticker not present is a no-op."""
    ticker = ticker.strip().upper()
    positions = [p for p in load_positions(path) if p["Ticker"] != ticker]
    save_positions(positions, path)
    return positions


def _position_alerts(pos: dict, scan: dict | None) -> list[str]:
    """Rule-based alerts for one position, given its scan row (or None)."""
    alerts = []
    if scan is None:
        alerts.append("not in today's scan — no live data, add to universe")
        return alerts

    price = scan.get("Current Price")
    stop  = pos.get("Stop")
    if price is not None and stop is not None and price < stop:
        alerts.append(f"⛔ below stop {stop:,.2f} (now {price:,.2f}) — exit rule hit")

    strategy = pos["Strategy"]
    if strategy == "longterm":
        if scan.get("Above_200MA") is False:
            alerts.append("⛔ closed below 200MA — long-term stage broken, review")
        if scan.get("Investment_Pass") is False and scan.get("Investment_Score") is not None:
            reason = str(scan.get("Investment_Reason") or "")
            failed = reason.split("FAILED:")[-1].strip() if "FAILED:" in reason else ""
            alerts.append("⚠ investment filters degraded"
                          + (f" ({failed})" if failed else ""))
    if strategy in ("day", "swing"):
        if scan.get("Category") == "Avoid":
            alerts.append("⚠ category now Avoid — no fresh setup backing the hold")
        dte = scan.get("Days_To_Earnings")
        if dte is not None and 0 <= dte <= EARNINGS_ALERT_DAYS:
            alerts.append(f"⚠ earnings in {int(dte)}d — gap risk on a "
                          f"{strategy} position")
    rs_rank = scan.get("RS_Rank")
    if strategy != "watch" and rs_rank is not None and rs_rank < 30:
        alerts.append(f"⚠ RS_Rank {rs_rank:.0f} — lagging the universe")
    return alerts


def _next_action(pos: dict, scan: dict | None, alerts: list[str],
                 price: float | None, gain_pct: float | None) -> str:
    """One imperative recommendation per row — the column a PM reads first.
    Rule order = severity: exits before trims before holds."""
    if scan is None:
        return "ADD TO SCAN — no live data for this ticker"
    strategy = pos["Strategy"]
    target = pos.get("Target")

    if pos["Is_Watch"]:
        ready = {"day":      scan.get("DayTrade_Pass"),
                 "swing":    scan.get("Swing_Pass"),
                 "longterm": scan.get("Investment_Pass")}
        if any(ready.values()) or scan.get("Grade") in ("A", "A+"):
            which = ", ".join(k for k, v in ready.items() if v) or "grade A"
            return f"ENTER? setup ready ({which}) — review the plan"
        return "WAIT — no qualified setup yet"

    if any("below stop" in a for a in alerts):
        return "EXIT — stop hit, follow the rule"
    if target and price is not None and price >= target:
        return f"TRIM — target {target:,.2f} hit, take partials / raise stop"
    if any("stage broken" in a for a in alerts):
        return "REVIEW — below 200MA, no adds until stage repairs"
    if any("earnings in" in a for a in alerts):
        return "REDUCE — earnings inside blackout window, cut gap risk"
    if any("filters degraded" in a for a in alerts):
        return "HOLD, NO ADDS — investment filters degraded"
    if any("lagging" in a for a in alerts):
        return "ROTATE? — RS lagging, compare vs current scan leaders"
    if gain_pct is not None and gain_pct >= 20:
        return "HOLD — raise stop to protect the gain"
    return "HOLD — thesis intact, no action"


def build_portfolio_view(positions: list[dict],
                         rows: list[dict]) -> list[dict]:
    """
    Join positions with scan rows. Each returned dict adds: Price, Value,
    Gain_Dollars, Gain_Pct, Days_Held, Risk (dollars from price to stop for
    the share count), Next_Action, Category, Grade, scores, and Alerts
    (list[str]). Sorted: alerted positions first, then by position value,
    watchlist rows last.
    """
    by_ticker = {r.get("Ticker"): r for r in rows if r.get("Ticker")}
    view = []
    for pos in positions:
        scan = by_ticker.get(pos["Ticker"])
        price  = scan.get("Current Price") if scan else None
        shares = pos["Shares"] or 0.0
        cost   = pos["Avg_Cost"]
        stop   = pos.get("Stop")
        value  = round(price * shares, 2) if (price is not None and shares) else None
        gain = gain_pct = None
        if price is not None and cost and shares:
            gain     = round((price - cost) * shares, 2)
            gain_pct = round((price / cost - 1) * 100, 2)
        days_held = ((date.today() - pos["Entry_Date"]).days
                     if pos.get("Entry_Date") and shares else None)
        # $ at risk if the stop is honored from here; 0 once price is at/below
        risk = (round(max(price - stop, 0.0) * shares, 2)
                if (price is not None and stop and shares) else None)
        p = {
            **pos,
            "Price":        price,
            "Value":        value,
            "Gain_Dollars": gain,
            "Gain_Pct":     gain_pct,
            "Days_Held":    days_held,
            "Risk":         risk,
            "Alloc_Pct":    (round(value / PORTFOLIO_VALUE * 100, 1)
                             if (value and PORTFOLIO_VALUE) else None),
            "Cap":          cap_bucket(scan.get("MarketCap")) if scan else None,
            "Sector":       scan.get("Sector") if scan else None,
            "Category":  scan.get("Category") if scan else None,
            "Grade":     scan.get("Grade") if scan else None,
            "RS_Rank":   scan.get("RS_Rank") if scan else None,
            "Investment_Score": scan.get("Investment_Score") if scan else None,
            "Swing_Score":      scan.get("Swing_Score") if scan else None,
            "DayTrade_Score":   scan.get("DayTrade_Score") if scan else None,
            "Alerts":    _position_alerts(pos, scan),
            "Is_Watch":  shares == 0,
        }
        p["Next_Action"] = _next_action(p, scan, p["Alerts"], price, gain_pct)
        view.append(p)
    view.sort(key=lambda p: (p["Is_Watch"],
                             0 if p["Alerts"] else 1,
                             -(p["Value"] or 0)))
    return view


def portfolio_totals(view: list[dict]) -> dict:
    """Aggregate value / P&L across real (non-watch) positions with data."""
    held = [p for p in view if not p["Is_Watch"] and p["Value"] is not None]
    total_value = round(sum(p["Value"] for p in held), 2)
    with_gain = [p for p in held if p["Gain_Dollars"] is not None]
    total_gain = (round(sum(p["Gain_Dollars"] for p in with_gain), 2)
                  if with_gain else None)
    total_cost = sum(p["Avg_Cost"] * p["Shares"] for p in with_gain)
    total_risk = round(sum(p["Risk"] for p in held if p["Risk"] is not None), 2)
    return {
        "positions":   len(held),
        "watching":    sum(1 for p in view if p["Is_Watch"]),
        "total_value": total_value,
        "total_gain":  total_gain,
        "total_gain_pct": (round(total_gain / total_cost * 100, 2)
                           if (total_gain is not None and total_cost) else None),
        "total_risk":  total_risk,
        "alerts":      sum(len(p["Alerts"]) for p in view),
        "portfolio_value": PORTFOLIO_VALUE,
        "invested_pct": (round(total_value / PORTFOLIO_VALUE * 100, 1)
                         if PORTFOLIO_VALUE else None),
        "cash": round(PORTFOLIO_VALUE - total_value, 2),
    }


def allocation_summary(view: list[dict]) -> dict:
    """
    Exposure vs the static PORTFOLIO_VALUE: allocation by market-cap bucket
    and by sector (percent of portfolio, cash implicit), plus warnings —
    currently small-cap exposure above SMALLCAP_MAX_PCT.
    """
    held = [p for p in view if not p["Is_Watch"] and p["Value"]]
    caps: dict[str, float] = {}
    sectors: dict[str, float] = {}
    for p in held:
        caps[p.get("Cap") or "Unknown"] = (
            caps.get(p.get("Cap") or "Unknown", 0.0) + p["Value"])
        sectors[p.get("Sector") or "Unknown"] = (
            sectors.get(p.get("Sector") or "Unknown", 0.0) + p["Value"])

    def _pcts(d):
        return sorted(((k, round(v / PORTFOLIO_VALUE * 100, 1))
                       for k, v in d.items()),
                      key=lambda kv: -kv[1]) if PORTFOLIO_VALUE else []

    cap_pcts, sector_pcts = _pcts(caps), _pcts(sectors)
    warnings = []
    small = dict(cap_pcts).get("Small", 0.0)
    if small > SMALLCAP_MAX_PCT:
        warnings.append(f"Small-cap exposure {small:.1f}% exceeds the "
                        f"{SMALLCAP_MAX_PCT:g}% target — trim or size down")
    top_sector = sector_pcts[0] if sector_pcts else None
    if top_sector and top_sector[1] > 50:
        warnings.append(f"{top_sector[0]} concentration {top_sector[1]:.1f}% "
                        f"of portfolio — diversification risk")
    return {"caps": cap_pcts, "sectors": sector_pcts, "warnings": warnings,
            "portfolio_value": PORTFOLIO_VALUE}
