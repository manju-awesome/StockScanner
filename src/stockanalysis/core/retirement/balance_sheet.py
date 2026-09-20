"""
The balance sheet: what you own, what you owe, and what is actually
available to fund a retirement.

Three totals, and they are not the same number
------------------------------------------------
NET WORTH is everything minus debt. It is the number people quote and
the least useful of the three, because it counts the house you live in.

INVESTABLE assets are what could be sold and spent without changing how
you live. The primary residence is excluded — you cannot eat it, and
selling it means buying or renting another one.

RETIREMENT-FUNDING assets are investable assets minus capital already
promised to another goal: the education fund, the emergency reserve, the
land you intend to buy. Section 12 of the spec exists because this is
where plans quietly fail — one pot funding two goals looks like it funds
both right up until the year they collide.

Tax buckets
-----------
Assets are also grouped by tax treatment (taxable / tax-deferred /
tax-free / cash), because the withdrawal sequence and every conversion
question later depends on the mix, and because a $1M traditional 401(k)
and a $1M Roth are not the same $1M. This module reports the mix; it
does not apply tax rules to it — no tax rule is asserted anywhere in
this engine without a CPA/CA confirmation flag.
"""

from __future__ import annotations

import csv
from pathlib import Path

TAX_BUCKETS = ["taxable", "tax_deferred", "tax_free", "cash", "non_financial"]
LIQUIDITY = ["liquid", "semi_liquid", "illiquid"]

_DATA = Path(__file__).resolve().parents[4] / "data"

# Assets that exist but cannot fund retirement spending without changing
# your life. Kept on the balance sheet, kept out of the withdrawal base.
_NOT_INVESTABLE_KINDS = {"primary_residence", "personal_property", "farm_use"}


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def normalise_asset(d: dict) -> dict:
    """One asset row, with its classification made explicit."""
    kind = str(d.get("kind") or "other").lower()
    tax = str(d.get("tax_status") or "").lower()
    if tax not in TAX_BUCKETS:
        tax = _infer_tax_status(kind)
    liq = str(d.get("liquidity") or "").lower()
    if liq not in LIQUIDITY:
        liq = _infer_liquidity(kind)
    return {
        "label": str(d.get("label") or kind.replace("_", " ").title()),
        "kind": kind,
        "value": _num(d.get("value")),
        "currency": str(d.get("currency") or "USD").upper(),
        "tax_status": tax,
        "liquidity": liq,
        "expected_return": _num(d.get("expected_return")),
        "risk": str(d.get("risk") or ""),
        "goal": str(d.get("goal") or "retirement"),
        "investable": bool(d.get("investable",
                                kind not in _NOT_INVESTABLE_KINDS)),
        "allocation": d.get("allocation") or {},
        "note": str(d.get("note") or ""),
    }


def _infer_tax_status(kind: str) -> str:
    return {
        "401k": "tax_deferred", "traditional_401k": "tax_deferred",
        "traditional_ira": "tax_deferred", "ira": "tax_deferred",
        "403b": "tax_deferred", "sep_ira": "tax_deferred",
        "roth_401k": "tax_free", "roth_ira": "tax_free", "hsa": "tax_free",
        "529": "tax_free",
        "brokerage": "taxable", "espp": "taxable", "rsu": "taxable",
        "india_equity": "taxable", "india_mutual_fund": "taxable",
        "nps": "tax_deferred", "ppf": "tax_free", "epf": "tax_deferred",
        "cash": "cash", "savings": "cash", "cd": "cash", "nre": "cash",
        "nro": "cash", "fd": "cash",
    }.get(kind, "non_financial" if kind in _NOT_INVESTABLE_KINDS
          else "taxable")


def _infer_liquidity(kind: str) -> str:
    if kind in ("cash", "savings", "brokerage", "cd", "fd", "nre", "nro"):
        return "liquid"
    if kind in _NOT_INVESTABLE_KINDS or kind in (
            "rental_property", "land", "private_equity", "ppf", "nps", "epf"):
        return "illiquid"
    return "semi_liquid"


def normalise_liability(d: dict) -> dict:
    return {
        "label": str(d.get("label") or "Debt"),
        "kind": str(d.get("kind") or "other").lower(),
        "balance": _num(d.get("balance")),
        "rate": _num(d.get("rate")),
        "monthly_payment": _num(d.get("monthly_payment")),
        "years_remaining": _num(d.get("years_remaining")),
        "currency": str(d.get("currency") or "USD").upper(),
        "deductible": bool(d.get("deductible", False)),
    }


def debt_disposition(liab: dict, expected_real_return: float,
                     inflation: float) -> dict:
    """PAY DOWN / REFINANCE / MAINTAIN / IGNORE, and why.

    The comparison is between the debt's REAL rate and the portfolio's
    expected REAL return, because paying down debt is a guaranteed return
    equal to its rate while investing is a risky one. Comparing a certain
    5% against an uncertain 5% and calling them equal is the standard
    error here: the guaranteed side deserves a premium, so a debt only
    reads MAINTAIN when the expected return clears it with room.

    Fixed-rate debt is also an inflation hedge — inflation erodes the
    balance while the payment stays flat — which is why the real rate,
    not the nominal one, is what gets compared.
    """
    rate = liab.get("rate")
    if rate is None:
        return {"action": "REVIEW", "reason": "Interest rate not recorded."}
    real_rate = rate - inflation
    edge = expected_real_return - real_rate

    if rate >= 12:
        return {"action": "PAY DOWN",
                "reason": (f"{rate:.1f}% is a guaranteed loss no portfolio "
                           f"reliably beats. This comes before investing.")}
    if rate >= 8:
        return {"action": "PAY DOWN",
                "reason": (f"Real rate {real_rate:.1f}% vs expected real "
                           f"return {expected_real_return:.1f}%. Certain "
                           f"cost, uncertain return — take the certain one.")}
    if edge >= 2.5:
        return {"action": "MAINTAIN",
                "reason": (f"Real rate {real_rate:.1f}% leaves a "
                           f"{edge:.1f}pt expected spread. Keep the debt, "
                           f"invest the difference — and only while the "
                           f"payment is comfortably covered.")}
    if edge >= 0:
        return {"action": "REFINANCE / MAINTAIN",
                "reason": (f"Real rate {real_rate:.1f}% is close to the "
                           f"{expected_real_return:.1f}% expected return. "
                           f"Too thin a spread to be worth the risk — "
                           f"refinance if you can, otherwise it is a wash.")}
    return {"action": "PAY DOWN",
            "reason": (f"Real rate {real_rate:.1f}% exceeds the "
                       f"{expected_real_return:.1f}% expected real return. "
                       f"Paying it down beats investing, with no risk.")}


