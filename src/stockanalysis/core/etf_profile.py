"""
etf_profile.py
===============
ETF-specific data the equity scan doesn't collect: what the fund is built
on, what it actually holds, what it costs to own, and how big it is.

Why this is separate from the scan pipeline
-------------------------------------------
Every fundamental the scan gathers — margins, ROE, EPS growth, debt — is
undefined for a fund. That isn't a data gap to fill; a fund has no income
statement. Running ETFs through the equity screens produces a Quality of
None and an entry gate rejection ("MarketCap<1B, Price<$5") that reads as a
verdict when it's really a category error. So ETFs get their own fields,
their own view, and their own refresh, and the equity scan is left alone.

The four things that matter for a thematic fund, and where they come from:

    theme            .info["category"] + longBusinessSummary — what index
                     or exposure the fund is built on
    top 10 holdings  .funds_data.top_holdings — name, ticker and weight,
                     which is the only way to see that two "AI" funds are
                     70% the same six companies
    expense ratio    .info["netExpenseRatio"], already a percent (0.35 =
                     0.35%/yr) — note .info["yield"] on the same object is
                     a *fraction* (0.002 = 0.2%), so the two can't share a
                     conversion
    size             .info["totalAssets"] — assets under management, the
                     honest analogue of market cap for a fund

Storage follows core/research_snapshot.py's rule: profiles merge per ticker
and a failed fetch never deletes what's already known, because the network
failing is not evidence that a fund stopped existing.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

PROFILE_FILENAME = "etf_profiles.json"

# Fields copied straight from .info, with the name they get here.
_INFO_FIELDS = (
    ("name", "longName"),
    ("family", "fundFamily"),
    ("category", "category"),
    ("theme", "longBusinessSummary"),
    ("legal_type", "legalType"),
    ("nav", "navPrice"),
    ("price", "regularMarketPrice"),
    ("prev_close", "previousClose"),
    ("aum", "totalAssets"),
    ("ytd_return", "ytdReturn"),
    ("beta_3y", "beta3Year"),
)


def profiles_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / PROFILE_FILENAME


def load_profiles(output_dir: str | Path) -> dict:
    f = profiles_path(output_dir)
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text())
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_profiles(output_dir: str | Path, profiles: dict) -> None:
    path = profiles_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profiles, indent=1, default=str))
    tmp.replace(path)


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def is_etf_row(entry: dict) -> bool:
    """The scan already labels funds with sector 'ETF' — that classification
    is what drives the ETF views, so nothing needs a second opinion."""
    if not entry:
        return False
    sector = entry.get("sector") or (entry.get("raw") or {}).get("Sector")
    return str(sector or "").strip().upper() == "ETF"


def fetch_profile(ticker: str) -> dict:
    """One fund's profile. Network call; returns {"ticker", "error"} on
    failure rather than raising, so one dead symbol can't abort a refresh."""
    import yfinance as yf

    out: dict = {"ticker": ticker,
                 "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        return {**out, "error": f"{type(e).__name__}: {e}"[:200]}

    if not info:
        return {**out, "error": "no data returned"}

    for key, src in _INFO_FIELDS:
        v = info.get(src)
        out[key] = v.strip() if isinstance(v, str) else v
    out["quote_type"] = info.get("quoteType")
    # Already a percent — do NOT scale. "yield" on the same payload is a
    # fraction, hence the ×100 there and not here.
    out["expense_ratio"] = _num(info.get("netExpenseRatio"))
    if out["expense_ratio"] is None:
        out["expense_ratio"] = _num(info.get("annualReportExpenseRatio"))
    y = _num(info.get("yield"))
    out["yield_pct"] = round(y * 100, 3) if y is not None else None

    # Today's move. regularMarketChangePercent is ALREADY a percent (1.83 =
    # +1.83%) — the opposite of "yield" two lines up, on the same payload.
    # Verified against (price - previousClose)/previousClose, which agrees to
    # three decimals, and that division is the fallback when the field is
    # absent (some funds return the prices but not the derived change).
    out["change_pct"] = _num(info.get("regularMarketChangePercent"))
    out["change_abs"] = _num(info.get("regularMarketChange"))
    px, pc = _num(info.get("regularMarketPrice")), _num(info.get("previousClose"))
    if out["change_pct"] is None and px is not None and pc:
        out["change_pct"] = round((px - pc) / pc * 100, 3)
    if out["change_abs"] is None and px is not None and pc is not None:
        out["change_abs"] = round(px - pc, 4)
    # The quote is a snapshot from this fetch, not a live feed — the views
    # timestamp it so a stale change isn't read as the current move.
    out["quote_at"] = out["updated_at"]
    for k in ("nav", "price", "prev_close", "aum", "ytd_return", "beta_3y"):
        out[k] = _num(out.get(k))

    # Price levels. Sourced here rather than read off the scan row so that
    # every fund has them — VTI/VXUS/QUAL/VTV were added after the last
    # scan and have no scan row at all, and a technicals column that is
    # blank for the newest holdings is the one you most want filled.
    out["week52_high"] = _num(info.get("fiftyTwoWeekHigh"))
    out["week52_low"] = _num(info.get("fiftyTwoWeekLow"))
    out["ma50"] = _num(info.get("fiftyDayAverage"))
    out["ma200"] = _num(info.get("twoHundredDayAverage"))
    out["ema8"] = _fetch_ema8(t)
    _attach_distances(out)

    out["holdings"] = _fetch_holdings(t)
    if out["holdings"]:
        out["top10_weight"] = round(
            sum(h["weight"] for h in out["holdings"][:10]
                if h.get("weight") is not None), 2)
    # How many holdings the provider actually publishes, which is NOT always
    # ten: Yahoo returns only 5 for some funds. Without this the weight
    # column reads as "top 10 = 61%" when it is really "top 5 = 61%, rest
    # undisclosed" — a materially different statement about concentration.
    out["holdings_count"] = len(out["holdings"])
    out["asset_mix"] = _fetch_asset_mix(t)
    out["sectors"] = _fetch_sectors(t)
    return out


def _fetch_sectors(ticker_obj) -> dict:
    """Full-fund sector weights, in percent.

    Unlike top_holdings — which is a truncated top-ten sample — these cover
    the whole portfolio and sum to ~100%. That is what makes an honest
    look-through possible: IWM publishes only 3.2% of its holdings but its
    sector weights describe all of it.
    """
    try:
        weights = ticker_obj.funds_data.sector_weightings or {}
    except Exception:
        return {}
    out = {}
    for key, frac in weights.items():
        v = _num(frac)
        if v:                                   # drop the many exact zeros
            out[str(key)] = round(v * 100, 2)
    return out


def _fetch_ema8(ticker_obj) -> float | None:
    """8-period EMA of daily closes. Not in .info — the provider publishes
    the 50 and 200 day simple averages only, so this one is computed."""
    try:
        hist = ticker_obj.history(period="3mo", interval="1d")
    except Exception:
        return None
    try:
        closes = hist["Close"].dropna()
        if len(closes) < 8:
            return None
        return round(float(closes.ewm(span=8, adjust=False).mean().iloc[-1]), 4)
    except Exception:
        return None


def _attach_distances(out: dict) -> None:
    """Percent from each level, which is what the table actually shows —
    "$580 vs a $671 high" needs mental arithmetic, "-13.6% off high" doesn't.
    Signed against price so above/below reads directly."""
    price = _num(out.get("price")) or _num(out.get("nav"))
    if price is None:
        return
    for key, level_key in (("dist_52w_high", "week52_high"),
                           ("dist_52w_low", "week52_low"),
                           ("dist_ma50", "ma50"), ("dist_ma200", "ma200"),
                           ("dist_ema8", "ema8")):
        level = _num(out.get(level_key))
        if level:
            out[key] = round((price - level) / level * 100, 2)


def _fetch_asset_mix(ticker_obj) -> dict:
    """Stock / cash / other split. `other` is the tell for a fund that takes
    part of its exposure through swaps rather than owning shares — those
    positions never appear in the holdings list at all."""
    try:
        mix = ticker_obj.funds_data.asset_classes or {}
    except Exception:
        return {}
    out = {}
    for key, src in (("stock", "stockPosition"), ("cash", "cashPosition"),
                     ("bond", "bondPosition"), ("other", "otherPosition")):
        v = _num(mix.get(src))
        if v is not None:
            out[key] = round(v * 100, 2)     # fractions on this payload
    return out


def _fetch_holdings(ticker_obj, limit: int = 10) -> list[dict]:
    """Top holdings as [{ticker, name, weight%}]. Best-effort: a fund with
    no published holdings (commodity trusts like GLD hold bullion, not
    equities) legitimately returns none."""
    try:
        df = ticker_obj.funds_data.top_holdings
    except Exception:
        return []
    if df is None or not hasattr(df, "iterrows") or df.empty:
        return []
    rows = []
    for symbol, row in df.head(limit).iterrows():
        pct = _num(row.get("Holding Percent"))
        rows.append({
            "ticker": str(symbol),
            "name": str(row.get("Name") or symbol),
            # yfinance returns a fraction (0.2083); the UI wants 20.83%.
            "weight": round(pct * 100, 2) if pct is not None else None,
        })
    return rows


def refresh_profiles(tickers: Iterable[str], output_dir: str | Path,
                     progress_cb: Callable[[str, int, int], None] | None = None
                     ) -> dict:
    """Fetch and merge profiles for `tickers`. Returns a summary dict.

    Merged, not replaced: a fetch that fails leaves the previous profile in
    place. The alternative — blanking a fund because Yahoo timed out — is
    the same failure this project has already been bitten by twice.
    """
    tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    out_dir = Path(output_dir)
    profiles = load_profiles(out_dir)
    ok = failed = 0
    errors: list[str] = []

    for i, ticker in enumerate(tickers):
        if progress_cb:
            try:
                progress_cb(f"fetch:{ticker}", i, len(tickers))
            except Exception:
                pass
        fresh = fetch_profile(ticker)
        if fresh.get("error"):
            failed += 1
            errors.append(f"{ticker}: {fresh['error']}")
            # Keep whatever we already had; only record that it went stale.
            prev = profiles.get(ticker)
            if prev:
                prev["last_error"] = fresh["error"]
                prev["last_error_at"] = fresh["updated_at"]
            else:
                profiles[ticker] = fresh
            continue
        ok += 1
        profiles[ticker] = {**(profiles.get(ticker) or {}), **fresh}
        profiles[ticker].pop("last_error", None)
        profiles[ticker].pop("last_error_at", None)

    save_profiles(out_dir, profiles)
    return {"requested": len(tickers), "ok": ok, "failed": failed,
            "errors": errors[:10], "total": len(profiles)}


# User-set theme. Kept under its own key rather than overwriting `category`
# so a refresh can't clobber it: refresh_profiles merges {**prev, **fresh},
# and `fresh` never carries this key, so the label survives every re-fetch.
THEME_KEY = "theme_label"


def display_theme(profile: dict) -> str | None:
    """What to show in the Theme column: the user's label if they set one,
    otherwise the provider's category.

    The provider's categories are broad to the point of being unhelpful for
    thematic funds — SMH and IGV are both "Technology", NLR is "Natural
    Resources" — so the override is usually the more informative label.
    """
    if not profile:
        return None
    return profile.get(THEME_KEY) or profile.get("category")


def set_theme(output_dir: str | Path, ticker: str,
              label: str | None) -> dict:
    """Set (or clear, with a blank label) the user's theme for one fund."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"ok": False, "message": "no ticker given"}
    profiles = load_profiles(output_dir)
    entry = dict(profiles.get(ticker) or {})
    if not entry:
        # Allow labelling a fund whose profile hasn't been fetched yet.
        entry = {"ticker": ticker}
    label = (label or "").strip()
    if label:
        entry[THEME_KEY] = label[:60]
    else:
        entry.pop(THEME_KEY, None)
    profiles[ticker] = entry
    save_profiles(output_dir, profiles)
    return {"ok": True, "ticker": ticker,
            "theme": display_theme(entry),
            "custom": bool(entry.get(THEME_KEY)),
            "message": (f"{ticker} theme set to “{label}”" if label
                        else f"{ticker} theme reset to the provider category")}


# Reference indices, not holdings. Shown in their own strip so a thematic
# fund is compared against the market rather than sitting in the same list as
# it — SMH being up 50% YTD means something different next to QQQ's number.
# IWM and VTI appear here and in the holdings table on purpose: they are
# genuinely both, and hiding either would misrepresent one of the two roles.
# ^VIX is an index, not a fund — no expense ratio, AUM or holdings — but it
# belongs on the strip because fear is the other half of the market read.
# Its direction is INVERTED relative to every other row here: VIX above its
# 200-day means rising fear, which is risk-off, so anything colouring "up =
# green" has to special-case it or it will report panic as strength.
BENCHMARKS = ("SPY", "VOO", "QQQ", "QQQM", "DIA", "MDY", "IWM", "VTI", "^VIX")
INVERTED = ("^VIX",)


def is_inverted(ticker: str) -> bool:
    """True when a rising value is a deterioration, not an improvement."""
    return str(ticker or "").upper() in INVERTED


def display_ticker(ticker: str) -> str:
    """^VIX reads as VIX everywhere a human sees it."""
    return str(ticker or "").lstrip("^")


def is_benchmark(ticker: str) -> bool:
    return str(ticker or "").upper() in BENCHMARKS


def etf_tickers(entries: Iterable[dict]) -> list[str]:
    """Every ETF in the research library, by the scan's own classification."""
    return sorted({str(e.get("ticker")) for e in entries
                   if e.get("ticker") and is_etf_row(e)})


def attach_profiles(rows: list[dict], profiles: dict) -> list[dict]:
    """Fold profile fields onto library rows for the ETF views. Only touches
    ETF rows, so an equity row is never given an expense ratio."""
    for r in rows:
        if not is_etf_row(r):
            continue
        p = profiles.get(r.get("ticker")) or {}
        if not p:
            continue
        r["etf"] = True
        for k in ("family", "category", "theme", "expense_ratio", "aum",
                  "yield_pct", "ytd_return", "beta_3y", "nav", "holdings",
                  "top10_weight", "change_pct", "change_abs", "prev_close",
                  "quote_at", "holdings_count", "asset_mix", "sectors",
                  "week52_high", "week52_low", "ma50", "ma200", "ema8",
                  "dist_52w_high", "dist_52w_low", "dist_ma50", "dist_ma200",
                  "dist_ema8"):
            if p.get(k) is not None:
                r[f"etf_{k}"] = p[k]
        # Resolved label plus a flag, so the UI can show which funds you've
        # renamed and which are still the provider's wording.
        r["etf_theme_name"] = display_theme(p)
        r["etf_theme_custom"] = bool(p.get(THEME_KEY))
        # The fund's own quote is more current than a stale index entry.
        if p.get("price") is not None:
            r.setdefault("price", p["price"])
    return rows
