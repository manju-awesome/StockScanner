"""
test_login_auth.py — the auth gate, through a real browser
===========================================================
These are the tests that need a browser: the gate is a redirect, a form, a
cookie and a POST sign-out, and what they defend is what the browser does with
them, not what a function returns.

The claims:

  1. Everything is behind the gate, and the gate remembers where you were
     going. A redirect that dropped `next` would send you to the dashboard
     after login and quietly lose the page you asked for.
  2. A failed login says the same thing for a wrong username and a wrong
     password. The moment those differ, the form enumerates accounts.
  3. The session cookie is HttpOnly and SameSite=Lax. A research page is
     generated HTML full of third-party-ish content; if JS could read the
     token, an XSS bug there would hand over the whole workstation.
  4. Five failures lock the account. Without it, a wordlist gets unlimited
     ~100ms guesses against a loopback port.

Run: python3 -m pytest tests_e2e/ui -m ui
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from pages.dashboard_page import DashboardPage
from pages.login_page import LoginPage
from pages.longterm_page import LongtermPage

pytestmark = pytest.mark.ui


def test_the_sign_in_page_renders(page):
    LoginPage(page).open().expect_loaded().expect_no_error()


def test_a_signed_out_visitor_is_sent_to_login(page):
    page.goto("/longterm")
    expect(page).to_have_url(re.compile(r"/login\?next=%2Flongterm$"))
    LoginPage(page).expect_loaded()


def test_the_gate_remembers_where_you_were_going(page, workstation,
                                                 fixture_library):
    page.goto("/longterm")
    # The hidden field is how `next` survives the POST — a redirect that
    # carried it only in the URL would lose it on submit.
    assert LoginPage(page).next_field.input_value() == "/longterm"

    LoginPage(page).sign_in(workstation.username, workstation.password)

    longterm = LongtermPage(page).expect_loaded()
    longterm.expect_rows_for("ELIT", "OVER", "WEAK")


def test_valid_credentials_land_on_the_dashboard(page, workstation):
    LoginPage(page).open().sign_in(workstation.username, workstation.password)
    (DashboardPage(page)
     .expect_loaded()
     .expect_signed_in_as(workstation.username))


def test_the_session_cookie_cannot_be_read_by_javascript(signed_in_page):
    cookie = next(c for c in signed_in_page.context.cookies()
                  if c["name"] == "ws_session")
    assert cookie["httpOnly"] is True, (
        "an XSS bug in a generated research page would exfiltrate the session")
    assert cookie["sameSite"] == "Lax", (
        "SameSite is what makes /run and /api/portfolio/* CSRF-resistant "
        "without a token on every form")


def test_a_wrong_password_says_nothing_about_the_account(page, workstation):
    login = LoginPage(page).open()
    login.sign_in(workstation.username, "not-the-password")
    login.expect_error()
    expect(page).to_have_url(re.compile(r"/login$"))
    # Still signed out: the form re-rendered, it did not let anything through.
    assert not [c for c in page.context.cookies() if c["name"] == "ws_session"]


def test_an_unknown_username_gets_the_identical_message(page, workstation):
    wrong_password = LoginPage(page).open()
    wrong_password.sign_in(workstation.username, "not-the-password")
    first = wrong_password.error.text_content()

    unknown_user = LoginPage(page).open()
    unknown_user.sign_in("no-such-account", "not-the-password")

    assert unknown_user.error.text_content() == first == (
        LoginPage.GENERIC_ERROR), (
        "a different message for an unknown user turns the form into an "
        "account-enumeration oracle")


def test_signing_out_ends_the_session(signed_in_page):
    DashboardPage(signed_in_page).expect_loaded().sign_out()
    LoginPage(signed_in_page).expect_loaded()

    signed_in_page.goto("/portfolio")
    expect(signed_in_page).to_have_url(
        re.compile(r"/login\?next=%2Fportfolio$"))


@pytest.mark.slow
def test_five_failures_lock_the_account(page):
    # A username of its own: the lockout is per account and lasts five
    # minutes, and locking the account the other tests sign in with would
    # fail them in a way that looks like a broken password.
    probe = "lockout-probe"
    login = LoginPage(page).open()
    for attempt in range(5):
        login.sign_in(probe, f"wrong-{attempt}")
        login.expect_error(LoginPage.GENERIC_ERROR)

    login.sign_in(probe, "wrong-again")
    expect(login.error).to_contain_text("Too many failed attempts")
