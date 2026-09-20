"""
themes.py — the secular theme library, and why TAM is a CLAIM not a metric
==========================================================================
Steps 1 and 2 of the framework ask for a secular theme and a TAM curve.
Neither is derivable from a filing. Revenue, margins, R&D and share counts
come out of the statements; "the advanced packaging market is $45B growing
to $95B" comes out of a research report, and no amount of yfinance calls
will produce it.

The tempting shortcut is to have the model estimate a TAM per company at
scan time. That is the one thing this file exists to prevent. A generated
TAM is unfalsifiable, silently different on every run, and — because TAM
carries 20% of the composite and market-share opportunity another 10% —
would let a fabricated number drive 30% of the ranking. The engine would
then be scoring the estimate rather than the company.

So TAM lives here instead: written down once, per THEME rather than per
company, with a source basis, an as-of date and a confidence level, and
versioned in git where a change to it shows up in a diff. When a figure is
stale or weak it is marked, and `confidence` propagates all the way to the
score — see `tam_confidence_factor()`. A LOW-confidence theme cannot earn a
top TAM score no matter how large the number is, which is the honest
treatment of "this market might be $12B in 2031, or it might not exist".

The mapping is also the universe
--------------------------------
`THEME_MEMBERS` maps ticker -> theme, and that mapping IS the scan universe.
There is no US-equity screener behind this project (yfinance has no screen
endpoint), so a universe has to be a list somebody wrote down. Making that
list the theme mapping means membership is never accidental: a ticker is in
the universe *because* a structural trend was identified for it, which is
exactly what Step 1 asks and the reverse of ranking whatever happened to be
in an index.

Market-cap band ($300M-$20B) is NOT enforced here. Caps move, this file
would go stale, and a name that compounded out of the band should be
reported as a graduate rather than quietly dropped. `engine.py` reads the
live cap and classifies.

Maintaining this file
---------------------
TAM figures are consensus mid-points across published industry trackers and
sell-side market models, rounded hard — they are order-of-magnitude inputs
to a score, not forecasts, and false precision in them would be worse than
the rounding. Refresh the `as_of` when you revisit one. If a theme's TAM
cannot be sourced to something you could show a reader, it belongs at LOW
confidence or not at all.
"""

from __future__ import annotations

# Confidence in the TAM curve itself. This is the guard against a
# speculative market's huge CAGR outranking a real one's measured growth.
#
#   HIGH    multiple independent trackers, a decade of shipped revenue to
#           anchor the base, disagreement between sources under ~25%
#   MEDIUM  the market is real and growing but the boundary is contested —
#           "AI infrastructure" means different things to different
#           analysts, and the 5-year number is a projection not a count
#   LOW     the market is largely pre-revenue. The TAM is a scenario. Any
#           score built on it must be capped, and it is.
CONFIDENCE_FACTOR = {"HIGH": 1.00, "MEDIUM": 0.88, "LOW": 0.65}

# A LOW-confidence theme cannot score above this on the secular leg however
# large or fast the claimed market is. Quantum computing is the case:
# arithmetically its ~40% TAM CAGR beats every other theme in the library,
# and it is a projection of a market that has not yet proven it will exist
# at scale.
#
# The ceiling is set BELOW what a solid, well-instrumented theme actually
# scores — semiconductor equipment lands near 49 — rather than at some
# comfortable-sounding round number. A ceiling above the real distribution
# is not a constraint at all, which is what the first version of this
# table got wrong: it read as a guard while permitting exactly the
# inversion it was written to stop.
CONFIDENCE_CEILING = {"HIGH": 100.0, "MEDIUM": 90.0, "LOW": 45.0}

B = 1_000_000_000.0

# ─────────────────────────────────────────────────────────────────────────────
# THE LIBRARY
# ─────────────────────────────────────────────────────────────────────────────
# tam_now / tam_5y / tam_10y  — serviceable market in USD, not the widest
#                               definition anybody publishes. Where a source
#                               quotes "total AI market" including consumer
#                               software the figure is cut back to the layer
#                               these companies actually sell into.
# adjacencies                 — count of separately-addressable markets the
#                               same technology reaches. Step 2 asks to
#                               reward "multiple adjacent markets"; this is
#                               that term, and it is a count of named
#                               markets rather than a vibe.
# durability                  — how much of the growth is contracted,
#                               regulated or physically committed (fabs,
#                               grid interconnects, defense programs) versus
#                               dependent on a spending cycle continuing.
#                               0-100. This is what separates a secular
#                               trend from a cyclical upswing, and Step 1
#                               explicitly asks for "capable of expanding
#                               for 5-10+ years" rather than "growing now".

