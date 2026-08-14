"""Cross-framework A2A client adapter (SPEC.txt F20).

This module is the client-side half of Synapse's A2A bridge: a reusable,
stdlib-only adapter that lets an *external* agent (Claude Code, Codex,
OpenCode, any JSON-RPC over HTTP client) talk to a Synapse agent exposed
through ``synapse-a2a-bridge`` (see :mod:`synapse.a2a_bridge`).

It implements the Linux Foundation A2A v1.0 flow end to end:

1. ``discover()``  — GET ``/.well-known/agent-card.json`` and parse the
   AgentCard v1.0 manifest (discover-before-talk: learn the agent's
   skills, interface and protocol version before sending anything).
2. ``delegate()``  — POST ``tasks/message`` to hand a task to the agent.
3. ``get()`` / ``list()`` / ``cancel()`` — track and cancel tasks.

The bridge is token-protected (any request without a valid token is
refused): this adapter always sends the ``X-Synapse-Token`` header it was
given, and declares its protocol version via ``A2A-Version`` on every
request (A2A §3.6 negotiation). Nothing here touches the local Unix
socket — the adapter only needs the bridge's HTTP endpoint, which is what
makes it usable from another process, another framework, or even another
host behind a tunnel.

Documented limits (mirroring the bridge): plain-text tasks only; no push
notifications (poll via ``list``/``get``); one agent per bridge.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

# Anti-abuse mirror of the bridge's bound: refuse to read a body bigger
# than the 1 MiB API limit.
_MAX_BODY_BYTES = 16 * 1024 * 1024

# AgentCard v1.0 well-known URI (RFC 8615 style) served by the bridge.
_WELL_KNOWN_CARD = "/.well-known/agent-card.json"

# A2A protocol version this client speaks, sent as the ``A2A-Version``
# header on every request (A2A §3.6: clients MUST declare the version they
# use, so agents can negotiate and stay compatible across upgrades).
_A2A_PROTOCOL_VERSION = "1.0"
_A2A_VERSION_HEADER = "A2A-Version"


class A2AClientError(Exception):
    """An A2A interaction failed at the transport or protocol level.

    ``code`` carries the JSON-RPC error code (e.g. ``-32601`` unknown
    method, ``-32000`` the underlying Synapse API error) or the HTTP
    status (``401`` refused, ``404`` unknown path, ``503`` agent
    unavailable).
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class A2AClient:
    """Client of an A2A bridge: discover, delegate, track, cancel.

    Args:
        base_url: bridge root, e.g. ``http://127.0.0.1:8090``.
        token: the bridge access token sent as ``X-Synapse-Token``.
        timeout: HTTP timeout in seconds.
    """

    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0,
                 hlc_provider: Any = None, hlc_observer: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # Causal time (C1, DESIGN_CAUSAL_TIME_HLC_v2 §5 M1): Synapse
        # extension hooks. ``hlc_provider`` supplies the sender's causal
        # stamp for the outbound envelope (server-stamped: it is the
        # sender instance's clock); ``hlc_observer`` absorbs the remote
        # hlc of every response (the merge rule) BEFORE the caller sees
        # the result. Both default to None: non-Synapse peers neither
        # send nor read the extension field (ignored per the design).
        self.hlc_provider = hlc_provider
        self.hlc_observer = hlc_observer

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(self) -> dict:
        """Fetches and returns the AgentCard v1.0 manifest (discover-before-talk).

        Raises:
            A2AClientError: 401 refused, 503 agent unavailable, or the
                response is not valid JSON.
        """
        data = self._get(_WELL_KNOWN_CARD)
        try:
            card = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise A2AClientError(-32700, "invalid AgentCard JSON") from exc
        if not isinstance(card, dict):
            raise A2AClientError(-32600, "invalid AgentCard: not an object")
        return card

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    def delegate(self, text: str, *, assignee: str | None = None) -> dict:
        """Hands a task to the agent (``tasks/message``).

        Args:
            text: the task description (the first message part's text).
            assignee: optional ``metadata.assignee`` designating the
                executing agent; defaults to the bridge's agent.

        Returns:
            The JSON-RPC ``result`` dict (``taskId`` + ``status``).
        """
        metadata: dict[str, str] = {}
        if assignee is not None:
            metadata["assignee"] = assignee
        if self.hlc_provider is not None:
            metadata["hlc"] = self.hlc_provider()
        return self._call("tasks/message", {
            "message": {"parts": [{"text": text}], "metadata": metadata},
        })

    def get(self, task_id: str) -> dict:
        """Reads a task (``tasks/get``). Returns the JSON-RPC result dict."""
        return self._call("tasks/get", {"id": task_id})

    def list(self) -> list[dict]:
        """Lists the agent's tasks (``tasks/list``). Returns the task list."""
        result = self._call("tasks/list", {})
        return list(result.get("tasks") or [])

    def cancel(self, task_id: str) -> dict:
        """Cancels a task (``tasks/cancel``). Returns the JSON-RPC result dict."""
        return self._call("tasks/cancel", {"id": task_id})

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _call(self, method: str, params: dict, *, rpc_id: int = 1) -> dict:
        """Sends a JSON-RPC 2.0 request and returns its ``result``.

        Raises:
            A2AClientError: transport errors (HTTP status) and JSON-RPC
                errors (negative codes) are both surfaced; the message is
                the server's where available.
        """
        body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method,
                           "params": params}).encode("utf-8")
        try:
            raw = self._post("/message", body)
        except A2AClientError:
            raise
        try:
            response = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise A2AClientError(-32700, "invalid JSON-RPC response") from exc
        if not isinstance(response, dict):
            raise A2AClientError(-32600, "invalid JSON-RPC response: not an object")
        error = response.get("error")
        if error is not None:
            raise A2AClientError(
                error.get("code", -32603),
                error.get("message", "A2A error"),
            )
        result = response.get("result") or {}
        # Merge rule: absorb the remote hlc BEFORE returning, so the
        # caller's next local stamp is causally after the remote point
        # (DESIGN §5 M1; no-op for non-Synapse peers).
        remote_hlc = result.get("hlc")
        if remote_hlc is not None and self.hlc_observer is not None:
            self.hlc_observer(remote_hlc)
        return result

    def _request(self, method: str, path: str, body: bytes | None = None) -> bytes:
        headers = {"X-Synapse-Token": self.token,
                   _A2A_VERSION_HEADER: _A2A_PROTOCOL_VERSION}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=body,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read(_MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise A2AClientError(exc.code, exc.reason or "HTTP error") from exc
        except urllib.error.URLError as exc:
            raise A2AClientError(0, f"bridge unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise A2AClientError(0, "bridge timed out") from exc

    def _get(self, path: str) -> bytes:
        return self._request("GET", path)

    def _post(self, path: str, body: bytes) -> bytes:
        return self._request("POST", path, body)
