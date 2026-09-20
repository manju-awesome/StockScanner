"""
profile.py — what the company actually does, and where it stands
================================================================
Context for the Long-Term reasoning panel: a two-sentence description of the
business, its sector and industry, and its competitive standing inside that
industry.

None of this is a verdict. The engine's gates never read it, and nothing here
moves LQuality, the valuation band or the action — which is exactly why it is
attached in the webapp layer next to position sizing rather than computed
inside `engine.evaluate()`. It answers the question the score cannot: the
panel can tell you a name is Elite quality at Stage 2 with 3.1R of headroom
and still leave you unsure what the company sells.

Two sources, both already in the project:

  description   the scan row's BusinessSummary (yfinance longBusinessSummary),
                trimmed to its opening sentences
  position      core.market_position — market-cap rank inside the ticker's
                industry, with data/market_structure.json able to override it

On "monopoly" and "duopoly"
---------------------------
The computed layer ranks on MARKET CAP across names in this library. That is a
proxy for size, not for market share, and the two come apart precisely on the
names where structure is interesting — ASML is the sole supplier of EUV
lithography and still ranks below Applied Materials by market cap. So the
computed reading is always rendered as what it is ("#1 of 11 tracked peers by
market cap"), and a structural word like "EUV monopoly" can only come from the
curated overlay, which ships empty on purpose. An unverified market-share
claim in a trading tool is worse than no claim.
"""

from __future__ import annotations

import re

from stockanalysis.core import market_position

# Two sentences is the whole budget. The reasoning panel already carries nine
# cards; a full yfinance summary runs 1,200+ characters and would bury them.
MAX_SENTENCES = 2
MAX_CHARS = 320

# A sentence break is ". " followed by a capital — which keeps "N.V. provides"
# and "Ltd. designs" intact, since those continue in lower case.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")

# Below this, a "sentence" is an abbreviation that happened to be followed by
# a capitalised word: "U.S. Bancorp provides…" splits after "U.S." otherwise,
# and the description would read "U.S."
_MIN_SENTENCE = 40


def summarize(text: str | None, max_sentences: int = MAX_SENTENCES,
              max_chars: int = MAX_CHARS) -> str | None:
    """The opening sentences of a business summary, or None.

    Trims on a sentence boundary rather than a character count so the result
    never ends mid-clause. A single sentence longer than the budget is cut at
    the last word and given an ellipsis — better a clipped sentence than a
    card that pushes the reasoning off the screen.
    """
    text = " ".join(str(text or "").split())
    if not text:
        return None

    out = ""
    for piece in _SENTENCE_END.split(text):
        candidate = f"{out} {piece}".strip() if out else piece
        # Keep absorbing while the accumulated text is too short to be a real
        # sentence (the abbreviation case) or still inside the budget.
        if out and len(out) >= _MIN_SENTENCE and (
                out.count(".") >= max_sentences or len(candidate) > max_chars):
            break
        out = candidate

    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out or None


def _industry_of(raw: dict) -> str | None:
    industry = (raw or {}).get("Industry")
    return industry.strip() if isinstance(industry, str) and industry.strip() \
        else None


def build(raw: dict, position: dict | None = None) -> dict:
    """One scan row (+ its market_position record) -> the profile dict."""
    position = position or {}
    return {
        "description": summarize((raw or {}).get("BusinessSummary")),
        "sector": (raw or {}).get("Sector") or None,
        "industry": _industry_of(raw),
        # Copied rather than nested so the view reads one flat dict, and
        # spelled out so it is obvious which keys this panel depends on.
        "peer_group": position.get("peer_group"),
        "peer_group_is_sector": position.get("peer_group_is_sector", False),
        "peer_rank": position.get("peer_rank"),
        "peer_count": position.get("peer_count") or 0,
        "peer_share_pct": position.get("peer_share_pct"),
        "position_label": position.get("position_label"),
        "position_tier": position.get("position_tier"),
        "structure": position.get("structure"),
        "structure_note": position.get("structure_note"),
    }


def attach(results: list[dict], raw_by_ticker: dict, entries=None) -> list[dict]:
    """Attach `result["profile"]` to each evaluated row, in place.

    `entries` is the full research index. It matters that this is the FULL
    library even when `results` is a filtered subset: a rank is computed
    against whoever else is in the peer group, so ranking a five-name
    watchlist against itself would report "#1 of 3 in Technology" for a name
    that is 40th in the library. Falls back to ranking within `results` only
    when no index is supplied, and says so by way of the peer count.
    """
    if entries is None:
        entries = [{"ticker": t,
                    "sector": (row or {}).get("Sector"),
                    "market_cap": (row or {}).get("MarketCap"),
                    "raw": row}
                   for t, row in (raw_by_ticker or {}).items()]
    try:
        positions = market_position.compute_peer_positions(entries)
    except Exception as e:                      # never fail the page over context
        print(f"[LongTerm] peer positions unavailable ({e})")
        positions = {}

    for result in results:
        ticker = result.get("ticker")
        result["profile"] = build(raw_by_ticker.get(ticker) or {},
                                  positions.get(ticker))
    return results
