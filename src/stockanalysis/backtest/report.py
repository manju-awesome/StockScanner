"""
report.py
=========
Turns a backtest signal CSV (from backtest.engine) into a research report:

  * outcomes, hit rate and expectancy by category, grade, and category×grade
  * market-regime split — QQQ above/below its 200MA on the signal date
  * does the R:R gate predict? (rr_t2 buckets)
  * holding-period stats and monthly signal counts / R
  * cumulative-R equity curve, with SPY buy-and-hold context

Prints the report and writes an HTML version (inline SVG equity curve — no
matplotlib in this environment) next to the input CSV.

Equity-curve assumption, stated where it's shown: every signal risks 1R = 1%
of equity, so cumulative R × 1% approximates the account return. R-multiples
ignore position-sizing interactions and concurrent-signal capital limits.

Usage:
    python -m stockanalysis.backtest.report                      # latest CSV
    python -m stockanalysis.backtest.report --csv path/to/file.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

if __package__ in (None, ""):   # direct run: make `stockanalysis.*` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

log = logging.getLogger(__name__)

RESOLVED = ("target_hit", "stop_hit", "expired_no_move")
OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "output"


# ── data prep ─────────────────────────────────────────────────────────────────

def load_signals(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in ("signal_date", "fill_date", "outcome_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _summarize(g: pd.DataFrame) -> pd.Series:
    res = g[g["outcome"].isin(RESOLVED)]
    n_res = len(res)
    return pd.Series({
        "signals":      len(g),
        "trig%":        round(100 * g["fill_price"].notna().mean()) if len(g) else None,
        "resolved":     n_res,
        "win%":         round(100 * (res["outcome"] == "target_hit").mean()) if n_res else None,
        "avg_R":        round(res["realized_r_multiple"].mean(), 2) if n_res else None,
        "total_R":      round(res["realized_r_multiple"].sum(), 1) if n_res else None,
    })


def _table(df: pd.DataFrame, by) -> str:
    t = df.groupby(by, observed=True).apply(_summarize, include_groups=False)
    return t.sort_values("total_R", ascending=False).to_string()


# ── sections ──────────────────────────────────────────────────────────────────

def sec_overview(df: pd.DataFrame) -> str:
    res = df[df["outcome"].isin(RESOLVED)]
    lines = [
        f"Signals {len(df)} · tickers {df['ticker'].nunique()} · "
        f"{df['signal_date'].min():%Y-%m-%d} → {df['signal_date'].max():%Y-%m-%d}",
        f"Outcomes: {df['outcome'].value_counts().to_dict()}",
    ]
    if len(res):
        held = (res["outcome_date"] - res["fill_date"]).dt.days.dropna()
        lines.append(
            f"Overall: win {100 * (res['outcome'] == 'target_hit').mean():.0f}% · "
            f"avg {res['realized_r_multiple'].mean():+.2f}R · "
            f"total {res['realized_r_multiple'].sum():+.1f}R · "
            f"median hold {held.median():.0f}d" if len(held) else "")
    return "\n".join(lines)


def sec_regime(df: pd.DataFrame, qqq: pd.DataFrame) -> str:
    """Split results by whether QQQ closed above its 200MA on the signal date."""
    close = qqq["Close"].ffill()
    above = (close > close.rolling(200).mean())
    above.index = above.index.date if above.index.tz is None \
        else above.index.tz_convert(None).date
    regime = df["signal_date"].dt.date.map(
        lambda d: above.get(d, None)).map(
        {True: "QQQ>200MA (risk-on)", False: "QQQ<200MA (risk-off)"})
    out = df.assign(regime=regime).dropna(subset=["regime"])
    if out.empty:
        return "(no regime data)"
    return _table(out, "regime")


def sec_rr_buckets(df: pd.DataFrame) -> str:
    """If the R:R-to-T2 gate means anything, higher buckets should earn more."""
    b = pd.cut(df["rr_t2"], bins=[0, 2, 3, 5, float("inf")],
               labels=["<2", "2-3", "3-5", ">5"])
    return _table(df.assign(rr_bucket=b).dropna(subset=["rr_bucket"]), "rr_bucket")


def sec_monthly(df: pd.DataFrame) -> str:
    res = df[df["outcome"].isin(RESOLVED)]
    m = pd.DataFrame({
        "signals": df.groupby(df["signal_date"].dt.to_period("M")).size(),
        "R": res.groupby(res["outcome_date"].dt.to_period("M"))
                ["realized_r_multiple"].sum().round(1),
    }).fillna(0)
    return m.to_string()


def equity_curve(df: pd.DataFrame) -> pd.Series:
    """Cumulative R by resolution date (resolved signals only)."""
    res = df[df["outcome"].isin(RESOLVED)].dropna(subset=["outcome_date"])
    daily_r = res.groupby("outcome_date")["realized_r_multiple"].sum()
    return daily_r.sort_index().cumsum()


def sec_equity(df: pd.DataFrame, spy: pd.DataFrame | None) -> str:
    curve = equity_curve(df)
    if curve.empty:
        return "(no resolved signals)"
    peak = curve.cummax()
    max_dd = (curve - peak).min()
    lines = [
        f"Cumulative R: {curve.iloc[-1]:+.1f}R over {len(curve)} resolution days "
        f"(≈ {curve.iloc[-1]:+.1f}% at 1% risk per signal)",
        f"Max drawdown: {max_dd:.1f}R · best day {curve.diff().max():+.1f}R · "
        f"worst day {curve.diff().min():+.1f}R",
    ]
    if spy is not None and not spy.empty:
        c = spy["Close"].ffill()
        spy_ret = (c.iloc[-1] / c.iloc[0] - 1) * 100
        lines.append(f"Context: SPY buy-and-hold {spy_ret:+.1f}% over the same window "
                     f"(different risk basis — see assumption above)")
    return "\n".join(lines)


# ── HTML output ───────────────────────────────────────────────────────────────

def _svg_curve(curve: pd.Series, width: int = 720, height: int = 240) -> str:
    """Inline SVG line chart of cumulative R — no plotting deps needed."""
    if curve.empty:
        return ""
    pad = 40
    xs = list(range(len(curve)))
    ys = curve.tolist()
    lo, hi = min(min(ys), 0), max(max(ys), 0)
    span = (hi - lo) or 1

    def px(i): return pad + i * (width - 2 * pad) / max(len(xs) - 1, 1)
    def py(v): return height - pad - (v - lo) * (height - 2 * pad) / span

    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in zip(xs, ys))
    zero_y = py(0)
    color = "#2e7d32" if ys[-1] >= 0 else "#c62828"
    return f"""
