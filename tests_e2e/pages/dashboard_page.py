"""
dashboard_page.py — "/" , and the proof a session is live
==========================================================
Where a successful login lands. The tests use it for one thing above all: a
signed-in page renders the shared layout, and a signed-out browser can never
see it — so `expect_loaded()` here is the assertion that a session exists.
"""

from __future__ import annotations

from playwright.sync_api import expect

from .base_page import BasePage


class DashboardPage(BasePage):
    path = "/"
    title = "Dashboard"

    def expect_signed_in_as(self, username: str):
        """The Sign out button carries the account name in its tooltip —
        the only place the layout names the signed-in user."""
        expect(self.sign_out_button).to_have_attribute(
            "title", f"Signed in as {username}")
        return self
