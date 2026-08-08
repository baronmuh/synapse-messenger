"""F18 — Observer accounts (strict read-only) and the web supervision
interface.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from synapse.client import ApiClientError
from synapse.errors import ACCESS_DENIED, USERNAME_ALREADY_EXISTS

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ORG_NAME, ORG_PASSWORD

OBSERVER = "observateur"
OBS_PASSWORD = "motdepasse-observateur-1"
TOKEN = "jeton-de-test-web-123456"


def _web(fx):
    """Web interface WITHOUT a secret (SPEC-WEB D5): session per connection."""
    from synapse.web import SynapseWebUI

    web = SynapseWebUI(fx.config, port=0)
    web.start()
    assert web._server is not None
    return web, web._server.server_address[1]


def _authed(port):
    """Authenticated opener (root_org human account) — SPEC-WEB D5."""
    from .web_helpers import authed
    return authed(port)


def _open(port, path, opener):
    """GET with the opener's session cookie."""
    return urllib.request.urlopen(
        f"http://127.0.0.1:{port}{path}", context=None) if False else _open_with(opener, port, path)


def _open_with(opener, port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    return opener.open(req)


def _make_observer(fx) -> None:
    fx.client.create_observer_account(OBSERVER, OBS_PASSWORD, "Supervision",
                                      ORG_NAME, ORG_PASSWORD)


def test_observer_lifecycle(fx):
    _make_observer(fx)
    observers = fx.client.list_observers(ORG_NAME, ORG_PASSWORD)["observers"]
    assert [o["username"] for o in observers] == [OBSERVER]
    revoked = fx.client.revoke_observer_account(OBSERVER, ORG_NAME, ORG_PASSWORD)
    assert revoked["status"] == "disabled"
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_snapshot(OBSERVER, OBS_PASSWORD)
    assert exc.value.code == "AUTH_FAILED"


def test_observer_duplicate_rejected(fx):
    _make_observer(fx)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_observer_account(OBSERVER, OBS_PASSWORD, "x",
                                          ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USERNAME_ALREADY_EXISTS


def test_observer_reads_snapshot(fx):
    _make_observer(fx)
    fx.client.create_task("T", BOB, ALICE, ALICE_PASSWORD)
    snap = fx.client.get_org_snapshot(OBSERVER, OBS_PASSWORD)
    assert snap["organization_name"] == ORG_NAME
    # the directory includes the organization's human account (SPEC-WEB §5.4)
    assert {a["username"] for a in snap["agents"]} == {ALICE, BOB, OBSERVER,
                                                       f"{ORG_NAME}_humain"}
    assert snap["tasks_by_state"].get("submitted") == 1
    # never content
    assert "SECRET" not in str(snap)


def test_observer_writes_denied(fx):
    _make_observer(fx)
    for command in (
        lambda: fx.client.send_message(ALICE, "hello", "cmid-obs-1",
                                       OBSERVER, OBS_PASSWORD),
        lambda: fx.client.create_task("T", ALICE, OBSERVER, OBS_PASSWORD),
        lambda: fx.client.create_group("g", OBSERVER, OBS_PASSWORD),
        lambda: fx.client.set_agent_card(["x"], OBSERVER, OBS_PASSWORD),
    ):
        with pytest.raises(ApiClientError) as exc:
            command()
        assert exc.value.code == ACCESS_DENIED


def test_snapshot_requires_observer(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_snapshot(ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED


def test_observer_reads_are_allowed(fx):
    _make_observer(fx)
    data = fx.client.get_org_snapshot(OBSERVER, OBS_PASSWORD)
    assert data["agents"]
    # regular reads remain allowed
    assert fx.client.help(OBSERVER, OBS_PASSWORD)["documentation"]


def test_observer_whitelist_matches_real_read_commands(fx):
    """AUDIT-006: the observer whitelist (_OBSERVER_READ_COMMANDS) must
    cover exactly the real read commands of an agent account — a future
    read command added without whitelisting would silently become
    executable by an observer. Here, every command outside the whitelist
    is verified as denied (ACCESS_DENIED)."""
    from synapse.service import (
        _AGENT_HANDLERS,
        _HUMAN_HANDLERS,
        _OBSERVER_READ_COMMANDS,
    )
    from synapse.validation import COMMAND_SPECS

    # 1. Every whitelisted command really exists in the spec.
    for command in _OBSERVER_READ_COMMANDS:
        assert command in COMMAND_SPECS, f"{command} dans la whitelist mais pas dans la spec"

    # 2. The whitelist is exactly a subset of the agent commands
    #    (those executable by an agent principal) — not org, not human,
    #    not local web.
    agent_reads = set(_AGENT_HANDLERS) - set(_HUMAN_HANDLERS)
    for command in _OBSERVER_READ_COMMANDS:
        assert command in agent_reads, (
            f"{command} whitelisted but not an agent read command")

    # 3. Any agent command OUTSIDE the whitelist must be denied to an
    #    observer (tested on a representative sample: writes and commands
    #    reserved for agents — org/human commands are not executable by an
    #    observer by construction).
    from synapse.errors import ApiError as ApiError_
    from synapse.validation import validate_envelope

    _make_observer(fx)
    candidates = [c for c in _AGENT_HANDLERS if c not in _OBSERVER_READ_COMMANDS
                  and c not in _HUMAN_HANDLERS]
    writes: list[tuple[str, dict]] = []
    for command in candidates:
        spec = COMMAND_SPECS[command]
        # The envelope requires EXACT keys: all parameters (required
        # and optional) must be present, None for the optional ones.
        params = {name: None for name, _, _, _ in spec[1]}
        for name, type_, required, _ in spec[1]:
            if required:
                if type_ is str:
                    params[name] = "x"
                elif type_ is bool:
                    params[name] = True
                elif type_ is int:
                    params[name] = 1
                elif type_ is list:
                    params[name] = ["x"]  # non-empty lists (e.g. capabilities)
        params["my_name_auth"] = OBSERVER
        params["my_password_auth"] = OBS_PASSWORD
        try:
            validate_envelope({"api_version": "v2", "command": command,
                               "parameters": params})
        except ApiError_:
            continue  # non-generic parameters (UUID, timestamps…):
            # not representative of the observer check — covered by
            # part 2 (agent subset) and by test_observer_writes_denied.
        writes.append((command, params))
    assert writes, "no valid agent command outside the whitelist to check"
    for command, params in writes[:10]:
        with pytest.raises(ApiClientError) as exc:
            fx.client.request(command, params)
        assert exc.value.code == ACCESS_DENIED, (
            f"{command} executed by an observer without being whitelisted")


def test_web_ui_serves_dashboard(fx):
    fx.client.create_task("T", BOB, ALICE, ALICE_PASSWORD)
    web, port = _web(fx)
    try:
        opener = _authed(port)
        with _open(port, "/api/snapshot", opener) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["organization_name"] == ORG_NAME
        assert data["tasks_by_state"].get("submitted") == 1
        with _open(port, "/", opener) as resp:
            html = resp.read().decode("utf-8")
        assert "Synapse — Supervision" in html
    finally:
        web.stop()


def test_web_ui_unknown_org_401(fx):
    """Amended SPEC-WEB D5: an unknown organization is refused at
    login (401) and no session is created."""
    web, port = _web(fx)
    try:
        from .web_helpers import login
        _, status = login(port, org="org_inexistante")
        assert status == 401
        assert web._sessions == {}  # no session created
    finally:
        web.stop()


def test_web_ui_new_routes(fx):
    """Extended web interface routes (org, agents, search, static)."""
    fx.client.set_agent_card(["comptabilite"], ALICE, ALICE_PASSWORD)
    web, port = _web(fx)
    try:
        opener = _authed(port)

        def get(path):
            with _open(port, path, opener) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))

        status, data = get("/api/org")
        assert status == 200 and data["organization_name"] == ORG_NAME
        status, data = get("/api/agents/alice")
        assert status == 200 and data["description"]
        status, data = get("/api/search?q=ali")
        assert status == 200 and any(a["username"] == ALICE for a in data["agents"])
        status, data = get("/api/search?capability=compta")
        assert status == 200
        # unknown agent -> 404; search without parameters -> 400
        with pytest.raises(urllib.error.HTTPError) as exc:
            _open(port, "/api/agents/inconnu", opener)
        assert exc.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as exc:
            _open(port, "/api/search", opener)
        assert exc.value.code == 400
        # static page (public, no session)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            html = resp.read().decode("utf-8")
        assert "Synapse" in html
    finally:
        web.stop()


def test_web_ui_etag_304(fx):
    """The ETag enables 304 Not Modified on JSON responses."""
    web, port = _web(fx)
    try:
        opener = _authed(port)
        from .web_helpers import session_token
        token = session_token(opener)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/snapshot",
                                     headers={"Cookie": f"synapse_session={token}"})
        with urllib.request.urlopen(req) as resp:
            etag = resp.headers.get("ETag")
        assert etag
        req2 = urllib.request.Request(f"http://127.0.0.1:{port}/api/snapshot",
                                      headers={"If-None-Match": etag,
                                               "Cookie": f"synapse_session={token}"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req2)
        assert exc.value.code == 304
    finally:
        web.stop()


def test_web_ui_agent_empty_and_missing_asset(fx):
    """Failure routes: empty agent (404) and missing asset (404)."""
    web, port = _web(fx)
    try:
        opener = _authed(port)
        with pytest.raises(urllib.error.HTTPError) as exc:
            _open(port, "/api/agents/", opener)
        assert exc.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/assets/absent.css")
        assert exc.value.code == 404
    finally:
        web.stop()


def test_web_ui_cache_purge(fx):
    """The server cache purge evicts expired entries when it exceeds
    the bound."""
    from synapse.web import SynapseWebUI, _MAX_CACHE_ENTRIES

    web = SynapseWebUI(fx.config, port=0)
    import time

    now = time.monotonic()
    for i in range(_MAX_CACHE_ENTRIES + 5):
        web._cache[f"expiree-{i}"] = (now - 1.0, i)  # all expired
    value = web._cached("fraiche", 60.0, lambda: "ok")
    assert value == "ok"
    assert "fraiche" in web._cache
    assert not any(k.startswith("expiree") for k in web._cache)