<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"
     style="max-width:{width}px;font:11px sans-serif">
  <line x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}"
        stroke="#999" stroke-dasharray="4 3"/>
  <polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/>
  <text x="{pad}" y="14">Cumulative R (resolved signals)</text>
  <text x="{pad}" y="{py(ys[0]) - 6:.1f}">{curve.index[0]:%Y-%m-%d}</text>
  <text x="{width - pad}" y="{py(ys[-1]) - 6:.1f}" text-anchor="end">
    {curve.index[-1]:%Y-%m-%d}: {ys[-1]:+.1f}R</text>
  <text x="{pad - 4}" y="{py(hi) + 4:.1f}" text-anchor="end">{hi:.0f}</text>
  <text x="{pad - 4}" y="{py(lo) + 4:.1f}" text-anchor="end">{lo:.0f}</text>
</svg>"""


def to_html(title: str, sections: list[tuple[str, str]], curve: pd.Series) -> str:
    body = [f"<h1>{title}</h1>", _svg_curve(curve)]
    for name, text in sections:
        body.append(f"<h2>{name}</h2><pre>{text}</pre>")
    body.append("<p><i>Assumptions & limitations: 1% equity risked per signal; "
                "conservative fills (buy-stop triggers, same-bar ties to stop); "
                "survivorship-biased universe (today's list); no point-in-time "
                "fundamentals (earnings gates inactive).</i></p>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{title}</title>"
            "<style>body{font:14px/1.45 sans-serif;max-width:860px;"
            "margin:24px auto;padding:0 12px}pre{background:#f6f6f6;"
            "padding:10px;overflow-x:auto}</style></head><body>"
            + "".join(body) + "</body></html>")


# ── assembly ──────────────────────────────────────────────────────────────────

def build_report(df: pd.DataFrame, qqq: pd.DataFrame | None = None,
                 spy: pd.DataFrame | None = None) -> tuple[str, list[tuple[str, str]]]:
    sections = [
        ("Overview", sec_overview(df)),
        ("By category", _table(df, "category")),
        ("By grade", _table(df, "grade")),
        ("Category × grade", _table(df, ["category", "grade"])),
        ("R:R-to-T2 buckets", sec_rr_buckets(df)),
    ]
    if qqq is not None:
        sections.append(("Market regime at signal", sec_regime(df, qqq)))
    sections.append(("Equity curve (1% risk per signal)", sec_equity(df, spy)))
    sections.append(("Monthly", sec_monthly(df)))

    text = "\n".join(f"\n{'═' * 64}\n  {name.upper()}\n{'═' * 64}\n{body}"
                     for name, body in sections)
    return text, sections


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description="Backtest analysis report")
    parser.add_argument("--csv", type=Path,
                        help="signal CSV from backtest.engine (default: latest)")
    args = parser.parse_args()

    path = args.csv or max(OUT_DIR.glob("backtest_*.csv"),
                           key=lambda p: p.stat().st_mtime, default=None)
    if path is None or not path.exists():
        sys.exit("No backtest CSV found — run backtest.engine first.")
    log.info("Reading %s", path)
    df = load_signals(path)
    if df.empty:
        sys.exit("Signal file is empty.")

    # Benchmarks from the shared cache (200MA regime needs ~1y of lead-in)
    from stockanalysis.backtest.data import load_daily
    start = (df["signal_date"].min() - timedelta(days=320)).date()
    end = pd.concat([df["outcome_date"], df["signal_date"]]).max().date()
    bench = load_daily(["QQQ", "SPY"], start, end)
    qqq, spy = bench.get("QQQ"), bench.get("SPY")
    if spy is not None:
        first_sig = df["signal_date"].min()
        spy = spy[spy.index >= first_sig.tz_localize(spy.index.tz)]

    text, sections = build_report(df, qqq, spy)
    print(text)

    out = path.with_name(path.stem + "_report.html")
    out.write_text(to_html(f"Backtest report — {path.stem}",
                           sections, equity_curve(df)))
    log.info("HTML report → %s", out)


if __name__ == "__main__":
    cli()
