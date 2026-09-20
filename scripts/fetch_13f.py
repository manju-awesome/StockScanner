#!/usr/bin/env python3
"""
fetch_13f.py
============
Institutional long call/put positions for one or more tickers, from the
SEC's bulk Form 13F datasets.

The first run downloads a ~95 MB quarterly zip into data/cache/13f/ and
builds a CUSIP index; both are reused after that, so only the first call
of a new quarter is slow.

    python scripts/fetch_13f.py NVDA AAPL
    python scripts/fetch_13f.py NVDA --json
    python scripts/fetch_13f.py --leaderboard Put --limit 20
    python scripts/fetch_13f.py NVDA --period 2025-12-31

Needs a contact address for the SEC. Set SEC_USER_AGENT in .env, or let it
reuse ALERT_EMAIL_TO.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core.institutional import engine as E   # noqa: E402
from stockanalysis.core.institutional import datasets as DS  # noqa: E402


def _load_env() -> None:
    """Read .env without a dependency — only for keys not already set."""
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    import os
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if not key:                      # this .env has a stray bare "=" line
            continue
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _money(v: float) -> str:
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= size:
            return f"${v / size:,.1f}{unit}"
    return f"${v:,.0f}"


def _print_side(label: str, side: dict) -> None:
    print(f"  {label:<6} {side['holders']:>5} holders   "
          f"{side['contracts']:>14,.0f} contracts   "
          f"{_money(side['value_usd']):>10}")
    for row in side["top"][:8]:
        flag = "  [rescaled]" if row["units"] == "contracts" else ""
        print(f"      {row['manager'][:44]:<44} "
              f"{row['contracts']:>12,.0f}{flag}")


def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*", help="tickers to report on")
    ap.add_argument("--period", help="report period YYYY-MM-DD (quarter end)")
    ap.add_argument("--leaderboard", choices=["Put", "Call"],
                    help="largest reported books by issuer instead of per-ticker")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--top", type=int, default=15, help="holders shown per side")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rebuild-index", action="store_true",
                    help="rebuild the CUSIP name index (slow, full table pass)")
    args = ap.parse_args()

    if not args.tickers and not args.leaderboard:
        ap.error("give at least one ticker, or --leaderboard")

    period = _dt.date.fromisoformat(args.period) if args.period else None
    try:
        ctx = E.prepare(period=period, rebuild_index=args.rebuild_index)
    except DS.SECAccessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    scan, fset = ctx["scan"], ctx["filings"]
    if not args.json:
        lag = (_dt.date.today() - ctx["period"]).days
        print(f"\nPeriod {ctx['period']} — {lag} days old   ({ctx['dataset']})")
        print(f"{fset.managers:,} managers, {len(fset.filings):,} filings kept, "
              f"{len(fset.superseded):,} superseded, {fset.notices:,} notices")
        print(f"{scan.option_rows:,} option rows over {scan.share_rows:,} share rows")
        print("Long positions only — written options are not reported on 13F.\n")

    if args.leaderboard:
        rows = E.leaderboard(ctx, args.leaderboard, args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        print(f"Largest reported long {args.leaderboard.upper()} books\n")
        print(f"{'':4}{'issuer':<34} {'cusip':<10} {'contracts':>13} "
              f"{'holders':>8}  rescaled")
        for i, r in enumerate(rows, 1):
            share = r["rescaled_share"]
            note = f"{share:>6.0%}" if share else "     -"
            print(f"{i:>3}. {r['issuer'][:34]:<34} {r['cusip']:<10} "
                  f"{r['contracts']:>13,.0f} {r['holders']:>8}  {note}")
        print("\n  rescaled = share of the total coming from rows the units "
              "gate reinterpreted\n  as contracts-per-share. A high figure "
              "means the ranking rests on a reading\n  of the filing, not on "
              "the filing as written.")
        return 0

    payloads = [E.for_ticker(t, ctx, top_n=args.top) for t in args.tickers]
    if args.json:
        print(json.dumps(payloads, indent=2))
        return 0

    for p in payloads:
        print(f"{p['ticker']}  {p.get('issuer', '')}")
        if p.get("error"):
            print(f"  {p['error']}")
            for c in p.get("candidates", []):
                print(f"      {c['cusip']}  {c['rows']:>7,} rows  {c['issuer'][:40]}")
            print()
            continue
        print(f"  CUSIP {p['cusip']} ({p['cusip_status']}, "
              f"confidence {p['cusip_confidence']:.0%})"
              + (f"   ref price ${p['reference_price']:,.2f}"
                 if p.get("reference_price") else ""))
        _print_side("CALLS", p["calls"])
        _print_side("PUTS", p["puts"])
        ratio = p["put_call_ratio_contracts"]
        print(f"  put/call by contracts: "
              + (f"{ratio:.2f}" if ratio is not None else "n/a"))
        if p["unreliable_rows"] or p["rescaled_rows"]:
            print(f"  units gate: {p['rescaled_rows']} rescaled, "
                  f"{p['unreliable_rows']} excluded as unreliable")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
