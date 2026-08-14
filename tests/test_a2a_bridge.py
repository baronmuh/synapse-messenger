"""F20 — A2A bridge: agent card in A2A format and JSON-RPC translation of
task operations.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from .conftest import ALICE, ALICE_PASSWORD, BOB

TOKEN = "jeton-de-test-a2a-123456"


def _rpc(port: int, method: str, params: dict, rpc_id: int = 1, *,
         version: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "X-Synapse-Token": TOKEN}
    if version is not None:
        headers["A2A-Version"] = version
    body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method,
                       "params": params}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/message", data=body, headers=headers,
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(port: int, path: str, *, version: str | None = None):
    headers = {"X-Synapse-Token": TOKEN}
    if version is not None:
        headers["A2A-Version"] = version
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers=headers,
    )
    return urllib.request.urlopen(req)


def _make_bridge(fx):
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(fx.config, ALICE, ALICE_PASSWORD, port=0, token=TOKEN)
    bridge.start()
    assert bridge._server is not None
    return bridge, bridge._server.server_address[1]


def test_oversized_body_rejected(fx):
    """An oversized JSON-RPC body is rejected (413) before reading: an
    anti-abuse bound consistent with the main API's 1 MiB limit."""
    bridge, port = _make_bridge(fx)
    try:
        big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tasks/list",
                          "params": {"padding": "x" * (1024 * 1024 + 10)}}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/message", data=big,
            headers={"Content-Type": "application/json", "X-Synapse-Token": TOKEN},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 413
        # the service still works with a normal body
        assert _rpc(port, "tasks/list", {})["result"] is not None
    finally:
        bridge.stop()


def test_agent_card_endpoint(fx):
    fx.client.set_agent_card(["comptabilite", "audit"], ALICE, ALICE_PASSWORD)
    bridge, port = _make_bridge(fx)
    try:
        with _get(port, "/.well-known/agent.json") as resp:
            card = json.loads(resp.read().decode("utf-8"))
        assert card["name"] == ALICE
        # AgentCard v1.0 vocabulary (discover-before-talk)
        assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
        assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
        assert card["capabilities"] == {
            "streaming": False, "pushNotifications": False,
            "extendedAgentCard": False,
        }
        assert card["defaultInputModes"] == ["text/plain"]
        assert card["defaultOutputModes"] == ["text/plain"]
        assert card["provider"]["organization"] == "root_org"
        assert card["version"]
        skills = {s["id"]: s for s in card["skills"]}
        assert set(skills) == {"comptabilite", "audit"}
        # each skill carries a description, tags and the default modes
        for skill in skills.values():
            assert skill["description"]
            assert skill["inputModes"] == ["text/plain"]
            assert skill["outputModes"] == ["text/plain"]
    finally:
        bridge.stop()


def test_agent_card_v10_well_known_path(fx):
    """The AgentCard is also served at the A2A v1.0 well-known URI."""
    fx.client.set_agent_card(["audit"], ALICE, ALICE_PASSWORD)
    bridge, port = _make_bridge(fx)
    try:
        with _get(port, "/.well-known/agent-card.json") as resp:
            card = json.loads(resp.read().decode("utf-8"))
        assert card["name"] == ALICE
        assert any(s["id"] == "audit" for s in card["skills"])
    finally:
        bridge.stop()


# A2A v1.0 AgentCard ``securitySchemes``: a map<string, SecurityScheme> — an
# OBJECT keyed by scheme name (the official SDK does ``.values()`` over it, so
# a LIST would be dropped / not iterable). Each value is the v1.0
# discriminated-union member ``apiKeySecurityScheme`` with its ``name`` and
# ``location`` fields.
_A2A_APIKEY_SCHEME = {
    "apiKeySecurityScheme": {
        "name": "X-Synapse-Token",
        "location": "header",
    },
}


def test_agent_card_declares_security_scheme(fx):
    """The AgentCard declares the X-Synapse-Token auth as an A2A v1.0
    ApiKey SecurityScheme — a name->scheme OBJECT (not a LIST) using the
    v1.0 apiKeySecurityScheme vocabulary, plus securityRequirements, so a
    compliant v1.0 client can discover where to present the credential."""
    fx.client.set_agent_card(["audit"], ALICE, ALICE_PASSWORD)
    bridge, port = _make_bridge(fx)
    try:
        with _get(port, "/.well-known/agent-card.json") as resp:
            card = json.loads(resp.read().decode("utf-8"))
        # A2A v1.0: securitySchemes is a map<string,SecurityScheme> — an
        # OBJECT keyed by scheme name, exactly the shape the official SDK
        # iterates with .values().
        schemes = card["securitySchemes"]
        assert isinstance(schemes, dict) and schemes == {"apiKey": _A2A_APIKEY_SCHEME}
        # the card requires this scheme on every operation
        assert card["securityRequirements"] == [{"schemes": {"apiKey": []}}]
    finally:
        bridge.stop()


