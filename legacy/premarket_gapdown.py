"""
S&P 500 Scanner — 52W High Pullback Setup
==========================================
Filters:
  1. Stock hit its 52-week high within the last 10 trading days
  2. Market cap > $1 Billion
  3. Relative Volume (RVOL) > 1.5  (today's vol vs 20-day avg)
  4. Pre-market price change < 3%  (pulling back, not extended)

Usage:
  pip install yfinance pandas requests
  python sp500_scanner.py

Run this before/during pre-market hours (4 AM – 9:30 AM ET) for best results.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import sys

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
MARKET_CAP_MIN    = 1_000_000_000   # $1 Billion
RVOL_MIN          = 1.5             # Relative volume threshold
PREMARKET_PCT_MAX = 3.0             # Pre-market % change must be < this
HIGH_WINDOW_DAYS  = 10              # 52W high must be within last N calendar days
VOL_AVG_DAYS      = 20             # Days to average for RVOL denominator

# ─────────────────────────────────────────────
# S&P 500 TICKERS  (as of mid-2026)
# ─────────────────────────────────────────────
SP500_TICKERS = [
    "AAOI", "AMD",  "AMZN", "ASML", "AVGO", "BE",   "CIEN", "COHR",
    "CRDO", "CRWV", "DRAM", "FLY",  "GLD",  "GLW",  "GOOGL","HIMS",
    "HOOD", "INOD", "INTC", "IONQ", "IREN", "LITE", "LLY",  "META",
    "MRVL", "MSFT", "MU",   "NVDA", "NVTS", "OKLO", "PLTR", "RGTI",
    "SMCI", "SNDK", "SOFI", "SPCX", "TSLA", "TSM",  "USAR",
]


'''
SP500_TICKERS = [
    "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB","AKAM","ALB","ARE","ALGN",
    "ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN","AMCR","AEE","AAL","AEP","AXP","AIG","AMT","AWK","AMP",
    "AME","AMGN","APH","ADI","ANSS","AON","APA","AAPL","AMAT","APTV","ACGL","ADM","ANET","AJG","AIZ",
    "T","ATO","ADSK","ADP","AZO","AVB","AVY","AXON","BKR","BALL","BAC","BK","BBWI","BAX","BDX","WRB",
    "BBY","BIO","TECH","BIIB","BLK","BX","BA","BSX","BMY","AVGO","BR","BRO","BF-B","BLDR","BG",
    "CDNS","CZR","CPT","CPB","COF","CAH","KMX","CCL","CARR","CTLT","CAT","CBOE","CBRE","CDW","CE","COR",
    "CNC","CNP","CF","CHRW","CRL","SCHW","CHTR","CVX","CMG","CB","CHD","CI","CINF","CTAS","CSCO","C",
    "CFG","CLX","CME","CMS","KO","CTSH","CL","CMCSA","CMA","CAG","COP","ED","STZ","CEG","COO","CPRT",
    "GLW","CPAY","CTVA","CSGP","COST","CTRA","CCI","CSX","CMI","CVS","DHR","DRI","DVA","DAY","DECK",
    "DE","DAL","DVN","DXCM","FANG","DLR","DFS","DG","DLTR","D","DPZ","DOV","DHI","DTE","DUK","DD",
    "EMN","ETN","EBAY","ECL","EIX","EW","EA","ELV","LLY","EMR","ENPH","ETR","EOG","EPAM","EQT","EFX",
    "EQIX","EQR","ESS","EL","ETSY","EG","ES","EXC","EXPE","EXPD","EXR","XOM","FFIV","FDS","FICO",
    "FAST","FRT","FDX","FIS","FITB","FSLR","FE","FI","FMC","F","FTNT","FTV","FOXA","FOX","BEN","FCX",
    "GRMN","IT","GE","GEHC","GEV","GEN","GNRC","GD","GIS","GM","GPC","GILD","GS","HAL","HIG","HAS",
    "HCA","DOC","HSIC","HSY","HES","HPE","HLT","HOLX","HD","HON","HRL","HST","HWM","HPQ","HUBB","HUM",
    "HBAN","HII","IBM","IEX","IDXX","ITW","INCY","IR","PODD","INTC","ICE","IFF","IP","IPG","INTU","ISRG",
    "IVZ","INVH","IQV","IRM","JBHT","JBL","JKHY","J","JNJ","JCI","JPM","JNPR","K","KVUE","KDP","KEY",
    "KEYS","KMB","KIM","KMI","KLAC","KHC","KR","LHX","LH","LRCX","LW","LVS","LDOS","LEN","LIN","LYV",
    "LKQ","LMT","L","LOW","LULU","LYB","MTB","MRO","MPC","MKTX","MAR","MMC","MLM","MAS","MA","MTCH",
    "MKC","MCD","MCK","MDT","MRK","META","MET","MTD","MGM","MCHP","MU","MSFT","MAA","MRNA","MHK","MOH",
    "TAP","MDLZ","MPWR","MNST","MCO","MS","MOS","MSI","MSCI","NDAQ","NTAP","NFLX","NEM","NWSA","NWS",
    "NEE","NKE","NI","NDSN","NSC","NTRS","NOC","NCLH","NRG","NUE","NVDA","NVR","NXPI","ORLY","OXY",
    "ODFL","OMC","ON","OKE","ORCL","OTIS","PCAR","PKG","PANW","PARA","PH","PAYX","PAYC","PYPL","PNR",
    "PEP","PFE","PCG","PM","PSX","PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR","PLD","PRU",
    "PEG","PTC","PSA","PHM","PWR","QCOM","DGX","RL","RJF","RTX","O","REG","REGN","RF","RSG",
    "RMD","RVTY","ROK","ROL","ROP","ROST","RCL","SPGI","CRM","SBAC","SLB","STX","SEE","SRE","NOW","SHW",
    "SPG","SWKS","SJM","SW","SNA","SOLV","SO","LUV","SWK","SBUX","STT","STLD","STE","SYK","SMCI","SYF",
    "SNPS","SYY","TMUS","TROW","TTWO","TPR","TRGP","TGT","TEL","TDY","TFX","TER","TSLA","TXN","TXT",
    "TMO","TJX","TSCO","TT","TDG","TRV","TRMB","TFC","TYL","TSN","USB","UBER","UDR","ULTA","UNP","UAL",
    "UPS","URI","UNH","UHS","VLO","VTR","VRSN","VRSK","VZ","VRTX","VLTO","VFC","VTRS","VICI","V","VST",
    "WAB","WBA","WMT","DIS","WBD","WM","WAT","WEC","WFC","WELL","WST","WDC","WY","WHR","WMB","WTW",
    "GWW","WYNN","XEL","XYL","YUM","ZBRA","ZBH","ZTS"
] 
'''

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def progress(msg, end="\n"):
    print(msg, end=end, flush=True)

def scan():
    today = datetime.now()
    cutoff_10d = today - timedelta(days=HIGH_WINDOW_DAYS)

    progress(f"\n{'='*65}")
    progress(f"  S&P 500 Scanner  |  {today.strftime('%Y-%m-%d %H:%M')}")
    progress(f"{'='*65}")
    progress(f"  Filters: 52WH in last {HIGH_WINDOW_DAYS}d | MCap>${MARKET_CAP_MIN/1e9:.0f}B "
             f"| RVOL>{RVOL_MIN} | PreMkt<{PREMARKET_PCT_MAX}%")
    progress(f"  Universe: {len(SP500_TICKERS)} S&P 500 stocks")
    progress(f"{'='*65}\n")

    results = []
    skipped = 0
    BATCH = 50

    for i in range(0, len(SP500_TICKERS), BATCH):
        batch = SP500_TICKERS[i:i+BATCH]
        batch_n = i // BATCH + 1
        total_batches = (len(SP500_TICKERS) - 1) // BATCH + 1
        progress(f"  Batch {batch_n}/{total_batches} — downloading {len(batch)} tickers...", end=" ")

        try:
            raw = yf.download(
                " ".join(batch),
                period="1y",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True
            )
        except Exception as e:
            progress(f"ERROR: {e}")
            continue

        for ticker in batch:
            try:
                # Extract ticker data
                if len(batch) == 1:
                    df = raw.copy()
                else:
                    if ticker not in raw.columns.get_level_values(0):
                        skipped += 1
                        continue
                    df = raw[ticker].copy()

                df = df.dropna(subset=["Close", "Volume"])
                if len(df) < VOL_AVG_DAYS + 5:
                    skipped += 1
                    continue

                # ── Filter 1: 52W High in last 10 days ──────────────────
                high_52w = df["High"].max()
                high_52w_date = df["High"].idxmax()

                if pd.Timestamp(high_52w_date) < pd.Timestamp(cutoff_10d):
                    continue   # 52W high was NOT recent

                # ── Filter 2: RVOL > 1.5 ────────────────────────────────
                avg_vol = df["Volume"].iloc[-(VOL_AVG_DAYS + 1):-1].mean()
                last_vol = df["Volume"].iloc[-1]
                rvol = last_vol / avg_vol if avg_vol > 0 else 0

                if rvol < RVOL_MIN:
                    continue

                # ── Filters 3 & 4: Market Cap + Premarket (via .info) ───
                t = yf.Ticker(ticker)
                info = t.info

                market_cap = info.get("marketCap") or 0
                if market_cap < MARKET_CAP_MIN:
                    continue

                last_close = df["Close"].iloc[-1]
                premarket_price = (
                    info.get("preMarketPrice")
                    or info.get("regularMarketPrice")
                    or last_close
                )
                premarket_pct = ((premarket_price - last_close) / last_close) * 100

                if abs(premarket_pct) >= PREMARKET_PCT_MAX:
                    continue  # Extended premarket move — skip

                # ── Passed all filters ────────────────────────────────────
                # Extra metrics
                pct_from_52wh = ((last_close - high_52w) / high_52w) * 100
                days_since_high = (today - high_52w_date.to_pydatetime().replace(tzinfo=None)).days

                results.append({
                    "Ticker":         ticker,
                    "Company":        (info.get("longName") or ticker)[:28],
                    "Sector":         info.get("sector", "N/A"),
                    "Last Close":     round(float(last_close), 2),
                    "PreMkt $":       round(float(premarket_price), 2),
                    "PreMkt Chg%":    round(premarket_pct, 2),
                    "52W High":       round(float(high_52w), 2),
                    "52W High Date":  high_52w_date.strftime("%Y-%m-%d"),
                    "Days Since 52WH":days_since_high,
                    "% From 52WH":    round(pct_from_52wh, 2),
                    "RVOL":           round(rvol, 2),
                    "Mkt Cap $B":     round(market_cap / 1e9, 2),
                })

            except Exception:
                skipped += 1
                continue

        progress("done")
        time.sleep(0.3)  # polite delay

    # ─────────────────────────────────────────────
    # OUTPUT
    # ─────────────────────────────────────────────
    progress(f"\n{'='*65}")
    progress(f"  SCAN COMPLETE  |  {len(results)} stocks matched  |  {skipped} skipped/errored")
    progress(f"{'='*65}\n")

    if not results:
        progress("  No stocks matched all filters today.")
        return

    df_out = pd.DataFrame(results).sort_values("RVOL", ascending=False)

    # Console table
    cols = ["Ticker", "Company", "Sector", "Last Close", "PreMkt Chg%",
            "% From 52WH", "Days Since 52WH", "RVOL", "Mkt Cap $B"]
    print(df_out[cols].to_string(index=False))

    # Save to CSV
    csv_name = f"sp500_scan_{today.strftime('%Y%m%d_%H%M')}.csv"
    df_out.to_csv(csv_name, index=False)
    progress(f"\n  ✓ Results saved to: {csv_name}")

    # Summary by sector
    progress("\n── By Sector ──────────────────────────────────────────────")
    sector_counts = df_out["Sector"].value_counts()
    for sector, count in sector_counts.items():
        progress(f"  {sector:<35} {count} stock(s)")

    return df_out


if __name__ == "__main__":
    try:
        scan()
    except KeyboardInterrupt:
        progress("\n\nScan interrupted by user.")
        sys.exit(0)