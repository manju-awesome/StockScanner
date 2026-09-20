"""
Volatility context for the CSP engine — is the premium actually rich?

The ranking question is never "which name has the highest IV". Raw IV
just finds the jumpiest tickers in the universe; a 55% IV name at the
bottom of its own range is a worse sale than a 30% name at the top of
its. What matters is IV relative to that ticker's OWN history.

The honest problem: **IV Rank needs a year of stored IV, and nothing in
this project has ever stored it.** yfinance serves only the current
surface, so there is no way to backfill. Rather than fabricate a rank
from a proxy and print it in the same column as a real one, this module:

  - persists one ATM-IV observation per ticker per run, building the
    history from today forward (`record`), and
  - reports IV Rank / percentile ONLY once there are enough distinct
    observations to mean something, with `available: False` and a
    reason until then (`rank`), and
  - always computes IV vs realised volatility, which needs no stored
    history and answers a closely related question today (`iv_vs_hv`).

The CSP score reads `iv_vs_hv` when the rank is unavailable and says
which one it used, so a run today is scoreable and a run in six months
is strictly better informed.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path

# Enough distinct days that a min/max range is not one lucky observation.
MIN_HISTORY = 30
# A full year is the convention; the store keeps a little more so the
# window is complete after trimming.
HISTORY_DAYS = 400

_STORE = (Path(__file__).resolve().parents[4] / "data" / "csp"
          / "iv_history.json")


# ─────────────────────────────────────────────────────────────────────────
# REALISED VOLATILITY — computable from price history alone
# ─────────────────────────────────────────────────────────────────────────

def realised_vol(closes, window=30):
    """Annualised close-to-close volatility over `window` sessions.

    252 trading days, not 365: this is measured from trading sessions,
    unlike the calendar-time IV the chain quotes. The two are compared
    as ratios, which is dimensionless, so the different day counts do
    not contaminate each other.
    """
    vals = [float(c) for c in (closes or []) if c is not None and c == c]
    if len(vals) < window + 1:
        return None
    recent = vals[-(window + 1):]

    rets = []
    for a, b in zip(recent, recent[1:]):
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < 5:
        return None

    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252.0)


def iv_vs_hv(iv, hv):
    """The variance-risk premium, as a ratio and a verdict.

    Selling options is paid when IV sits above realised — that gap IS
    the edge. A ratio near or below 1.0 means the option is priced at
    or under what the stock has actually been doing, which is a poor
    sale regardless of the headline IV number.
    """
    if iv is None or hv is None or hv <= 0:
        return {"ratio": None, "label": None, "verdict": None}

    ratio = iv / hv
    # Banded so that "about equal to realised" reads as WEAK rather than
    # fair. Selling at realised vol means collecting no variance risk
    # premium at all — you are taking equity downside for the statistical
    # break-even, which is not a reason to tie up collateral.
    if ratio > 1.30:
        label, verdict = "⚠️ Very rich — find the catalyst", "INVESTIGATE"
    elif ratio >= 1.15:
        label, verdict = "🟢 Excellent", "EXCELLENT"
    elif ratio >= 1.00:
        label, verdict = "🟢 Good", "GOOD"
    elif ratio >= 0.85:
        label, verdict = "🟡 Weak — near realised vol", "WEAK"
    else:
        label, verdict = "🔴 Poor — below realised vol", "POOR"

    return {"ratio": round(ratio, 2), "label": label, "verdict": verdict,
            "iv": round(iv * 100, 1), "hv": round(hv * 100, 1),
            # A ratio this high is usually the market pricing a known
            # event. Rich premium and a pending catalyst is the one
            # combination that looks best and behaves worst.
            "needs_catalyst_check": ratio > 1.30}


# ─────────────────────────────────────────────────────────────────────────
# IV RANK — needs stored history, so it accumulates
# ─────────────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        return json.loads(_STORE.read_text()) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except OSError as e:
        print(f"[CSP] IV history unreadable ({e})")
        return {}


def record(ticker: str, iv, today=None) -> None:
    """Append today's ATM IV for `ticker`, one observation per day.

    Re-running the scan overwrites the same day rather than stacking
    duplicates, so an afternoon re-run refines the reading instead of
    weighting today four times in the range.
    """
    if not ticker or iv is None:
        return
    day = str(today or _dt.date.today())
    store = _load()
    series = {str(k): v for k, v in (store.get(ticker) or {}).items()}
    series[day] = round(float(iv), 6)

    cutoff = str((_dt.date.today() - _dt.timedelta(days=HISTORY_DAYS)))
    store[ticker] = {k: v for k, v in series.items() if k >= cutoff}

    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        _STORE.write_text(json.dumps(store, indent=0, sort_keys=True))
    except OSError as e:
        print(f"[CSP] IV history unwritable ({e})")


def history_size(ticker: str) -> int:
    return len(_load().get(ticker) or {})


def rank(ticker: str, iv):
    """IV Rank and IV Percentile against this ticker's stored history.

    IV Rank    = (IV - min) / (max - min)      — position in the range
    IV Pctile  = share of days IV closed lower — position in the mass

    They disagree when the range has one spike, and both are reported
    because that disagreement is informative.

    Returns `available: False` with a reason whenever the store is too
    short. The caller must not substitute a default: an IV Rank is a
    claim about a year of history, and inventing one from a month is
    the kind of number that looks authoritative and is not.
    """
    series = _load().get(ticker) or {}
    n = len(series)
    if iv is None:
        return {"available": False, "reason": "no ATM IV in the chain",
                "observations": n}
    if n < MIN_HISTORY:
        return {"available": False,
                "reason": (f"IV history is {n} day(s); needs {MIN_HISTORY}. "
                           f"Building from each scan — no rank until then"),
                "observations": n}

    vals = sorted(float(v) for v in series.values())
    lo, hi = vals[0], vals[-1]
    below = sum(1 for v in vals if v < iv)

    return {
        "available": True,
        "observations": n,
        "rank": (round((iv - lo) / (hi - lo) * 100, 1)
                 if hi > lo else None),
        "percentile": round(below / n * 100, 1),
        "low": round(lo * 100, 1),
        "high": round(hi * 100, 1),
        "iv": round(iv * 100, 1),
    }


def iv_opportunity(rank_info, ratio_info) -> dict:
    """The single 0-100 IV reading the CSP score consumes.

    Prefers a real IV Rank; falls back to IV/HV; names its source so the
    page can show which one was used. Returns score None when neither is
    available — a missing input is reported, never scored as average.
    """
    if rank_info.get("available") and rank_info.get("rank") is not None:
        r = rank_info["rank"]
        # Below ~25 the premium is not compensating for assignment risk;
        # above ~70 it is rich. Linear between, clamped.
        score = max(0.0, min(100.0, (r - 20.0) / 55.0 * 100.0))
        return {"score": round(score), "source": "IV Rank",
                "detail": f"IV Rank {r:.0f} over {rank_info['observations']} days",
                "rank": r}

    ratio = ratio_info.get("ratio")
    if ratio is not None:
        # Anchored to the bands above: 1.00 (selling at realised) is the
        # break-even and scores 50, 1.15 scores 75, 1.30 tops out. Above
        # 1.30 the score is HELD DOWN rather than raised — a ratio that
        # rich is usually an event being priced, and rewarding it would
        # point the ranking straight at the trades most likely to gap.
        if ratio > 1.30:
            score = 70.0
        else:
            score = max(0.0, min(100.0, (ratio - 0.70) / 0.60 * 100.0))
        return {"score": round(score), "source": "IV vs realised",
                "detail": (f"IV {ratio_info['iv']:.0f}% vs realised "
                           f"{ratio_info['hv']:.0f}% — {ratio:.2f}×"),
                "rank": None,
                "needs_catalyst_check": ratio_info.get("needs_catalyst_check")}

    return {"score": None, "source": None,
            "detail": "no volatility reading available", "rank": None}
