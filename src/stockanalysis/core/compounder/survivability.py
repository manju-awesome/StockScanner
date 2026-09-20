"""
survivability.py — Step 9. Can it survive long enough to be right?
==================================================================
The brief is explicit: "Do NOT require mature-company profitability. Reward
companies that can survive long enough for the thesis to play out." That is
a different question from profitability and it deserves a different answer
shape, so this module returns a CLASSIFICATION rather than a pass/fail:

    SELF-FUNDED         free cash flow positive. The thesis has no clock.
    NEAR SELF-FUNDED    burning, but under 15% of its cash a year — a
                        crossing is reachable from here without a raise.
    CAPITAL DEPENDENT   burning enough that a raise is part of the plan.
                        Not a rejection: this is the normal state of a
                        company building ahead of a market, and it is why
                        the classification exists instead of a filter.
    HIGH DILUTION RISK  under ~2 years of runway, or already diluting fast.
                        The thesis now depends on the equity window being
                        open when it needs to be.
    DISTRESSED          under a year of runway with a widening burn, or
                        debt beyond what the cash and cash flow support.

Runway is the number that matters
---------------------------------
    RUNWAY = (cash − debt due) ÷ annual burn

and it is expressed in YEARS, because that is the unit the thesis is in. A
10-year compounding story funded for 14 months is not a 10-year story; it is
a financing event with a narrative attached, and no amount of TAM fixes it.
That is the one thing in this whole engine that genuinely can end a thesis
regardless of how good everything else looks — not because the company is
bad, but because it may not get to find out.

Dilution is measured, not assumed
---------------------------------
Share count comes off four years of balance sheets, so the dilution rate is
a measurement rather than an inference from cash burn. A company that funded
itself with debt and one that funded itself by issuing 40% more shares can
have identical cash flows, and the second has already transferred most of
the upside away from the shareholder who is reading this.
"""

from __future__ import annotations

from stockanalysis.core.longterm._common import blend, inverse, scale

CLASSES = ("SELF-FUNDED", "NEAR SELF-FUNDED", "CAPITAL DEPENDENT",
           "HIGH DILUTION RISK", "DISTRESSED")

CLASS_ICONS = {"SELF-FUNDED": "🟢", "NEAR SELF-FUNDED": "🟡",
               "CAPITAL DEPENDENT": "🟠", "HIGH DILUTION RISK": "🔴",
               "DISTRESSED": "💀"}

# Burn as a share of cash on hand, per year. Under this, a company is close
# enough to self-funding that the next margin point closes the gap.
NEAR_SELF_BURN_PCT = 15.0

# Runway thresholds, in years.
RUNWAY_DISTRESS = 1.0
RUNWAY_DILUTION = 2.0
RUNWAY_COMFORT = 4.0

# Annualised share-count growth. Above ~8%/yr the shareholder's claim is
# shrinking faster than most theses compound.
DILUTION_OK, DILUTION_BAD = 2.0, 18.0


def _annualised_share_growth(shares) -> float | None:
    """Compound annual growth in shares outstanding, in percent."""
    clean = [v for v in shares if v is not None and v > 0]
    if len(clean) < 2:
        return None
    newest, oldest = clean[0], clean[-1]
    years = len(clean) - 1
    return round(((newest / oldest) ** (1.0 / years) - 1.0) * 100.0, 1)


def compute(data: dict, lev: dict) -> dict:
    cash = data.get("cash")
    debt = data.get("debt")
    fcf = lev.get("fcf_now")
    fcf_state = lev.get("fcf_state")
    shares = data.get("shares_annual") or []

    burn = None if fcf is None else (-fcf if fcf < 0 else 0.0)
    net_cash = None if cash is None else cash - (debt or 0)

    # Runway is computed off CASH, not net cash: debt has a maturity
    # schedule this data does not carry, and treating all of it as due
    # tomorrow would call every leveraged-but-solvent company distressed.
    # Net cash is reported separately as the leverage reading.
    runway = None
    if burn is not None and cash is not None:
        runway = (float("inf") if burn <= 0
                  else round(cash / burn, 1))

    burn_pct = (None if burn is None or not cash or cash <= 0
                else round(burn / cash * 100, 1))

    dilution = _annualised_share_growth(shares)
    debt_to_equity = None
    equity = data.get("equity")
    if debt is not None and equity and equity > 0:
        debt_to_equity = round(debt / equity, 2)

    classification, why = _classify(fcf, burn_pct, runway, dilution,
                                    net_cash, debt_to_equity, fcf_state)

    # ── Score ────────────────────────────────────────────────────────────
    # Only 5% of the composite, per the brief's weights — survivability is
    # a risk to classify, not a ranking axis. What it must never do is
    # quietly reject, which is why the low weight and the loud label.
    runway_score = None
    if runway is not None:
        runway_score = (100.0 if runway == float("inf")
                        else scale(runway, 0.5, RUNWAY_COMFORT))
    scored = blend([
        ("Cash runway", 40, runway_score,
         _runway_text(runway)),
        ("Dilution rate", 30,
         None if dilution is None else inverse(dilution, DILUTION_OK,
                                               DILUTION_BAD),
         f"{dilution:+.1f}%/yr share count" if dilution is not None
         else "no share history"),
        ("Balance sheet", 30,
         None if debt_to_equity is None
         else inverse(debt_to_equity, 0.2, 2.5),
         f"debt/equity {debt_to_equity:.2f}" if debt_to_equity is not None
         else "balance sheet incomplete"),
    ])

    return {
        "score": scored["score"],
        "coverage": scored["coverage"],
        "components": scored["components"],
        "classification": classification,
        "icon": CLASS_ICONS.get(classification, "⚪"),
        "why": why,
        "cash": cash, "debt": debt, "net_cash": net_cash,
        "debt_to_equity": debt_to_equity,
        "annual_burn": burn,
        "burn_pct_of_cash": burn_pct,
        "runway_years": runway,
        # The rendered form, computed here rather than at display time.
        # `runway_years` is legitimately float('inf') for a company with no
        # burn, and `Infinity` is not valid JSON — the snapshot writes it as
        # None, so a page reading the raw number after a round-trip would
        # show a self-funded company as "runway not measurable", which is
        # the opposite of what was measured.
        "runway_label": _runway_text(runway),
        "dilution_pct_yr": dilution,
        "share_history": shares,
        # Named separately from the classification because the brief asks
        # for a dilution RISK read alongside the funding one, and a
        # self-funded company can still be diluting heavily through
        # stock compensation.
        "dilution_risk": _dilution_risk(dilution),
    }


