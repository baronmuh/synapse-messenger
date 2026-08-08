"""A2A validation bench: end-to-end interoperability with a real external
client (SPEC.txt F20).

An independent HTTP client (stdlib only, no project code) speaks the A2A
protocol to the ``synapse a2a start`` bridge launched by the CLI: agent
card discovery (/.well-known/agent.json), task delegation (tasks/message),
tracking (tasks/get, tasks/list), cancellation (tasks/cancel), and
rejection of illegitimate requests (401 without token, -32700 invalid
JSON, -32601 unknown method, 413 oversized body). Created tasks are
verified SERVER-SIDE via ``synapse task status``: the proof does not rely
on the bridge's self-reporting.
"""

from __future__ import annotations

import http.client
import json

from tests.cli_helpers import run_cli

TOKEN = "jeton-interop-a2a-1"
PORT = 18097


def _bootstrap(env):
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    proc = run_cli(env, "server", "start")
    assert proc.returncode == 0, proc.stderr.decode()
    proc = run_cli(env, "agent", "create", "data", "--password-stdin",
                   stdin="motdepasse-data-1\n")
    assert proc.returncode == 0, proc.stderr.decode()
    # a real capability to verify the card on the client side
    proc = run_cli(env, "agent", "card", "data", "--set", "--capability", "audit",
                   "--my-name", "data", "--password-stdin",
                   stdin="motdepasse-data-1\n")
    assert proc.returncode == 0, proc.stderr.decode()


def _client(port: int) -> http.client.HTTPConnection:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    return conn


def _rpc(conn, method: str, params: dict, rpc_id=1) -> dict:
    """JSON-RPC 2.0 call via the bridge's POST /message."""
    body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method,
                       "params": params})
    conn.request("POST", "/message", body=body.encode("utf-8"),
                 headers={"Content-Type": "application/json",
                          "X-Synapse-Token": TOKEN})
    resp = conn.getresponse()
    assert resp.status == 200, resp.status
    return json.loads(resp.read().decode("utf-8"))


