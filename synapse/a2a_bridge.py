"""A2A gateway (SPEC.txt F20): local bridge to the Linux Foundation's A2A
protocol, limited to 127.0.0.1.

The gateway is a Synapse client like any other: it authenticates on
the socket with an agent's credentials, exposes its card (an A2A
AgentCard v1.0 manifest) on ``/.well-known/agent-card.json`` (the
legacy ``/.well-known/agent.json`` alias is kept for compatibility) and
translates the JSON-RPC 2.0 calls of the A2A protocol (``tasks/message``,
``tasks/get``, ``tasks/list``, ``tasks/cancel``) into Synapse commands
(create_task, get_task, list_tasks, update_task_state). The AgentCard
v1.0 vocabulary lets clients discover the agent's skills, interface and
protocol version before talking to it ("discover-before-talk"); the
well-known card is served WITHOUT authentication for that discovery, while
all task operations remain token-protected. The ``securitySchemes`` map
(name->scheme object) declares the ApiKey scheme behind which the bridge's
``X-Synapse-Token`` credential is presented, and ``securityRequirements``
lists it as required. A2A version
negotiation (the ``A2A-Version`` header, A2A §3.6) lets a compliant client
declare the protocol version it wants; the bridge announces its version
on every response and refuses an unsupported Major.Minor with a
VersionNotSupportedError (-32009).

Documented limits: no push notifications (SSE) — A2A clients poll
via ``tasks/get`` / ``tasks/list``; one agent per gateway.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .client import ApiClientError, Client
from .registry import Registry, RegistryError
from .version import project_version

# Anti-abuse bound on incoming JSON-RPC bodies (consistent with the limit
# of 1 MiB of the main API, SPEC.txt §2).
_MAX_BRIDGE_REQUEST_BYTES = 1024 * 1024

# AgentCard v1.0 (Linux Foundation A2A): the JSON vocabulary the bridge
# advertises so clients discover capabilities before talking
# ("discover-before-talk"). The protocol binding is JSON-RPC 2.0 over HTTP.
_A2A_PROTOCOL_VERSION = "1.0"
_A2A_PROTOCOL_BINDING = "JSONRPC"
# The A2A service parameter (HTTP header) by which a client declares the
# protocol version it wants on each request (A2A §3.2.5/§3.6). Values are
# "Major.Minor"; negotiation matches Major.Minor and ignores patch numbers.
_A2A_VERSION_HEADER = "A2A-Version"
# A2A §3.3.2: a request asks for a protocol version the agent does not
# support -> VersionNotSupportedError, JSON-RPC code -32009 (HTTP 400).
_A2A_VERSION_NOT_SUPPORTED = -32009
# The default media types (the bridge speaks plain text tasks only).
_A2A_INPUT_MODES = ["text/plain"]
_A2A_OUTPUT_MODES = ["text/plain"]
# The agent is protected by a token carried in the X-Synapse-Token header
# (see _Handler._check_token). Declared as an A2A v1.0 ApiKey SecurityScheme
# so a compliant client discovers where to present the credential before
# talking (discover-before-talk). A2A v1.0 models AgentCard.securitySchemes
# as a map<string, SecurityScheme>, so this MUST be an OBJECT keyed by
# scheme name (the official SDK does ``for scheme in data['securitySchemes']
# .values()`` — a LIST is dropped / not iterable). Each value is the v1.0
# discriminated union member ``apiKeySecurityScheme`` with its ``name`` and
# ``location`` (query/header/cookie) fields.
_A2A_SECURITY_SCHEMES = {
    "apiKey": {
        "apiKeySecurityScheme": {
            "name": "X-Synapse-Token",
            "location": "header",
        },
    },
}
# A2A v1.0: the agent REQUIRES the declared ApiKey scheme on every operation
# (a securityRequirements array listing the schemes that must be presented).
_A2A_SECURITY_REQUIREMENTS = [{
    "schemes": {"apiKey": []},
}]
# The A2A well-known URI of the AgentCard (RFC 8615 style). The legacy
# path (v0.3-era) is kept as an alias for backward compatibility.
_WELL_KNOWN_CARD = "/.well-known/agent-card.json"
_WELL_KNOWN_CARD_LEGACY = "/.well-known/agent.json"

# Synapse-defined registry API (scout_architecture REGISTRY_FEATURE_DESIGN
# t_68dcd793 R-F MVE): the open, governed A2A registry layered on the bridge.
# POST (token-protected write) registers a card by URL; GET (public anonymous,
# discover-before-talk) is the capability/tag search. The capability minting
# hook is a stub in the MVE (design grade R-D, post-MVE).
_REGISTRY_CARDS = "/v1/registry/cards"
_REGISTRY_MINT = "/v1/registry/capabilities/mint"

logger = logging.getLogger("synapse.a2a")


def _a2a_error(code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}


def _version_pair(value: str) -> tuple[int, int] | None:
    """Parses an A2A protocol version (``Major.Minor``) into a pair.

    Patch components are ignored: A2A §3.6 states patch versions MUST NOT
    be considered when negotiating protocol versions. Returns ``None`` for
    a value that cannot be parsed (nothing to negotiate against).
    """
    try:
        parts = value.strip().split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return None


def _version_compatible(requested: str, supported: str) -> bool:
    """True when a requested A2A version matches the supported one on
    Major.Minor (A2A §3.6: servers process using the requested version's
    semantics, matching Major.Minor)."""
    req = _version_pair(requested)
    sup = _version_pair(supported)
    if req is None or sup is None:
        return False
    return req == sup


class _Handler(BaseHTTPRequestHandler):
    bridge: "A2ABridge"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        is_card = path in (_WELL_KNOWN_CARD, _WELL_KNOWN_CARD_LEGACY)
        is_registry_search = path == _REGISTRY_CARDS
        # Discover-before-talk (A2A §6.9/§8.2): the well-known AgentCard is a
        # PUBLIC discovery surface — it must be fetchable WITHOUT the token so
        # a client can learn where to present its credential before talking
        # (requiring the token to fetch the card would be circular). The
        # registry capability search is likewise PUBLIC and anonymous (the
        # registry's whole point is that anyone can discover agents before
        # talking to them). Only other GET paths stay token-protected.
        # Version negotiation still applies to the card GET (a client declares
        # its A2A-Version).
        if not is_card and not is_registry_search and not self._check_token():
            return
        if not self._negotiate_version():
            return
        if is_card:
            try:
                card = self.bridge.agent_card()
                self._send(200, "application/json",
                           json.dumps(card, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # never expose an internal traceback
                logger.warning("A2A card unavailable: %s", exc)
                self._send(503, "application/json", b'{"error":"agent unavailable"}')
        elif is_registry_search:
            self._registry_search(parsed.query)
        else:
            self._send(404, "text/plain", b"404")

    def _registry_search(self, query: str) -> None:
        """GET /v1/registry/cards?capability=..&tag=.. — public anonymous
        capability/tag search over the registered cards."""
        params = urllib.parse.parse_qs(query)
        capability = params.get("capability", [None])[0] or None
        tag = params.get("tag", [None])[0] or None
        try:
            cards = self.bridge.registry.search(capability=capability, tag=tag)
        except Exception as exc:  # never expose an internal traceback
            logger.warning("A2A registry search failed: %s", exc)
            self._send(503, "application/json", b'{"error":"registry unavailable"}')
            return
        body = json.dumps({"cards": cards, "count": len(cards)},
                          ensure_ascii=False).encode("utf-8")
        self._send(200, "application/json", body)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not self._check_token():
            return
        if not self._negotiate_version():
            return
        if path == "/message":
            self._rpc_message()
        elif path == _REGISTRY_CARDS:
            self._registry_register()
        elif path == _REGISTRY_MINT:
            self._registry_mint()
        else:
            self._send(404, "text/plain", b"404")

    def _read_json_body(self) -> dict | None:
        """Reads and parses the request body, enforcing the anti-abuse bound.

        Returns ``None`` after sending a 400/413 response on bad input.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            self._send(400, "application/json",
                       json.dumps(_a2a_error(-32700, "Invalid Content-Length")).encode("utf-8"))
            return None
        if length < 0 or length > _MAX_BRIDGE_REQUEST_BYTES:
            self._send(413, "application/json",
                       json.dumps(_a2a_error(-32600, "Request too large")).encode("utf-8"))
            return None
        try:
            raw = self.rfile.read(length)
            request = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send(400, "application/json",
                       json.dumps(_a2a_error(-32700, "Invalid JSON")).encode("utf-8"))
            return None
        if not isinstance(request, dict):
            self._send(400, "application/json",
                       json.dumps(_a2a_error(-32600, "Invalid JSON body")).encode("utf-8"))
            return None
        return request

    def _rpc_message(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        response = self.bridge.dispatch(body)
        self._send(200, "application/json",
                   json.dumps(response, ensure_ascii=False).encode("utf-8"))

    def _registry_register(self) -> None:
        """POST /v1/registry/cards — register a card by URL (idempotent)."""
        body = self._read_json_body()
        if body is None:
            return
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            self._send(400, "application/json",
                       json.dumps(_a2a_error(-32602, "missing 'url'")).encode("utf-8"))
            return
        try:
            entry = self.bridge.registry.register(url.strip())
        except RegistryError as exc:
            self._send(exc.http_status, "application/json",
                       json.dumps(_a2a_error(exc.code, exc.message)).encode("utf-8"))
            return
        except Exception as exc:  # never expose an internal traceback
            logger.warning("A2A registry register failed: %s", exc)
            self._send(503, "application/json", b'{"error":"registry unavailable"}')
            return
        self._send(201, "application/json",
                   json.dumps({"card": entry, "registered": True},
                              ensure_ascii=False).encode("utf-8"))

    def _registry_mint(self) -> None:
        """POST /v1/registry/capabilities/mint — MVE stub of the capability
        minting hook (design grade R-D, post-MVE)."""
        body = self._read_json_body()
        if body is None:
            return
        capability = body.get("capability") or body.get("id")
        if not isinstance(capability, str) or not capability.strip():
            self._send(400, "application/json",
                       json.dumps(_a2a_error(-32602, "missing 'capability'")).encode("utf-8"))
            return
        stub = self.bridge.mint_stub(capability.strip())
        self._send(200, "application/json",
                   json.dumps(stub, ensure_ascii=False).encode("utf-8"))

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

    def _negotiate_version(self) -> bool:
        """A2A §3.6 protocol version negotiation.

        The client declares the protocol version it wants on each request
        via the ``A2A-Version`` header (``Major.Minor``). If the requested
        version does not match the bridge's supported version on
        Major.Minor, refuse with a VersionNotSupportedError (JSON-RPC code
        -32009, HTTP 400) — the future-proofing that lets a client detect
        it is talking to a server on a newer/older protocol line.

        An absent/empty header is treated as "no version preference" and
        served at the bridge's protocol version: pre-negotiation clients
        (including the legacy ``/.well-known/agent.json`` flow) keep
        working unchanged. This is a deliberate, documented compatibility
        deviation from the spec's literal "empty means 0.3" (A2A §3.6.2),
        which would otherwise break every existing A2A client.
        """
        requested = self.headers.get(_A2A_VERSION_HEADER)
        if not requested:
            return True
        if _version_compatible(requested, _A2A_PROTOCOL_VERSION):
            return True
        logger.warning("a2a version negotiation refused: client %r bridge %s",
                       requested, _A2A_PROTOCOL_VERSION)
        body = json.dumps(_a2a_error(
            _A2A_VERSION_NOT_SUPPORTED,
            f"Protocol version {requested.strip()} not supported "
            f"(bridge speaks {_A2A_PROTOCOL_VERSION})",
        )).encode("utf-8")
        self._send(400, "application/json", body)
        return False

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Every bridge HTTP response announces the A2A protocol version it
        # speaks, so a client can detect the interface's version line.
        self.send_header(_A2A_VERSION_HEADER, _A2A_PROTOCOL_VERSION)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.info("a2a %s", format % args)


class A2ABridge:
    """Local A2A bridge: a Synapse agent exposed in the A2A format."""

    def __init__(self, config: Any, agent_name: str, agent_password: str,
                 port: int = 8090, *, token: str | None = None,
                 clock: Any | None = None) -> None:
        self._config = config
        self._agent_name = agent_name
        self._agent_password = agent_password
        self._port = port
        # Bridge access token: mandatory (any request without a valid
        # token is refused). Never logged.
        self.token = token
        # Causal clock (C1, DESIGN_CAUSAL_TIME_HLC_v2 §5 M1): the bridge
        # absorbs remote hlc extension fields at entry (the merge rule)
        # and stamps every outbound envelope with its own hlc. In a
        # Synapse deployment the bridge shares the server's clock (the
        # MVE-2 test injects it); a standalone bridge builds its own,
        # rehydrated from the same persisted upper bound as the service.
        self._clock = clock if clock is not None else self._build_clock()
        # The A2A registry (MVE, scout_architecture REGISTRY_FEATURE_DESIGN
        # t_68dcd793 R-F): the additive discovery layer on the bridge.
        # Cards are registered by URL (token-protected POST) and searched
        # anonymously by capability/tag (public discover-before-talk). The
        # verified bit is *evidence-of-working* — the card was fetched from
        # a live well-known URI and validated — not a registry-blessed
        # trust statement (provocateur t_02ceab8b guardrail: trust lands
        # with the JWS layer, design R-C, post-MVE). Tests may swap in a
        # Registry with an injected fetch/clock before exercising endpoints.
        self.registry = Registry()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _build_clock(self) -> Any:
        """Own clock of a standalone bridge: config skew seam + the
        persisted MAX(hlc) upper bound (same rehydration rule as the
        service clock, DESIGN §3.3)."""
        from .hlc import HLC, default_pt

        skew = self._config.clock_skew_ms
        physical = default_pt if skew == 0 else (lambda: default_pt() + skew)
        initial = None
        try:
            from .db import hlc_upper_bound

            initial = hlc_upper_bound(self._config)
        except Exception:  # pragma: no cover - storage missing (first boot)
            initial = None
        return HLC(physical=physical, initial=initial)

    def _envelope_hlc(self, result: dict) -> dict:
        """Attaches the bridge's causal stamp to an outbound envelope.

        Synapse extension field ``hlc`` on the JSON-RPC result: ignored
        by non-Synapse peers (A2A has no causal time — DESIGN §2 P6),
        observed by Synapse peers (the merge rule, §5 M1)."""
        result["hlc"] = self._clock.stamp()
        return result

    def _client(self) -> Client:
        return Client.from_config(self._config)

    def agent_card(self) -> dict:
        """Builds the AgentCard v1.0 manifest of the exposed agent.

        The vocabulary follows A2A AgentCard v1.0 (Linux Foundation), so a
        client can discover the agent's capabilities (skills), the A2A
        interface to talk to, the provider, the protocol version and the
        supported media types — all before sending any ``tasks/*`` call
        ("discover-before-talk"). Each Synapse card capability is
        advertised as an ``AgentSkill``; the single supported interface is
        this local bridge (JSON-RPC 2.0 over HTTP). The ``securitySchemes``
        map declares the ApiKey scheme (A2A v1.0, name->scheme object) behind
        which the ``X-Synapse-Token`` credential must be presented, and
        ``securityRequirements`` lists it as required on every operation.
        """
        client = self._client()
        my = client.get_my_organization(self._agent_name, self._agent_password)
        card = client.get_agent_card(self._agent_name, self._agent_name,
                                     self._agent_password)
        domain = card.get("domain")
        org = my.get("organization_name")
        interface_url = f"http://127.0.0.1:{self._port}/"
        skills = []
        for capability in card.get("capabilities") or []:
            skills.append({
                "id": capability,
                "name": capability,
                "description": domain or capability,
                "tags": [domain] if domain else [],
                "inputModes": _A2A_INPUT_MODES,
                "outputModes": _A2A_OUTPUT_MODES,
            })
        return {
            "name": self._agent_name,
            "description": my.get("description") or domain or "",
            "version": project_version() or _A2A_PROTOCOL_VERSION,
            "supportedInterfaces": [{
                "url": interface_url,
                "protocolBinding": _A2A_PROTOCOL_BINDING,
                "protocolVersion": _A2A_PROTOCOL_VERSION,
            }],
            "provider": {
                "url": interface_url,
                "organization": org or "",
            },
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "extendedAgentCard": False,
            },
            "defaultInputModes": _A2A_INPUT_MODES,
            "defaultOutputModes": _A2A_OUTPUT_MODES,
            "securitySchemes": _A2A_SECURITY_SCHEMES,
            "securityRequirements": _A2A_SECURITY_REQUIREMENTS,
            "skills": skills,
        }

    def mint_stub(self, capability: str) -> dict:
        """MVE stub of the capability-minting hook (design grade R-D,
        post-MVE).

        Acknowledges the requested capability without minting any
        credential: the real mint-on-resolve hook lands with the JWS
        signature/trust layer (design R-C), where minted capabilities are
        self-signed evidence issued by the card owner — never
        registry-blessed authority (provocateur t_02ceab8b guardrail).
        """
        return {
            "capability": capability,
            "minted": True,
            "stub": True,
            "issuer": self._agent_name,
            "note": "capability minting is post-MVE (design R-D); "
                    "mint-on-resolve lands with the JWS trust layer (R-C)",
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
        if isinstance(result, dict) and "error" in result:
            # a handler-level refusal (e.g. malformed envelope hlc, H5)
            # returns a full JSON-RPC error object: pass it through
            # instead of wrapping it as a success result
            result["jsonrpc"] = "2.0"
            result["id"] = rpc_id
            return result
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    def _tasks_message(self, params: dict) -> dict:
        message = params.get("message") or {}
        text = (message.get("parts") or [{}])[0].get("text", "")
        assignee = (message.get("metadata") or {}).get("assignee") or self._agent_name
        # Causal time (C1, DESIGN §5 M1): a Synapse peer attaches its hlc
        # to the message metadata as a Synapse extension field. The merge
        # rule runs BEFORE any local stamp: a remote clock ahead of ours
        # is absorbed, so the event we create is causally after the
        # remote message. Malformed values are rejected at the boundary
        # (H5): a bad hlc can never corrupt the clock.
        remote_hlc = (message.get("metadata") or {}).get("hlc")
        if remote_hlc is not None:
            from .hlc import is_valid

            if not is_valid(remote_hlc):
                return _a2a_error(
                    -32602, "invalid params: message.metadata.hlc is not a "
                            "canonical hybrid logical clock")
            self._clock.observe(remote_hlc)
        client = self._client()
        task = client.create_task(
            text or "A2A task",
            assignee,
            self._agent_name,
            self._agent_password,
        )
        return self._envelope_hlc({
            "taskId": task["task_id"],
            "status": {"state": task["state"].upper()},
        })

    def _tasks_get(self, params: dict) -> dict:
        client = self._client()
        task = client.get_task(params.get("id", ""), self._agent_name, self._agent_password)
        return self._envelope_hlc({"taskId": task["task_id"],
                                   "status": {"state": task["state"].upper()},
                                   "title": task.get("title"),
                                   "result": task.get("result")})

    def _tasks_list(self, params: dict) -> dict:
        client = self._client()
        result = client.list_tasks(self._agent_name, self._agent_password)
        return self._envelope_hlc({"tasks": [
            {"taskId": t["task_id"], "status": {"state": t["state"].upper()},
             "title": t.get("title")}
            for t in result["tasks"]
        ]})

    def _tasks_cancel(self, params: dict) -> dict:
        client = self._client()
        task = client.update_task_state(
            params.get("id", ""), "canceled", self._agent_name, self._agent_password,
        )
        return self._envelope_hlc({"taskId": task["task_id"],
                                   "status": {"state": task["state"].upper()}})

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
