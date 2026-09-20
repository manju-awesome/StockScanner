"""
Ticker to CUSIP, which the SEC does not publish.

13F identifies securities by CUSIP. Every public SEC mapping goes ticker
to CIK, which identifies the COMPANY, not the security — and a company
with two share classes has one CIK and two CUSIPs. There is no free
authoritative ticker-to-CUSIP file; CUSIP is licensed.

So it gets derived, and the derivation leans on an asymmetry visible in
the raw data:

    7601 NVIDIA CORPORATION            | COM           | 67066G104
     395 NVIDIA CORP                   | COM           | 67066G104
     377 NVIDIA Corp                   | Option        | 67066G104
     374 NVIDIA CORPORATION COM        | Stock         | 67066G104
     220 NVIDIA CORP                   | Common Stock  | 67066G104

NAMEOFISSUER and TITLEOFCLASS are free text and filers spell them however
they like. CUSIP is the one field they agree on. That makes name-to-CUSIP
a many-to-one collapse rather than a fuzzy match: normalise the names,
count the CUSIPs behind each, and the right answer wins by volume.

WHERE THIS BREAKS, AND WHY IT SAYS SO
-------------------------------------
Dual-class issuers. GOOGL and GOOG are one company, one CIK, one name in
every SEC file, and two different CUSIPs. Nothing in the ticker-to-name
direction can separate them, so guessing which class is "the" CUSIP would
silently attribute one class's institutional book to the other.

This module refuses that guess. A ticker whose name resolves to more than
one plausible CUSIP is returned AMBIGUOUS with its candidates and their
row counts, and the caller is expected to surface that rather than pick.
`data/institutional/cusip_overrides.json` is the place to pin one, and
listing candidates makes filling it in a ten-second job.

The same applies in reverse: `Resolution.confidence` is a real signal, not
decoration. A name matched by 8,000 rows is settled; one matched by 3 is a
coincidence waiting to happen.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .datasets import user_agent
from .filings import INFOTABLE

TICKER_URL = "https://www.sec.gov/files/company_tickers.json"

DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "institutional"
OVERRIDES = DATA_DIR / "cusip_overrides.json"
FUND_NAMES = DATA_DIR / "fund_names.json"

# Words that carry no identity: legal form, share class, instrument type.
# Stripped from both sides so "NVIDIA CORPORATION COM" and "NVIDIA Corp"
# collapse together.
_NOISE = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ltd", "limited", "plc", "lp", "llc", "llp", "nv", "sa", "ag", "se", "spa",
    "the", "com", "common", "stock", "shares", "share", "ordinary", "ord",
    "class", "cl", "a", "b", "c", "adr", "ads", "sponsored", "spons",
    "holdings", "holding", "hldgs", "hldg", "group", "grp", "trust", "reit",
    "new", "del", "de", "usd", "npv", "unit", "units", "series",
}

# Fund-wrapper words, stripped ONLY for the combined issuer+class key.
#
# They cannot go in _NOISE. An asset manager's own stock shares a name with
# the funds it runs: strip "FUND"/"ETF"/"TRUST" globally and "BLACKROCK
# INC" collapses into the same bucket as "BLACKROCK ETF TRUST" and
# "BLACKROCK MUNIYIELD FUND", dominance falls under the threshold, and BLK
# — along with GS, TROW, VTV and VXUS — stops resolving. Measured: adding
# these globally recovered 4 ETFs and lost 5 operating companies.
_FUND_NOISE = _NOISE | {"tr", "etf", "fund", "funds", "index", "idx"}

_PUNCT = re.compile(r"[^A-Za-z0-9 ]+")
_SPACES = re.compile(r"\s+")

# EDGAR appends the registrant's state of incorporation to company titles:
# "AMERICAN TOWER CORP /MA/", "PULTEGROUP INC/MI/", "WELLS FARGO & COMPANY/MN".
# No 13F filer writes it, so it is pure mismatch fuel and gets cut before
# anything else. Two to four letters, slash-delimited, always at the end.
# Spacing around the slashes is not consistent in EDGAR titles:
# "PULTEGROUP INC/MI/" sits next to "VERTEX PHARMACEUTICALS INC / MA".
_REGISTRANT = re.compile(r"/\s*[A-Za-z]{2,4}\s*/?\s*$")

# Bumped whenever normalise() changes shape. A cached index built under the
# old rules keys on strings the new rules will never produce, so every
# lookup would miss and every ticker would come back "unknown" — a silent,
# total failure that looks exactly like a bad quarter of data.
INDEX_VERSION = 4

# Below this share of rows, the winning CUSIP is not a winner.
MIN_DOMINANCE = 0.60
# Below this many rows, no CUSIP is trustworthy however dominant.
MIN_ROWS = 5


def normalise(name: str, fund: bool = False) -> str:
    """Collapse a free-text issuer name to its identity tokens.

    `fund=True` additionally drops fund-wrapper words, and is for the
    combined issuer+class key only — see _FUND_NOISE for why it must not
    apply to the issuer-name index.

    Tokens come back SORTED, so the key is a set rather than a phrase.
    EDGAR files companies under an inverted registrant style that no filer
    copies — "HORTON D R INC /DE/" against "D R HORTON INC", "HUNT J B
    TRANSPORT" against "J B HUNT TRANSPORT" — and those are the same
    company by any reading. Order-insensitive matching absorbs the whole
    class of inversions; the dominance check still catches the rare case
    where two different issuers share a token multiset.
    """
    text = _REGISTRANT.sub(" ", (name or "").strip())
    text = _PUNCT.sub(" ", text.lower())
    noise = _FUND_NOISE if fund else _NOISE
    tokens = [t for t in _SPACES.sub(" ", text).strip().split(" ") if t]
    kept = [t for t in tokens if t not in noise]
    return " ".join(sorted(kept or tokens))


@dataclass
class Resolution:
    ticker: str
    cusip: str | None
    status: str                       # resolved | ambiguous | unknown | override
    confidence: float = 0.0           # share of rows behind the winning CUSIP
    rows: int = 0
    issuer: str = ""
    candidates: list[dict] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.cusip is not None and self.status in ("resolved", "override")


def load_overrides(path: Path | None = None) -> dict[str, str]:
    """Hand-pinned ticker -> CUSIP. Absent file is the normal state."""
    path = path or OVERRIDES
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    # Leading-underscore keys are documentation for whoever opens the file,
    # not tickers. Without this the "_comment" line becomes a pin for a
    # ticker named _COMMENT — harmless, but it shows up in every audit.
    return {str(k).upper(): str(v).upper().strip()
            for k, v in raw.items()
            if isinstance(v, str) and v.strip() and not str(k).startswith("_")}


def fetch_ticker_index() -> dict[str, str]:
    """SEC ticker -> registrant name. Public, no key, no CUSIP in it."""
    req = urllib.request.Request(
        TICKER_URL, headers={"User-Agent": user_agent(), "Host": "www.sec.gov"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    out = {}
    for entry in payload.values():
        ticker = str(entry.get("ticker", "")).upper().strip()
        title = str(entry.get("title", "")).strip()
        if ticker and title:
            out[ticker] = title
    return out


def build_name_index(zip_path: Path) -> dict[str, Counter]:
    """normalised issuer name -> Counter of CUSIPs seen under it.

    Builds a SECOND index at the same time, keyed on issuer name and class
    together, and stashes it for `resolve` to fall back on.

    That second key exists for funds. An operating company puts its
    identity in NAMEOFISSUER, but every iShares ETF files as issuer
    "ISHARES TR" with the actual fund in TITLEOFCLASS:

        ISHARES TR | RUSSELL 2000 ETF   | 464287655
        ISHARES TR | CORE S&P500 ETF    | 464287200
        ISHARES TR | SILVER TRUST       | 46428Q109

    Keyed on issuer alone those are one bucket of dozens of CUSIPs, which
    resolves to nothing — correctly, since "ISHARES TR" genuinely does not
    identify a security. Keyed on both they separate cleanly.

    A full pass over the information table. Slow enough to be worth caching
    (see `save_index`), cheap enough to rebuild once per quarter.
    """
    index: dict[str, Counter] = {}
    combined: dict[str, Counter] = {}
    names: dict[str, Counter] = {}
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(INFOTABLE) as fh:
            stream = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
            for row in csv.DictReader(stream, delimiter="\t"):
                cusip = (row.get("CUSIP") or "").strip().upper()
                raw_name = (row.get("NAMEOFISSUER") or "").strip()
                if not cusip or not raw_name:
                    continue
                key = normalise(raw_name)
                if not key:
                    continue
                index.setdefault(key, Counter())[cusip] += 1
                names.setdefault(cusip, Counter())[raw_name] += 1

                title = (row.get("TITLEOFCLASS") or "").strip()
                both = normalise(f"{raw_name} {title}", fund=True)
                if both and both != key:
                    combined.setdefault(both, Counter())[cusip] += 1
    # Attach the most common spelling of each CUSIP, and the combined-key
    # index, for resolve() and save_index() to pick up.
    build_name_index.display = {          # type: ignore[attr-defined]
        c: n.most_common(1)[0][0] for c, n in names.items()}
    build_name_index.combined = combined  # type: ignore[attr-defined]
    return index


def resolve(ticker: str, name_index: dict[str, Counter],
            ticker_index: dict[str, str],
            overrides: dict[str, str] | None = None,
            display: dict[str, str] | None = None,
            fund_names: dict[str, str] | None = None,
            combined_index: dict[str, Counter] | None = None) -> Resolution:
    """Resolve one ticker, reporting how sure the answer is.

    `fund_names` maps ticker -> full product name for securities the SEC's
    company_tickers.json does not carry. That file lists REGISTRANTS, and an
    ETF is not one: SPY, IWM, SMH and QQQ are all absent from it, which is
    exactly the population whose institutional option books matter most on
    a page like this. A name from anywhere else (yfinance longName, here)
    plus the combined-key index closes that hole.
    """
    ticker = ticker.upper().strip()
    overrides = overrides if overrides is not None else load_overrides()
    display = display or getattr(build_name_index, "display", {})
    combined_index = (combined_index if combined_index is not None
                      else getattr(build_name_index, "combined", {}))

    if ticker in overrides:
        cusip = overrides[ticker]
        return Resolution(ticker, cusip, "override", 1.0,
                          issuer=display.get(cusip, ""))

    title = ticker_index.get(ticker)
    counts = name_index.get(normalise(title)) if title else None

    # Funds: try the combined issuer+class key against the supplied product
    # name. Only when the registrant path found nothing usable, so an
    # operating company never gets resolved by a fund name that happens to
    # share tokens with it.
    if not counts and fund_names:
        fund = fund_names.get(ticker)
        if fund:
            counts = combined_index.get(normalise(fund, fund=True))
            title = title or fund

    if not title:
        return Resolution(ticker, None, "unknown")
    if not counts:
        return Resolution(ticker, None, "unknown", issuer=title)

    total = sum(counts.values())
    ranked = counts.most_common()
    top_cusip, top_rows = ranked[0]
    dominance = top_rows / total if total else 0.0
    candidates = [{"cusip": c, "rows": n, "issuer": display.get(c, "")}
                  for c, n in ranked[:5]]

    if total < MIN_ROWS:
        return Resolution(ticker, None, "unknown", dominance, total, title, candidates)
    if dominance < MIN_DOMINANCE:
        return Resolution(ticker, None, "ambiguous", dominance, total, title, candidates)
    return Resolution(ticker, top_cusip, "resolved", dominance, total,
                      display.get(top_cusip, title), candidates)


def save_index(index: dict[str, Counter], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": INDEX_VERSION,
        "names": {k: dict(v) for k, v in index.items()},
        "combined": {k: dict(v)
                     for k, v in getattr(build_name_index, "combined", {}).items()},
        "display": getattr(build_name_index, "display", {}),
    }
    path.write_text(json.dumps(payload))


class StaleIndex(RuntimeError):
    """Cached index predates the current normalisation rules."""


def load_index(path: Path) -> tuple[dict[str, Counter], dict[str, str]]:
    payload = json.loads(path.read_text())
    if payload.get("version") != INDEX_VERSION:
        raise StaleIndex(
            f"{path.name} was built under normalisation v"
            f"{payload.get('version')}, current is v{INDEX_VERSION}")
    index = {k: Counter(v) for k, v in payload.get("names", {}).items()}
    display = payload.get("display", {})
    build_name_index.display = display     # type: ignore[attr-defined]
    build_name_index.combined = {          # type: ignore[attr-defined]
        k: Counter(v) for k, v in payload.get("combined", {}).items()}
    return index, display


def load_fund_names(path: Path | None = None) -> dict[str, str]:
    """Ticker -> product name for securities absent from the SEC's
    registrant file. Learned once and cached; see `learn_fund_names`."""
    path = path or FUND_NAMES
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k).upper(): str(v) for k, v in raw.items() if v}


def learn_fund_names(tickers: list[str], path: Path | None = None) -> dict[str, str]:
    """Look up names for tickers we could not resolve, and cache them.

    Networked, so it runs only over the leftovers — a handful of ETFs out
    of hundreds of names — and never over tickers the registrant path
    already answered. A lookup that fails is cached as a miss too, so a
    delisted or renamed ticker does not re-hit the network every run.
    """
    path = path or FUND_NAMES
    known = load_fund_names(path)
    todo = [t for t in {t.upper() for t in tickers} if t not in known]
    if not todo:
        return known
    try:
        import yfinance as yf
    except ImportError:
        return known
    for ticker in todo:
        name = ""
        try:
            info = yf.Ticker(ticker).info or {}
            name = str(info.get("longName") or info.get("shortName") or "")
        except Exception:
            name = ""
        known[ticker] = name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(known, indent=1, sort_keys=True))
    return known
