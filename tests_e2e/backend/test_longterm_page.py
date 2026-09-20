"""
test_longterm_page.py — /longterm without a browser
====================================================
The Long-Term page is server-rendered: no rule builder, no per-keystroke
re-query, every filter a query string and a page load. So the whole of its
behaviour is reachable over HTTP and in-process, and a browser would only add
seconds to a claim about a number the server already computed.

Two layers, in the order a failure should be read:

  HTTP        the route, the auth gate, the query-string filters, and the
              page's refusal to 500 on bad input. Against the real server.
  in-process  api.longterm() and longterm_view.longterm_page() over the same
              fixture rows — the verdict, the hierarchy, and the invariant
              that a row missing optional fields still renders.

The library is three companies this suite writes (fixtures/library.py), so
every assertion below is about the page and not about today's scan:

  ELIT  elite business, clean 8/21 EMA pullback, confirmed   -> BUY NOW
  OVER  same business priced for far more cash than it makes  -> stops at valuation
  WEAK  perfect chart, gutted business                        -> AVOID at the quality gate

Run: python3 -m pytest tests_e2e/backend -m backend
"""

from __future__ import annotations

import re

import pytest

from fixtures import library
from stockanalysis.core.longterm import position_sizing as PS
from stockanalysis.webapp import api, longterm_view

pytestmark = pytest.mark.backend

SETTINGS = {"capital": 100_000.0, "risk_pct": 1.0,
            "max_allocation_pct": 10.0, "atr_multiplier": 2.0}


# ─────────────────────────────────────────────────────────────────────────────
# reading the rendered page
# ─────────────────────────────────────────────────────────────────────────────

class Rendered:
    """The HTML the page returned, addressed by the anchors it renders.

    The same job the page objects do for the browser suite: the tests below
    ask for "the ELIT row", and only this class knows that a row is a
    <tr data-main="1" id="ltr-TICKER">.
    """

    ROW = r'<tr data-main="1" id="ltr-{ticker}">(.*?)</tr>'

    def __init__(self, html: str):
        self.html = html

    @property
    def tickers(self) -> list[str]:
        return re.findall(r'<tr data-main="1" id="ltr-([A-Z.\-]+)">', self.html)

    def row(self, ticker: str) -> str:
        match = re.search(self.ROW.format(ticker=ticker), self.html, re.S)
        assert match, f"no row for {ticker}; table holds {self.tickers}"
        return match.group(1)

    def action(self, ticker: str) -> str:
        """The action cell's text — the last column, §15's ordering."""
        cell = re.search(r'<td data-col="action".*?</td>', self.row(ticker),
                         re.S)
        assert cell, f"{ticker} rendered without an action cell"
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell.group(0))).strip()


def fetch(session, workstation, **params) -> Rendered:
    response = session.get(workstation.url("/longterm"), params=params or None,
                           timeout=30)
    assert response.status_code == 200, response.text[:400]
    return Rendered(response.text)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP — the route and its gate
# ─────────────────────────────────────────────────────────────────────────────

def test_a_signed_out_request_never_reaches_the_page(anonymous, workstation):
    response = anonymous.get(workstation.url("/longterm"),
                             allow_redirects=False)
    # 303, not 302: a login POST has to become a GET of the destination so a
    # refresh after signing in cannot re-submit the credentials.
    assert response.status_code == 303
    assert response.headers["Location"] == "/login?next=%2Flongterm"
    # The verdicts are the data worth protecting; none of them leaked.
    assert "ltr-" not in response.text


def test_a_signed_out_api_call_gets_json_not_the_login_page(anonymous,
                                                            workstation):
    # A stale tab's fetch() must fail as an error, not arrive as HTML where
    # JSON was expected.
    response = anonymous.get(workstation.url("/api/jobs"),
                             allow_redirects=False)
    assert response.status_code == 401
    assert response.json() == {"ok": False, "error": "not signed in"}


def test_every_company_in_the_library_gets_a_row(signed_in, workstation,
                                                 fixture_library):
    page = fetch(signed_in, workstation)
    assert page.tickers == ["ELIT", "OVER", "WEAK"] or set(page.tickers) == {
        "ELIT", "OVER", "WEAK"}, page.tickers


def test_each_verdict_reaches_the_page(signed_in, workstation,
                                       fixture_library):
    page = fetch(signed_in, workstation)
    assert "BUY NOW" in page.action("ELIT")
    assert "AVOID" in page.action("WEAK")
    assert "WAIT FOR PRICE" in page.action("OVER")


def test_the_action_filter_narrows_the_table(signed_in, workstation,
                                             fixture_library):
    page = fetch(signed_in, workstation, action="AVOID")
    assert page.tickers == ["WEAK"]


def test_a_ticker_search_narrows_and_names_what_it_could_not_find(
        signed_in, workstation, fixture_library):
    page = fetch(signed_in, workstation, q="ELIT, NOPE")
    assert page.tickers == ["ELIT"]
    # Named, not silently dropped: a search that quietly returns fewer rows
    # than tickers reads as a verdict on the missing ones.
    assert "Not in the research library" in page.html
    assert "NOPE" in page.html