def test_responses_carry_a2a_version_header(fx):
    """Every bridge HTTP response announces the A2A protocol version it
    speaks (the ``A2A-Version`` header, A2A §3.6/§14.2.1)."""
    bridge, port = _make_bridge(fx)
    try:
        resp = _get(port, "/.well-known/agent.json")
        try:
            assert resp.headers.get("A2A-Version") == "1.0"
        finally:
            resp.close()
        # the JSON-RPC task endpoint announces it too
        resp = urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/message",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tasks/list",
                             "params": {}}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Synapse-Token": TOKEN}))
        try:
            assert resp.headers.get("A2A-Version") == "1.0"
        finally:
            resp.close()
    finally:
        bridge.stop()


def test_a2a_version_negotiation_accepted(fx):
    """A client declaring a compatible A2A-Version is served; patch
    components are ignored in negotiation (A2A §3.6)."""
    bridge, port = _make_bridge(fx)
    try:
        result = _rpc(port, "tasks/list", {}, version="1.0")
        assert "error" not in result
        result = _rpc(port, "tasks/list", {}, version="1.0.3")
        assert "error" not in result
    finally:
        bridge.stop()


def test_a2a_version_not_supported_rejected(fx):
    """A client requesting an unsupported protocol version gets a
    VersionNotSupportedError (JSON-RPC -32009, HTTP 400) — the A2A §3.6
    negotiation failure that future-proofs against v1.1/1.2."""
    bridge, port = _make_bridge(fx)
    try:
        for bad in ("0.5", "1.1", "2.0", "bogus"):
            with pytest.raises(urllib.error.HTTPError) as exc:
                _rpc(port, "tasks/list", {}, version=bad)
            assert exc.value.code == 400
            body = json.loads(exc.value.read().decode("utf-8"))
            assert body["error"]["code"] == -32009
            assert "not supported" in body["error"]["message"]
    finally:
        bridge.stop()


def test_a2a_version_negotiation_on_card_get(fx):
    """Version negotiation applies to the AgentCard GET too."""
    fx.client.set_agent_card(["audit"], ALICE, ALICE_PASSWORD)
    bridge, port = _make_bridge(fx)
    try:
        with _get(port, "/.well-known/agent-card.json", version="1.0") as resp:
            card = json.loads(resp.read().decode("utf-8"))
        assert card["name"] == ALICE
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(port, "/.well-known/agent-card.json", version="9.0")
        assert exc.value.code == 400
        body = json.loads(exc.value.read().decode("utf-8"))
        assert body["error"]["code"] == -32009
    finally:
        bridge.stop()


def test_a2a_version_absent_defaults_to_supported(fx):
    """An absent A2A-Version header (a pre-negotiation client) is served at
    the bridge's protocol version — backward compatible, no regression."""
    bridge, port = _make_bridge(fx)
    try:
        result = _rpc(port, "tasks/list", {})
        assert "error" not in result
        # legacy card path still works without a version header
        with _get(port, "/.well-known/agent.json") as resp:
            assert json.loads(resp.read().decode("utf-8"))["name"] == ALICE
    finally:
        bridge.stop()


def test_tasks_message_creates_task(fx):
    bridge, port = _make_bridge(fx)
    try:
        result = _rpc(port, "tasks/message",
                      {"message": {"parts": [{"text": "Monthly report"}],
                                   "metadata": {"assignee": BOB}}})
        assert "error" not in result
        assert result["result"]["status"]["state"] == "SUBMITTED"
        # the task is visible on the Synapse side
        task = fx.client.get_task(result["result"]["taskId"], ALICE, ALICE_PASSWORD)
        assert task["title"] == "Monthly report"
    finally:
        bridge.stop()


