"""A2A gateway (SPEC.txt F20): local bridge to the Linux Foundation's A2A
protocol, limited to 127.0.0.1.

The gateway is a Synapse client like any other: it authenticates on
the socket with an agent's credentials, exposes its card (A2A agent card)
on ``/.well-known/agent.json`` and translates the JSON-RPC 2.0 calls of the
A2A protocol (``tasks/message``, ``tasks/get``, ``tasks/list``,
``tasks/cancel``) into Synapse commands (create_task, get_task,
list_tasks, update_task_state).

Documented limits: no push notifications (SSE) — A2A clients poll
via ``tasks/get`` / ``tasks/list``; one agent per gateway.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .client import ApiClientError, Client

# Anti-abuse bound on incoming JSON-RPC bodies (consistent with the limit
# of 1 MiB of the main API, SPEC.txt §2).
_MAX_BRIDGE_REQUEST_BYTES = 1024 * 1024

logger = logging.getLogger("synapse.a2a")


def _a2a_error(code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}


class _Handler(BaseHTTPRequestHandler):
    bridge: "A2ABridge"

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_token():
            return
        if self.path == "/.well-known/agent.json":
            try:
                card = self.bridge.agent_card()
                self._send(200, "application/json",
                           json.dumps(card, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # never expose an internal traceback
                logger.warning("A2A card unavailable: %s", exc)
                self._send(503, "application/json", b'{"error":"agent unavailable"}')
        else:
            self._send(404, "text/plain", b"404")

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_token():
            return
        if self.path != "/message":
            self._send(404, "text/plain", b"404")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > _MAX_BRIDGE_REQUEST_BYTES:
                # Anti-abuse bound: an oversized body must not be able to
                # exhaust the process memory (consistent with the limit
                # of 1 MiB of the main API).
                self._send(413, "application/json",
                           json.dumps(_a2a_error(-32600, "Request too large")).encode("utf-8"))
                return
            raw = self.rfile.read(length)
            request = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send(400, "application/json",
                       json.dumps(_a2a_error(-32700, "Invalid JSON")).encode("utf-8"))
            return
        response = self.bridge.dispatch(request)
        self._send(200, "application/json",
                   json.dumps(response, ensure_ascii=False).encode("utf-8"))

    def _check_token(self) -> bool:
        """Requires the bridge access token (constant-time comparison).

        The bridge acts on behalf of an agent (creation/cancellation of
        tasks): like the Unix socket, it is only reachable by
        holders of the secret. The token is never logged.
        """
        import hmac

        provided = self.headers.get("X-Synapse-Token", "")
        expected = self.bridge.token
        if expected is None or not hmac.compare_digest(provided.encode("utf-8"),
                                                       expected.encode("utf-8")):
            logger.warning("access denied: missing or invalid token")
            self._send(401, "text/plain", b"401")
            return False
        return True

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.info("a2a %s", format % args)


class A2ABridge:
    """Local A2A bridge: a Synapse agent exposed in the A2A format."""

    def __init__(self, config: Any, agent_name: str, agent_password: str,
                 port: int = 8090, *, token: str | None = None) -> None:
        self._config = config
        self._agent_name = agent_name
        self._agent_password = agent_password
        self._port = port
        # Bridge access token: mandatory (any request without a valid
        # token is refused). Never logged.
        self.token = token
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _client(self) -> Client:
        return Client.from_config(self._config)

    def agent_card(self) -> dict:
        client = self._client()
        my = client.get_my_organization(self._agent_name, self._agent_password)
        card = client.get_agent_card(self._agent_name, self._agent_name,
                                     self._agent_password)
        skills = []
        for capability in card.get("capabilities") or []:
            skills.append({"id": capability, "name": capability})
        return {
            "name": self._agent_name,
            "description": my.get("description") or card.get("domain") or "",
            "url": f"http://127.0.0.1:{self._port}/",
            "skills": skills,
            "organization": my.get("organization_name"),
        }

    def dispatch(self, request: dict) -> dict:
        if request.get("jsonrpc") != "2.0" or "method" not in request:
            return _a2a_error(-32600, "Invalid JSON-RPC 2.0 request")
        method = request["method"]
        params = request.get("params") or {}
        rpc_id = request.get("id")
        try:
            if method == "tasks/message":
                result = self._tasks_message(params)
            elif method == "tasks/get":
                result = self._tasks_get(params)
            elif method == "tasks/list":
                result = self._tasks_list(params)
            elif method == "tasks/cancel":
                result = self._tasks_cancel(params)
            else:
                return {"jsonrpc": "2.0", "id": rpc_id,
                        "error": {"code": -32601, "message": f"Unknown method: {method}"}}
        except ApiClientError as exc:
            return {"jsonrpc": "2.0", "id": rpc_id,
                    "error": {"code": -32000, "message": exc.message or exc.code}}
        except Exception as exc:  # pragma: no cover - safety net
            logger.warning("A2A %s failed: %s", method, exc)
            return {"jsonrpc": "2.0", "id": rpc_id,
                    "error": {"code": -32603, "message": "Internal error"}}
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    def _tasks_message(self, params: dict) -> dict:
        message = params.get("message") or {}
        text = (message.get("parts") or [{}])[0].get("text", "")
        assignee = (message.get("metadata") or {}).get("assignee") or self._agent_name
        client = self._client()
        task = client.create_task(
            text or "A2A task",
            assignee,
            self._agent_name,
            self._agent_password,
        )
        return {
            "taskId": task["task_id"],
            "status": {"state": task["state"].upper()},
        }

    def _tasks_get(self, params: dict) -> dict:
        client = self._client()
        task = client.get_task(params.get("id", ""), self._agent_name, self._agent_password)
        return {"taskId": task["task_id"], "status": {"state": task["state"].upper()},
                "title": task.get("title"), "result": task.get("result")}

    def _tasks_list(self, params: dict) -> dict:
        client = self._client()
        result = client.list_tasks(self._agent_name, self._agent_password)
        return {"tasks": [
            {"taskId": t["task_id"], "status": {"state": t["state"].upper()},
             "title": t.get("title")}
            for t in result["tasks"]
        ]}

    def _tasks_cancel(self, params: dict) -> dict:
        client = self._client()
        task = client.update_task_state(
            params.get("id", ""), "canceled", self._agent_name, self._agent_password,
        )
        return {"taskId": task["task_id"], "status": {"state": task["state"].upper()}}

    def start(self) -> None:
        if not self.token:
            raise ValueError("The A2A bridge requires an access token (token parameter)")
        _Handler.bridge = self
        self._server = ThreadingHTTPServer(("127.0.0.1", self._port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("A2A bridge at http://127.0.0.1:%d (agent %s)",
                    self._port, self._agent_name)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def run_bridge(config: Any, agent_name: str, agent_password: str, port: int = 8090,
               token: str | None = None) -> A2ABridge:  # pragma: no cover
    """CLI entry point: starts the bridge and blocks (Ctrl-C to
    stop)."""
    bridge = A2ABridge(config, agent_name, agent_password, port, token=token)
    bridge.start()
    try:
        import time

        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        bridge.stop()
    return bridge


def bridge_main() -> None:  # pragma: no cover
    """CLI ``synapse-a2a-bridge``: exposes a Synapse agent in the A2A format
    on 127.0.0.1. The agent password is read via ``--password-stdin``
    or ``getpass`` (never in clear text on the command line)."""
    import argparse
    import getpass
    import json
    import sys

    from .config import Config

    parser = argparse.ArgumentParser(
        description="Local A2A bridge (127.0.0.1) for a Synapse agent"
    )
    parser.add_argument("--config", required=True, help="JSON configuration file")
    parser.add_argument("--agent-name", required=True, help="exposed agent")
    parser.add_argument("--password-stdin", action="store_true",
                        help="read the password from standard input")
    parser.add_argument("--token-stdin", action="store_true",
                        help="read the bridge access token from standard input")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as fh:
        config = Config.from_dict(json.load(fh))
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Agent password: ")
    if args.token_stdin:
        token = sys.stdin.readline().rstrip("\n")
    else:
        token = getpass.getpass("Bridge access token: ")
    if not token:
        print("synapse-a2a-bridge: an access token is required", file=sys.stderr)
        sys.exit(1)
    run_bridge(config, args.agent_name, password, args.port, token=token)
