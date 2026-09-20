# tests_e2e — page-object UI tests and backend page tests

A second, separate suite from `tests/`. `tests/` holds fast in-process unit
tests (`python -m unittest tests.test_x`); this one starts the real
workstation and tests it from outside — through a browser where the browser is
the point, and over plain HTTP where it isn't.

## Install and run

```bash
python3 -m pip install -r tests_e2e/requirements-test.txt
python3 -m playwright install chromium

python3 -m pytest tests_e2e              # everything (~3s)
python3 -m pytest tests_e2e -m ui        # browser only
python3 -m pytest tests_e2e -m backend   # no browser
python3 -m pytest tests_e2e -m ui --headed --slowmo 400   # watch it
```

Failures worth looking at visually: `--tracing retain-on-failure` writes a
trace to `test-results/`, opened with `python3 -m playwright show-trace <file>`.

## What is where

```
conftest.py                     one test server per session + shared fixtures
_server.py                      the app, pointed at a tmp data dir, offline
fixtures/library.py             the three-company research library under test
pages/                          page objects — the only files with selectors
ui/test_login_auth.py           the auth gate, through a real browser
backend/test_longterm_page.py   /longterm over HTTP and in-process
```

## The two rules the suite is built on

**No selector outside `pages/`.** A test names intentions — `sign_in`,
`expect_rows_for("ELIT")` — and a page object owns the locators. When the
markup moves, one file changes. `Rendered` in the backend test file is the
same idea for HTML that no browser parsed.

**Nothing reads or writes the real `data/`.** `_server.py` redirects every
module-level path at a tmp directory before the modules that copy those
constants get imported, and it never calls `app.main()`, so the scheduler and
the SPY daemon — both of which write to `data/` on a timer — do not exist in
the test process. Two seams are stubbed so no page render can reach the
network: the ^TNX quote and the market-regime lookup.

## The library the tests own

`fixtures/library.py` builds three companies whose verdict is known before the
test runs, from the same baseline row as `tests/test_longterm_engine.py`:

| ticker | shape                                              | verdict            |
|--------|----------------------------------------------------|--------------------|
| `ELIT` | elite business, clean 8/21 EMA pullback, confirmed | BUY NOW            |
| `OVER` | same business, priced for far more cash            | stops at valuation |
| `WEAK` | perfect chart, gutted business                     | AVOID (quality)    |

Written per test into the server's tmp `data/output/research_index.json`; the
page re-reads it on every request, so a test that wants a different library
just writes one.

## Where the browser layer is deliberately thin

`/longterm` is server-rendered — no rule builder, no per-keystroke re-query —
so its filters, verdicts and sizing are all reachable over HTTP in
milliseconds. The browser earns its seconds on the auth gate, which *is* a
redirect, a form, a cookie and a POST sign-out. Extend the UI suite for
things only a browser can see: the screener's rule builder, the job-tray
polling, sortable table headers, the CSP picker.

## Two things in the app that would make this suite better

1. **`data-testid` hooks.** Only the screener carries ids today; `/longterm`
   is addressable because it happens to render `id="ltr-TICKER"` and
   `tr[data-main="1"]`. Adding test ids in the `views.py` helpers (`badge`,
   `card`, the table builders) would give every page stable anchors at once.
2. **Env-overridable data paths.** `views.OUTPUT_DIR`/`DATA_DIR` are computed
   from `__file__`, which is why `_server.py` has to patch module globals by
   name — including `api.OUTPUT_DIR`, which is a second, independent copy.
   A `WORKSTATION_DATA_DIR` env var read in one place would delete that whole
   function.

Minor, found while writing the login page object: the password field's
`<label for="password">` points at an id the input does not carry, so
`get_by_label("Password")` matches nothing (the object uses
`input[name='password']`). Adding `id="password"` fixes the label association
for screen readers too.
