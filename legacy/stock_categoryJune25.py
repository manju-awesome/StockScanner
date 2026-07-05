"""
Stock Categorizer - Hedge Fund Style
Categorizes stocks into: Long-Term Invest, Swing Trade, Day Trade, Avoid, Watch
Input: CSV or dict/DataFrame with the watchlist metrics
"""

import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────────
# THRESHOLDS  (tune here without touching scoring logic)
# ─────────────────────────────────────────────────────────────
THRESH_LT    = 50   # min score → Long-Term
THRESH_SW    = 45   # min score → Swing Trade
THRESH_DT    = 40   # min score → Day Trade
THRESH_AVOID = 50   # avoid_score >= this → AVOID

# Output column order — matches reference CSV exactly
OUTPUT_COLS = [
    "Ticker", "Sector", "Current Price", "RS", "RVOL",
    "% From 50MA", "Dist_52W_High%", "EPS_Growth%",
    "VolumeDryingUp", "ATR Shrinking",
    "Above 8EMA/21EMA/50MA/200MA",
    "Category", "Score", "Reasons", "Avoid_Score",
]

CAT_ORDER = {
    "Long-Term Invest":  1, "Long-Term + Swing": 2,
    "Swing Trade":       3, "Day Trade":         4,
    "Day Trade (ETF)":   5, "Watch":             6, "AVOID": 7,
}

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _g(row, col, default=np.nan):
    v = row.get(col, default)
    if v is None:
        return default
    try:
        if np.isnan(v):
            return default
    except (TypeError, ValueError):
        pass
    return v

def _bool_col(row, col):
    v = row.get(col, False)
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, float)):
        try:
            if not np.isnan(v):
                return bool(v)
        except Exception:
            pass
    return str(v).strip().lower() == "true"

# ─────────────────────────────────────────────────────────────
# SCORING LOGIC
# ─────────────────────────────────────────────────────────────

def score_long_term(row):
    """CANSLIM-style long-term scoring (0-100+)."""
    score = 0; reasons = []
    rs    = _g(row, "RS")
    eps   = _g(row, "EPS_Growth%")
    rev   = _g(row, "Revenue")
    dh    = _g(row, "Dist_52W_High%")
    above = _bool_col(row, "Above 8EMA/21EMA/50MA/200MA")
    can   = _bool_col(row, "CANSLIM_Pass")
    if pd.notna(rs):
        if rs > 50:   score += 25; reasons.append(f"Strong RS ({rs:.1f})")
        elif rs > 10: score += 12; reasons.append(f"Moderate RS ({rs:.1f})")
        elif rs > 0:              reasons.append(f"Weak RS ({rs:.1f})")
    if pd.notna(eps):
        if eps > 100: score += 20; reasons.append(f"EPS growth {eps:.0f}%")
        elif eps > 25: score += 10; reasons.append(f"EPS growth {eps:.0f}%")
    if above:
        score += 20; reasons.append("Above all MAs")
    if pd.notna(rev) and rev > 20:
        score += 15; reasons.append(f"Revenue growth {rev:.0f}%")
    if pd.notna(dh) and dh > -20:
        score += 10; reasons.append(f"Near 52W high ({dh:.1f}%)")
    if can:
        score += 10; reasons.append("CANSLIM pass")
    return score, reasons


def score_swing_trade(row):
    """Swing trade scoring — 5-20 day setups (0-100+)."""
    score = 0; reasons = []
    p50  = _g(row, "% From 50MA")
    p8   = _g(row, "% From 8EMA")
    rs   = _g(row, "RS")
    vdu  = _g(row, "VolumeDryingUp", 0)
    atr  = _g(row, "ATR Shrinking", 0)
    sr3m = _g(row, "stock_return_3m")
    q3m  = _g(row, "qqq_return_3m")
    if pd.notna(p50) and p50 > 0:
        score += 15; reasons.append(f"Above 50MA (+{p50:.1f}%)")
    if pd.notna(p8) and -3 <= p8 <= 5:
        score += 10; reasons.append(f"Near 8EMA ({p8:.1f}%)")
    if vdu:
        score += 20; reasons.append("Volume drying up (VCP setup)")
    if atr:
        score += 15; reasons.append("ATR shrinking (contraction)")
    if pd.notna(rs) and rs > 0:
        score += 15; reasons.append(f"Positive RS ({rs:.1f})")
    if pd.notna(p50) and 0 < p50 <= 20:
        score += 10; reasons.append(f"Not extended from 50MA ({p50:.1f}%)")
    if pd.notna(sr3m) and pd.notna(q3m) and sr3m > q3m:
        score += 15; reasons.append(f"Outperforming QQQ 3M ({sr3m:.1f}% vs {q3m:.1f}%)")
    return score, reasons


