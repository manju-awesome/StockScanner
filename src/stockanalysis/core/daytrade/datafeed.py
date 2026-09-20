"""
datafeed.py — every network call the day-trade engine makes
===========================================================
All I/O is quarantined here so that §1-§17 can be pure functions of data
already in memory, and therefore unit-testable against hand-built frames
with no mocking beyond this one module.

Universe discovery uses `yf.screen()` with an `EquityQuery`, which screens
server-side across the whole US market. That matters more than it sounds:
a small-cap gapper scanner has no fixed universe to iterate — the whole
point is that today's candidates were not on any list yesterday — and
pulling 4,000 tickers through per-ticker yfinance calls to find the 20 that
moved would be throttled into uselessness. It also keeps the project's
stated "yfinance is the only market-data dependency" rule intact, which a
broker-side screener would have broken.

Rate limiting
-------------
Yahoo throttles hard under scanning load. The retry policy is lifted from
core/metrics.py (`_is_throttle` / `_with_retry`): back off on 429s only,
raise immediately on anything else, because retrying a delisted symbol just
stalls the loop. Bars are batch-downloaded per interval rather than per
ticker — one call for 25 symbols instead of 25 — which is the single
biggest reason a scan of this size completes at all.
"""

from __future__ import annotations

import logging
import time as _time

import pandas as pd

try:
    import yfinance as yf
    from yfinance import EquityQuery
except ImportError:  # pragma: no cover - matches metrics.py's guard
    raise SystemExit("yfinance not installed. Run: pip install yfinance")

from stockanalysis.core.daytrade._common import MARKET_TZ, to_et

log = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0

# Benchmarks for §7 and §15. IWM is the one that actually matters for this
# strategy — small caps track the Russell far more closely than the S&P —
# but SPY/QQQ are carried because §7 names them explicitly.
BENCHMARKS = ("SPY", "QQQ", "IWM")
VIX_TICKER = "^VIX"

# yfinance sector string → sector ETF, for §7's "relative strength vs
# sector". Deliberately the liquid large-cap sector ETFs: the question is
# whether the sector is bid today, and the sector ETFs answer that with
# tight prints, which small-cap sector proxies do not.
SECTOR_ETF = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}

# Biotech is the dominant small-cap catalyst sector and XLV — half of it
# UnitedHealth and Lilly — does not represent it. When the industry says
# biotech, XBI is the honest comparison.
_BIOTECH_ETF = "XBI"


def _is_throttle(e: Exception) -> bool:
    s = str(e).lower()
    return "rate limit" in s or "too many requests" in s or "429" in s


def _with_retry(fn, ticker: str, what: str):
    """fn() with backoff on rate-limit errors ONLY."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return fn()
        except Exception as e:
            if not _is_throttle(e) or attempt == RETRY_ATTEMPTS - 1:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            log.warning("%s: %s throttled — retrying in %.0fs", ticker, what, delay)
            _time.sleep(delay)


# ── §1 universe discovery ────────────────────────────────────────────────────

def screen_movers(max_market_cap: float = 2_000_000_000,
                  price_min: float = 2.0, price_max: float = 30.0,
                  min_day_volume: int = 500_000,
                  min_abs_change_pct: float = 3.0,
                  size: int = 100,
                  min_market_cap: float = 0.0,
                  profile: dict | None = None) -> list[dict]:
    """Server-side screen for movers in a profile's cap band, both directions.

    Two queries rather than one because `EquityQuery` has no absolute-value
    predicate and the engine classifies shorts (§17) as well as longs — a
    -22% halted-and-resumed dumper is a day-trade candidate in exactly the
    way the spec means, and screening only for gainers would silently make
    this a long-only scanner while still printing a SHORT label it could
    never reach.
    """
    # A profile supplies the whole band in one argument, so callers do not
    # have to keep four numbers consistent with each other.
    if profile is not None:
        max_market_cap = profile["market_cap_max"]
        min_market_cap = profile["market_cap_min"]
        price_min, price_max = profile["price_min"], profile["price_max"]
        min_abs_change_pct = profile["screen_min_abs_change"]
        min_day_volume = int(profile["min_avg_volume"])

    rows: dict[str, dict] = {}
    for direction, predicate in (("up", "gt"), ("down", "lt")):
        threshold = min_abs_change_pct if direction == "up" else -min_abs_change_pct
        terms = [
            EquityQuery("eq", ["region", "us"]),
            EquityQuery("gt", ["dayvolume", min_day_volume]),
            EquityQuery("btwn", ["intradayprice", price_min, price_max]),
            EquityQuery(predicate, ["percentchange", threshold]),
        ]
        # An unbounded upper cap is expressed by omitting the term rather
        # than by passing infinity, which the query language cannot encode.
        if max_market_cap != float("inf"):
            terms.append(EquityQuery("lt", ["intradaymarketcap", max_market_cap]))
        if min_market_cap > 0:
            terms.append(EquityQuery("gt", ["intradaymarketcap", min_market_cap]))
        query = EquityQuery("and", terms)
        try:
            res = _with_retry(
                lambda q=query: yf.screen(q, size=size, sortField="percentchange",
                                          sortAsc=(direction == "down")),
                f"screen:{direction}", "equity screen")
        except Exception as e:
            log.warning("screen (%s) failed: %s", direction, e)
            continue
        for q in (res or {}).get("quotes", []):
            sym = q.get("symbol")
            if not sym:
                continue
            rows[sym] = {
                "ticker": sym,
                "name": q.get("shortName") or q.get("longName"),
                "price": q.get("regularMarketPrice"),
                "change_pct": q.get("regularMarketChangePercent"),
                "day_volume": q.get("regularMarketVolume"),
                "market_cap": q.get("marketCap"),
                "avg_volume_3m": q.get("averageDailyVolume3Month"),
                "exchange": q.get("fullExchangeName"),
                "direction": direction,
            }
    return list(rows.values())


# ── bars ─────────────────────────────────────────────────────────────────────

def _normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Daily bars indexed by their true trading date, tz-naive at midnight.

    Daily frames must NOT go through `to_et`. Yahoo returns them tz-naive
    at midnight, so localising to UTC and converting to Eastern moves every
    bar back four or five hours — across the date boundary — and every
    stamp lands on the *previous* calendar day. The failure is silent and
    nasty: "previous close" then resolves to the session's own close, and
    RCEL's +63% earnings gap was reported as -16.3%. Dates in, dates out.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_convert(MARKET_TZ).tz_localize(None)
    out.index = idx.normalize()
    return out.sort_index()


def _split_frame(raw: pd.DataFrame, tickers: list[str],
                 intraday: bool = True) -> dict[str, pd.DataFrame]:
    """yf.download returns MultiIndex columns for multi-ticker requests and
    (version-dependently) for single-ticker ones too. Both shapes are
    normalised to {ticker: OHLCV frame}."""
    fix = to_et if intraday else _normalize_daily
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        level = 1 if raw.columns.names and raw.columns.names[-1] == "Ticker" else 0
        available = set(raw.columns.get_level_values(level))
        for t in tickers:
            if t not in available:
                continue
            sub = raw.xs(t, axis=1, level=level).dropna(how="all")
            if not sub.empty:
                out[t] = fix(sub)
    elif len(tickers) == 1:
        sub = raw.dropna(how="all")
        if not sub.empty:
            out[tickers[0]] = fix(sub)
    return out


def fetch_bars(tickers: list[str], interval: str, period: str,
               prepost: bool = True) -> dict[str, pd.DataFrame]:
    """Batch OHLCV download, keyed by ticker.

    Yahoo's intraday retention caps this: 1m goes back 7 days, 5m about 60.
    Callers pick the interval by what they need it for — 1m for today's
    structure, 5m for the multi-week time-of-day volume baseline.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]
    if not tickers:
        return {}
    try:
        raw = _with_retry(
            lambda: yf.download(tickers, period=period, interval=interval,
                                prepost=prepost, progress=False,
                                auto_adjust=False, group_by="column",
                                threads=True),
            ",".join(tickers[:3]) + "...", f"{interval} bars")
    except Exception as e:
        log.warning("bar download failed (%s/%s): %s", interval, period, e)
        return {}
    return _split_frame(raw, tickers, intraday=interval.endswith(("m", "h")))


