"""
scan_daytrade.py
================
CLI for the stock day-trade engine (core/daytrade).

This is a different question from scan_universe.py and deliberately shares
none of its scoring. That scanner grades swing setups and reads
fundamentals; this one ranks intraday tradeable volatility and reads none.
A stock can be an "Avoid" there and enterable here without either being
wrong — they are not measuring the same thing.

Examples
--------
    python -m stockanalysis.scanners.scan_daytrade
    python -m stockanalysis.scanners.scan_daytrade --profile large
    python -m stockanalysis.scanners.scan_daytrade --limit 40 --scorecards
    python -m stockanalysis.scanners.scan_daytrade --tickers AAPL RCEL --profile auto
    python -m stockanalysis.scanners.scan_daytrade --risk-pct 0.5 --save

Run it premarket for §5 structure, and again after 09:45 once the opening
range exists. Outside market hours it reports the last completed session
and says so — every level is that session's, not a live one.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stockanalysis.core.daytrade import plan as P            # noqa: E402
from stockanalysis.core.daytrade import report as RPT        # noqa: E402
from stockanalysis.core.daytrade import scan as SCAN         # noqa: E402
from stockanalysis.core.daytrade import store as STORE       # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Stock day-trade momentum scanner (small/mid/large-cap profiles)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--limit", type=int, default=25,
                        help="how many screened names get full analysis (default 25)")
    parser.add_argument("--tickers", nargs="+", metavar="TICKER",
                        help="skip the §1 screen and analyse these symbols")
    parser.add_argument("--min-grade", default="C",
                        choices=["A+", "A", "B+", "B", "C", "ALL"],
                        help="hide rows below this grade (default C)")
    parser.add_argument("--risk-pct", type=float,
                        help="account %% risked per trade (default 1.0; account "
                             "capital comes from data/risk_settings.json)")
    parser.add_argument("--capital", type=float, help="override account capital")
    parser.add_argument("--profile", default="small",
                        choices=["small", "mid", "large", "auto"],
                        help="market-cap calibration (default small). 'auto' "
                             "judges each name against its own cap band — use "
                             "it with --tickers, since a screen needs a band")
    parser.add_argument("--at-time", metavar="HH:MM",
                        help="replay the session as of this ET time (e.g. 10:15) — "
                             "nothing later is visible to the scan")
    parser.add_argument("--scorecards", action="store_true",
                        help="show the §10 arithmetic behind every confluence score")
    parser.add_argument("--save", action="store_true",
                        help="write the §16 markdown table and full JSON to data/output/")
    parser.add_argument("--out-dir", type=Path, default=OUTPUT_DIR,
                        help="where --save writes (default data/output)")
    args = parser.parse_args()

    at_time = None
    if args.at_time:
        try:
            at_time = datetime.strptime(args.at_time, "%H:%M").time()
        except ValueError:
            parser.error(f"--at-time must be HH:MM, got {args.at_time!r}")

    settings = P.load_settings({"risk_pct": args.risk_pct, "capital": args.capital})
    result = SCAN.run(limit=args.limit, tickers=args.tickers, settings=settings,
                      at_time=at_time, profile=args.profile,
                      progress_cb=lambda m: print(f"  · {m}", file=sys.stderr))

    if args.min_grade != "ALL":
        order = ["A+", "A", "B+", "B", "C"]
        keep = set(order[:order.index(args.min_grade) + 1])
        shown = [r for r in result["rows"] if r.get("grade") in keep]
        hidden = len(result["rows"]) - len(shown)
        if hidden:
            result["notes"].append(
                f"{hidden} row(s) below grade {args.min_grade} hidden — use --min-grade ALL")
        result = {**result, "rows": shown}

    print(RPT.render(result, show_scorecards=args.scorecards))

    if args.save:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        md = args.out_dir / f"daytrade_scan_{stamp}.md"
        js = args.out_dir / f"daytrade_scan_{stamp}.json"
        md.write_text(
            f"# Stock day-trade scan — session {result.get('asof')}\n\n"
            + RPT.render_regime(result.get("regime")) + "\n\n"
            + RPT.render_table(result["rows"]) + "\n")
        STORE.save(result, js)
        print(f"\nSaved:\n  {md}\n  {js}", file=sys.stderr)
        # The webapp reads one fixed path. Only refresh it when the run was
        # a full scan writing to the real output directory — an ad-hoc
        # --tickers run or one aimed at a scratch directory must not
        # overwrite the page's snapshot with three rows.
        if args.out_dir == OUTPUT_DIR and not args.tickers:
            STORE.save(result)
            print(f"  {STORE.SNAPSHOT}  (page snapshot)", file=sys.stderr)


if __name__ == "__main__":
    cli()
