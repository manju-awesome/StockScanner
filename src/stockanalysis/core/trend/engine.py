"""
engine.py — bars in, classified rows out, snapshot on disk
===========================================================
The only module in core.trend that touches the network or the filesystem.
Everything it calls is pure, which is what lets a backtest classify any
historical date by slicing a frame and calling row_from_frame() directly.

Why a snapshot instead of a live render
---------------------------------------
The universe is the research library plus every watchlist — around 670
names. Even batched at 120 tickers a download, a full pass is a minute or
two of network, well past what an HTTP request should block on. So the
/trend page renders the last snapshot and a background job replaces it,
the shape /leaders, /compounder and /stockdaytrade already use.

Two years of bars, not one
--------------------------
The 200 SMA needs 200 bars before it exists at all, and its 10-day slope
needs 210. One year of daily bars leaves roughly forty sessions of usable
200 SMA history, and the slope at the left edge of that is computed off an
average that is still filling. Two years costs nothing extra per request —
it is the same call — and every average is fully formed.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from stockanalysis.core.longterm._common import f
from stockanalysis.core.trend import entry as E
from stockanalysis.core.trend import filters as F
from stockanalysis.core.trend import indicators as I
from stockanalysis.core.trend import regime as R
from stockanalysis.core.trend import score as S

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
SNAPSHOT = OUTPUT_DIR / "trend_regime.json"

CHUNK = 120
PERIOD = "2y"
# Below this a name has no 200 SMA and no long-term regime. It is still
# classified — the short-term structure is real — but it can never reach
# the leader filters, and the row says why rather than going blank.
MIN_BARS = 60

# Live pandas objects the classifier needs and json.dumps cannot encode.
_DROP_KEYS = ("series",)


def _plain(v):
    """numpy/pandas scalars -> builtins. yfinance hands back numpy types all
    the way through, and json.dumps refuses every one of them."""
    if v is None or isinstance(v, (str, bool, int, float, list, dict)):
        return v
    for attr in ("item", "tolist"):
        if hasattr(v, attr):
            try:
                return getattr(v, attr)()
            except Exception:
                pass
    return str(v)


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if k not in _DROP_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return _plain(obj)


def row_from_frame(ticker: str, daily, price=None, sector: str | None = None,
                   name: str | None = None) -> dict:
    """Classify one name from its daily frame. Never raises.

    The returned row is flat enough for the filters and the page to read
    without knowing which module produced which field, and carries every
    column section 14 asks for.
    """
    try:
        ind = I.compute(daily, current_price=price)
    except Exception as e:                      # a malformed frame is data,
        ind = {}                                # not a reason to stop a scan
        print(f"[Trend] {ticker}: indicators failed ({e})", file=sys.stderr)

    verdict = R.classify(ind)
    sc = S.compute(ind, verdict)
    en = E.evaluate(ind, verdict, sc)

    lt = verdict["long_term"]
    dist = ind.get("dist_pct") or {}
    row = {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "price": ind.get("price"),
        "bars": ind.get("bars"),
        "insufficient_history": bool((ind.get("bars") or 0) < MIN_BARS),

        # ── the section 14 columns ──
        "regime": verdict["regime"],
        "score": sc["score"],
        "score_band": sc["band"],
        "score_coverage": sc["coverage"],
        "score_capped": sc["capped"],
        "score_components": sc["components"],
        "long_term_trend": verdict["long_term_trend"],
        "intermediate_trend": verdict["intermediate_trend"],
        "short_term_trend": verdict["short_term_trend"],
        "long_term_regime": lt["regime"],
        "long_term_detail": lt["detail"],
        "alignment": ind.get("alignment"),
        "alignment_pct": ind.get("alignment_pct"),
        "structure": ind.get("structure"),
        "dist_8ema": dist.get("EMA8"),
        "dist_21ema": dist.get("EMA21"),
        "dist_50sma": dist.get("SMA50"),
        "dist_200sma": dist.get("SMA200"),
        "adx": ind.get("adx"),
        "rsi": ind.get("rsi"),
        "rvol": ind.get("rvol"),
        "confidence": R.confidence(ind, verdict),
        "extension_status": ind.get("extension_status"),
        "extension_flags": ind.get("extension_flags") or [],
        "pullback_status": verdict["pullback"]["status"],
        "reversal_status": verdict["reversal"]["status"],
        "momentum_status": verdict["momentum"]["state"],
        "entry_status": en["status"],
        "entry_detail": en["detail"],
        "wait_for": en["wait_for"],
        "key_reason": verdict["reason"],
        "risk": en["risk"],

        # ── the raw material the filters and the detail panel read ──
        "ma": ind.get("ma") or {},
        "slope": ind.get("slope") or {},
        "slope_state": ind.get("slope_state") or {},
        "dist_pct": dist,
        "dist_atr": ind.get("dist_atr") or {},
        "cross": ind.get("cross") or {},
        "atr": ind.get("atr"),
        "atr_pct": ind.get("atr_pct"),
        "above_200": (None if not ind.get("ma", {}).get("SMA200")
                      else f(ind.get("price")) > ind["ma"]["SMA200"]),
        "high_20d": ind.get("high_20d"),
        "low_20d": ind.get("low_20d"),
        "high_52w": ind.get("high_52w"),
        "low_52w": ind.get("low_52w"),
        "pct_below_52w_high": ind.get("pct_below_52w_high"),
        "at_20d_high": ind.get("at_20d_high"),
        "vol_trend": ind.get("vol_trend"),
        "down_vol_ratio": ind.get("down_vol_ratio"),
        "closes_below_200_10d": ind.get("closes_below_200_10d"),
        "closes_below_50_30d": ind.get("closes_below_50_30d"),
        "swing_high": ind.get("last_swing_high"),
        "swing_low": ind.get("last_swing_low"),
    }
    row["ma_slopes"] = _slope_summary(row)
    row["filters"] = [k for k, spec in F.FILTERS.items() if spec["test"](row)]
    return row


def _slope_summary(row) -> str:
    """The MA_SLOPES column: 'All rising', 'Fast rising, 200 flat', etc."""
    states = row.get("slope_state") or {}
    live = {k: states.get(k) for k in I.CORE_MAS if states.get(k) not in
            (None, "unknown")}
    if not live:
        return "unknown"
    vals = set(live.values())
    if vals == {"rising"}:
        return "All rising"
    if vals == {"falling"}:
        return "All falling"
    if vals == {"flat"}:
        return "All flat"
    return ", ".join(f"{k.replace('SMA', '').replace('EMA', '')} {v}"
                     for k, v in live.items())


# ── Section 14's one-line output ─────────────────────────────────────────

LINE_COLUMNS = (
    "TICKER", "PRICE", "TREND_REGIME", "TREND_SCORE", "LONG_TERM_TREND",
    "INTERMEDIATE_TREND", "SHORT_TERM_TREND", "MA_ALIGNMENT", "MA_SLOPES",
    "PRICE_STRUCTURE", "DIST_8EMA", "DIST_21EMA", "DIST_50SMA", "DIST_200SMA",
    "ADX", "RSI", "RVOL", "TREND_CONFIDENCE", "EXTENSION_STATUS",
    "PULLBACK_STATUS", "REVERSAL_STATUS", "ENTRY_STATUS", "KEY_REASON", "RISK",
)

_ALIGN_SHORT = {"FULL_BULLISH_ALIGNMENT": "Full bullish alignment",
                "FULL_BEARISH_ALIGNMENT": "Full bearish alignment",
                "MIXED_ALIGNMENT": "Mixed alignment"}


def format_line(row) -> str:
    """The pipe-delimited row of section 14. Missing values print as '—',
    never as 0 — a zero distance and an unmeasured one are different facts."""
    def pct(v):
        return "—" if v is None else f"{v:+.0f}%"

    def num(v, nd=0):
        return "—" if v is None else f"{v:.{nd}f}"

    return " | ".join([
        row.get("ticker") or "—",
        "—" if row.get("price") is None else f"${row['price']:,.2f}",
        row.get("regime") or "—",
        num(row.get("score")),
        row.get("long_term_trend") or "—",
        row.get("intermediate_trend") or "—",
        row.get("short_term_trend") or "—",
        _ALIGN_SHORT.get(row.get("alignment"), "—"),
        row.get("ma_slopes") or "—",
        row.get("structure") or "—",
        pct(row.get("dist_8ema")), pct(row.get("dist_21ema")),
        pct(row.get("dist_50sma")), pct(row.get("dist_200sma")),
        num(row.get("adx")), num(row.get("rsi")), num(row.get("rvol"), 1),
        row.get("confidence") or "—",
        row.get("extension_status") or "—",
        row.get("pullback_status") or "—",
        row.get("reversal_status") or "—",
        row.get("entry_status") or "—",
        row.get("key_reason") or "—",
        row.get("risk") or "—",
    ])


# ── Universe ─────────────────────────────────────────────────────────────

def universe_tickers(extra: list[str] | None = None) -> list[str]:
    """The research library plus every watchlist, deduped, order-preserving.

    Both sources rather than one: the library is what has been researched,
    the watchlists are what is being watched, and a name can easily be in
    the second without having reached the first.
    """
    tickers, seen = [], set()

    def add(t):
        t = (t or "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            tickers.append(t)

    for t in extra or ():
        add(t)
    try:
        from stockanalysis.reporting.research import load_research_index
        for t in load_research_index(OUTPUT_DIR):
            add(t)
    except Exception as e:
        print(f"[Trend] research index unavailable ({e})", file=sys.stderr)
    try:
        from stockanalysis.reporting.research import load_watchlists
        for names in (load_watchlists() or {}).values():
            for t in names or ():
                add(t)
    except Exception as e:
        print(f"[Trend] watchlists unavailable ({e})", file=sys.stderr)
    return tickers


def _sector_map() -> dict:
    try:
        from stockanalysis.reporting.research import load_research_index
        index = load_research_index(OUTPUT_DIR)
    except Exception:
        return {}
    out = {}
    for ticker, entry in (index or {}).items():
        if isinstance(entry, dict):
            out[ticker] = {"sector": entry.get("sector"),
                           "name": entry.get("company") or entry.get("name")}
    return out


def classify_universe(tickers: list[str], progress_cb=None) -> list[dict]:
    """Download bars in batches and classify each name."""
    from stockanalysis.core.daytrade.datafeed import fetch_bars

    meta = _sector_map()
    rows, done = [], 0
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        frames = fetch_bars(chunk, interval="1d", period=PERIOD, prepost=False)
        for ticker in chunk:
            frame = frames.get(ticker)
            done += 1
            if frame is None or frame.empty:
                continue
            info = meta.get(ticker) or {}
            rows.append(row_from_frame(ticker, frame, sector=info.get("sector"),
                                       name=info.get("name")))
        if progress_cb:
            progress_cb("classifying", min(done, len(tickers)), len(tickers))
        print(f"  classified {len(rows)}/{done}", file=sys.stderr)
    return rows


def scan_and_store(tickers: list[str] | None = None, progress_cb=None) -> dict:
    """Full pass over the universe, written to the snapshot."""
    tickers = tickers or universe_tickers()
    if progress_cb:
        progress_cb("fetching bars", 0, len(tickers))
    rows = classify_universe(tickers, progress_cb=progress_cb)
    rows.sort(key=lambda r: -(f(r.get("score")) or 0))
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "universe": len(tickers),
        "classified": len(rows),
        "period": PERIOD,
        "counts": F.counts(rows),
        "regime_counts": regime_counts(rows),
        "rows": rows,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(_clean(payload), indent=1))
    return payload


def regime_counts(rows) -> dict:
    out = {r: 0 for r in R.REGIMES}
    for row in rows:
        key = row.get("regime")
        if key in out:
            out[key] += 1
    return out


def load_snapshot() -> dict:
    """The last stored scan, or an empty shell if there has never been one."""
    try:
        return json.loads(SNAPSHOT.read_text())
    except Exception:
        return {"generated": None, "universe": 0, "classified": 0,
                "counts": {}, "regime_counts": {}, "rows": []}


def classify_live(tickers: list[str]) -> list[dict]:
    """Classify a handful of names right now, bypassing the snapshot.

    For the page's manual ticker box and the CLI. Capped by the caller —
    this is a foreground path and each batch is a real download.
    """
    return classify_universe([t.strip().upper() for t in tickers if t.strip()])


def _main(argv) -> int:
    args = [a for a in argv if not a.startswith("-")]
    if "--scan" in argv:
        res = scan_and_store()
        print(f"{res['classified']}/{res['universe']} classified -> {SNAPSHOT}")
        for key, spec in F.FILTERS.items():
            print(f"  {key} {spec['name']:<20} {res['counts'].get(key, 0)}")
        return 0
    if not args:
        print("usage: python -m stockanalysis.core.trend.engine NVDA AMD\n"
              "       python -m stockanalysis.core.trend.engine --scan")
        return 2
    for row in classify_live(args):
        print(format_line(row))
    return 0


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    raise SystemExit(_main(sys.argv[1:]))
