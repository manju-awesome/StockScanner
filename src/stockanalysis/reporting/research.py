"""
research.py
===========
Per-ticker mini research pages: data/output/research/<TICKER>.html, one per
scanned row, linked from every ticker on the main dashboard (📄).

Each page has: overview (name, sector, cap, 52W range), an inline-SVG price
chart with 8/21 EMA + 50/200 MA overlays, volume, and the trade plan's
entry/stop/target drawn as level lines; the full technical and fundamental
metric tables; a catalysts section (earnings countdown, squeeze fuel,
optional news headline); the three strategy scores + conviction verdict; and
a plain-language summary.

Design constraints:
  - Self-contained HTML (inline SVG, no JS/CDN) — pages open from disk or via
    the static data/output server.
  - Offline-capable: charts need one yfinance fetch per ticker; pass
    charts=False to build pages without the network (chart shows a notice).
  - Only metrics the scanner actually collects are shown — no placeholder
    fundamentals (ROE / margins / analyst targets aren't fetched upstream).

Usage
-----
    from stockanalysis.reporting.research import generate_research_pages
    tickers = generate_research_pages(rows, out_dir)   # -> set of tickers
"""

from __future__ import annotations

import html as _html
from datetime import datetime
from pathlib import Path

RESEARCH_DIRNAME = "research"

_ACTION_COLORS = {"READY": ("#E1F5EE", "#085041"),
                  "WATCH": ("#FAEEDA", "#633806"),
                  "AVOID": ("#FCEBEB", "#791F1F")}
_WHY_MARK = {"+": "✅", "!": "⚠", "-": "❌"}


def _v(row, key, default=None):
    val = row.get(key)
    return val if val is not None else default


def _fmt(val, prefix="$", nd=2):
    if val is None:
        return "—"
    try:
        return f"{prefix}{float(val):,.{nd}f}"
    except Exception:
        return str(val)


def _pct(val, nd=1):
    if val is None:
        return "—"
    try:
        v = float(val)
        return f"{'+' if v >= 0 else ''}{v:.{nd}f}%"
    except Exception:
        return str(val)


def _cap_fmt(mc) -> str:
    if not mc:
        return "—"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if mc >= div:
            return f"${mc / div:,.2f}{suf}"
    return f"${mc:,.0f}"


def _yn(val) -> str:
    return "—" if val is None else ("✓ yes" if val else "✗ no")


# ─────────────────────────────────────────────────────────────────────────────
# CHART — inline SVG, 1y daily bars with MA overlays + plan level lines
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_bars(ticker: str):
    """1y daily OHLCV (dividend-adjusted, matching the scanner's series)."""
    import yfinance as yf
    bars = yf.Ticker(ticker).history(period="1y", interval="1d",
                                     auto_adjust=True)
    return None if (bars is None or bars.empty) else bars


