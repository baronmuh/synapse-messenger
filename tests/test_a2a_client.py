"""F20 — A2A client adapter: reusable cross-framework client of the A2A bridge.

The client is the *other half* of the bridge (server side, see
test_a2a_bridge.py): an external agent uses :class:`A2AClient` to
discover a Synapse agent's AgentCard v1.0 and delegate/track/cancel
tasks over the A2A JSON-RPC protocol — discover-before-talk, exactly the
vocabulary the bridge advertises. Tasks created through the client are
verified server-side via the socket client (independent proof).
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from .conftest import ALICE, ALICE_PASSWORD, BOB
from .test_a2a_bridge import TOKEN, _make_bridge

TOKEN_2 = "jeton-de-test-a2a-client-2"


def _client(port: int, token: str = TOKEN):
    from synapse.a2a_client import A2AClient

    return A2AClient(f"http://127.0.0.1:{port}", token)


def test_discover_returns_agent_card(fx):
    """discover() fetches and parses the AgentCard v1.0 manifest."""
    fx.client.set_agent_card(["comptabilite", "audit"], ALICE, ALICE_PASSWORD)
    bridge, port = _make_bridge(fx)
    try:
        card = _client(port).discover()
        assert card["name"] == ALICE
        assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
        assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
        assert card["provider"]["organization"] == "root_org"
        assert {s["id"] for s in card["skills"]} == {"comptabilite", "audit"}
    finally:
        bridge.stop()


def test_discover_requires_no_token(fx):
    """Discover-before-talk (A2A §6.9/§8.2): discover() fetches the public
    AgentCard with NO token — discovery is anonymous, and a wrong token no
    longer defeats it (the card must be readable before the credential can
    be presented)."""
    from synapse.a2a_client import A2AClientError

    fx.client.set_agent_card(["audit"], ALICE, ALICE_PASSWORD)
    bridge, port = _make_bridge(fx)
    try:
        # no token at all
        assert _client(port, token="").discover()["name"] == ALICE
        # a wrong token no longer blocks discovery either
        assert _client(port, token="mauvais-jeton").discover()["name"] == ALICE
        # but a task operation still refuses a wrong token (401)
        with pytest.raises(A2AClientError) as exc:
            _client(port, token="mauvais-jeton").delegate("test")
        assert exc.value.code == 401
    finally:
        bridge.stop()


def test_delegate_creates_task_server_side(fx):
    """delegate() hands a task to the agent, verified via the socket client."""
    bridge, port = _make_bridge(fx)
    try:
        result = _client(port).delegate("Monthly report", assignee=BOB)
        assert result["status"]["state"] == "SUBMITTED"
        task = fx.client.get_task(result["taskId"], ALICE, ALICE_PASSWORD)
        assert task["title"] == "Monthly report"
        assert task["assignee_username"] == BOB
    finally:
        bridge.stop()


def test_delegate_without_assignee_defaults_to_agent(fx):
    """Without ``assignee`` the bridge's agent is the executor."""
    bridge, port = _make_bridge(fx)
    try:
        result = _client(port).delegate("Comptabilité")
        task = fx.client.get_task(result["taskId"], ALICE, ALICE_PASSWORD)
        assert task["assignee_username"] == ALICE
    finally:
        bridge.stop()


def test_get_and_list_track_tasks(fx):
    """get()/list() track a task the client created."""
    bridge, port = _make_bridge(fx)
    client = _client(port)
    try:
        created = client.delegate("Suivre la facture")
        task_id = created["taskId"]
        got = client.get(task_id)
        assert got["taskId"] == task_id
        ids = [t["taskId"] for t in client.list()]
        assert task_id in ids
    finally:
        bridge.stop()


def test_cancel_updates_state(fx):
    """cancel() transitions the task to CANCELED, verified server-side."""
    bridge, port = _make_bridge(fx)
    client = _client(port)
    try:
        task_id = client.delegate("Annule moi")["taskId"]
        result = client.cancel(task_id)
        assert result["status"]["state"] == "CANCELED"
        task = fx.client.get_task(task_id, ALICE, ALICE_PASSWORD)
        assert task["state"] == "canceled"
    finally:
        bridge.stop()


def test_list_is_empty_without_tasks(fx):
    """list() on an agent with no tasks returns an empty list."""
    bridge, port = _make_bridge(fx)
    try:
        assert _client(port).list() == []
    finally:
        bridge.stop()


