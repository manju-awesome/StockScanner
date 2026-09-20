"""
base_page.py — what every page object gets for free
====================================================
The rule this suite follows: a test names intentions ("sign in", "filter to
Buy now"), a page object owns locators, and no test file contains a CSS
selector. When the markup moves, one file changes.

The workstation is server-rendered — every filter is a link and a page load,
not a client-side re-query — so navigation here is `goto` with a query string
rather than clicking and waiting for XHR. Playwright's auto-waiting still does
the work that matters: `expect(...)` retries, so the job tray polling on the
shared layout can never make an assertion flaky.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from playwright.sync_api import Page, expect

ANY_PAGE_TITLE = re.compile(r".* · Trading Workstation$")


class BasePage:
    path = "/"
    title = ""            # the <title> prefix this page renders under

    def __init__(self, page: Page):
        self.page = page

    # ── navigation ──────────────────────────────────────────────────────────
    def open(self, **params):
        """Go to this page, carrying whatever query state was asked for.

        Empty values are dropped rather than sent as `key=`, so the URL a
        test builds looks like the URL a click builds.
        """
        kept = {k: v for k, v in params.items() if v not in ("", None)}
        query = f"?{urlencode(kept, doseq=True)}" if kept else ""
        self.page.goto(f"{self.path}{query}")
        return self

    # ── the shared layout, present on every signed-in page ──────────────────
    @property
    def sidebar(self):
        return self.page.locator(".sidebar")

    @property
    def sign_out_button(self):
        return self.page.get_by_role("button", name="Sign out")

    @property
    def global_search(self):
        return self.page.locator("#global-search")

    def expect_loaded(self):
        expect(self.sidebar).to_be_visible()
        expect(self.global_search).to_be_visible()
        expect(self.page).to_have_title(
            re.compile(rf"^{re.escape(self.title)} · Trading Workstation$")
            if self.title else ANY_PAGE_TITLE)
        return self

    def nav_to(self, label: str):
        """Click a sidebar entry by its visible label ("Long-Term", "CSP")."""
        self.sidebar.get_by_role("link", name=label).click()
        return self

    def sign_out(self):
        self.sign_out_button.click()
        return self
