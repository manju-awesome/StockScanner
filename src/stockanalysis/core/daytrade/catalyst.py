"""
catalyst.py — §2 catalyst engine, 0-100
=======================================
Why is it moving today? A gap without a reason is a gap that fills.

Two independent axes, multiplied
---------------------------------
Materiality (what kind of news) and freshness (how old) are not additive.
A phase-3 readout from three weeks ago is not "a strong catalyst, slightly
stale" — it is priced, and the move happening now is something else. So
the score is materiality × decay, and an old headline collapses toward
zero however dramatic it was. Additive scoring would leave stale
blockbuster news outranking today's real news, which is the failure mode
this design exists to avoid.

Materiality is calibrated for small caps specifically. A clinical readout
or a buyout can re-rate a $200M company by triple digits, so those sit at
the top. An analyst price-target change moves a large cap and barely dents
a micro cap that just gapped 60% on its own news, so it sits near the
bottom.

The §2 floor
------------
"No identifiable catalyst should receive a low score" is implemented
literally: absence of news scores `UNIDENTIFIED_SCORE`, not None. This is
deliberately different from every other engine in the package, where a
missing input is unknown rather than bad. Here the absence *is* the
finding — a stock up 40% on no news is a specific and mostly unfavourable
condition, not a measurement failure.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from stockanalysis.core.daytrade._common import f

# Ordered most-specific first: "fda approval" must win before the generic
# "approval", and "government contract" before plain "contract".
CATALYST_RULES = [
    ("FDA / clinical", 100, (
        "fda", "phase 1", "phase 2", "phase 3", "phase i", "phase ii", "phase iii",
        "clinical", "trial results", "topline", "top-line", "pdufa", "510(k)",
        "breakthrough therapy", "orphan drug", "ind ", "nda ", "bla ",
        "ema approval", "efficacy", "endpoint", "enrollment")),
    ("Acquisition", 100, (
        "acquisition", "acquire", "merger", "buyout", "takeover", "to be acquired",
        "definitive agreement", "tender offer", "strategic alternatives")),
    ("Government award", 90, (
        "government contract", "defense contract", "darpa", "department of defense",
        "federal award", "grant award", "awarded a contract", "gsa", "nasa",
        "army", "navy", "air force")),
    ("Contract / order", 80, (
        "contract", "purchase order", "order for", "awarded", "wins deal",
        "supply agreement", "letter of intent", "backlog")),
    ("Earnings", 75, (
        "earnings", "q1 ", "q2 ", "q3 ", "q4 ", "quarterly results", "beats",
        "misses", "revenue", "eps", "results for the", "fiscal")),
    ("Guidance", 70, (
        "guidance", "raises outlook", "lowers outlook", "forecast", "outlook",
        "preliminary results", "updates expectations")),
    ("Partnership", 65, (
        "partnership", "partners with", "collaboration", "joint venture",
        "strategic investment", "licensing agreement", "distribution agreement")),
    ("Product launch", 60, (
        "launch", "unveil", "introduces", "release of", "now available",
        "commercial availability", "rollout")),
    ("Short squeeze", 60, (
        "short squeeze", "short interest", "heavily shorted", "gamma squeeze")),
    ("Analyst action", 45, (
        "upgrade", "downgrade", "initiated coverage", "price target", "reiterates",
        "buy rating", "sell rating", "overweight", "underweight")),
    ("SEC filing", 40, (
        "8-k", "10-q", "10-k", "s-1", "s-3", "424b", "13d", "13g", "form 4",
        "sec filing", "registration statement", "prospectus")),
    ("Offering / dilution", 15, (
        "offering", "atm program", "at-the-market", "private placement",
        "direct offering", "warrant", "convertible note", "reverse split",
        "dilution", "shelf registration")),
    ("Sector sympathy", 35, (
        "sector", "peers", "rally in", "group move", "sympathy")),
]

OTHER_NEWS = ("Other news", 40)
NO_CATALYST = ("No identifiable catalyst", 12)

# Decay by age. A catalyst is "fresh" for the session it broke in and the
# one after; past that the move is continuation, not reaction.
FRESHNESS = ((6, 1.00), (24, 0.95), (48, 0.75), (96, 0.45), (168, 0.25))
STALE_FACTOR = 0.10

UNIDENTIFIED_SCORE = NO_CATALYST[1]

# Slack for publisher clock skew before a headline counts as future-dated.
LOOKAHEAD_TOLERANCE_H = 0.5


# Corporate suffixes stripped before matching a company name in a headline.
# "Bloom Energy Corporation" must match a headline that says "Bloom Energy".
_NAME_NOISE = (
    "corporation", "corp", "incorporated", "inc", "company", "co",
    "limited", "ltd", "plc", "holdings", "holding", "group", "technologies",
    "technology", "international", "industries", "systems", "solutions",
    "the", "&", "sa", "nv", "ag", "class", "common", "stock",
)


def _name_tokens(company: str | None) -> list[str]:
    """The distinctive words of a company name, longest first."""
    if not company:
        return []
    words = re.findall(r"[A-Za-z][A-Za-z0-9'’-]+", company)
    keep = [w for w in words if w.lower().strip(".") not in _NAME_NOISE]
    return keep


def attributable(item: dict, ticker: str | None, company: str | None) -> bool:
    """Is this headline actually about the company we asked about?

    yfinance's `.news` for a symbol is a mixed feed: for BE it returned one
    Bloom Energy story followed by GE Vernova, OR Royalties, Realty Income
    and SoFi. Nothing in the payload attributes an item to a ticker — the
    only `finance` key is a premium-content flag — so attribution has to
    come from the text.

    That mattered because the engine scores the single best headline by
    materiality × freshness, and "OR Royalties Q2 Earnings Call Highlights"
    (Earnings, 75) beat Bloom Energy's own product story (Other news, 40).
    A different company's earnings became BE's catalyst, and catalyst is
    15% of confluence and one of §12's confirmations.

    Matched against the title and summary: the ticker as an upper-case
    standalone token — lower-cased so the English word "be" cannot match —
    or any distinctive word of the company name.
    """
    if not ticker and not company:
        return True
    text = " ".join(str(item.get(k) or "") for k in ("title", "summary"))
    if ticker and re.search(rf"\b{re.escape(ticker.upper())}\b", text):
        return True
    tokens = _name_tokens(company)
    if not tokens:
        return False
    # A single distinctive token is enough ("Bloom"), but a one-word name
    # that is also an ordinary word would over-match, so short generic
    # tokens must appear alongside a second one.
    lowered = text.lower()
    hits = [t for t in tokens if re.search(rf"\b{re.escape(t.lower())}\b", lowered)]
    if not hits:
        return False
    return len(hits) >= 2 or max(len(t) for t in hits) >= 5


def _age_hours(published, now: datetime) -> float | None:
    """Hours since publication. Accepts epoch seconds or ISO-8601, which is
    what the two yfinance news shapes respectively return."""
    if published is None:
        return None
    try:
        if isinstance(published, (int, float)):
            ts = datetime.fromtimestamp(float(published), tz=timezone.utc)
        else:
            text = str(published).replace("Z", "+00:00")
            ts = datetime.fromisoformat(text)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None
    return (now - ts).total_seconds() / 3600.0


def classify(title: str) -> tuple[str, int]:
    """Headline → (catalyst type, materiality 0-100)."""
    text = (title or "").lower()
    for label, weight, keywords in CATALYST_RULES:
        if any(k in text for k in keywords):
            return label, weight
    return OTHER_NEWS


def _decay(age_h: float | None) -> float:
    if age_h is None:
        return 0.5          # undated: real news, unknown timing
    if age_h < 0:
        age_h = 0.0
    for bound, factor in FRESHNESS:
        if age_h <= bound:
            return factor
    return STALE_FACTOR


def compute(news: list[dict], now: datetime | None = None,
            ticker: str | None = None, company: str | None = None) -> dict:
    """§2 catalyst score, 0-100.

    Scores the single best headline rather than summing them. Ten rewrites
    of one press release are one catalyst, and additive scoring would rank
    a widely-syndicated minor story above a exclusive material one.
    """
    now = now or datetime.now(timezone.utc)

    # Headlines published after the reference instant are dropped, not
    # decayed. In an as-of replay `now` is the session's last visible bar,
    # and a story filed at 16:40 must not inform a 10:15 scan — it printed
    # as "Earnings (-37h ago)", took full freshness credit through a
    # negative age, and was pure look-ahead. Live, `now` is the wall clock
    # and this removes nothing.
    news = [n for n in news
            if (_age_hours(n.get("published"), now) or 0) >= -LOOKAHEAD_TOLERANCE_H]

    # Then drop anything not about this company. If nothing survives, the
    # move is unexplained by company news — which is a finding, not a gap.
    returned = len(news)
    if ticker or company:
        news = [n for n in news if attributable(n, ticker, company)]
    unattributed = returned - len(news)

    if not news:
        detail = ("no headlines found — move is unexplained" if not returned
                  else f"{returned} headline(s) returned, none about this "
                       f"company — move is unexplained")
        return {"score": float(UNIDENTIFIED_SCORE), "type": NO_CATALYST[0],
                "headline": None, "age_hours": None, "fresh": False,
                "detail": detail, "dilution_headline": False,
                "candidates": [], "unattributed": unattributed}

    scored = []
    for item in news:
        label, weight = classify(item.get("title", ""))
        age = _age_hours(item.get("published"), now)
        scored.append({
            "type": label, "materiality": weight, "age_hours": age,
            "score": weight * _decay(age),
            "headline": item.get("title"), "publisher": item.get("publisher"),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]

    # An offering headline is surfaced separately even when it is not the
    # top-scoring item. §1 wants dilution penalised, and this is the one
    # dilution signal that is actually observable without EDGAR — missing
    # it because a louder headline outranked it would waste the one check
    # available.
    dilution = any(s["type"] == "Offering / dilution" and (s["age_hours"] or 999) < 168
                   for s in scored)

    fresh = best["age_hours"] is not None and best["age_hours"] <= 24
    detail = f"{best['type']}"
    if best["age_hours"] is not None:
        detail += f", {best['age_hours']:.0f}h old"
    else:
        detail += ", undated"

    return {
        "score": round(best["score"], 1),
        "type": best["type"],
        "materiality": best["materiality"],
        "headline": best["headline"],
        "publisher": best.get("publisher"),
        "age_hours": best["age_hours"],
        "fresh": fresh,
        "detail": detail,
        "dilution_headline": dilution,
        "candidates": scored[:5],
        "unattributed": unattributed,
    }
