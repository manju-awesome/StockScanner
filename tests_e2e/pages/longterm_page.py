"""
longterm_page.py — /longterm, the Long-Term Buy Engine
=======================================================
The page renders one <tr data-main="1" id="ltr-TICKER"> per company plus a
collapsed detail row, filter chips, and a coverage note. All of its state
lives in the query string (`q`, `action`, `list`, `near`, `limit`, `regime`),
which is why `open()` takes them as keyword arguments rather than this object
exposing a click for each control: a filter here IS a URL.

Used by the UI suite for a smoke test. The verdicts themselves are asserted in
tests_e2e/backend/test_longterm_page.py, without a browser — a browser adds
nothing to a claim about a number the server computed.
"""

from __future__ import annotations

from playwright.sync_api import expect

from .base_page import BasePage


class LongtermPage(BasePage):
    path = "/longterm"
    title = "Long-Term Buy Engine"

    # ── locators ────────────────────────────────────────────────────────────
    @property
    def table(self):
        return self.page.locator("#lt-table")

    @property
    def rows(self):
        """The summary rows only — every company also renders a hidden
        detail row, and counting both would double every count."""
        return self.table.locator("tr[data-main='1']")

    def row(self, ticker: str):
        return self.page.locator(f"#ltr-{ticker}")

    def tickers(self) -> list[str]:
        return [(el or "").removeprefix("ltr-")
                for el in self.rows.evaluate_all(
                    "els => els.map(e => e.id)")]

    def chip(self, label: str):
        """A filter chip, e.g. "Buy now 3". Matched on its leading text so a
        test never has to know the count."""
        return self.page.get_by_role("link").filter(
            has_text=label).first

    # ── assertions ──────────────────────────────────────────────────────────
    def expect_rows_for(self, *tickers: str):
        for ticker in tickers:
            expect(self.row(ticker)).to_be_visible()
        expect(self.rows).to_have_count(len(tickers))
        return self

    def expect_verdict(self, ticker: str, action: str):
        """The action cell is the last thing on the row — §15's ordering, the
        eight judgments first and the action last."""
        expect(self.row(ticker)).to_contain_text(action)
        return self