def test_an_unreadable_limit_falls_back_to_the_default(signed_in, workstation,
                                                       fixture_library):
    # Someone typing a limit into the URL must not be able to 500 the page.
    page = fetch(signed_in, workstation, limit="not-a-number")
    assert set(page.tickers) == {"ELIT", "OVER", "WEAK"}


def test_an_empty_library_renders_an_empty_state(signed_in, workstation):
    # No fixture_library fixture: research_index.json does not exist. A fresh
    # checkout must show a friendly page, never a stack trace.
    page = fetch(signed_in, workstation)
    assert page.tickers == []
    assert "Traceback" not in page.html


# ─────────────────────────────────────────────────────────────────────────────
# in-process — the engine run behind the page
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def engine(monkeypatch):
    """api.longterm() over fixture rows, with every outside input pinned.

    The three seams: the library (disk), the 10-year Treasury (network) and
    the market regime (a cached scan). Pinning them is what makes a verdict
    assertion mean something — otherwise BUY NOW depends on today's tape.
    """

    class Engine:
        rows = library.default_rows()

        def use(self, rows):
            self.rows = rows
            return self

        def run(self, regime_override=None):
            return api.longterm(regime_override)

        def render(self, query=None):
            body, _js = longterm_view.longterm_page(query or {})
            return Rendered(body)

    engine = Engine()
    monkeypatch.setattr(api, "_longterm_entries",
                        lambda: library.entries(engine.rows))
    monkeypatch.setattr(api, "longterm_risk_free",
                        lambda: (0.042, "10Y Treasury 4.20% (test fixture)"))
    monkeypatch.setattr(api, "longterm_regime",
                        lambda override=None: (
                            (str(override).upper() if override
                             else "SELECTIVE"), "test fixture"))
    monkeypatch.setattr(api, "longterm_lists", lambda: {"e2e": ["ELIT"]})
    monkeypatch.setattr(api, "load_risk_settings",
                        lambda: PS.normalize_settings(SETTINGS))
    return engine


def test_the_run_is_shaped_for_the_page(engine):
    data = engine.run()

    assert {r["ticker"] for r in data["rows"]} == {"ELIT", "OVER", "WEAK"}
    assert all(r.get("action") and r.get("gate") for r in data["rows"])
    # The chips are built from `counts`; a count that disagreed with the table
    # would offer "Avoid 3" and return one row when clicked.
    assert sum(data["counts"].values()) == len(data["rows"])
    assert data["coverage"]["total"] == len(engine.rows)
    assert data["coverage"]["needs_rescan"] is False


def test_the_hierarchy_survives_the_web_layer(engine):
    """The engine's central claim, restated at the layer the page reads.

    WEAK's chart is the elite row's chart, untouched. If the web layer ever
    blends the gates into a score, this is where it shows up.
    """
    by_ticker = {r["ticker"]: r for r in engine.run()["rows"]}

    assert by_ticker["WEAK"]["gate"] == "quality"
    assert by_ticker["WEAK"]["action"] == "AVOID"
    assert by_ticker["OVER"]["gate"] == "valuation"
    assert not by_ticker["OVER"]["action"].startswith("BUY")
    assert by_ticker["ELIT"]["action"] == "BUY NOW"


def test_a_regime_override_is_carried_through_the_page(engine):
    body = engine.render({"regime": ["DEFENSIVE"]})
    assert "DEFENSIVE" in body.html, (
        "the page has to say which tape it scored against — the regime moves "
        "the gates")


def test_sizing_uses_the_settings_it_was_given(engine):
    """Position sizing is attached after the verdict, against the account the
    user configured. $100k at a 10% allocation cap is a $10,000 position."""
    action_cell = engine.render().action("ELIT")
    assert "$10,000" in action_cell, action_cell
    assert "$800 risk" in action_cell, action_cell


def test_a_row_that_lost_its_optional_fields_still_renders(engine):
    """A library scanned by an older version arrives without some columns.

    The page must render what it is handed. A missing input is not a
    satisfied condition either — an unmeasured candle is not a confirmation —
    so the verdict is allowed to change; what is not allowed is a 500.
    """
    stripped = library.scan_row(
        "OLD", **{"MA200_Slope%": None, "Reversal_Candle": None,
                  "Prior_Breakout_Level": None, "FCF_CAGR%": None,
                  "Pullback_Vol_Ratio": None, "ATR_Pct": None})
    page = engine.use([library.scan_row(), stripped]).render()

    assert set(page.tickers) == {"ELIT", "OLD"}
    assert page.action("OLD")                       # a verdict, whatever it is
    assert page.action("ELIT").startswith("🟢 BUY NOW")


def test_an_empty_library_renders_in_process_too(engine):
    page = engine.use([]).render()
    assert page.tickers == []