def read_brokerage_positions(path: Path | None = None) -> dict:
    """Equity market values from the workstation's own portfolio.csv.

    Read rather than re-entered so the retirement balance sheet cannot
    drift from the broker sync that already runs, and offline (market
    values as last synced) so this never turns a page render into a
    network call. Staleness is reported, not hidden.
    """
    path = path or (_DATA / "portfolio.csv")
    if not path.exists():
        return {"total": None, "positions": [], "as_of": None,
                "source": str(path), "error": "portfolio.csv not found"}
    rows, latest = [], None
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            mv = _num(r.get("Market_Value"))
            if mv is None or mv == 0:
                continue
            rows.append({"ticker": (r.get("Ticker") or "").strip().upper(),
                         "value": mv,
                         "strategy": (r.get("Strategy") or "").strip(),
                         "account": (r.get("Account") or "").strip()})
            synced = (r.get("Last_Synced") or "").strip()
            if synced and (latest is None or synced > latest):
                latest = synced
    total = sum(r["value"] for r in rows)
    for r in rows:
        r["weight"] = 100.0 * r["value"] / total if total else None
    rows.sort(key=lambda r: -r["value"])
    return {"total": total, "positions": rows, "as_of": latest,
            "source": str(path), "error": None}


def concentration_flags(positions: list[dict]) -> list[dict]:
    """Section 7's flags, applied to whatever the brokerage actually holds.

    Deliberately stops at classification. A concentrated position in a
    company you understand and intend to hold is a different object from
    one you drifted into, and this engine has no way to tell them apart —
    so it names the risk and leaves the disposition to you.
    """
    out = []
    for p in positions:
        w = p.get("weight")
        if w is None:
            continue
        if w > 10:
            band, flag = "HIGH", "🔴"
        elif w >= 5:
            band, flag = "MODERATE", "🟠"
        else:
            band, flag = "LOWER", "🟢"
        out.append({**p, "band": band, "flag": flag})
    return out


def build(profile, expected_real_return: float, inflation: float,
          link_brokerage: bool = True) -> dict:
    """The whole balance sheet, classified and totalled."""
    assets = [normalise_asset(a) for a in (profile.raw.get("assets") or [])]

    brokerage = read_brokerage_positions() if link_brokerage else None
    if brokerage and brokerage.get("total"):
        for a in assets:
            if a["kind"] == "brokerage" and a.get("value") is None:
                a["value"] = brokerage["total"]
                a["note"] = (a["note"] + " " if a["note"] else "") + \
                    f"From portfolio.csv as of {brokerage['as_of'] or '?'}."

    known = [a for a in assets if a["value"] is not None]
    unknown = [a for a in assets if a["value"] is None]

    liabilities = [normalise_liability(l)
                   for l in (profile.raw.get("liabilities") or [])]
    for l in liabilities:
        l["disposition"] = debt_disposition(l, expected_real_return, inflation)

    total_assets = sum(a["value"] for a in known)
    total_debt = sum(l["balance"] for l in liabilities
                     if l["balance"] is not None)

    by_tax = {b: sum(a["value"] for a in known if a["tax_status"] == b)
              for b in TAX_BUCKETS}
    by_liquidity = {b: sum(a["value"] for a in known if a["liquidity"] == b)
                    for b in LIQUIDITY}
    by_currency: dict[str, float] = {}
    for a in known:
        by_currency[a["currency"]] = by_currency.get(a["currency"], 0.0) \
            + a["value"]

    investable = sum(a["value"] for a in known if a["investable"])
    earmarked = sum(a["value"] for a in known
                    if a["investable"] and a["goal"] not in
                    ("retirement", "", None))
    retirement_assets = investable - earmarked

    monthly_debt = sum(l["monthly_payment"] for l in liabilities
                       if l["monthly_payment"] is not None)
    gross = profile.gross_income
    dti = (100.0 * monthly_debt * 12 / gross) if gross else None

    return {
        "assets": known + unknown,
        "unknown_count": len(unknown),
        "liabilities": liabilities,
        "total_assets": total_assets,
        "total_debt": total_debt,
        "net_worth": total_assets - total_debt,
        "investable": investable,
        "earmarked": earmarked,
        "retirement_assets": retirement_assets,
        "by_tax": by_tax,
        "by_liquidity": by_liquidity,
        "by_currency": by_currency,
        "monthly_debt_payment": monthly_debt,
        "debt_to_income_pct": dti,
        "brokerage": brokerage,
        "concentration": concentration_flags(
            (brokerage or {}).get("positions") or []),
    }