def score_day_trade(row):
    """Intraday day-trade scoring (0-100+)."""
    score = 0; reasons = []
    rvol   = _g(row, "RVOL")
    pm_lo  = _g(row, "Pre-Market Low")
    pm_hi  = _g(row, "Pre-Market High")
    vol20  = _g(row, "Vol_vs_20D")
    rs     = _g(row, "RS")
    curr   = _g(row, "Current Price")
    pv_lo  = _g(row, "Prev-Day Low")
    pv_hi  = _g(row, "Prev-Day High")
    if pd.notna(rvol):
        if rvol >= 1.5:   score += 30; reasons.append(f"High RVOL ({rvol:.2f}x)")
        elif rvol >= 1.1: score += 15; reasons.append(f"Elevated RVOL ({rvol:.2f}x)")
    if pd.notna(pm_lo) and pd.notna(pm_hi) and pm_lo > 0:
        pmr = (pm_hi - pm_lo) / pm_lo * 100
        if pmr >= 3:   score += 25; reasons.append(f"Wide PM range ({pmr:.1f}%)")
        elif pmr >= 1: score += 12; reasons.append(f"Moderate PM range ({pmr:.1f}%)")
    if pd.notna(vol20) and vol20 >= 1.2:
        score += 20; reasons.append(f"Vol above 20D avg ({vol20:.2f}x)")
    if pd.notna(rs) and rs > 50:
        score += 15; reasons.append(f"Strong momentum RS ({rs:.1f})")
    if pd.notna(pv_lo) and pd.notna(pv_hi) and pd.notna(curr):
        if pv_lo <= curr <= pv_hi * 1.05:
            score += 10; reasons.append("Price near prev-day range")
    return score, reasons


def score_avoid(row):
    """Penalty score — higher = stronger avoid signal (0-100+)."""
    score = 0; reasons = []
    p50  = _g(row, "% From 50MA")
    rs   = _g(row, "RS")
    dh   = _g(row, "Dist_52W_High%")
    rev  = _g(row, "Revenue")
    atr  = _g(row, "ATR Shrinking", 0)
    vdu  = _g(row, "VolumeDryingUp", 0)
    if pd.notna(p50) and p50 < -10:
        score += 20; reasons.append(f"Far below 50MA ({p50:.1f}%)")
    if pd.notna(rs) and rs < -20:
        score += 25; reasons.append(f"Deeply negative RS ({rs:.1f})")
    if pd.notna(dh) and dh < -50:
        score += 25; reasons.append(f">{abs(dh):.0f}% off 52W high")
    if pd.notna(rev) and rev < 0:
        score += 15; reasons.append(f"Negative revenue growth ({rev:.0f}%)")
    if atr and vdu and pd.notna(p50) and p50 < -5:
        score += 15; reasons.append("Contraction below MAs (downtrend)")
    return score, reasons


# ─────────────────────────────────────────────────────────────
# CLASSIFICATION ENGINE
# ─────────────────────────────────────────────────────────────

def classify_stock(row):
    """Returns dict: Ticker, Category, Score, Reasons (list), Avoid_Score."""
    ticker = row.get("Ticker", "?")
    sector = str(row.get("Sector", "")).strip()

    av_score, av_reasons = score_avoid(row)

    # ── ETFs ──
    if sector == "ETF":
        dt_score, dt_reasons = score_day_trade(row)
        pm_lo = _g(row, "Pre-Market Low", 0)
        pm_hi = _g(row, "Pre-Market High", 0)
        rvol  = _g(row, "RVOL", 0)
        pmr   = (pm_hi - pm_lo) / pm_lo * 100 if pm_lo > 0 else 0
        if rvol >= 1.5 or pmr >= 5:
            return dict(Ticker=ticker, Category="Day Trade (ETF)",
                        Score=dt_score, Reasons=dt_reasons, Avoid_Score=int(av_score))
        return dict(Ticker=ticker, Category="Watch",
                    Score=10, Reasons=["ETF — monitor for breakout"], Avoid_Score=int(av_score))

    # ── Hard avoid ──
    if av_score >= THRESH_AVOID:
        return dict(Ticker=ticker, Category="AVOID",
                    Score=int(av_score), Reasons=av_reasons, Avoid_Score=int(av_score))

    lt_score, lt_reasons = score_long_term(row)
    sw_score, sw_reasons = score_swing_trade(row)
    dt_score, dt_reasons = score_day_trade(row)

    qualifies_lt = lt_score >= THRESH_LT
    qualifies_sw = sw_score >= THRESH_SW
    qualifies_dt = dt_score >= THRESH_DT

    # ── Dual Long-Term + Swing ──
    if qualifies_lt and qualifies_sw:
        combined = list(dict.fromkeys(lt_reasons + sw_reasons))  # dedup, preserve order
        return dict(Ticker=ticker, Category="Long-Term + Swing",
                    Score=max(lt_score, sw_score), Reasons=combined,
                    Avoid_Score=int(av_score))

    if qualifies_lt:
        return dict(Ticker=ticker, Category="Long-Term Invest",
                    Score=lt_score, Reasons=lt_reasons, Avoid_Score=int(av_score))

    if qualifies_sw:
        return dict(Ticker=ticker, Category="Swing Trade",
                    Score=sw_score, Reasons=sw_reasons, Avoid_Score=int(av_score))

    if qualifies_dt:
        return dict(Ticker=ticker, Category="Day Trade",
                    Score=dt_score, Reasons=dt_reasons, Avoid_Score=int(av_score))

    best = max(lt_score, sw_score, dt_score)
    return dict(Ticker=ticker, Category="Watch",
                Score=best, Reasons=["Does not meet any category threshold"],
                Avoid_Score=int(av_score))


