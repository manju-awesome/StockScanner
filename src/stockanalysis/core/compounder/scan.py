"""
scan.py — run the compounder scan and store it.
===============================================
    python -m stockanalysis.core.compounder.scan
    python -m stockanalysis.core.compounder.scan --tickers ALAB RKLB CRDO
    python -m stockanalysis.core.compounder.scan --theme nuclear
    python -m stockanalysis.core.compounder.scan --list-themes

A full run is roughly six network calls per company across the whole theme
library, so it belongs on the command line or the scheduler rather than in a
request. The page reads what this writes.

A targeted run (--tickers / --theme) MERGES into the stored snapshot rather
than replacing it, so refreshing six names after their earnings does not
delete the other hundred and twenty.
"""

from __future__ import annotations

import argparse
import sys

from . import engine as CE
from . import store as CS
from . import themes as TH


def _progress(done, total, ticker):
    bar_width = 28
    filled = int(bar_width * done / max(total, 1))
    sys.stdout.write(
        f"\r  [{'█' * filled}{'·' * (bar_width - filled)}] "
        f"{done}/{total}  {ticker:<6}")
    sys.stdout.flush()
    if done == total:
        sys.stdout.write("\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Future Compounder / Emerging Leader scan")
    p.add_argument("--tickers", nargs="+",
                   help="scan only these (merges into the stored snapshot)")
    p.add_argument("--theme", help="scan only one theme's members")
    p.add_argument("--list-themes", action="store_true",
                   help="print the theme library and exit")
    p.add_argument("--top", type=int, default=20,
                   help="watchlist size (default 20)")
    p.add_argument("--no-save", action="store_true",
                   help="print results without writing the snapshot")
    args = p.parse_args(argv)

    if args.list_themes:
        cov = TH.coverage()
        print(f"\n{len(TH.THEMES)} themes, {len(TH.universe())} tickers\n")
        for key, n in cov.items():
            t = TH.theme(key)
            print(f"  {key:<28} {n:>3} names  "
                  f"TAM ${t['tam_now'] / 1e9:,.0f}B → "
                  f"${(t.get('tam_5y') or 0) / 1e9:,.0f}B "
                  f"({t.get('tam_cagr_5y')}%/yr, {t['confidence']})")
            print(f"  {'':<28}     {', '.join(TH.members(key))}")
        return 0

    tickers = args.tickers
    if args.theme:
        if args.theme not in TH.THEMES:
            print(f"Unknown theme '{args.theme}'. "
                  f"Options: {', '.join(sorted(TH.THEMES))}")
            return 2
        tickers = TH.members(args.theme)

    targeted = bool(tickers)
    tickers = [t.upper() for t in (tickers or TH.universe())]

    unmapped = [t for t in tickers if t not in TH.THEME_MEMBERS]
    if unmapped:
        # Scored anyway — the secular and opportunity legs report unmeasured
        # and blend() renormalises. Named so the reader knows the score is
        # missing 30% of the intended weight rather than wondering why it
        # looks low.
        print(f"  ⚠ no theme mapped for {', '.join(unmapped)} — these will "
              f"score without the TAM legs")

    print(f"\nScanning {len(tickers)} companies…")
    rows = CE.evaluate_universe(tickers, progress=_progress)

    wl = CE.watchlist(rows, args.top)

    print(f"\n{'':2}{'#':<3}{'TICKER':<8}{'SCORE':<7}{'STAGE':<14}"
          f"{'GROWTH':<9}{'FUNDING':<20}THEME")
    for i, r in enumerate(wl["rows"], 1):
        g = (r.get("growth") or {}).get("latest")
        print(f"{'':2}{i:<3}{r['ticker']:<8}"
              f"{(r.get('score') or 0):<7.0f}"
              f"{(r.get('stage') or {}).get('label', '?'):<14}"
              f"{(f'{g:.0f}%' if g is not None else '—'):<9}"
              f"{(r.get('survivability') or {}).get('classification', '?'):<20}"
              f"{r.get('theme_label') or '—'}")

    if wl.get("concentration_note"):
        print(f"\n  ⚠ {wl['concentration_note']}")
    if wl.get("excluded_mature"):
        print(f"\n  Scored well but already mature, so off a ten-year list: "
              + ", ".join(f"{e['ticker']} ({e['score']})"
                          for e in wl["excluded_mature"][:10]))
    if wl.get("excluded_thin"):
        print(f"  Held off the list for thin data (not for a low score): "
              f"{', '.join(e['ticker'] for e in wl['excluded_thin'][:10])}")
    if wl.get("unscored"):
        print(f"  Unscorable — nothing measurable came back: "
              f"{', '.join(wl['unscored'][:10])}")

    if not args.no_save:
        path = CS.save(rows, wl, meta={"targeted": targeted,
                                       "requested": tickers},
                       merge=targeted)
        print(f"\n  Saved → {path}"
              + ("  (merged into the existing snapshot)" if targeted else ""))
        if targeted:
            # The stored watchlist was recomputed from the MERGED row set, so
            # it can differ from the table printed above, which only covers
            # the names this run touched.
            merged = CS.load() or {}
            full = CE.watchlist(merged.get("rows") or [], args.top)
            CS.save(merged.get("rows") or [], full,
                    meta=merged.get("meta"), merge=False)
            print(f"  Watchlist recomputed across all "
                  f"{len(merged.get('rows') or [])} stored names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