THEMES = {
    "ai_infrastructure": {
        "label": "AI data-center compute & interconnect",
        "tam_now": 420 * B, "tam_5y": 1150 * B, "tam_10y": 2000 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 78,
        "basis": "Hyperscaler + neocloud accelerated-compute capex, "
                 "consensus mid-point; excludes consumer AI software",
        "adjacent_markets": ["enterprise on-prem AI", "sovereign AI",
                             "edge inference", "networking silicon"],
        "risk": "Concentrated in ~6 buyers. A capex digestion year cuts "
                "order rates far faster than the 10-year curve implies.",
    },
    "advanced_packaging": {
        "label": "Advanced semiconductor packaging",
        "tam_now": 48 * B, "tam_5y": 98 * B, "tam_10y": 165 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 3, "durability": 85,
        "basis": "CoWoS/SoIC/hybrid-bonding capacity plus OSAT packaging "
                 "revenue; the physical bottleneck in AI accelerator supply",
        "adjacent_markets": ["HBM assembly", "chiplet interconnect",
                             "co-packaged optics"],
        "risk": "Capacity is being added faster than demand in some tiers; "
                "packaging is where the shortage un-shortages first.",
    },
    "semicap": {
        "label": "Semiconductor manufacturing equipment",
        "tam_now": 115 * B, "tam_5y": 175 * B, "tam_10y": 250 * B,
        "confidence": "HIGH", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 80,
        "basis": "SEMI wafer-fab-equipment billings plus test and assembly; "
                 "one of the best-instrumented markets in the library",
        "adjacent_markets": ["metrology/inspection", "ion implant",
                             "test & burn-in", "subfab & abatement"],
        "risk": "Genuinely cyclical underneath the secular trend. WFE has "
                "fallen 20%+ in a year twice in the last decade.",
    },
    "photonics_optical": {
        "label": "Optical interconnect & photonics",
        "tam_now": 21 * B, "tam_5y": 52 * B, "tam_10y": 95 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 80,
        "basis": "Datacom/telecom transceivers, optical engines and silicon "
                 "photonics; driven by scale-up/scale-out bandwidth per GPU",
        "adjacent_markets": ["co-packaged optics", "DCI/coherent",
                             "optical switching", "sensing & LiDAR"],
        "risk": "Brutal price erosion per generation — unit growth of 30% "
                "has repeatedly produced revenue growth of 10%.",
    },
    "power_grid": {
        "label": "Electrical grid & transmission equipment",
        "tam_now": 290 * B, "tam_5y": 470 * B, "tam_10y": 720 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 92,
        "basis": "T&D equipment, switchgear, transformers and grid services; "
                 "underpinned by multi-year regulated utility capex plans",
        "adjacent_markets": ["data-center interconnection", "electrification",
                             "grid hardening/resilience", "HVDC"],
        "risk": "Utility capex is regulated, therefore slow to accelerate as "
                "well as slow to fall. Backlogs already price several years.",
    },
    "datacenter_infrastructure": {
        "label": "Data-center power & thermal infrastructure",
        "tam_now": 78 * B, "tam_5y": 185 * B, "tam_10y": 310 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 3, "durability": 82,
        "basis": "Power distribution, UPS, busway and liquid-cooling content "
                 "per MW, times announced MW build-out",
        "adjacent_markets": ["liquid cooling", "on-site generation",
                             "modular/prefab data centers"],
        "risk": "Content per MW is the whole thesis; if rack density stalls, "
                "the market grows with floorspace instead of with compute.",
    },
    "power_semis": {
        "label": "Power semiconductors & electrification silicon",
        "tam_now": 36 * B, "tam_5y": 64 * B, "tam_10y": 100 * B,
        "confidence": "HIGH", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 76,
        "basis": "Discrete + module power semis including SiC/GaN; long "
                 "shipping history and well-tracked unit data",
        "adjacent_markets": ["data-center power delivery", "EV traction",
                             "industrial drives", "renewables inverters"],
        "risk": "SiC/GaN capacity was built for an EV ramp that slowed; "
                "pricing has been the casualty.",
    },
    "energy_storage": {
        "label": "Grid-scale energy storage",
        "tam_now": 62 * B, "tam_5y": 155 * B, "tam_10y": 280 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 3, "durability": 74,
        "basis": "BESS system revenue plus integration and long-duration "
                 "pilots; excludes EV cells",
        "adjacent_markets": ["long-duration storage", "behind-the-meter",
                             "grid services/software"],
        "risk": "Cell cost is the product. Integrators without their own "
                "chemistry are pass-through margin businesses.",
    },
    "nuclear": {
        "label": "Nuclear — SMR, fuel cycle & services",
        "tam_now": 11 * B, "tam_5y": 38 * B, "tam_10y": 110 * B,
        "confidence": "LOW", "as_of": "2026-Q2",
        "adjacencies": 3, "durability": 88,
        "basis": "Enrichment/fuel and existing-fleet services are real "
                 "revenue today; SMR deployment is a scenario, and the "
                 "5- and 10-year figures are dominated by it",
        "adjacent_markets": ["HALEU fuel", "existing-fleet uprates",
                             "data-center PPAs"],
        "risk": "The 10-year number assumes reactors that have not been "
                "licensed, financed or built. Treat as a scenario, not a "
                "forecast — which is why the theme is capped at LOW.",
    },
    "cybersecurity": {
        "label": "Cybersecurity",
        "tam_now": 225 * B, "tam_5y": 380 * B, "tam_10y": 590 * B,
        "confidence": "HIGH", "as_of": "2026-Q2",
        "adjacencies": 5, "durability": 90,
        "basis": "Security software + services spend; among the most "
                 "consistently tracked enterprise budgets",
        "adjacent_markets": ["identity", "cloud/workload security", "SASE",
                             "data security posture", "AI model security"],
        "risk": "Consolidation into platforms is squeezing point solutions "
                "out — being in the theme is not being in the winner.",
    },
    "robotics_automation": {
        "label": "Robotics & industrial automation",
        "tam_now": 82 * B, "tam_5y": 150 * B, "tam_10y": 265 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 78,
        "basis": "Industrial + service robotics hardware, motion control and "
                 "machine vision; excludes speculative humanoid figures",
        "adjacent_markets": ["warehouse/logistics", "machine vision",
                             "surgical robotics", "agricultural autonomy"],
        "risk": "Capital-goods cyclicality. Factory automation orders track "
                "manufacturing PMI far more tightly than any secular story.",
    },
    "defense_tech": {
        "label": "Defense technology & autonomy",
        "tam_now": 68 * B, "tam_5y": 132 * B, "tam_10y": 225 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 86,
        "basis": "Software-defined defense, C4ISR, counter-UAS and munitions "
                 "modernisation inside allied procurement budgets",
        "adjacent_markets": ["counter-UAS", "space-based ISR",
                             "electronic warfare", "munitions"],
        "risk": "One customer with an appropriations cycle. Program of "
                "record or not is a binary that dwarfs execution.",
    },
    "space": {
        "label": "Space infrastructure & services",
        "tam_now": 74 * B, "tam_5y": 145 * B, "tam_10y": 260 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 72,
        "basis": "Launch, satellite manufacturing, ground systems and EO/"
                 "comms services; excludes consumer broadband subscriptions",
        "adjacent_markets": ["earth observation", "in-space logistics",
                             "ground segment", "national security space"],
        "risk": "A single vertically-integrated launch provider sets the "
                "price floor for most of the value chain.",
    },
    "digital_infrastructure": {
        "label": "Digital infrastructure & connectivity",
        "tam_now": 54 * B, "tam_5y": 98 * B, "tam_10y": 160 * B,
        "confidence": "MEDIUM", "as_of": "2026-Q2",
        "adjacencies": 3, "durability": 80,
        "basis": "Fibre, DCI, edge capacity and network software attached to "
                 "data-center build-out",
        "adjacent_markets": ["subsea/DCI", "edge compute", "network software"],
        "risk": "Capital intensity is permanent; returns depend on financing "
                "costs as much as on demand.",
    },
    "biotech_tools": {
        "label": "Life-science tools & bioprocessing",
        "tam_now": 128 * B, "tam_5y": 190 * B, "tam_10y": 285 * B,
        "confidence": "HIGH", "as_of": "2026-Q2",
        "adjacencies": 4, "durability": 84,
        "basis": "Instruments, consumables, bioprocessing and CDMO capacity; "
                 "consumables give it a recurring base most themes lack",
        "adjacent_markets": ["cell & gene manufacturing", "proteomics",
                             "spatial biology", "diagnostics"],
        "risk": "Instrument placement is capex for customers and stops "
                "abruptly when biotech funding tightens.",
    },
    "water_infrastructure": {
        "label": "Water infrastructure & treatment",
        "tam_now": 95 * B, "tam_5y": 138 * B, "tam_10y": 205 * B,
        "confidence": "HIGH", "as_of": "2026-Q2",
        "adjacencies": 3, "durability": 93,
        "basis": "Municipal + industrial treatment, metering and pipe "
                 "replacement; driven by regulation and asset age",
        "adjacent_markets": ["PFAS remediation", "industrial reuse",
                             "smart metering"],
        "risk": "Municipal budgets grow slowly and predictably. This is a "
                "durable market, not a fast one.",
    },
    "quantum": {
        "label": "Quantum computing",
        "tam_now": 2.4 * B, "tam_5y": 13 * B, "tam_10y": 55 * B,
        "confidence": "LOW", "as_of": "2026-Q2",
        "adjacencies": 2, "durability": 60,
        "basis": "Systems, cloud access and government programs. Today's "
                 "revenue is overwhelmingly research grants, not production",
        "adjacent_markets": ["post-quantum cryptography", "quantum sensing"],
        "risk": "No commercial quantum advantage has been demonstrated at "
                "production scale. The TAM curve is a hypothesis.",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE — ticker -> theme
# ─────────────────────────────────────────────────────────────────────────────
# Small- and mid-cap names with a defensible claim on one of the themes
# above. Index membership is deliberately not a criterion (several of these
# are in no major index, which is the point) and market cap is checked live
# rather than encoded here.
#
# One theme per ticker, chosen as the one whose TAM the company actually
# sells into. Multi-theme companies exist, but assigning two themes would
# double-count the largest TAM and quietly reward conglomerates.
#
# Maintenance note — a dead ticker is silent
# ------------------------------------------
# A ticker that has been acquired or delisted returns no data at all, and
# the engine correctly scores it as unmeasurable and reports it. But it
# shows up as a coverage gap rather than as an error, so it is easy to
# carry a list of ghosts for months without noticing the theme has thinned.
# `python -m stockanalysis.core.compounder.scan` prints unscorable names at
# the end of every run for exactly this reason; when one appears, check
# whether it was acquired and either remove it here or accept the gap.
#
# Removed in the 2026-08 pass, all acquired or delisted: PSTG, CYBR, SCWX,
# THR, CSWI, EMKR, INFN, CDMO, OMIC, AQUA — plus AAPB, which was never a
# common share.

THEME_MEMBERS = {
    # AI infrastructure & interconnect
    "ALAB": "ai_infrastructure", "CRDO": "ai_infrastructure",
    "MRVL": "ai_infrastructure", "SMCI": "ai_infrastructure",
    "SMTC": "ai_infrastructure", "NTNX": "ai_infrastructure",
    "APLD": "ai_infrastructure", "IREN": "ai_infrastructure",
    "CRWV": "ai_infrastructure", "NBIS": "ai_infrastructure",
    "GDS": "ai_infrastructure", "AI": "ai_infrastructure",
    "SANM": "ai_infrastructure", "PLXS": "ai_infrastructure",

    # Advanced packaging
    "AEIS": "advanced_packaging", "ONTO": "advanced_packaging",
    "CAMT": "advanced_packaging", "KLIC": "advanced_packaging",
    "COHU": "advanced_packaging", "AMKR": "advanced_packaging",
    "FORM": "advanced_packaging",

    # Semiconductor equipment
    "ACLS": "semicap", "UCTT": "semicap", "ICHR": "semicap",
    "AEHR": "semicap", "VECO": "semicap", "NVMI": "semicap",
    "PLAB": "semicap", "AXTI": "semicap", "ACMR": "semicap",

    # Photonics & optical
    "LITE": "photonics_optical", "AAOI": "photonics_optical",
    "POET": "photonics_optical", "FN": "photonics_optical",
    "IPGP": "photonics_optical", "LWLG": "photonics_optical",
    "CIEN": "photonics_optical", "MTSI": "photonics_optical",

    # Grid & transmission
    "POWL": "power_grid", "AZZ": "power_grid", "NVT": "power_grid",
    "ATKR": "power_grid", "PRIM": "power_grid", "MYRG": "power_grid",
    "IESC": "power_grid", "VMI": "power_grid", "AMSC": "power_grid",

    # Data-center power & cooling
    "VRT": "datacenter_infrastructure", "MOD": "datacenter_infrastructure",
    "SPXC": "datacenter_infrastructure", "BE": "datacenter_infrastructure",
    "GNRC": "datacenter_infrastructure", "AAON": "datacenter_infrastructure",
    "FIX": "datacenter_infrastructure",

    # Power semis & electrification
    "NVTS": "power_semis", "AOSL": "power_semis", "VICR": "power_semis",
    "ALGM": "power_semis", "WOLF": "power_semis", "SITM": "power_semis",
    "POWI": "power_semis", "DIOD": "power_semis",

    # Energy storage
    "FLNC": "energy_storage", "ESS": "energy_storage", "EOSE": "energy_storage",
    "STEM": "energy_storage", "ENS": "energy_storage",

    # Nuclear
    "LEU": "nuclear", "SMR": "nuclear", "OKLO": "nuclear",
    "BWXT": "nuclear", "LTBR": "nuclear", "NNE": "nuclear",

    # Cybersecurity
    "TENB": "cybersecurity", "RPD": "cybersecurity", "VRNS": "cybersecurity",
    "OSPN": "cybersecurity", "RBRK": "cybersecurity",
    "QLYS": "cybersecurity", "S": "cybersecurity",

    # Robotics & automation
    "SYM": "robotics_automation", "KRNT": "robotics_automation",
    "NNDM": "robotics_automation", "OII": "robotics_automation",
    "THRM": "robotics_automation", "ATS": "robotics_automation",
    "CGNX": "robotics_automation", "NDSN": "robotics_automation",
    "SERV": "robotics_automation", "RR": "robotics_automation",
    "NVEC": "robotics_automation",

    # Defense technology
    "KTOS": "defense_tech", "AVAV": "defense_tech", "MRCY": "defense_tech",
    "DRS": "defense_tech", "VSEC": "defense_tech", "CACI": "defense_tech",
    "PSN": "defense_tech", "AIRO": "defense_tech", "ONDS": "defense_tech",
    "UMAC": "defense_tech", "ESE": "defense_tech",

    # Space
    "RKLB": "space", "PL": "space", "BKSY": "space", "SPCE": "space",
    "ASTS": "space", "MNTS": "space", "SATS": "space", "IRDM": "space",
    "RDW": "space", "LUNR": "space",

    # Digital infrastructure
    "DGII": "digital_infrastructure", "CALX": "digital_infrastructure",
    "HLIT": "digital_infrastructure", "EXTR": "digital_infrastructure",
    "CCOI": "digital_infrastructure", "ATEX": "digital_infrastructure",

    # Life-science tools
    "TXG": "biotech_tools", "PACB": "biotech_tools", "NAUT": "biotech_tools",
    "QSI": "biotech_tools", "TWST": "biotech_tools", "RGEN": "biotech_tools",
    "MASS": "biotech_tools", "CRL": "biotech_tools", "AZTA": "biotech_tools",

    # Water
    "ERII": "water_infrastructure", "CDZI": "water_infrastructure",
    "MWA": "water_infrastructure", "BMI": "water_infrastructure",
    "FELE": "water_infrastructure", "WTS": "water_infrastructure",
    "ZWS": "water_infrastructure", "CECO": "water_infrastructure",
    "TTEK": "water_infrastructure",

    # Quantum
    "IONQ": "quantum", "RGTI": "quantum", "QBTS": "quantum",
    "QUBT": "quantum", "ARQQ": "quantum",
}


# ─────────────────────────────────────────────────────────────────────────────
# READS
# ─────────────────────────────────────────────────────────────────────────────

def universe() -> list[str]:
    """Every ticker with a theme assignment, sorted."""
    return sorted(THEME_MEMBERS)


def theme_for(ticker: str) -> dict | None:
    """The theme record for a ticker, with its key and TAM CAGRs attached.

    None for an unmapped ticker — which the engine reports as "no secular
    theme identified" rather than scoring zero. A company outside this
    library is one nobody has classified, not one in a bad market.
    """
    key = THEME_MEMBERS.get((ticker or "").strip().upper())
    if not key:
        return None
    return theme(key)


def theme(key: str) -> dict | None:
    rec = THEMES.get(key)
    if not rec:
        return None
    out = dict(rec)
    out["key"] = key
    out["tam_cagr_5y"] = _cagr(rec["tam_now"], rec.get("tam_5y"), 5)
    out["tam_cagr_10y"] = _cagr(rec["tam_now"], rec.get("tam_10y"), 10)
    return out


def _cagr(now, later, years) -> float | None:
    if not now or not later or now <= 0 or later <= 0:
        return None
    return round(((later / now) ** (1.0 / years) - 1.0) * 100.0, 1)


def tam_confidence_factor(theme_rec: dict | None) -> float:
    return CONFIDENCE_FACTOR.get((theme_rec or {}).get("confidence"), 0.65)


def members(theme_key: str) -> list[str]:
    """Every ticker in a theme — the peer set competitive position is
    measured against."""
    return sorted(t for t, k in THEME_MEMBERS.items() if k == theme_key)


def coverage() -> dict:
    """Names per theme. Used by the page to say what the library actually
    covers, so a thin theme is visible rather than implied."""
    out: dict[str, int] = {}
    for key in THEME_MEMBERS.values():
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
