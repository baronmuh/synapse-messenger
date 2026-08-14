"""Shared helpers for the human web interface tests (SPEC-WEB D5).

The web server starts WITHOUT a secret (no observer or token anymore); the
tests authenticate via POST /api/login (human account) and keep the
HttpOnly session cookie in a urllib opener (HTTPCookieProcessor).
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.request

import pytest

from .conftest import ORG_NAME, ORG_PASSWORD

HUMAN = f"{ORG_NAME}_humain"


@pytest.fixture()
def web(fx):
    """Test web interface, without any secret at startup."""
    from synapse.web import SynapseWebUI

    ui = SynapseWebUI(fx.config, port=0)
    ui.start()
    try:
        assert ui._server is not None
        yield ui, ui._server.server_address[1]
    finally:
        ui.stop()


def _opener():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.cookiejar = jar  # exposed to tests
    return opener


def opener_factory():
    """Public opener (equivalent to _opener) for tests that want the
    cookie jar: ``cookiejar`` attribute exposed."""
    return _opener()


def session_token(opener) -> str | None:
    """Value of the session cookie (for manual requests)."""
    for c in opener.cookiejar:
        if c.name == "synapse_session":
            return c.value
    return None


def login(port, org=ORG_NAME, password=ORG_PASSWORD, opener=None):
    """POST /api/login; returns (opener, HTTP code)."""
    opener = opener or _opener()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/login",
        data=json.dumps({"organization_name": org,
                         "organization_password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = opener.open(req)
        return opener, resp.status
    except urllib.error.HTTPError as exc:
        return opener, exc.code


def authed(port, org=ORG_NAME, password=ORG_PASSWORD):
    """Opener with a valid session (successful login)."""
    opener = _opener()
    _, status = login(port, org, password, opener)
    assert status == 200, f"login failed (HTTP {status})"
    return opener


def get(port, path, opener=None):
    """GET with the session cookie (401 if not logged in)."""
    opener = opener or _opener()
    return opener.open(f"http://127.0.0.1:{port}{path}")


def json_get(port, path, opener=None):
    with get(port, path, opener) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post(port, path, body, opener=None):
    """POST JSON with the session cookie."""
    opener = opener or _opener()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return opener.open(req)