def _classify(fcf, burn_pct, runway, dilution, net_cash, dte, fcf_state):
    if fcf is None and runway is None:
        return "CAPITAL DEPENDENT", ("cash flow could not be read — "
                                     "classified conservatively, not "
                                     "measured")

    distressed = (
        (runway is not None and runway != float("inf")
         and runway < RUNWAY_DISTRESS)
        or (dte is not None and dte > 4.0 and (fcf or 0) < 0))
    if distressed:
        return "DISTRESSED", _distress_why(runway, dte)

    if fcf is not None and fcf > 0:
        if dilution is not None and dilution > 10:
            return "SELF-FUNDED", (
                f"free cash flow positive, but the share count is still "
                f"growing {dilution:.1f}%/yr — funded, and diluting anyway")
        return "SELF-FUNDED", ("generates its own cash — the thesis has no "
                               "financing clock on it")

    if runway is not None and runway != float("inf") and runway < RUNWAY_DILUTION:
        return "HIGH DILUTION RISK", (
            f"{runway:.1f} years of runway at the current burn — a raise is "
            f"likely inside the next four quarters, and the terms depend on "
            f"a window nobody controls")

    if dilution is not None and dilution > DILUTION_BAD:
        return "HIGH DILUTION RISK", (
            f"share count compounding {dilution:.1f}%/yr — the business can "
            f"succeed while the per-share claim shrinks")

    if burn_pct is not None and burn_pct <= NEAR_SELF_BURN_PCT:
        return "NEAR SELF-FUNDED", (
            f"burning {burn_pct:.0f}% of its cash a year with "
            f"{_runway_text(runway)} — reachable without a raise if the "
            f"margin trend holds")

    if fcf_state in ("IMPROVING", "INFLECTED"):
        return "CAPITAL DEPENDENT", (
            f"still burning, but the burn is shrinking — {_runway_text(runway)}")

    return "CAPITAL DEPENDENT", (
        f"funding the build from the balance sheet — {_runway_text(runway)}. "
        f"Normal for this stage; it is a clock, not a flaw")


def _distress_why(runway, dte) -> str:
    bits = []
    if runway is not None and runway != float("inf"):
        bits.append(f"{runway:.1f} years of cash at the current burn")
    if dte is not None and dte > 4.0:
        bits.append(f"debt/equity {dte:.1f} against negative cash flow")
    return "; ".join(bits) or "balance sheet cannot support the burn"


def _dilution_risk(dilution) -> dict:
    if dilution is None:
        return {"level": "UNMEASURED", "icon": "⚪",
                "detail": "no share-count history"}
    if dilution <= 2:
        return {"level": "LOW", "icon": "🟢",
                "detail": f"share count {dilution:+.1f}%/yr"}
    if dilution <= 8:
        return {"level": "MODERATE", "icon": "🟡",
                "detail": f"share count {dilution:+.1f}%/yr — ordinary for a "
                          f"growth company funding with equity comp"}
    if dilution <= 18:
        return {"level": "HIGH", "icon": "🟠",
                "detail": f"share count {dilution:+.1f}%/yr — compounding "
                          f"against the shareholder"}
    return {"level": "SEVERE", "icon": "🔴",
            "detail": f"share count {dilution:+.1f}%/yr — a doubling every "
                      f"{72 / max(dilution, 1):.0f} years of holding period"}


def _runway_text(runway) -> str:
    if runway is None:
        return "runway not measurable"
    if runway == float("inf"):
        return "no burn — funded by operations"
    return f"{runway:.1f} years of runway"
