"""
conftest.py — the test server, and the fixtures both suites share
==================================================================
One workstation process per test session, on a free port, against a tmp data
directory (see _server.py). The UI suite drives it through a browser; the
backend suite talks to it with plain HTTP and calls the same code in-process.

Nothing in here — and nothing in any test — reads or writes the project's real
data/ or data/output/. That is not a style preference: the pages under test
render the library, and a suite that wrote fixtures into it would corrupt the
thing it is measuring.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))                    # pages/, fixtures/
sys.path.insert(0, str(PROJECT_ROOT / "src"))         # stockanalysis.*

from fixtures import library                          # noqa: E402
from pages.login_page import LoginPage                # noqa: E402

USERNAME = "e2e-tester"
PASSWORD = "playwright-test-pw"
STARTUP_TIMEOUT = 60          # PBKDF2 at 240k iterations is ~100ms; imports dominate


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Workstation:
    """A running test server and the paths it was pointed at."""

    def __init__(self, base_url: str, data_dir: Path, log: list[str]):
        self.base_url = base_url
        self.data_dir = data_dir
        self.output_dir = data_dir / "output"
        self.username = USERNAME
        self.password = PASSWORD
        self._log = log

    def url(self, path: str = "/") -> str:
        return self.base_url + path

    @property
    def log(self) -> str:
        return "".join(self._log)

    def session(self, signed_in: bool = True) -> requests.Session:
        """An HTTP client. Signed in by default, because every path but
        /login answers a signed-out request with a redirect."""
        s = requests.Session()
        if signed_in:
            r = s.post(self.url("/login"),
                       data={"username": self.username,
                             "password": self.password, "next": "/"},
                       allow_redirects=False)
            assert r.status_code in (302, 303), (
                f"test login failed: {r.status_code}\n{self.log}")
        return s


@pytest.fixture(scope="session")
def workstation(tmp_path_factory) -> Workstation:
    data_dir = tmp_path_factory.mktemp("workstation-data")
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(TESTS_DIR / "_server.py"),
         "--port", str(port), "--data-dir", str(data_dir),
         "--user", USERNAME, "--password", PASSWORD],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    # Drained on a thread so the server can never block on a full pipe, and
    # so a startup traceback is in the failure message instead of lost.
    log: list[str] = []

    def _drain():
        for line in proc.stdout:                       # type: ignore[union-attr]
            log.append(line)

    threading.Thread(target=_drain, daemon=True).start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"test server exited during startup:\n{''.join(log)}")
        try:
            if requests.get(f"{base_url}/login", timeout=2).status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.15)
    else:
        proc.terminate()
        pytest.fail(f"test server never came up:\n{''.join(log)}")

    ws = Workstation(base_url, data_dir, log)
    yield ws

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def base_url(workstation: Workstation) -> str:
    """Overrides pytest-playwright's fixture so page.goto("/login") works."""
    return workstation.base_url


@pytest.fixture
def fixture_library(workstation: Workstation):
    """Three known companies as the whole research library.

    Written per test rather than once per session: the server re-reads
    research_index.json on every request, so a test that wants a different
    library just writes one, and no test inherits another's.
    """
    path = library.write_index(workstation.output_dir)
    yield library.default_rows()
    path.unlink(missing_ok=True)


@pytest.fixture
def signed_in(workstation: Workstation) -> requests.Session:
    return workstation.session()


@pytest.fixture
def anonymous(workstation: Workstation) -> requests.Session:
    return workstation.session(signed_in=False)


@pytest.fixture
def signed_in_page(page, workstation: Workstation):
    """A browser already through the login gate."""
    LoginPage(page).open().sign_in(workstation.username, workstation.password)
    return page
