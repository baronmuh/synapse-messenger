"""Human web interface (SPEC-WEB): local HTTP server restricted to 127.0.0.1.

The interface is exclusively for humans: login only accepts
``organization_name`` + ``organization_password`` (agents do not have this
secret — that is the real anti-agent control, §6.3/I9). The server starts without
any secret and no longer holds an observer account nor a static token.

Each authenticated session goes through the Synapse socket with the identity of the
human account (messaging and organization reading) and the powers of
the organization (account management), the password of the organization
residing only in session memory. No sensitive data is written to
disk nor to the logs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .client import ApiClientError, Client
from . import jsonutil
from . import transport
from .service import WEB_TOKEN_FILENAME, _WEB_LOCAL
from .validation import human_username_for

logger = logging.getLogger("synapse.web")

_WEBUI_DIR = Path(__file__).resolve().parent / "webui"

_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

# Server cache TTL (s): the snapshot is the hot polling data.
_SNAPSHOT_TTL = 3.0
_ORG_TTL = 10.0
_AGENT_TTL = 15.0
_SEARCH_TTL = 5.0
_CONVERSATIONS_TTL = 3.0
_MAX_CACHE_ENTRIES = 512

_SESSION_COOKIE = "synapse_session"


@dataclass(frozen=True)
class _Session:
    """Web session: human identity + organization powers in memory."""

    org_name: str
    human_username: str
    org_password: str  # memory only, never written nor logged
    created_at: float
    last_used_at: float


class _Handler(BaseHTTPRequestHandler):
    web: "SynapseWebUI"

    # ------------------------------------------------------------------
    # Sessions (HttpOnly cookie, SameSite=Strict — replaces the static key)
    # ------------------------------------------------------------------
    def _session_token(self) -> str | None:
        header = self.headers.get("Cookie", "")
        for part in header.split(";"):
            key, _, value = part.strip().partition("=")
            if key == _SESSION_COOKIE and value:
                return value
        return None

    def _session(self) -> _Session | None:
        token = self._session_token()
        if token is None:
            return None
        return self.web.get_session(token)

    def _require_session(self) -> _Session | None:
        session = self._session()
        if session is None:
            self._send(401, "application/json; charset=utf-8",
                       jsonutil.dumps({"error": "session required"}),
                       extra=self._clear_cookie_header())
            return None
        return session

    def _session_cookie_header(self, token: str, max_age: int) -> dict[str, str]:
        return {
            "Set-Cookie": f"{_SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; "
            f"Path=/; Max-Age={max_age}",
        }

    def _clear_cookie_header(self) -> dict[str, str]:
        return {
            "Set-Cookie": f"{_SESSION_COOKIE}=; HttpOnly; SameSite=Strict; "
            f"Path=/; Max-Age=0"
        }

    # ------------------------------------------------------------------
    # Request body (bounded, consistent with the API's 1 MiB limit)
    # ------------------------------------------------------------------
    def _read_body(self) -> dict | None:
        """Reads and decodes the JSON body; None if an error was already
        sent (400/413)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > self.web.max_body_bytes:
            # Bounded drain before the 413: the client must be able to finish
            # its send and read the response (otherwise, depending on the
            # socket buffer fill, urllib raises URLError — non-deterministic).
            # The bound protects the server against huge bodies.
            drain = min(length, self.web.max_body_bytes + 65536)
            if drain > 0:
                self.rfile.read(drain)
            self._send(413, "application/json; charset=utf-8",
                       jsonutil.dumps({"error": "request body too large"}))
            return None
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except Exception:
            self._send(400, "application/json; charset=utf-8",
                       jsonutil.dumps({"error": "Invalid JSON"}))
            return None
        if not isinstance(body, dict):
            self._send(400, "application/json; charset=utf-8",
                       jsonutil.dumps({"error": "JSON object expected"}))
            return None
        return body

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (API http.server)
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # Shell and static assets: served without a session — they
        # contain NO organization data (everything is loaded via
        # /api/*), the interface must be able to load to connect.
        if path == "/onboarding":
            self._serve_static("onboarding.html")
            return
        if path in ("/", "/index.html"):
            # Onboarding gate: when no organization exists yet, send the
            # user to the interactive guide instead of the login screen.
            try:
                orgs = self.web.list_orgs() if self.web.web_token else None
                no_org = bool(orgs) is False or not (orgs or {}).get("organizations")
            except ApiClientError:
                no_org = True
            if no_org:
                self.send_response(302)
                self.send_header("Location", "/onboarding")
                self.end_headers()
                return
            self._serve_static("index.html")
            return
        if path.startswith("/assets/"):
            self._serve_static(path[len("/assets/"):])
            return
        if path == "/api/orgs":
            # Organization list for the selection
            # login screen (SPEC-WEB D5 amended): public (active org
            # names only), served by the local web identity.
            self._handle_orgs()
            return
        if path == "/api/status":
            # Web state for local supervision (SPEC_CLI ``web
            # status``): port, active in-memory sessions, startup.
            # Metadata only — same trust domain as
            # /api/orgs (local workstation).
            self._send_json(200, {
                "port": self.web.port,
                "sessions_active": self.web.session_count(),
                "started_at": self.web.started_at,
            })
            return
        session = self._require_session()
        if session is None:
            return
        if path == "/api/session":
            self._send_json(200, self.web.session_info(session))
        elif path == "/api/snapshot":
            self._serve_json(lambda: self.web.snapshot(session), session=session)
        elif path == "/api/org":
            self._serve_json(lambda: self.web.org(session), session=session)
        elif path == "/api/search":
            query = parse_qs(parsed.query)
            q = query.get("q", [""])[0].strip()
            capability = query.get("capability", [""])[0].strip() or None
            domain = query.get("domain", [""])[0].strip() or None
            if not q and not capability and not domain:
                self._send_json(400, {"error": "parameter q, capability or domain required"})
            else:
                self._serve_json(
                    lambda: self.web.search(session, q, capability, domain), session=session)
        elif path == "/api/conversations":
            self._serve_json(lambda: self.web.conversations(session), session=session)
        elif path == "/api/conversation":
            query = parse_qs(parsed.query)
            conversation_id = query.get("conversation_id", [""])[0].strip()
            if not conversation_id:
                self._send_json(400, {"error": "conversation_id required"})
            else:
                self._serve_json(
                    lambda: self.web.conversation(session, conversation_id), session=session)
        elif path.startswith("/api/agents/"):
            username = path[len("/api/agents/"):]
            if not username or "/" in username:
                self._send_json(404, {"error": "agent not found"})
            else:
                self._serve_json(
                    lambda: self.web.agent(session, username), session=session, not_found=True)
        else:
            self._send(404, "text/plain; charset=utf-8", b"404")

    def do_POST(self) -> None:  # noqa: N802 (API http.server)
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/login":
            self._handle_login()
            return
        if path == "/api/orgs":
            # Organization creation FROM THE LOGIN PAGE
            # (SPEC-WEB D5 amended): no session required — the local web
            # (trust token) creates the organization and its human account,
            # web equivalent of the local synapse-init-org procedure.
            body = self._read_body()
            if body is None:
                return
            try:
                data = self.web.create_org_public(body)
            except ApiClientError as exc:
                # 400: business error (name already used, invalid
                # password…); 503: local service not ready (token missing).
                code = 503 if exc.code == "AUTH_FAILED" else 400
                self._send_json(code, {"error": exc.message})
                return
            self._send_json(200, data)
            return
        session = self._require_session()
        if session is None:
            return
        if path == "/api/logout":
            self.web.destroy_session(self._session_token())
            self._send_json(200, {"ok": True}, extra=self._clear_cookie_header())
        elif path == "/api/send":
            body = self._read_body()
            if body is None:
                return
            self._serve_json(lambda: self.web.send(session, body), session=session)
        elif path == "/api/agents":
            body = self._read_body()
            if body is None:
                return
            self._serve_json(lambda: self.web.create_agent(session, body), session=session)
        elif path.startswith("/api/agents/") and path.endswith("/deactivate"):
            username = path[len("/api/agents/"):-len("/deactivate")]
            self._serve_json(
                lambda: self.web.deactivate_agent(session, username), session=session)
        elif path.startswith("/api/agents/") and path.endswith("/reactivate"):
            username = path[len("/api/agents/"):-len("/reactivate")]
            self._serve_json(
                lambda: self.web.reactivate_agent(session, username), session=session)
        elif path.startswith("/api/agents/") and path.endswith("/description"):
            username = path[len("/api/agents/"):-len("/description")]
            body = self._read_body()
            if body is None:
                return
            self._serve_json(
                lambda: self.web.change_agent_description(session, username, body),
                session=session)
        elif path == "/api/orgs/disable":
            body = self._read_body()
            if body is None:
                return
            self._serve_json(lambda: self.web.disable_org(session, body), session=session)
        else:
            self._send(404, "text/plain; charset=utf-8", b"404")

    def _handle_orgs(self) -> None:
        """Lists the active organizations (selection login screen).
        Relies on the local web identity (_web_local + trust token
        from the run dir) — without a session or an entered password."""
        token = self.web.web_token
        if token is None:
            self._send_json(503, {"error": "local service not ready (web token missing)"})
            return
        try:
            data = self.web.list_orgs()
        except ApiClientError:
            self._send_json(503, {"error": "local service unreachable"})
            return
        self._send_json(200, data)

    def _handle_login(self) -> None:
        body = self._read_body()
        if not body:
            return
        org_name = body.get("organization_name")
        if not isinstance(org_name, str):
            self._send_json(401, {"error": "invalid credentials"})
            return
        org_name = org_name.lower()
        try:
            token, ttl = self.web.login(org_name)
        except _LoginLocked:
            self._send_json(429, {"error": "too many attempts, try again later"})
            return
        except _LoginFailed:
            self._send_json(401, {"error": "invalid credentials"})
            return
        session = self.web.get_session(token)
        if session is None:  # pragma: no cover - defensive
            self._send_json(500, {"error": "cannot create the session"})
            return
        self._send_json(200, self.web.session_info(session),
                        extra=self._session_cookie_header(token, ttl))

    # ------------------------------------------------------------------
    # Responses
    # ------------------------------------------------------------------
    def _serve_static(self, rel: str) -> None:
        try:
            target = (_WEBUI_DIR / rel).resolve()
            target.relative_to(_WEBUI_DIR)  # anti-traversal
        except (ValueError, OSError):
            self._send(404, "text/plain; charset=utf-8", b"404")
            return
        if not target.is_file():
            self._send(404, "text/plain; charset=utf-8", b"404")
            return
        ctype = _MIME_TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] \
            or "application/octet-stream"
        try:
            body = target.read_bytes()
        except OSError:
            self._send(500, "text/plain; charset=utf-8", b"500")
            return
        self._send(200, ctype, body,
                   extra={"Cache-Control": "public, max-age=300"})

    def _serve_json(self, fn: Callable[[], Any], *, session: _Session | None = None,
                    not_found: bool = False) -> None:
        try:
            data = fn()
        except ApiClientError as exc:
            if exc.code == "AUTH_FAILED":
                # The session credentials are no longer valid
                # (password rotation, deactivated organization):
                # the session is destroyed, the client returns to login.
                if session is not None:
                    self.web.destroy_session_for(session)
                self._send_json(401, {"error": "session expired"},
                                extra=self._clear_cookie_header())
                return
            if not_found and exc.code == "USER_NOT_FOUND":
                self._send_json(404, {"error": "agent not found"})
                return
            # Business errors (POLICY_DENIED, QUOTA_EXCEEDED,
            # INVALID_ARGUMENT, RECIPIENT_NOT_FOUND, TASK_STATE_INVALID,
            # …) are expected API answers, not server failures: they are
            # relayed as 400 with their message and code so the UI can
            # distinguish a refused operation from a real outage.
            logger.warning("api %s : %s", self.path, exc.code)
            self._send_json(400, {"error": exc.message, "code": exc.code})
            return
        except Exception as exc:  # pragma: no cover - safety net
            logger.warning("api %s : %s", self.path, exc)
            self._send_json(500, {"error": "cannot read the state"})
            return
        body = jsonutil.dumps(data)
        etag = '"%s"' % hashlib.sha1(body).hexdigest()[:16]
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self._send(200, "application/json; charset=utf-8", body, extra={"ETag": etag})

    def _send_json(self, code: int, data: dict,
                   extra: dict[str, str] | None = None) -> None:
        self._send(code, "application/json; charset=utf-8",
                   jsonutil.dumps(data), extra=extra)

    def _send(self, code: int, content_type: str, body: bytes,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.info("web %s", format % args)


class _LoginFailed(Exception):
    pass


class _LoginLocked(Exception):
    pass


class SynapseWebUI:
    """Human web interface of a Synapse instance (SPEC-WEB).

    Starts without any secret; login (org + password) creates an
    in-memory session. Listens on 127.0.0.1 only.
    """

    def __init__(self, config: Any, port: int = 8080,
                 snapshot_ttl: float = _SNAPSHOT_TTL) -> None:
        self._config = config
        self._port = port
        self._snapshot_ttl = snapshot_ttl
        self.max_body_bytes = config.max_request_bytes
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.Lock()
        # Login failures per organization (rate-limit, SPEC-WEB §6.3).
        self._login_failures: dict[str, tuple[int, float]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()
        self.started_at: float | None = None  # set by start() (local supervision)
        # Local trust token (SPEC-WEB D5 amended): read from the run dir
        # (0600 file written by the server), used to authenticate
        # to the service without a password (selection login).
        self._web_token: str | None = None

    @property
    def web_token(self) -> str | None:
        return self._web_token

    def list_orgs(self) -> dict:
        """Lists the active organizations via the local web identity."""
        return self._client().list_orgs(_WEB_LOCAL, self._web_token or "")

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    def login(self, org_name: str, password: str | None = None) -> tuple[str, int]:
        """Creates a session for the chosen organization (SPEC-WEB D5 amended).

        No more typed password: the web authenticates to the service
        with the local trust token (0600 file of the run dir), on behalf
        of the organization's human account. ``password`` is only accepted
        for HTTP test backward compatibility; the interface never sends
        any password.

        The validation goes through the socket API: the command
        ``get_my_organization`` run with the human account identity
        fails if the organization is unknown/deactivated or if the human
        account is missing (generic AUTH_FAILED — no revelation)."""
        secret = password if password is not None else self._web_token
        if secret is None:
            raise _LoginFailed()  # service not started or token missing
        now = time.monotonic()
        with self._sessions_lock:
            failures, locked_until = self._login_failures.get(org_name, (0, 0.0))
            if locked_until > now:
                raise _LoginLocked()
        client = self._client()
        human = human_username_for(org_name)
        try:
            client.get_my_organization(human, secret)
        except ApiClientError:
            with self._sessions_lock:
                count, _ = self._login_failures.get(org_name, (0, 0.0))
                count += 1
                locked_until = 0.0
                if count >= self._config.web_login_max_attempts:
                    locked_until = now + self._config.web_login_lockout_seconds
                    count = 0  # counter reset after lockout
                self._login_failures[org_name] = (count, locked_until)
            raise _LoginFailed()
        with self._sessions_lock:
            self._login_failures.pop(org_name, None)
            self._prune_sessions_locked(now)
            sessions = [t for t, s in self._sessions.items() if s.org_name == org_name]
            max_sessions = self._config.web_max_sessions
            for token in sessions[:max(0, len(sessions) - max_sessions + 1)]:
                del self._sessions[token]
            token = secrets.token_hex(32)
            self._sessions[token] = _Session(
                org_name=org_name, human_username=human, org_password=secret,
                created_at=now, last_used_at=now,
            )
        return token, self._config.web_session_ttl_seconds

    def get_session(self, token: str | None) -> _Session | None:
        if token is None:
            return None
        now = time.monotonic()
        with self._sessions_lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if now - session.last_used_at > self._config.web_session_ttl_seconds:
                del self._sessions[token]
                return None
            self._sessions[token] = _Session(
                org_name=session.org_name, human_username=session.human_username,
                org_password=session.org_password,
                created_at=session.created_at, last_used_at=now,
            )
            return session

    def destroy_session(self, token: str | None) -> None:
        if token is None:
            return
        with self._sessions_lock:
            self._sessions.pop(token, None)

    def destroy_session_for(self, session: _Session) -> None:
        with self._sessions_lock:
            for token, s in list(self._sessions.items()):
                if s is session:
                    del self._sessions[token]
                    return

    def session_info(self, session: _Session) -> dict:
        # Expiration in clock time (the client compares it to Date.now()).
        now_mono = time.monotonic()
        remaining = self._config.web_session_ttl_seconds - (now_mono - session.last_used_at)
        return {
            "organization_name": session.org_name,
            "human_username": session.human_username,
            "principal_type": "human",
            "expires_at": int(time.time() + max(0.0, remaining)),
        }

    def _prune_sessions_locked(self, now: float) -> None:
        expired = [
            t for t, s in self._sessions.items()
            if now - s.last_used_at > self._config.web_session_ttl_seconds
        ]
        for token in expired:
            del self._sessions[token]

    # ------------------------------------------------------------------
    # Server cache (short TTLs, per organization — the data is
    # identical for all humans of the same organization).
    # ------------------------------------------------------------------
    def _invalidate(self, key: str) -> None:
        with self._cache_lock:
            self._cache.pop(key, None)

    def _cached(self, key: str, ttl: float, fn: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        value = fn()
        with self._cache_lock:
            if len(self._cache) > _MAX_CACHE_ENTRIES:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            self._cache[key] = (now + ttl, value)
        return value

    def _client(self) -> Client:
        return Client.from_config(self._config)

    # ------------------------------------------------------------------
    # API (session identity)
    # ------------------------------------------------------------------
    def snapshot(self, session: _Session) -> dict:
        key = f"snapshot:{session.org_name}"
        return self._cached(key, self._snapshot_ttl, lambda: self._client()
                            .get_org_snapshot(session.human_username, session.org_password))

    def org(self, session: _Session) -> dict:
        def fn() -> dict:
            info = self._client().get_my_organization(
                session.human_username, session.org_password)
            info["human_username"] = session.human_username
            info["principal_type"] = "human"
            info["read_only"] = False
            return info
        return self._cached(f"org:{session.org_name}", _ORG_TTL, fn)

    def agent(self, session: _Session, username: str) -> dict:
        def fn() -> dict:
            client = self._client()
            out: dict[str, Any] = dict(client.get_agent_description(
                username, session.human_username, session.org_password))
            try:
                out["card"] = client.get_agent_card(
                    username, session.human_username, session.org_password)
            except ApiClientError as exc:
                if exc.code != "USER_NOT_FOUND":  # pragma: no cover - defensive
                    raise
            try:
                out["reputation"] = client.get_agent_reputation(
                    username, session.human_username, session.org_password)
            except ApiClientError as exc:
                if exc.code != "USER_NOT_FOUND":  # pragma: no cover - defensive
                    raise
            return out
        return self._cached(f"agent:{session.org_name}:{username}", _AGENT_TTL, fn)

    def search(self, session: _Session, q: str, capability: str | None = None,
               domain: str | None = None) -> dict:
        def fn() -> dict:
            return self._client().find_agents(
                session.human_username, session.org_password,
                capability=capability, domain=domain,
                name_contains=q or None, limit=12)
        return self._cached(
            f"search:{session.org_name}:{q}|{capability}|{domain}", _SEARCH_TTL, fn)

    def conversations(self, session: _Session) -> dict:
        key = f"conversations:{session.org_name}"
        return self._cached(key, _CONVERSATIONS_TTL, lambda: self._client()
                            .list_org_conversations(
                                session.human_username, session.org_password, limit=100))

    def conversation(self, session: _Session, conversation_id: str) -> dict:
        # Content reading: never cached (freshness + tracing).
        client = self._client()
        data = client.get_org_conversation(
            conversation_id, session.human_username, session.org_password, limit=100)
        # Unread handling (messaging): consulting the conversation marks
        # read the messages addressed to the human. The service only marks
        # messages whose human is the recipient (idempotent, non-disclosure
        # kept); a race (already read/revoked message) is non-blocking.
        marked = 0
        for m in data.get("messages", []):
            if m.get("recipient_username") == session.human_username and m.get("read_at") is None:
                try:
                    read = client.read_message(
                        m["message_id"], session.human_username, session.org_password)
                    m["read_at"] = read.get("read_at")
                    marked += 1
                except ApiClientError:
                    pass  # already read in the meantime: non-blocking
        if marked:
            # The conversation list is cached: it must reflect
            # the read state immediately (unread counter badge).
            self._invalidate(f"conversations:{session.org_name}")
        return data

    def send(self, session: _Session, body: dict) -> dict:
        recipient = body.get("recipient_username")
        message = body.get("message")
        if not isinstance(recipient, str) or not isinstance(message, str):
            raise ApiClientError("INVALID_ARGUMENT", "missing required fields")
        client_message_id = body.get("client_message_id")
        if not isinstance(client_message_id, str) or not client_message_id:
            client_message_id = str(uuid.uuid4())
        return self._client().send_message(
            recipient, message, client_message_id,
            session.human_username, session.org_password)

    def create_agent(self, session: _Session, body: dict) -> dict:
        username = body.get("username")
        password = body.get("password")
        description = body.get("description")
        if not isinstance(username, str) or not isinstance(password, str) \
                or not isinstance(description, str):
            raise ApiClientError("INVALID_ARGUMENT", "missing required fields")
        return self._client().create_agent(
            username, password, description, session.org_name, session.org_password)

    def deactivate_agent(self, session: _Session, username: str) -> dict:
        return self._client().deactivate_agent(
            username, session.org_name, session.org_password)

    def reactivate_agent(self, session: _Session, username: str) -> dict:
        return self._client().reactivate_agent(
            username, session.org_name, session.org_password)

    def change_agent_description(self, session: _Session, username: str,
                                 body: dict) -> dict:
        description = body.get("description")
        if not isinstance(description, str):
            raise ApiClientError("INVALID_ARGUMENT", "description required")
        return self._client().change_agent_description(
            username, description, session.org_name, session.org_password)

    def create_org_public(self, body: dict) -> dict:
        """Creates an organization FROM THE LOGIN PAGE (SPEC-WEB D5
        amended). No session: the web authenticates to the service
        with the local web identity + local trust token (like
        ``list_orgs``) — web equivalent of the local procedure
        ``synapse-init-org``. The organization gets its human account in
        the same transaction (password delegated to the org's)."""
        org_name = body.get("organization_name")
        org_password = body.get("organization_password")
        if not isinstance(org_name, str) or not isinstance(org_password, str):
            raise ApiClientError("INVALID_ARGUMENT", "missing required fields")
        if self._web_token is None:
            raise ApiClientError("AUTH_FAILED", "local service not ready (web token missing)")
        return self._client().create_org(
            org_name, org_password, _WEB_LOCAL, self._web_token)

    def disable_org(self, session: _Session, body: dict) -> dict:
        org_name = body.get("organization_name")
        if not isinstance(org_name, str):
            raise ApiClientError("INVALID_ARGUMENT", "organization_name required")
        return self._client().disable_org(
            org_name, session.human_username, session.org_password)

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    def start(self) -> None:
        _Handler.web = self
        # Local trust token: written by the server in the run dir
        # (0600) at startup; the web reads it to authenticate to the
        # service without a password (selection login, SPEC-WEB D5
        # amended). If missing, the web stays reachable but logins
        # fail cleanly (service not started).
        token_path = os.path.join(transport.run_dir(self._config),
                                  WEB_TOKEN_FILENAME)
        try:
            with open(token_path, encoding="ascii") as fh:
                self._web_token = fh.read().strip() or None
        except OSError:
            self._web_token = None
            logger.warning("web token absent (%s): connections impossible while "
                           "that the service is not started", token_path)
        self._server = ThreadingHTTPServer(("127.0.0.1", self._port), _Handler)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.started_at = time.time()
        logger.info("web interface at http://127.0.0.1:%d (human login required)",
                    self._port)

    @property
    def port(self) -> int:
        """Effective port (resolved after bind, useful with port=0)."""
        return self._port

    def session_count(self) -> int:
        with self._sessions_lock:
            return len(self._sessions)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def web_main() -> None:  # pragma: no cover
    """CLI ``synapse-web``: human web interface on 127.0.0.1.

    The server starts without any secret; login (organization +
    password) creates a session per user (SPEC-WEB §6).
    """
    import argparse

    from .config import Config

    parser = argparse.ArgumentParser(
        description="Human Synapse web interface (127.0.0.1, organization login)"
    )
    parser.add_argument("--config", required=True, help="JSON configuration file")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as fh:
        config = Config.from_dict(json.load(fh))
    web = SynapseWebUI(config, port=args.port)
    web.start()
    try:
        import time

        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        web.stop()


if __name__ == "__main__":  # pragma: no cover
    web_main()