def test_a2a_external_client_full_cycle(cli_env):
    """Discovery → delegation → tracking → cancellation, verified server-side."""
    _, _, env = cli_env
    _bootstrap(env)
    proc = run_cli(env, "a2a", "start", "--agent-name", "data",
                   "--port", str(PORT), "--password-stdin", "--token-stdin",
                   stdin=f"motdepasse-data-1\n{TOKEN}\n")
    assert proc.returncode == 0, proc.stderr.decode()
    conn = _client(PORT)
    try:
        # --- discovery: public agent card (with token) ---
        conn.request("GET", "/.well-known/agent.json",
                     headers={"X-Synapse-Token": TOKEN})
        resp = conn.getresponse()
        assert resp.status == 200
        card = json.loads(resp.read().decode("utf-8"))
        assert card["name"] == "data"
        assert card["organization"] == "acme"
        assert card["url"] == f"http://127.0.0.1:{PORT}/"
        assert {"id": "audit", "name": "audit"} in card["skills"]

        # --- discovery without token: 401 refusal ---
        conn.request("GET", "/.well-known/agent.json")
        assert conn.getresponse().status == 401

        # --- task delegation (tasks/message) ---
        result = _rpc(conn, "tasks/message", {
            "message": {"parts": [{"text": "Rapport mensuel"}],
                        "metadata": {"assignee": "data"}},
        })
        assert result.get("error") is None, result
        task_id = result["result"]["taskId"]
        assert result["result"]["status"]["state"] == "SUBMITTED"

        # --- SERVER-SIDE verification via the CLI (independent proof) ---
        proc = run_cli(env, "task", "status", task_id, "--json",
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        assert proc.returncode == 0, proc.stderr.decode()
        task = json.loads(proc.stdout.decode())["data"]
        assert task["task_id"] == task_id
        assert task["state"] == "submitted"
        assert task["assignee_username"] == "data"

        # --- tracking: tasks/get and tasks/list ---
        result = _rpc(conn, "tasks/get", {"id": task_id}, rpc_id=2)
        assert result["result"]["title"] == "Rapport mensuel"
        assert result["result"]["status"]["state"] == "SUBMITTED"

        result = _rpc(conn, "tasks/list", {}, rpc_id=3)
        ids = [t["taskId"] for t in result["result"]["tasks"]]
        assert task_id in ids

        # --- cancellation (tasks/cancel), verified server-side ---
        result = _rpc(conn, "tasks/cancel", {"id": task_id}, rpc_id=4)
        assert result["result"]["status"]["state"] == "CANCELED"
        proc = run_cli(env, "task", "status", task_id, "--json",
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        assert json.loads(proc.stdout.decode())["data"]["state"] == "canceled"
    finally:
        conn.close()
        run_cli(env, "a2a", "stop")
        run_cli(env, "server", "stop")


def test_a2a_external_client_rejects_invalid(cli_env):
    """Protocol rejections: invalid JSON, unknown method, giant body."""
    _, _, env = cli_env
    _bootstrap(env)
    run_cli(env, "a2a", "start", "--agent-name", "data",
            "--port", str(PORT), "--password-stdin", "--token-stdin",
            stdin=f"motdepasse-data-1\n{TOKEN}\n")
    conn = _client(PORT)
    try:
        # invalid JSON → -32700 (Parse error)
        conn.request("POST", "/message", body=b"pas du json",
                     headers={"Content-Type": "application/json",
                              "X-Synapse-Token": TOKEN})
        resp = conn.getresponse()
        assert resp.status == 400
        assert json.loads(resp.read())["error"]["code"] == -32700

        # unknown method → -32601 (Method not found)
        result = _rpc(conn, "tasks/explose", {})
        assert result["error"]["code"] == -32601

        # oversized body → 413 (1 MiB anti-abuse bound)
        big = b'{"jsonrpc": "2.0", "id": 1, "method": "tasks/list", "params": {"pad": "' \
            + b"x" * (1024 * 1024 + 1) + b'"}}'
        conn.request("POST", "/message", body=big,
                     headers={"Content-Type": "application/json",
                              "X-Synapse-Token": TOKEN})
        assert conn.getresponse().status == 413

        # wrong token → 401
        conn.request("POST", "/message",
                     body=b'{"jsonrpc":"2.0","id":1,"method":"tasks/list","params":{}}',
                     headers={"Content-Type": "application/json",
                              "X-Synapse-Token": "mauvais-jeton"})
        assert conn.getresponse().status == 401
    finally:
        conn.close()
        run_cli(env, "a2a", "stop")
        run_cli(env, "server", "stop")


def test_a2a_external_client_assignee_metadata(cli_env):
    """The ``assignee`` metadata of the A2A message designates the executing agent."""
    _, _, env = cli_env
    _bootstrap(env)
    # second agent for cross delegation
    proc = run_cli(env, "agent", "create", "comptable", "--password-stdin",
                   stdin="motdepasse-comptable-1\n")
    assert proc.returncode == 0, proc.stderr.decode()
    run_cli(env, "a2a", "start", "--agent-name", "data",
            "--port", str(PORT), "--password-stdin", "--token-stdin",
            stdin=f"motdepasse-data-1\n{TOKEN}\n")
    conn = _client(PORT)
    try:
        result = _rpc(conn, "tasks/message", {
            "message": {"parts": [{"text": "Close"}],
                        "metadata": {"assignee": "comptable"}},
        })
        assert result.get("error") is None, result
        task_id = result["result"]["taskId"]
        proc = run_cli(env, "task", "status", task_id, "--json",
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        task = json.loads(proc.stdout.decode())["data"]
        assert task["assignee_username"] == "comptable"
        assert task["creator_username"] == "data"  # the bridge acts on behalf of the agent
    finally:
        conn.close()
        run_cli(env, "a2a", "stop")
        run_cli(env, "server", "stop")