def test_tasks_get_and_list(fx):
    tid = fx.client.create_task("A2A get", BOB, ALICE, ALICE_PASSWORD)["task_id"]
    bridge, port = _make_bridge(fx)
    try:
        got = _rpc(port, "tasks/get", {"id": tid})
        assert got["result"]["taskId"] == tid
        listing = _rpc(port, "tasks/list", {})
        ids = [t["taskId"] for t in listing["result"]["tasks"]]
        assert tid in ids
    finally:
        bridge.stop()


def test_tasks_cancel(fx):
    tid = fx.client.create_task("A2A cancel", BOB, ALICE, ALICE_PASSWORD)["task_id"]
    bridge, port = _make_bridge(fx)
    try:
        result = _rpc(port, "tasks/cancel", {"id": tid})
        assert result["result"]["status"]["state"] == "CANCELED"
    finally:
        bridge.stop()


def test_unknown_method_and_invalid_json(fx):
    bridge, port = _make_bridge(fx)
    try:
        result = _rpc(port, "inconnue", {})
        assert result["error"]["code"] == -32601
        # invalid JSON -> 400 with a JSON-RPC error
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/message", data=b"{pas du json",
            headers={"Content-Type": "application/json", "X-Synapse-Token": TOKEN},
        )
        try:
            urllib.request.urlopen(req)
            raise AssertionError("must fail")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        bridge.stop()


def test_errors_mapped_from_synapse(fx):
    bridge, port = _make_bridge(fx)
    try:
        result = _rpc(port, "tasks/get", {"id": "11111111-1111-4111-8111-111111111111"})
        assert result["error"]["code"] == -32000
    finally:
        bridge.stop()


def test_agent_card_unavailable_503(fx):
    """Card unavailable (invalid credentials) -> 503 without internal trace."""
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(fx.config, ALICE, "wrong-password", port=0, token=TOKEN)
    bridge.start()
    try:
        assert bridge._server is not None
        port = bridge._server.server_address[1]
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(port, "/.well-known/agent.json")
        assert exc.value.code == 503
    finally:
        bridge.stop()


def test_post_wrong_path_404(fx):
    bridge, port = _make_bridge(fx)
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tasks/list",
                           "params": {}}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/autre", data=body,
                                     headers={"X-Synapse-Token": TOKEN})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 404
    finally:
        bridge.stop()


def test_dispatch_rejects_non_jsonrpc(fx):
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(fx.config, ALICE, ALICE_PASSWORD, port=0)
    try:
        result = bridge.dispatch({"method": "tasks/list"})  # sans jsonrpc 2.0
        assert result["error"]["code"] == -32600
    finally:
        bridge.stop()


def test_get_card_without_token_allowed_for_discovery(fx):
    """Discover-before-talk (A2A §6.9/§8.2): the public well-known card is
    served WITHOUT a token, so a client can learn the auth scheme before
    presenting it. Requiring the token to fetch the card would be circular."""
    fx.client.set_agent_card(["audit"], ALICE, ALICE_PASSWORD)
    bridge, port = _make_bridge(fx)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/.well-known/agent.json")
        with urllib.request.urlopen(req) as resp:
            card = json.loads(resp.read().decode("utf-8"))
        assert card["name"] == ALICE
        assert any(s["id"] == "audit" for s in card["skills"])
        # the v1.0 well-known path is public too
        req = urllib.request.Request(f"http://127.0.0.1:{port}/.well-known/agent-card.json")
        with urllib.request.urlopen(req) as resp:
            assert json.loads(resp.read().decode("utf-8"))["name"] == ALICE
    finally:
        bridge.stop()


def test_get_non_card_path_still_requires_token(fx):
    """P0-2 must not over-broaden anonymous access: only the well-known card
    is public; every other GET path still requires the token."""
    bridge, port = _make_bridge(fx)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/autre")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 401
    finally:
        bridge.stop()


def test_get_wrong_path_404(fx):
    """A GET on an unknown path returns 404 (not the card)."""
    bridge, port = _make_bridge(fx)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/autre", headers={"X-Synapse-Token": TOKEN})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 404
    finally:
        bridge.stop()


def test_post_without_token_401(fx):
    """The task endpoint refuses a POST without a valid token."""
    bridge, port = _make_bridge(fx)
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tasks/list",
                           "params": {}}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/message", data=body,
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 401
    finally:
        bridge.stop()


def test_start_without_token_raises(fx):
    """Starting a bridge without an access token is refused (ValueError)."""
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(fx.config, ALICE, ALICE_PASSWORD, port=0)
    try:
        with pytest.raises(ValueError):
            bridge.start()
    finally:
        bridge.stop()