def _chart_svg(bars, row: dict, width: int = 880, height: int = 380) -> str:
    """Close line + 8/21 EMA + 50/200 MA + volume + entry/stop/target lines."""
    close = bars["Close"].ffill()
    vol   = bars["Volume"].fillna(0)
    n     = len(close)
    if n < 30:
        return ""
    overlays = [
        (close.ewm(span=8,  adjust=False).mean(), "#c9a227", "8 EMA"),
        (close.ewm(span=21, adjust=False).mean(), "#8a5fbf", "21 EMA"),
        (close.rolling(50).mean(),  "#185FA5", "50 MA"),
        (close.rolling(200).mean(), "#A32D2D", "200 MA"),
    ]

    # Plan levels from the graded swing plan (numeric _levels dict when the
    # rows came straight from a scan; absent on CSV-reloaded rows)
    levels = []
    lv = row.get("_levels")
    if isinstance(lv, dict):
        for key, label, color in (("entry", "Entry", "#0F6E56"),
                                  ("stop", "Stop", "#A32D2D"),
                                  ("t2", "Target", "#185FA5")):
            if lv.get(key):
                levels.append((float(lv[key]), label, color))

    pad_l, pad_r, pad_t = 52, 8, 8
    price_h, vol_h, pad_b = height - 110, 70, 24
    ys = [float(v) for v in close]
    all_y = ys + [p for p, _, _ in levels]
    for series, _, _ in overlays:
        all_y += [float(v) for v in series.dropna()]
    lo, hi = min(all_y), max(all_y)
    span = (hi - lo) or 1.0
    lo -= span * 0.04
    hi += span * 0.04
    span = hi - lo
    plot_w = width - pad_l - pad_r

    def x(i):  return pad_l + i / (n - 1) * plot_w
    def y(v):  return pad_t + (1 - (v - lo) / span) * price_h

    def poly(series, color, w="1.6"):
        pts = " ".join(f"{x(i):.1f},{y(float(v)):.1f}"
                       for i, v in enumerate(series)
                       if v == v)  # skip nan
        return (f'<polyline points="{pts}" fill="none" stroke="{color}" '
                f'stroke-width="{w}"/>') if pts else ""

    parts = [f'<svg viewBox="0 0 {width} {height}" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;background:white">']

    # y gridlines + labels
    for k in range(5):
        v = lo + span * k / 4
        yy = y(v)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" '
                     f'y2="{yy:.1f}" stroke="#f1efea" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{yy + 3:.1f}" text-anchor="end" '
                     f'font-size="10" fill="#898781">{v:,.0f}</text>')
    # month ticks
    dates = list(bars.index)
    last_m = None
    for i, d in enumerate(dates):
        if d.month != last_m:
            last_m = d.month
            parts.append(f'<text x="{x(i):.1f}" y="{height - 6}" font-size="9" '
                         f'fill="#898781">{d.strftime("%b")}</text>')

    # volume bars
    vmax = float(vol.max()) or 1.0
    vw = max(1.0, plot_w / n * 0.7)
    v_base = pad_t + price_h + vol_h
    for i, v in enumerate(vol):
        h = float(v) / vmax * vol_h
        parts.append(f'<rect x="{x(i) - vw / 2:.1f}" y="{v_base - h:.1f}" '
                     f'width="{vw:.1f}" height="{h:.1f}" fill="#d9d7ce"/>')

    for series, color, _ in overlays:
        parts.append(poly(series, color, "1.2"))
    parts.append(poly(close, "#0b0b0b", "1.8"))

    for px, label, color in levels:
        if lo <= px <= hi:
            yy = y(px)
            parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" '
                         f'y2="{yy:.1f}" stroke="{color}" stroke-width="1.2" '
                         f'stroke-dasharray="6 4"/>')
            parts.append(f'<text x="{width - pad_r - 4}" y="{yy - 4:.1f}" '
                         f'text-anchor="end" font-size="10" font-weight="600" '
                         f'fill="{color}">{label} {px:,.2f}</text>')

    legend_x = pad_l + 8
    legend = [("Close", "#0b0b0b")] + [(lbl, c) for _, c, lbl in overlays]
    for i, (lbl, c) in enumerate(legend):
        parts.append(f'<rect x="{legend_x + i * 78}" y="{pad_t + 4}" width="14" '
                     f'height="3" fill="{c}"/>')
        parts.append(f'<text x="{legend_x + i * 78 + 18}" y="{pad_t + 9}" '
                     f'font-size="10" fill="#52514e">{lbl}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE SECTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _table(rows_kv: list[tuple[str, str]], cols: int = 2) -> str:
    cells = "".join(
        f'<div style="display:flex;justify-content:space-between;gap:10px;'
        f'padding:5px 0;border-bottom:0.5px solid #f1efea;font-size:12px">'
        f'<span style="color:#898781">{_html.escape(k)}</span>'
        f'<span style="color:#0b0b0b;font-weight:500;text-align:right">{v}</span></div>'
        for k, v in rows_kv)
    return (f'<div style="columns:{cols};column-gap:32px">{cells}</div>')


def _section(title: str, body: str) -> str:
    return (f'<div style="background:white;border:0.5px solid #e1e0d9;'
            f'border-radius:12px;padding:16px 18px;margin-bottom:14px">'
            f'<h3 style="font-size:14px;font-weight:600;color:#0b0b0b;'
            f'margin:0 0 10px">{title}</h3>{body}</div>')


def _range_bar(row: dict) -> str:
    lo, hi, p = _v(row, "52W Low"), _v(row, "52W High"), _v(row, "Current Price")
    if not (lo and hi and p and hi > lo):
        return "—"
    pos = max(0, min(100, (p - lo) / (hi - lo) * 100))
    return (f'<div style="display:flex;align-items:center;gap:8px;font-size:11px">'
            f'<span>{_fmt(lo)}</span>'
            f'<div style="flex:1;height:6px;background:#f1efea;border-radius:3px;'
            f'position:relative;min-width:120px">'
            f'<div style="position:absolute;left:{pos:.0f}%;top:-3px;width:3px;'
            f'height:12px;background:#0b0b0b;border-radius:1px"></div></div>'
            f'<span>{_fmt(hi)}</span></div>')


# Keyword → tag for classifying headlines (deals, earnings, M&A, …).
# Checked lowercase-substring; a headline can carry several tags.
_NEWS_TAGS = (
    ("earnings", "EARNINGS"), ("beat", "EARNINGS"), ("miss", "EARNINGS"),
    ("guidance", "GUIDANCE"), ("outlook", "GUIDANCE"), ("forecast", "GUIDANCE"),
    ("acquir", "M&A"), ("merger", "M&A"), ("takeover", "M&A"), ("stake", "M&A"),
    ("deal", "DEAL"), ("contract", "DEAL"), ("partner", "DEAL"),
    ("agreement", "DEAL"), ("order", "DEAL"), ("supply", "DEAL"),
    ("upgrade", "ANALYST"), ("downgrade", "ANALYST"),
    ("price target", "ANALYST"), ("rating", "ANALYST"),
    ("buyback", "CAPITAL RETURN"), ("dividend", "CAPITAL RETURN"),
    ("lawsuit", "LEGAL"), ("probe", "LEGAL"), ("investigat", "LEGAL"),
    ("regulat", "LEGAL"), ("antitrust", "LEGAL"),
    ("launch", "PRODUCT"), ("unveil", "PRODUCT"), ("chip", "PRODUCT"),
    (" ai ", "AI"), ("artificial intelligence", "AI"),
)


def _tag_headline(title: str) -> list[str]:
    t = f" {title.lower()} "
    tags = {label for kw, label in _NEWS_TAGS if kw in t}
    return sorted(tags) or ["NEWS"]


def _fetch_ticker_news(ticker: str, limit: int = 6) -> list[dict]:
    """Recent headlines via yfinance. Handles both the nested 'content'
    layout (yfinance ≥0.2.5x) and the legacy flat item layout."""
    import yfinance as yf
    items = yf.Ticker(ticker).news or []
    out = []
    for it in items:
        c = it.get("content") or it
        title = c.get("title")
        if not title:
            continue
        pub = ((c.get("provider") or {}).get("displayName")
               or it.get("publisher") or "")
        when = str(c.get("pubDate") or "")[:10]
        if not when and it.get("providerPublishTime"):
            when = datetime.fromtimestamp(
                it["providerPublishTime"]).strftime("%Y-%m-%d")
        url = ((c.get("clickThroughUrl") or {}).get("url")
               or (c.get("canonicalUrl") or {}).get("url")
               or it.get("link") or "")
        out.append({"title": str(title), "publisher": str(pub),
                    "when": when, "url": str(url),
                    "tags": _tag_headline(str(title))})
        if len(out) >= limit:
            break
    return out


# The news card is wrapped in these markers so update_news() can splice a
# fresh feed into an existing page without rebuilding metrics or the chart
NEWS_START = "<!--NEWS_START-->"
NEWS_END   = "<!--NEWS_END-->"


def _news_section_block(ticker: str) -> str:
    """Marker-wrapped Latest News card (self-contained, splice-replaceable)."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = (_news_html(ticker)
            + f'<div style="font-size:10px;color:#898781;margin-top:6px">'
              f'news as of {stamp}</div>')
    return (NEWS_START
            + _section("Latest News — earnings · deals · market", body)
            + NEWS_END)


def _splice_news(page_src: str, block: str) -> str:
    """Replace the marker-wrapped news card in a page (pure, testable).
    Legacy pages without markers get the card inserted before the footer."""
    start = page_src.find(NEWS_START)
    end   = page_src.find(NEWS_END)
    if start != -1 and end != -1:
        return page_src[:start] + block + page_src[end + len(NEWS_END):]
    footer = '<p style="font-size:10px;color:#898781;text-align:center">'
    if footer in page_src:
        return page_src.replace(footer, block + footer, 1)
    return page_src + block


def update_news(tickers: list[str] | None = None,
                output_dir: str | Path | None = None) -> set:
    """
    News-only refresh: fetch the latest headlines for each ticker and splice
    them into its EXISTING research page — no metrics, chart, or scan
    refetch, so updating all pages costs one news request per ticker.
    tickers=None → every page currently in the research directory.
    """
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    rdir = out_dir / RESEARCH_DIRNAME
    if tickers is None:
        tickers = sorted(f.stem for f in rdir.glob("*.html"))
    updated = set()
    for ticker in dict.fromkeys(t.upper() for t in tickers if t):
        f = rdir / f"{ticker}.html"
        if not f.exists():
            continue
        try:
            f.write_text(_splice_news(f.read_text(),
                                      _news_section_block(ticker)))
            updated.add(ticker)
        except Exception as e:
            print(f"[Research] {ticker}: news update failed ({e})")
    return updated


def _news_html(ticker: str) -> str:
    """Recent-news block: date · tag chips · linked headline · source."""
    try:
        items = _fetch_ticker_news(ticker)
    except Exception as e:
        return (f'<div style="font-size:11px;color:#898781">news unavailable '
                f'({_html.escape(str(e)[:80])})</div>')
    if not items:
        return ('<div style="font-size:11px;color:#898781">no recent '
                'headlines from the news feed</div>')
    lis = []
    for n in items:
        chips = "".join(
            f'<span style="background:#EEEDFE;color:#26215C;font-size:9px;'
            f'font-weight:600;padding:1px 6px;border-radius:3px;'
            f'margin-right:4px">{t}</span>' for t in n["tags"])
        title = _html.escape(n["title"][:150])
        link = (f'<a href="{_html.escape(n["url"])}" target="_blank" '
                f'style="color:#0b0b0b;text-decoration:none">{title}</a>'
                if n["url"] else title)
        lis.append(
            f'<div style="font-size:12px;line-height:1.5;margin-bottom:7px">'
            f'<span style="color:#898781;font-size:10px">{_html.escape(n["when"])}</span> '
            f'{chips}<br>{link}'
            f'<span style="color:#898781;font-size:10px"> — {_html.escape(n["publisher"])}</span>'
            f'</div>')
    return "".join(lis)


def _catalysts(row: dict, fetch_news: bool) -> list[tuple[str, str]]:
    out = []
    dte = row.get("Days_To_Earnings")
    ed  = _v(row, "EarningsDate", "N/A")
    if ed and ed != "N/A":
        cd = f" — in {int(dte)} days" if dte is not None and dte >= 0 else ""
        out.append(("Next earnings", f"{_html.escape(str(ed))}{cd}"))
    if row.get("EarningsBeat") is not None:
        out.append(("Last report", "beat estimates ✓"
                    if row["EarningsBeat"] else "missed estimates ✗"))
    si = row.get("Short_Interest%")
    if si is not None and si >= 10:
        out.append(("Short squeeze fuel", f"{si:.1f}% of float short"))
    d52 = row.get("Days_Since_52W_High")
    if d52 is not None and d52 <= 10:
        out.append(("Momentum catalyst", f"52W high set {int(d52)}d ago"))
    chg = row.get("Inst_Own_Chg")
    if chg is not None and chg > 0.5:
        out.append(("Accumulation", f"institutions added {chg:+.1f}% (latest filings)"))
    if not out:
        out.append(("Catalysts", "none identified in scan data"))
    return out


def _summary_text(row: dict) -> str:
    """Plain-language verdict assembled from the conviction layer."""
    action = _v(row, "Conv_Action", "—")
    reason = _v(row, "Conv_Action_Reason", "")
    q, s, t = (_v(row, "Conv_Quality", 0), _v(row, "Conv_Setup", 0),
               _v(row, "Conv_Timing", 0))
    bits = [f"Rating: {reason or action}."]
    if q >= 70 and t < 50:
        bits.append("Fundamentals are strong but timing is poor — let the "
                    "price come to you rather than chasing.")
    elif q >= 70 and s >= 70:
        bits.append("Both the company and the setup grade well.")
    elif q < 40:
        bits.append("Fundamentals are weak — treat any position as a trade, "
                    "not an investment.")
    warns = [txt for m, txt in (row.get("Conv_Why") or []) if m == "!"]
    if warns:
        bits.append("Watch-outs: " + "; ".join(warns[:2]) + ".")
    lt = _v(row, "LT_Entry_Timing")
    if row.get("Investment_Pass") and lt:
        bits.append(f"Long-term: {lt}.")
    return " ".join(bits)


def _build_page(row: dict, charts: bool, fetch_news: bool) -> str:
    ticker = _v(row, "Ticker", "?")
    name   = _v(row, "LongName", ticker)
    price  = _v(row, "Current Price")

    a_bg, a_fg = _ACTION_COLORS.get(_v(row, "Conv_Action"),
                                    ("#F1EFE8", "#444441"))
    stars_n = _v(row, "Conv_Stars", 0) or 0
    stars = ('<span style="color:#c9a227;font-size:18px;letter-spacing:2px">'
             + "★" * stars_n + '<span style="color:#d9d7ce">'
             + "☆" * (5 - stars_n) + "</span></span>")

    # Chart
    chart_html = ('<p style="font-size:12px;color:#898781">Chart skipped — '
                  'built offline (run with network for price history).</p>')
    if charts:
        try:
            bars = _fetch_bars(ticker)
            svg = _chart_svg(bars, row) if bars is not None else ""
            if svg:
                chart_html = svg
            else:
                chart_html = ('<p style="font-size:12px;color:#898781">'
                              'No price history available.</p>')
        except Exception as e:
            chart_html = (f'<p style="font-size:12px;color:#898781">Chart '
                          f'unavailable ({_html.escape(str(e)[:80])}).</p>')

    overview = _table([
        ("Company", _html.escape(str(name))),
        ("Sector", _html.escape(str(_v(row, "Sector", "—")))),
        ("Market cap", _cap_fmt(row.get("MarketCap"))),
        ("Price", _fmt(price)),
        ("52-week range", _range_bar(row)),
        ("Off 52W high", _pct(row.get("Dist_52W_High%"))),
        ("Above 52W low", _pct(row.get("Pct_From_52W_Low%"))),
        ("Scan category", _html.escape(str(_v(row, "Category", "—")))
         + f' · grade {_v(row, "Grade", "—")}'),
    ])

    tech = _table([
        ("RSI (14)", f'{_v(row, "RSI_14", "—")}'),
        ("ADX (14)", f'{_v(row, "ADX_14", "—")} · {_v(row, "Trend_Strength", "—")}'),
        ("RS vs QQQ (3mo)", _pct(row.get("RS"))),
        ("RS rank (universe)", f'{_v(row, "RS_Rank", "—")}'),
        ("VWAP", _fmt(row.get("VWAP")) + (" · above ✓" if row.get("Above_VWAP")
                                          else " · below" if row.get("Above_VWAP") is False else "")),
        ("ATR (20)", _fmt(row.get("ATR20")) + f' ({_pct(row.get("ATR_Pct"))})'
         + (" · shrinking ✓" if row.get("ATR Shrinking") else "")),
        ("Bollinger %B", f'{_v(row, "BB_PctB", "—")}'),
        ("RVOL (time-adj / daily)",
         f'{_v(row, "RVOL_Intraday", "—")} / {_v(row, "RVOL_EOD", _v(row, "RVOL", "—"))}'),
        ("Volume vs 20d", f'{_v(row, "Vol_vs_20D", "—")}×'
         + (" · drying up" if row.get("VolumeDryingUp") else "")),
        ("Pullback volume ratio", f'{_v(row, "Pullback_Vol_Ratio", "—")}'),
        ("8 EMA", _fmt(row.get("8EMA")) + f' ({_pct(row.get("Pct_vs_8EMA"), 2)})'),
        ("21 EMA", _fmt(row.get("21EMA"))),
        ("50 MA", _fmt(row.get("50MA")) + f' ({_pct(row.get("Price_vs_50MA%"))})'),
        ("200 MA", _fmt(row.get("200MA")) + f' ({_pct(row.get("Price_vs_200MA%"))})'
         + (" · above ✓" if row.get("Above_200MA") else "")),
        ("Opening range", f'{_fmt(row.get("ORB_Low"))}–{_fmt(row.get("ORB_High"))}'
         + f' ({_v(row, "ORB_Status", "—")})'),
        ("Gap today", _pct(row.get("Gap%")) + f' (now {_pct(row.get("Gap_Now%"))})'),
        ("Support (prev day / 52W low)",
         f'{_fmt(row.get("Prev-Day Low"))} / {_fmt(row.get("52W Low"))}'),
        ("Resistance (prev day / 52W high)",
         f'{_fmt(row.get("Prev-Day High"))} / {_fmt(row.get("52W High"))}'),
    ])

    fund = _table([
        ("Revenue growth", _pct(row.get("Revenue"))),
        ("EPS growth (fwd vs ttm)", _pct(row.get("EPS_Growth%"))),
        ("Free cash flow positive", _yn(row.get("FCF_Positive"))),
        ("Institutional ownership", _pct(row.get("Inst_Own%"))),
        ("Institutional change", _pct(row.get("Inst_Own_Chg"))),
        ("Short interest", _pct(row.get("Short_Interest%"))),
        ("Last earnings beat", _yn(row.get("EarningsBeat"))),
        ("CANSLIM composite", _yn(row.get("CANSLIM_Pass"))),
    ])

    cat_html = _table(_catalysts(row, fetch_news), cols=1)
    news_section = (_news_section_block(ticker) if fetch_news else
                    NEWS_START + NEWS_END)   # empty markers: updatable later

    scores = _table([
        ("Investment score", f'{_v(row, "Investment_Score", "—")} '
         f'({"PASS ✓" if row.get("Investment_Pass") else "not all filters"})'),
        ("Swing score", f'{_v(row, "Swing_Score", "—")} '
         f'({"PASS ✓" if row.get("Swing_Pass") else "not all filters"})'),
        ("Day-trade score", f'{_v(row, "DayTrade_Score", "—")} '
         f'({"PASS ✓" if row.get("DayTrade_Pass") else "not all filters"})'),
        ("Quality / Setup / Timing",
         f'{_v(row, "Conv_Quality", "—")} / {_v(row, "Conv_Setup", "—")} / '
         f'{_v(row, "Conv_Timing", "—")}'),
        ("R:R to T2", f'{_v(row, "RR_T2", "—")}'),
        ("Size flag", f'{_v(row, "SizeFlag", "—")}'),
    ])

    why_html = "".join(
        f'<div style="font-size:12px;line-height:1.7">'
        f'{_WHY_MARK.get(m, "·")} {_html.escape(txt)}</div>'
        for m, txt in (row.get("Conv_Why") or [])) or "—"

    plan_rows = []
    for key in ("Entry", "Stop", "Target"):
        val = _v(row, key)
        if isinstance(val, str) and val.strip() and val != "N/A":
            plan_rows.append(
                f'<div style="font-size:12px;line-height:1.6;margin-bottom:4px">'
                f'<b>{key}:</b> {_html.escape(val[:260])}</div>')
    plan_html = "".join(plan_rows) or \
        '<span style="font-size:12px;color:#898781">No trade plan (gate failed or Avoid).</span>'

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ticker} — research</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background:#faf9f5; color:#0b0b0b; margin:0; padding:20px;
         max-width:960px; margin-left:auto; margin-right:auto }}
</style></head><body>
<div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:4px">
  <a href="../" style="font-size:12px;color:#898781;text-decoration:none">← dashboard files</a>
</div>
<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:14px">
  <h1 style="font-size:28px;margin:0">{ticker}</h1>
  <span style="font-size:18px;color:#52514e">{_fmt(price)}</span>
  {stars}
  <span style="background:{a_bg};color:{a_fg};font-size:12px;font-weight:600;
               padding:3px 12px;border-radius:6px">{_v(row, "Conv_Action", "—")}</span>
  <span style="margin-left:auto;font-size:11px;color:#898781">scan {ts}</span>
</div>

{_section("AI Summary", f'<p style="font-size:13px;line-height:1.6;color:#52514e;margin:0">{_html.escape(_summary_text(row))}</p>')}
{_section("Chart — 1y daily · 8/21 EMA · 50/200 MA · volume · plan levels", chart_html)}
{_section("Overview", overview)}
{_section("Why This Stock?", why_html)}
{_section("Trade Plan", plan_html)}
{_section("Technical Analysis", tech)}
{_section("Fundamentals", fund)}
{_section("Catalysts", cat_html)}
{news_section}
{_section("Strategy Scores", scores)}
<p style="font-size:10px;color:#898781;text-align:center">
  Generated by the scan pipeline — not financial advice. Metrics as of scan time.</p>
</body></html>"""


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "data" / "output"


def refresh_research(tickers: list[str], output_dir: str | Path | None = None,
                     charts: bool = True, fetch_news: bool = True) -> set:
    """
    Fetch fresh data for ONLY the given tickers and rebuild their research
    pages under <output_dir>/research/ — one metrics fetch per ticker instead
    of a full universe scan, so refreshing a handful of names doesn't hammer
    the data source. Runs the same pipeline a scan would (categorize → grade →
    strategy scores → conviction), so the pages match what a full scan shows.
    """
    # Local imports — scan_universe imports dashboard which imports this
    # module, so a top-level import would be circular
    from stockanalysis.scanners.scan_universe import fetch_qqq_return, categorize
    from stockanalysis.core.metrics import get_metrics
    from stockanalysis.core.grade_signals import enrich_rows
    from stockanalysis.core.strategy_scores import attach_strategy_scores
    from stockanalysis.core.conviction import attach_conviction

    tickers = list(dict.fromkeys(t.upper() for t in tickers if t))
    if not tickers:
        return set()
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR

    qqq_3m = fetch_qqq_return()
    rows = []
    for ticker in tickers:
        try:
            row = get_metrics(ticker, qqq_3m)
            cat, reason, score = categorize(row)
            row["Category"], row["Cat_Reason"], row["Rank_Score"] = (
                cat, reason, score)
            rows.append(row)
        except Exception as e:
            print(f"[Research] {ticker}: metrics fetch failed ({e})")
    enrich_rows(rows)
    attach_strategy_scores(rows)   # RS_Rank uses the small-universe fallback
    attach_conviction(rows)
    return generate_research_pages(rows, out_dir, charts=charts,
                                   fetch_news=fetch_news)


def generate_research_pages(rows: list[dict], output_dir: str | Path,
                            charts: bool = True,
                            fetch_news: bool = False) -> set:
    """
    Write research/<TICKER>.html under `output_dir` for every non-Error row.
    Returns the set of tickers written (drives the dashboard's 📄 links).
    Failures on one ticker never abort the rest.
    """
    out = Path(output_dir) / RESEARCH_DIRNAME
    out.mkdir(parents=True, exist_ok=True)
    # Self-heal when called on rows that skipped the dashboard pipeline
    # (e.g. CSV-reloaded): the summary/verdict sections need Conv_* keys
    if rows and "Conv_Overall" not in rows[0]:
        try:
            from stockanalysis.core.conviction import attach_conviction
            attach_conviction(rows)
        except Exception as e:
            print(f"[Research] conviction unavailable ({e})")
    written = set()
    for row in rows:
        ticker = row.get("Ticker")
        if not ticker or _v(row, "Category") == "Error":
            continue
        try:
            (out / f"{ticker}.html").write_text(
                _build_page(row, charts=charts, fetch_news=fetch_news))
            written.add(ticker)
        except Exception as e:
            print(f"[Research] {ticker}: page failed ({e})")
    return written