def test_synapse_error_mapped(fx):
    """A missing task surfaces as -32000 (the bridge's API error code)."""
    from synapse.a2a_client import A2AClientError

    bridge, port = _make_bridge(fx)
    try:
        with pytest.raises(A2AClientError) as exc:
            _client(port).get("11111111-1111-4111-8111-111111111111")
        assert exc.value.code == -32000
    finally:
        bridge.stop()


def test_unknown_method_mapped(fx):
    """An unknown JSON-RPC method surfaces as -32601 (method not found)."""
    from synapse.a2a_client import A2AClientError

    bridge, port = _make_bridge(fx)
    try:
        with pytest.raises(A2AClientError) as exc:
            _client(port)._call("tasks/explose", {})
        assert exc.value.code == -32601
    finally:
        bridge.stop()


def test_bridge_unreachable_raises(fx):
    """A bridge that is not listening raises a transport error."""
    from synapse.a2a_client import A2AClientError

    with pytest.raises(A2AClientError) as exc:
        _client(1).discover()  # nothing on port 1
    assert exc.value.code == 0


def test_discover_invalid_json_raises(fx):
    """A non-JSON AgentCard response surfaces as -32700."""
    from unittest import mock

    from synapse.a2a_client import A2AClientError

    client = _client(1)
    with mock.patch.object(client, "_get", return_value=b"pas du json"):
        with pytest.raises(A2AClientError) as exc:
            client.discover()
        assert exc.value.code == -32700


def test_discover_non_object_raises(fx):
    """A JSON AgentCard that is not an object surfaces as -32600."""
    from unittest import mock

    from synapse.a2a_client import A2AClientError

    client = _client(1)
    with mock.patch.object(client, "_get", return_value=b"[1,2,3]"):
        with pytest.raises(A2AClientError) as exc:
            client.discover()
        assert exc.value.code == -32600


def test_http_error_surfaces_code(fx):
    """A non-JSON-RPC error (404 on a wrong path) surfaces the HTTP code."""
    from synapse.a2a_client import A2AClientError

    bridge, port = _make_bridge(fx)
    try:
        client = _client(port)
        with pytest.raises(A2AClientError) as exc:
            client._get("/autre")
        assert exc.value.code == 404
    finally:
        bridge.stop()


def test_bad_rpc_response_raises(fx):
    """A non-object JSON-RPC response surfaces as -32600."""
    from unittest import mock

    from synapse.a2a_client import A2AClientError

    client = _client(1)
    with mock.patch.object(client, "_post", return_value=b"[1]"):
        with pytest.raises(A2AClientError) as exc:
            client._call("tasks/list", {})
        assert exc.value.code == -32600


def test_unparseable_rpc_response_raises(fx):
    """A non-JSON JSON-RPC response surfaces as -32700."""
    from unittest import mock

    from synapse.a2a_client import A2AClientError

    client = _client(1)
    with mock.patch.object(client, "_post", return_value=b"pas du json"):
        with pytest.raises(A2AClientError) as exc:
            client._call("tasks/list", {})
        assert exc.value.code == -32700


def test_transport_error_propagates_through_call(fx):
    """An A2AClientError raised by the transport is re-raised by ``_call``."""
    from unittest import mock

    from synapse.a2a_client import A2AClientError

    client = _client(1)
    with mock.patch.object(client, "_post",
                           side_effect=A2AClientError(401, "refused")):
        with pytest.raises(A2AClientError) as exc:
            client._call("tasks/list", {})
        assert exc.value.code == 401


def test_timeout_raises(fx):
    """A timed-out bridge surfaces as a transport error (code 0)."""
    from unittest import mock

    from synapse.a2a_client import A2AClientError

    client = _client(1)
    with mock.patch.object(urllib.request, "urlopen",
                           side_effect=TimeoutError("too slow")):
        with pytest.raises(A2AClientError) as exc:
            client.discover()
        assert exc.value.code == 0


def test_client_sends_a2a_version_header(fx):
    """The client declares its A2A protocol version on every request via
    the ``A2A-Version`` header (A2A §3.6.1: clients MUST send it)."""
    from unittest import mock

    from synapse.a2a_client import A2AClientError

    client = _client(1)
    seen = {}

    def _fake_urlopen(req, timeout=10.0):  # noqa: A002 (matches urllib sig)
        seen["headers"] = dict(req.header_items())
        raise urllib.error.URLError("stop")

    with mock.patch.object(urllib.request, "urlopen",
                           side_effect=_fake_urlopen):
        with pytest.raises(A2AClientError):
            client.discover()
    # urllib lowercases header names; HTTP header names are case-insensitive
    lowered = {k.lower(): v for k, v in seen["headers"].items()}
    assert lowered["a2a-version"] == "1.0"
