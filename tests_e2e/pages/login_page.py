"""
login_page.py — the sign-in gate
=================================
Deliberately not a BasePage: /login is rendered outside the shared layout
(login_view.py), so it has no sidebar, no search box and no job tray. A page
object that inherited those locators would let a test assert against elements
this page must not have.

Locator note: the password field is addressed by `name`, not by label. Its
<label for="password"> points at an id the input does not carry, so
get_by_label("Password") finds nothing. When the app grows data-testid hooks,
these three locators are the first to move onto them.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect


class LoginPage:
    path = "/login"
    title = "Sign in · Trading Workstation"

    # The message the app shows for a wrong username AND a wrong password.
    # Identical by design — the form must not reveal which accounts exist.
    GENERIC_ERROR = "Incorrect username or password."

    def __init__(self, page: Page):
        self.page = page

    # ── locators ────────────────────────────────────────────────────────────
    @property
    def username(self):
        return self.page.locator("#username")

    @property
    def password(self):
        return self.page.locator("input[name='password']")

    @property
    def submit(self):
        return self.page.get_by_role("button", name="Sign in")

    @property
    def error(self):
        return self.page.locator(".error")

    @property
    def next_field(self):
        return self.page.locator("input[name='next']")

    # ── actions ─────────────────────────────────────────────────────────────
    def open(self, next_path: str | None = None):
        suffix = f"?next={next_path}" if next_path else ""
        self.page.goto(f"{self.path}{suffix}")
        return self

    def fill(self, username: str, password: str):
        self.username.fill(username)
        self.password.fill(password)
        return self

    def submit_form(self):
        self.submit.click()
        return self

    def sign_in(self, username: str, password: str):
        """Fill and submit, and wait for the navigation that follows."""
        self.fill(username, password)
        with self.page.expect_navigation():
            self.submit.click()
        return self

    # ── assertions ──────────────────────────────────────────────────────────
    def expect_loaded(self):
        expect(self.page).to_have_title(self.title)
        expect(self.username).to_be_visible()
        expect(self.password).to_have_attribute("type", "password")
        expect(self.submit).to_be_visible()
        return self

    def expect_error(self, text: str | None = None):
        expect(self.error).to_have_text(text or self.GENERIC_ERROR)
        return self

    def expect_no_error(self):
        expect(self.error).to_have_count(0)
        return self
