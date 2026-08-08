"""F20 — A2A bridge: agent card in A2A format and JSON-RPC translation of
task operations.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD

TOKEN = "jeton-de-test-a2a-123456"


def _rpc(port: int, method: str, params: dict, rpc_id: int = 1) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method,
                       "params": params}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/message", data=body,
        headers={"Content-Type": "application/json", "X-Synapse-Token": TOKEN},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(port: int, path: str):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"X-Synapse-Token": TOKEN},
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
        assert {s["id"] for s in card["skills"]} == {"comptabilite", "audit"}
        assert card["organization"] == "root_org"
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