# ─────────────────────────────────────────────────────────────
# MAIN FUNCTION
# ─────────────────────────────────────────────────────────────

def categorize_watchlist(df: pd.DataFrame) -> pd.DataFrame:
    """
    Input:  DataFrame with the watchlist columns
    Output: DataFrame with Category, Score, Reasons columns added
    """
    df = df.copy()

    # Normalise column names
    df.columns = df.columns.str.strip()

    # Coerce all numeric input columns
    numeric_cols = [
        "Current Price", "52W Low", "52W High", "% From 52W Low", "% From 52W High",
        "Dist_52W_High%", "200MA", "50MA", "21EMA", "8EMA",
        "% From 50MA", "% From 21EMA", "% From 8EMA",
        "Revenue", "EPS_Growth%", "stock_return_3m", "qqq_return_3m",
        "RS", "RVOL", "Vol_vs_20D", "VolumeDryingUp", "ATR Shrinking",
        "Prev-Day Low", "Prev-Day High", "Pre-Market Low", "Pre-Market High",
        "_today_low", "_today_high",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Classify every row
    results = df.apply(classify_stock, axis=1).tolist()
    results_df = pd.DataFrame(results)  # Ticker, Category, Score, Reasons, Avoid_Score

    # Drop any pre-existing classification columns before merge
    df = df.drop(columns=["Category", "Score", "Reasons", "Avoid_Score"], errors="ignore")
    out = df.merge(results_df, on="Ticker", how="left")

    # Sort: category priority first, then descending Score
    out["_ord"] = out["Category"].map(CAT_ORDER).fillna(99)
    out = out.sort_values(["_ord", "Score"], ascending=[True, False]).drop(columns=["_ord"])

    # Reorder columns to match reference CSV exactly (extra input cols appended)
    leading  = [c for c in OUTPUT_COLS if c in out.columns]
    trailing = [c for c in out.columns if c not in leading]
    out = out[leading + trailing].reset_index(drop=True)

    return out


def save_csv(out: pd.DataFrame, path: str):
    """
    Save DataFrame to CSV with Reasons formatted as a Python list string,
    e.g.  "['Strong RS (96.1)', 'Above all MAs']"
    — exactly matching the reference CSV format.
    """
    export = out.copy()
    export["Reasons"] = export["Reasons"].apply(
        lambda r: str(r) if isinstance(r, list) else r
    )
    export.to_csv(path, index=False)
    print(f"Saved to {path}  ({len(export)} rows, {len(export.columns)} columns)")


def print_report(out: pd.DataFrame):
    """Pretty-print the categorisation report grouped by category."""
    icons = {
        "Long-Term Invest":  "💎",
        "Long-Term + Swing": "🔷",
        "Swing Trade":       "📈",
        "Day Trade":         "⚡",
        "Day Trade (ETF)":   "⚡",
        "Watch":             "👁",
        "AVOID":             "🚫",
    }
    present = [c for c in CAT_ORDER if c in out["Category"].values]

    print("\n" + "═" * 74)
    print("  HEDGE FUND WATCHLIST — CATEGORIZATION REPORT")
    print("═" * 74)

    for cat in present:
        subset = out[out["Category"] == cat]
        icon = icons.get(cat, "•")
        print(f"\n{icon}  {cat.upper()}  ({len(subset)} stocks)")
        print("─" * 74)
        for _, row in subset.iterrows():
            r = row["Reasons"]
            rsn = " | ".join(r[:3]) if isinstance(r, list) else str(r)
            rs_s  = f"{row['RS']:.1f}"       if pd.notna(row.get("RS"))          else "N/A"
            p50_s = f"{row['% From 50MA']:.1f}%" if pd.notna(row.get("% From 50MA")) else "N/A"
            print(f"  {row['Ticker']:<6}  Score:{row['Score']:>3}  RS:{rs_s:<9}"
                  f"From50MA:{p50_s:<9}  {rsn}")

    print("\n" + "═" * 74)
    print(f"  Total stocks: {len(out)}")
    print("═" * 74 + "\n")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Build DataFrame directly from the watchlist data ──
    # To load from CSV instead: df = pd.read_csv("watchlist.csv")
    raw = {
        "Ticker":     ["AAOI","AMD","AMZN","ASML","AVGO","BE","CIEN","COHR","CRDO","CRWV","DRAM","FLY","GLD","GLW","GOOGL","HIMS","HOOD","INOD","INTC","IONQ","IREN","LITE","LLY","META","MRVL","MSFT","MU","NVDA","NVTS","OKLO","PLTR","RGTI","SMCI","SNDK","SOFI","SPCX","SPY","TSLA","TSM","USAR"],
        "Sector":     ["Technology","Technology","Consumer Cyclical","Technology","Technology","Industrials","Technology","Technology","Technology","Technology","ETF","Industrials","ETF","Technology","Communication Services","Healthcare","Financial Services","Technology","Technology","Technology","Financial Services","Technology","Healthcare","Communication Services","Technology","Technology","Technology","Technology","Technology","Utilities","Technology","Technology","Technology","Technology","Financial Services","Industrials","ETF","Consumer Cyclical","Technology","Basic Materials"],
        "Current Price": [146.97,519.74,234.27,1762.77,382.07,326.19,463.51,392.5,268.99,100.88,69.93,24.98,365.92,205.83,345.29,32.7,97.19,81.55,131.65,53.6,50.3,842.53,1117.26,557.67,276.7,365.46,1048.51,199,18.32,54.06,113.5,19.53,32.45,1914.46,17.31,154.54,733.24,375.53,440.83,21.42],
        "Dist_52W_High%": [-37.1,-7.7,-15.9,-10,-22.8,-6.8,-27.3,-10.8,-12.9,-44,None,-66.2,-28.2,-5.2,-15.5,-53.6,-36.8,-34.8,-6.9,-36.7,-34.6,-22.4,-5.5,-30,-16.1,-34.2,-13.6,-15.9,-46.4,-72.1,-45.3,-66.4,-48,-18.7,-47.1,None,-3.6,-24.7,-7.5,-51.3],
        "% From 50MA": [-13.76,21.35,-8.81,10.11,-7.41,20.31,-10.79,8.06,29.97,-9.65,None,-35.13,-11.75,13.67,-6.44,19.26,14.73,3.32,24.5,-0.91,-8.25,-6.76,9.41,-9.72,33.27,-11.41,35.53,-5.34,-11.84,-17.33,-17.52,-3.22,-2.67,30.26,1.94,None,0.04,-7.23,7.41,-10.94],
        "% From 8EMA": [-9.45,-0.23,-1.81,-3.43,-2.03,5.77,1.44,0.26,1.54,-6.12,None,-16.37,-4.35,6.3,-3.01,2.25,-2.43,-12.44,2.67,-5.92,-10.13,-2.76,0.27,-2.36,-3.37,-3.85,-1.43,-2.87,-16.61,-6.39,-8.23,-6.47,-1.22,-3.89,0.41,None,-0.95,-4.53,-0.09,-6.59],
        "Revenue":    [51.4,37.8,16.6,13.2,47.9,130.4,39.5,20.5,157,111.6,None,44.8,None,20,21.8,3.8,15.1,54.4,7.2,754.7,0,90.1,55.5,33.1,27.6,18.3,196.3,85.2,-38.7,None,84.7,198.9,122.7,251,42.5,None,None,15.8,35.1,None],
        "EPS_Growth%":[834.2,337.5,33.5,61,223.3,14645.1,220.2,285.7,256.5,68.8,None,73.9,None,102.1,10.9,1088.9,40.5,78.6,353.5,-370.1,-222.1,221.5,57.9,31.9,112.2,15.5,192.3,94.9,78,0.7,133.9,77.2,66.9,524.9,81.4,None,None,129.4,72.2,100.8],
        "RS":         [7.44,114.93,-10.37,5.44,-1.18,96.12,-15.13,23.26,137.84,-5.84,None,-30.29,-33.12,19.62,-2.34,35.89,12.96,65.72,158.01,46.69,0.39,-12.61,0.91,-27.28,160.03,-22.53,153.39,-9.65,72.23,-23.21,-47.78,7.97,13.9,161.4,-16.49,None,-9.39,-23.72,5.74,7.7],
        "RVOL":       [0.84,0.69,1.51,1.06,1.12,1,1.01,0.74,0.8,0.73,2.07,0.92,1.77,1.4,1.46,0.47,0.92,0.45,0.74,0.72,0.81,0.75,0.88,0.83,0.78,1.19,1.31,0.93,0.91,0.63,1.09,0.76,0.98,0.76,1.62,None,1.06,0.67,0.71,0.72],
        "Vol_vs_20D": [0.74,0.85,1.43,0.98,0.8,1,0.73,0.73,0.67,0.71,None,0.77,1.5,1.38,1.26,0.65,0.82,0.58,0.77,0.9,0.82,0.73,0.89,0.72,0.48,1.07,1.2,0.89,1.04,0.7,1.07,0.71,0.67,0.95,1.4,None,0.94,0.79,0.72,0.79],
        "VolumeDryingUp": [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,1,0,1,0,0,0,0,0,0,0,0,1,1,0,0,0,0,0,None,0,0,0,0],
        "ATR Shrinking":  [1,0,0,0,0,0,1,0,0,0,0,1,0,0,0,0,0,0,0,1,1,1,0,0,0,0,0,1,1,1,0,1,0,0,0,None,0,0,0,1],
        "Above 8EMA/21EMA/50MA/200MA": ["False","False","False","False","False","True","False","True","True","False","False","False","False","True","False","False","False","False","True","False","False","False","True","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False"],
        "CANSLIM_Pass": ["False","False","False","False","False","False","False","False","False","False","False","False","False","True","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False","False"],
        "Pre-Market Low":  [151.14,519.74,232.62,1762.77,382.07,326.19,471.88,392.5,268.99,104,69.93,24.96,364.46,205.83,340.01,32.8,98.01,82.13,131.65,53.6,51.66,842.53,1111.22,554.08,286.5,362.52,1048.51,199,19.3,54.6,110.86,19.83,32.42,1914.46,17.26,153.43,733.24,374.5,440.83,21.77],
        "Pre-Market High": [154.8,545.99,234.6,1860.03,391.53,347.6,484.82,412.8,287,106.3,79.83,25.85,369,227.32,345.29,33.17,99.69,84.5,140.55,55.15,52.7,887.81,1123.85,559.26,292.18,366.53,1250,202.19,19.82,55.89,113.6,20.11,33.85,2244.49,17.61,165.49,739.92,379.66,456.91,22.1],
        "Prev-Day Low":    [137.7,503.5,232.95,1730.29,376.96,315.01,440.7,365.01,259.71,98.25,None,24.68,363.32,190.93,341.93,31.62,96.29,81.27,127.95,52.36,48.84,803.24,1100.98,555.55,263.66,364.78,991.1,196.58,17.42,52.87,112.25,19.01,31.39,1861.01,17.18,None,730.84,373.05,432.58,21.11],
        "Prev-Day High":   [150.34,524.96,242.42,1779.65,388.74,345.5,480.59,397,284,104.54,None,26.99,370.9,217.09,353.48,33.75,104.27,87.26,136.08,57.35,54.79,866.43,1135.14,569.04,281.95,378.88,1083.32,201.67,21.13,56.69,118,20.97,33.98,2021.5,18.43,None,739.95,384.58,443.86,22.56],
        "stock_return_3m": [28.459,135.956,10.6561,26.4641,19.8425,117.142,5.89673,44.2802,158.868,15.1861,None,-9.26263,-12.0997,40.6423,18.6849,56.9098,33.9813,86.7415,179.038,67.7096,21.4096,8.41001,21.9304,-6.25662,181.056,-1.50389,174.414,11.3723,93.249,-2.18925,-26.7553,28.996,34.9272,182.427,4.52899,None,11.6348,-2.69983,26.7664,28.726],
        "qqq_return_3m":   [21.0236]*40,
    }
    df = pd.DataFrame(raw)

    out = categorize_watchlist(df)
    print_report(out)
    save_csv(out, "/tmp/categorized_watchlist.csv")

    print("\nCATEGORY SUMMARY")
    print("─" * 52)
    for cat in [c for c in CAT_ORDER if c in out["Category"].values]:
        tickers = ", ".join(out[out["Category"] == cat]["Ticker"].tolist())
        print(f"  {cat:<22}  {tickers}")
