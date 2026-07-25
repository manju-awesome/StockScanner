"""
One-time repair: fill missing MarketCap values in research_index.json.

Yahoo's quote endpoint throttles under heavy scanning, so past scan runs
left some entries with MarketCap=None (sector present, cap missing — a
partial .info response). This refetches just the missing caps via
fast_info (light endpoint) and patches both the curated `market_cap`
field and `raw.MarketCap` in place. Safe to rerun; only touches entries
that are still missing a value.

Run from the project root:  python3 scripts/backfill_market_caps.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "output" / "research_index.json"

import yfinance as yf

idx = json.loads(INDEX.read_text())
missing = [t for t, e in idx.items()
           if e.get("market_cap") is None
           and (e.get("raw") or {}).get("MarketCap") is None]
print(f"{len(missing)} of {len(idx)} entries missing MarketCap")

fixed = failed = 0
for i, t in enumerate(sorted(missing), 1):
    cap = None
    try:
        cap = yf.Ticker(t).fast_info["market_cap"]
    except Exception:
        pass
    if cap:
        cap = int(cap)
        idx[t]["market_cap"] = cap
        idx[t].setdefault("raw", {})["MarketCap"] = cap
        fixed += 1
    else:
        failed += 1
    if i % 25 == 0:
        print(f"  {i}/{len(missing)} … ({fixed} fixed)")
    time.sleep(0.15)

INDEX.write_text(json.dumps(idx))
print(f"Done: {fixed} filled, {failed} still unavailable "
      f"(delisted/renamed tickers stay blank).")
