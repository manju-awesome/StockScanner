"""
The institutional options engine — orchestration.

    1. Pick the newest report period that a bulk zip actually covers.
    2. Resolve which filings describe that period (amendments applied).
    3. Stream the information table, keeping long option rows.
    4. Normalise units against each CUSIP's reference price.
    5. Aggregate per issuer, and per ticker once a CUSIP resolves.

WHAT THE OUTPUT IS AND IS NOT
-----------------------------
It is: every institution that reported holding calls or puts on a name at
quarter end, with size, sourced from filings those institutions signed.

It is not flow, not sentiment, and not the whole market. Three limits ride
on every number and are carried in the payload rather than left in a
docstring:

  * LONG ONLY. Written options do not appear in 13F at all. A large put
    line means someone bought downside; nothing here says who sold it.
  * QUARTER-END SNAPSHOT. Positions opened and closed inside the quarter
    are invisible, and the photograph is up to 45 days old before it is
    even filed.
  * 13F FILERS ONLY. The $100M discretionary-AUM threshold excludes market
    makers acting as such, most of the options market's actual volume, and
    every foreign manager without a US filing obligation.

A put/call ratio computed from this is a ratio of REPORTED LONG POSITIONS
among filers, which is a much narrower claim than "the put/call ratio",
and `ratio_basis` says so in words so the number cannot travel without it.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import defaultdict
from pathlib import Path

from . import cusip as CU
from . import store as ST
from . import datasets as DS
from . import filings as FL
from . import holdings as HD

CACHE_INDEX = CU.DATA_DIR / "cusip_index.json"

RATIO_BASIS = (
    "Long option positions reported on Form 13F by filers with >$100M in "
    "US discretionary AUM, as held on the period end date. Excludes all "
    "written/short options, all non-filers, and all intra-quarter activity.")


def _blank(side: str) -> dict:
    return {"side": side, "holders": 0, "contracts": 0.0,
            "underlying_shares": 0.0, "value_usd": 0.0, "top": []}


def prepare(period: _dt.date | None = None,
            cache_dir: Path | None = None,
            rebuild_index: bool = False) -> dict:
    """Download, resolve filings and scan. Returns the shared context that
    every per-ticker query reads, so the 400 MB pass happens once."""
    dss = DS.available_datasets()
    if period is None:
        period, ds = DS.latest_complete_period(datasets=dss)
    else:
        ds = DS.dataset_for_period(period, dss)
        if ds is None:
            raise DS.SECAccessError(
                f"No published bulk dataset covers period {period}. The zip "
                "lands roughly two weeks after the filing deadline; until "
                "then that quarter is only on EDGAR filing by filing.")

    zip_path = DS.ensure_dataset(ds, cache_dir=cache_dir)
    filing_set = FL.load_filings(zip_path, period)
    scan = HD.scan(zip_path, filing_set.accessions)

    index = display = None
    if not rebuild_index and CACHE_INDEX.exists():
        try:
            index, display = CU.load_index(CACHE_INDEX)
        except (CU.StaleIndex, json.JSONDecodeError, OSError):
            index = None      # rebuilt below; a stale index resolves nothing
    if index is None:
        index = CU.build_name_index(zip_path)
        CU.save_index(index, CACHE_INDEX)
        display = getattr(CU.build_name_index, "display", {})

    by_cusip: dict[str, list[HD.OptionRow]] = defaultdict(list)
    for row in scan.options:
        by_cusip[row.cusip].append(row)

    return {
        "period": period,
        "dataset": ds.filename,
        "zip_path": zip_path,
        "filings": filing_set,
        "scan": scan,
        "by_cusip": by_cusip,
        "name_index": index,
        "display": display,
        "ticker_index": CU.fetch_ticker_index(),
        "overrides": CU.load_overrides(),
        "fund_names": CU.load_fund_names(),
    }


def _aggregate(rows: list[HD.OptionRow], side: str,
               managers: dict[str, FL.Filing], top_n: int) -> dict:
    picked = [r for r in rows if r.put_call.lower() == side.lower() and r.reliable]
    if not picked:
        return _blank(side)
    out = _blank(side)
    out["holders"] = len({managers[r.accession].cik
                          for r in picked if r.accession in managers})
    out["underlying_shares"] = sum(r.underlying_shares for r in picked)
    out["contracts"] = out["underlying_shares"] / 100.0
    out["value_usd"] = sum(r.value_usd for r in picked
                           if r.units == HD.UNITS_SHARES)
    ranked = sorted(picked, key=lambda r: r.underlying_shares, reverse=True)
    out["top"] = [{
        "manager": managers[r.accession].manager if r.accession in managers else "",
        "cik": managers[r.accession].cik if r.accession in managers else "",
        "contracts": round(r.contracts, 1),
        "underlying_shares": round(r.underlying_shares),
        "value_usd": round(r.value_usd),
        "units": r.units,
    } for r in ranked[:top_n]]
    return out


def for_ticker(ticker: str, ctx: dict, top_n: int = 15) -> dict:
    """Institutional long calls and puts on one ticker."""
    res = CU.resolve(ticker, ctx["name_index"], ctx["ticker_index"],
                     ctx["overrides"], ctx["display"],
                     fund_names=ctx.get("fund_names"))
    payload = {
        "ticker": ticker.upper(),
        "period": ctx["period"].isoformat(),
        "dataset": ctx["dataset"],
        "cusip": res.cusip,
        "cusip_status": res.status,
        "cusip_confidence": round(res.confidence, 3),
        "issuer": res.issuer,
        "candidates": res.candidates,
        "ratio_basis": RATIO_BASIS,
        "long_only": True,
    }
    if not res.usable:
        payload["error"] = (
            f"CUSIP for {ticker.upper()} is {res.status}. "
            "Pin it in data/institutional/cusip_overrides.json — candidates "
            "are listed above with the row count behind each.")
        return payload

    rows = ctx["by_cusip"].get(res.cusip, [])
    managers = ctx["filings"].filings
    calls = _aggregate(rows, "Call", managers, top_n)
    puts = _aggregate(rows, "Put", managers, top_n)

    payload["calls"] = calls
    payload["puts"] = puts
    payload["put_call_ratio_contracts"] = (
        round(puts["contracts"] / calls["contracts"], 3)
        if calls["contracts"] else None)
    payload["unreliable_rows"] = sum(1 for r in rows if not r.reliable)
    payload["rescaled_rows"] = sum(1 for r in rows
                                   if r.units == HD.UNITS_CONTRACTS)
    payload["reference_price"] = round(
        ctx["scan"].reference_price.get(res.cusip, 0.0), 2) or None
    return payload


def leaderboard(ctx: dict, side: str = "Put", limit: int = 25) -> list[dict]:
    """The largest reported long option books by issuer, either side.

    Ranked on underlying shares rather than filed VALUE: VALUE is the one
    field filers treat inconsistently for options (some report underlying
    value, some report premium), while normalised share counts have already
    been through the units gate.

    `rescaled_share` is not decoration. A rescale is a REINTERPRETATION of
    what a filer meant, and at the top of a leaderboard one rescaled row can
    outweigh every honest one beneath it. The sample quarter has an
    Eversource put line where a single row reads 2.2M contracts after
    rescaling — ~60% of the company's shares outstanding, held by one filer
    — and it alone lifts the name into the global top fifteen.

    That row is genuinely undecidable from the filing: either SSHPRNAMT is
    contracts and VALUE is right, or SSHPRNAMT is shares and VALUE is 100x
    too large. The gate trusts VALUE, because VALUE is what the share rows
    agree on. But a reader ranking names by put interest deserves to see
    that the ranking rests on a reinterpreted row, so the proportion rides
    along with the number instead of being averaged into it.

    `issuer` carries the CUSIP alongside it because issuer names are not
    unique per security: every iShares ETF files as "ISHARES TR", and a
    leaderboard of eight identical names is unreadable without it.
    """
    totals: list[dict] = []
    for cusip, rows in ctx["by_cusip"].items():
        picked = [r for r in rows
                  if r.put_call.lower() == side.lower() and r.reliable]
        if not picked:
            continue
        shares = sum(r.underlying_shares for r in picked)
        rescaled = sum(r.underlying_shares for r in picked
                       if r.units == HD.UNITS_CONTRACTS)
        totals.append({
            "cusip": cusip,
            "issuer": ctx["display"].get(cusip, picked[0].issuer),
            "holders": len({r.accession for r in picked}),
            "contracts": round(shares / 100.0, 1),
            "rescaled_share": round(rescaled / shares, 3) if shares else 0.0,
        })
    totals.sort(key=lambda d: d["contracts"], reverse=True)
    return totals[:limit]


def build_snapshot(tickers: list[str], top_n: int = 15,
                   board_limit: int = 25, progress=None) -> dict:
    """One run: resolve the quarter, scan it, and store what the page needs.

    Every ticker is kept in the payload including the ones that failed to
    resolve, carrying their candidate CUSIPs. A name that silently vanished
    from the table would read as "no institution holds options on it",
    which is a claim about the market rather than about our plumbing.
    """
    def step(msg):
        if progress is not None:
            progress.stage(msg)

    step("resolving the newest published quarter")
    ctx = prepare()

    step(f"reading {len(tickers)} tickers off {ctx['dataset']}")
    rows = [for_ticker(t, ctx, top_n=top_n) for t in tickers]

    # Second pass over the leftovers only. ETFs are absent from the SEC's
    # registrant file, so they all land here; naming them costs one network
    # call each and turns "unknown" into the index positioning that is half
    # the point of the page.
    unresolved = [r["ticker"] for r in rows if r.get("error")]
    if unresolved:
        step(f"naming {len(unresolved)} unresolved tickers")
        ctx["fund_names"] = CU.learn_fund_names(unresolved)
        by_ticker = {r["ticker"]: i for i, r in enumerate(rows)}
        for ticker in unresolved:
            rows[by_ticker[ticker]] = for_ticker(ticker, ctx, top_n=top_n)

    step("ranking the largest reported books")
    scan, fset = ctx["scan"], ctx["filings"]
    payload = {
        "period": ctx["period"].isoformat(),
        "dataset": ctx["dataset"],
        "universe": [t.upper() for t in tickers],
        "tickers": rows,
        "leaderboard_put": leaderboard(ctx, "Put", board_limit),
        "leaderboard_call": leaderboard(ctx, "Call", board_limit),
        "ratio_basis": RATIO_BASIS,
        "coverage": {
            "managers": fset.managers,
            "filings_kept": len(fset.filings),
            "superseded": len(fset.superseded),
            "notices": fset.notices,
            "option_rows": scan.option_rows,
            "share_rows": scan.share_rows,
            "unreliable_rows": sum(1 for r in scan.options if not r.reliable),
            "rescaled_rows": sum(1 for r in scan.options
                                 if r.units == HD.UNITS_CONTRACTS),
        },
    }
    step("saving")
    ST.save(payload)
    return payload