def fetch_daily(tickers: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """Daily bars for ATR, the 52-week high and prior-day levels."""
    return fetch_bars(tickers, interval="1d", period=period, prepost=False)


# ── §4 float / short interest, §1 spread ─────────────────────────────────────

_INFO_FIELDS = (
    "floatShares", "sharesOutstanding", "marketCap", "sharesShort",
    "shortPercentOfFloat", "shortRatio", "sharesShortPriorMonth",
    "bid", "ask", "averageVolume", "averageVolume10days", "sector",
    "industry", "regularMarketPrice", "previousClose", "longName", "quoteType",
)


def fetch_info(ticker: str) -> dict:
    """The `.info` fields §1/§4 need. Missing keys stay missing.

    `.info` is unreliable in a specific, quiet way: it returns None for
    fields it has no data for AND for fields it merely failed to fetch, and
    the two are indistinguishable. AMC came back with a float but a None
    marketCap during development. Nothing here fills a gap with a guess —
    the supply engine renormalises over what it actually got and reports
    coverage, so an unknown float reads as unknown rather than as small.
    """
    try:
        raw = _with_retry(lambda: yf.Ticker(ticker).info, ticker, "info") or {}
    except Exception as e:
        log.warning("%s: info failed: %s", ticker, e)
        return {}
    return {k: raw.get(k) for k in _INFO_FIELDS if raw.get(k) is not None}


def fetch_news(ticker: str, limit: int = 12) -> list[dict]:
    """Recent headlines for §2. Shape varies across yfinance versions —
    older ones put fields at the top level, newer ones nest them under
    `content` — so both are flattened to {title, publisher, published}."""
    try:
        raw = _with_retry(lambda: yf.Ticker(ticker).news, ticker, "news") or []
    except Exception as e:
        log.warning("%s: news failed: %s", ticker, e)
        return []
    out = []
    for item in raw[:limit]:
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = content.get("title") or item.get("title")
        if not title:
            continue
        provider = content.get("provider")
        publisher = (provider.get("displayName") if isinstance(provider, dict)
                     else item.get("publisher"))
        out.append({
            "title": title,
            # Carried purely so catalyst.attributable can tell whether the
            # story is about this company — yfinance returns a mixed feed
            # with no ticker attribution, so the text is all there is.
            "summary": content.get("summary") or content.get("description") or "",
            "publisher": publisher,
            "published": (content.get("pubDate") or content.get("displayTime")
                          or item.get("providerPublishTime")),
        })
    return out


def sector_etf(sector: str | None, industry: str | None = None) -> str | None:
    if industry and "biotech" in str(industry).lower():
        return _BIOTECH_ETF
    return SECTOR_ETF.get(sector) if sector else None


def fetch_context(extra: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Intraday bars for the benchmarks, VIX and any sector ETFs needed.

    One batched call for everything §7 and §15 compare against, so market
    context costs a single request no matter how many candidates there are.
    """
    tickers = list(BENCHMARKS) + [VIX_TICKER] + list(extra or [])
    return fetch_bars(tickers, interval="5m", period="5d", prepost=True)
