"""Messaging service: command dispatch and business logic.

Each request strictly follows the path:
    1. envelope and parameter validation (``INVALID_ARGUMENT``,
       ``UNKNOWN_COMMAND``) ;
    2. authentication (``AUTH_FAILED``, rate limiting) — agent or
       organisation selon la commande ;
    3. business operation in a transaction.

No business data is read or modified before authentication.
Passwords and contents are never logged. An organization
never accesses the content of messages, conversations or notifications.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import threading
import time
from typing import Any, Callable

from . import db
from . import helpdoc
from .config import Config
from .cursor import build_payload, decode_cursor, encode_cursor, validate_cursor_binding
from .errors import (
    ACCESS_DENIED,
    AUTH_FAILED,
    CONVERSATION_NOT_FOUND,
    GROUP_NOT_FOUND,
    INVALID_ARGUMENT,
    INTERNAL_ERROR,
    MESSAGE_NOT_FOUND,
    POLICY_DENIED,
    QUOTA_EXCEEDED,
    RECIPIENT_NOT_FOUND,
    TASK_DEPENDENCY_NOT_MET,
    TASK_NOT_FOUND,
    TASK_STATE_INVALID,
    USERNAME_ALREADY_EXISTS,
    USER_NOT_FOUND,
    ApiError,
)
from .security import hash_password, human_password_sentinel, load_or_create_key, verify_dummy, verify_password
from .store import accounts, audit, authfail, cards, events, messages, organizations, queries, tasks
from .validation import (
    COMMAND_SPECS,
    human_username_for,
    is_reserved_human_username,
    now_utc,
    now_utc_offset,
    parse_json_request,
    validate_envelope,
)

logger = logging.getLogger("synapse.service")

_SORT_DESC = queries.SORT_DESC
_SORT_ASC = queries.SORT_ASC
_SORT_USERNAME = "username_asc"

# Time bound never reached for the event journal (append-only,
# pagination par ``seq`` uniquement — le snapshot temporel figerait le polling).
_EVENTS_NO_BOUNDARY = "9999-12-31T23:59:59.999Z"

# System identity of the local web interface (SPEC-WEB D5 amended): the web
# authenticates to the service with the local trust token and this
# identity — limited to the ``list_orgs`` command (no other power).
_WEB_LOCAL = "_web_local"

# Name of the local trust token file (run dir, 0600), written by the
# server at startup, read by the web interface.
WEB_TOKEN_FILENAME = "web_token"

# Allowed logging fields (never passwords or content).
_TARGET_FIELD: dict[str, str | None] = {
    "create_agent": "username",
    "deactivate_agent": "username",
    "reactivate_agent": "username",
    "change_agent_password": "username",
    "set_agent_visibility": "username",
    "get_agent_description": "username",
    "send_message": "recipient_username",
    "get_messages": "conversation_id",
    "get_conversation": "other_username",
    "read_message": "message_id",
    "get_notifications": None,
    "mark_conversation_no_reply": "conversation_id",
    "help": None,
    "set_agent_card": None,
    "get_agent_card": "username",
    "approve_agent_card": "username",
    "find_agents": None,
    "create_task": "task_id",
    "get_task": "task_id",
    "list_tasks": None,
    "update_task_state": "task_id",
    "transfer_task": "task_id",
    "request_approval": "task_id",
    "approve_task": "task_id",
    "reject_task": "task_id",
    "get_my_work": None,
    "get_events": None,
    "set_escalation_policy": None,
    "set_agent_budget": "username",
    "create_department": "department_name",
    "set_agent_department": "username",
    "change_agent_description": "username",
    "create_org": "organization_name",
    "disable_org": "organization_name",
    "get_org_conversation": "conversation_id",
    "get_org_structure": None,
    "list_department_tasks": "department_name",
    "get_org_audit": None,
    "get_org_metrics": None,
    "get_server_status": None,
    "create_group": None,
    "add_group_member": "username",
    "remove_group_member": "username",
    "send_group_message": "group_id",
    "get_group_messages": "group_id",
    "get_group_members": "group_id",
    "list_my_groups": None,
    "get_agent_reputation": "username",
    "create_delegation": "task_id",
    "revoke_delegation": "task_id",
    "get_my_delegations": None,
    "create_observer_account": "observer_name",
    "revoke_observer_account": "observer_name",
    "list_observers": None,
    "get_org_snapshot": None,
}


# Maximum number of authentication cache entries before purging
# expired entries (bounded by the number of active principals on the server).
_MAX_AUTH_CACHE_ENTRIES = 2048

# Write commands audited by the dispatch point (the
# coordination s'auditent dans leur propre transaction).
_AUDITED_COMMANDS = frozenset(
    {
        "create_agent",
        "deactivate_agent",
        "reactivate_agent",
        "change_agent_password",
        "set_agent_visibility",
        "set_organization_policy",
        "change_organization_password",
        "change_agent_description",
        "send_message",
        "read_message",
        "mark_conversation_no_reply",
        "set_agent_card",
        "approve_agent_card",
        "create_department",
        "set_agent_department",
        "create_org",
        "disable_org",
        "get_org_conversation",
    }
)

# Read commands allowed to an observer account (SPEC.txt F18):
# any command outside this set is refused (ACCESS_DENIED).
_OBSERVER_READ_COMMANDS = frozenset(
    {
        "get_my_organization",
        "get_agent_description",
        "list_org_agents",
        "get_messages",
        "get_conversation",
        "get_notifications",
        "help",
        "get_agent_card",
        "find_agents",
        "get_task",
        "list_tasks",
        "get_my_work",
        "get_events",
        "get_group_messages",
        "get_group_members",
        "list_my_groups",
        "get_agent_reputation",
        "get_my_delegations",
        "get_org_snapshot",
    }
)


class Service:
    """Single entry point for API v2 commands."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._cursor_key: bytes | None = None
        # Authentication cache (F1): result of successful verifications,
        # cached TTL seconds per principal. Failures are never
        # cached; a hash rotation (password change)
        # invalidates the entry automatically (hash comparison on every
        # request). Lock-protected: the Argon2id computation runs outside the
        # lock (it must never be held for 255 ms).
        self._auth_cache: dict[str, tuple[str, str, float]] = {}
        self._auth_cache_lock = threading.Lock()
        # Compteurs de serveur (F12, get_server_status).
        self._started_at = time.monotonic()
        self._requests_total = 0
        self._requests_lock = threading.Lock()
        # Local trust token (SPEC-WEB D5 amended): injected by the
        # server at startup, shared with the web interface via a
        # 0600 file in the run dir. The web uses it instead of the
        # organization password (selection login). Compared in constant time.
        self._web_token: str | None = None

    # ------------------------------------------------------------------
    # Jeton de confiance local (interface web)
    # ------------------------------------------------------------------
    def set_web_token(self, token: str) -> None:
        """Injects the local trust token (called by the server at
        startup, BEFORE the first client)."""
        self._web_token = token

    def web_token_matches(self, password: Any) -> bool:
        if self._web_token is None or not isinstance(password, str):
            return False
        return hmac.compare_digest(password, self._web_token)

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------
    @property
    def cursor_secret(self) -> bytes:
        if self._cursor_key is None:
            self._cursor_key = load_or_create_key(self.config.cursor_key_path)
        return self._cursor_key

    # ------------------------------------------------------------------
    # Handling a complete request
    # ------------------------------------------------------------------
    def process(self, raw: bytes) -> tuple[dict, dict]:
        """Handles a raw request and returns ``(response, meta-logs)``."""
        meta: dict[str, Any] = {
            "username": None,
            "command": None,
            "target_id": None,
            "result": "ok",
        }
        with self._requests_lock:
            self._requests_total += 1
        try:
            request = parse_json_request(raw)
            command, params = validate_envelope(request)
            meta["command"] = command
            target_field = _TARGET_FIELD.get(command)
            if target_field and isinstance(params.get(target_field), str):
                meta["target_id"] = params[target_field]
            # Attempted principal name (logged even on
            # auth failure; replaced by the verified name on success).
            attempted = params.get("my_name_auth") or params.get("organization_name_auth")
            if isinstance(attempted, str):
                meta["username"] = attempted.lower()
            data = self._dispatch(command, params, meta)
            if command == "help" and params.get("command_name") is None:
                # The full help response is static: envelope and bytes
                # pre-serialized in cache (the server avoids ~1.5 ms of
                # serialization per call, measured — see docs/PERFORMANCE.md).
                return helpdoc.full_help_envelope(), {
                    **meta,
                    "pre_serialized": helpdoc.full_help_payload(),
                }
            return {"success": True, "data": data, "error": None}, meta
        except ApiError as exc:
            meta["result"] = exc.code
            return (
                {"success": False, "data": None, "error": {"code": exc.code, "message": exc.message}},
                meta,
            )
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("Internal service error")
            meta["result"] = INTERNAL_ERROR
            meta["internal_error"] = exc
            return (
                {
                    "success": False,
                    "data": None,
                    "error": {"code": INTERNAL_ERROR, "message": "Internal service error"},
                },
                meta,
            )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _dispatch(self, command: str, params: dict, meta: dict) -> dict:
        spec = COMMAND_SPECS[command]
        with db.connect(self.config) as conn:
            if spec[2]:  # commande d'organisation
                org_name = self._authenticate_organization(
                    conn, params["organization_name_auth"], params["organization_password_auth"]
                )
                meta["username"] = org_name
                handler = _ORG_HANDLERS[command]
                data = handler(self, conn, params, org_name)
                self._audit_action(conn, command, params, actor=org_name, org_name=org_name)
                return data
            user = self._authenticate(conn, params["my_name_auth"], params["my_password_auth"])
            meta["username"] = user
            row = accounts.get(conn, user)
            if row is not None and bool(row["is_observer"]) and command not in _OBSERVER_READ_COMMANDS:
                raise ApiError(ACCESS_DENIED, "Observer account is read-only")
            if command == "list_orgs":
                # Reserved for the local web identity (_web_local) and to
                # comptes humains : liste les organisations actives pour
                # the selection login screen (SPEC-WEB D5 amended).
                if user != _WEB_LOCAL and (row is None or row["principal_type"] != "human"):
                    raise ApiError(ACCESS_DENIED,
                                   "Command reserved for the web interface and human accounts")
                return _human_list_orgs(self, conn, params, web_local=(user == _WEB_LOCAL))
            if user == _WEB_LOCAL:
                # The local web identity (local trust token) has two
                # powers (SPEC-WEB D5 amended): list_orgs (selection
                # login screen) and create_org (creating an
                # organization from the login page — web equivalent
                # of the local synapse-init-org procedure). It
                # matches no real account: nothing else.
                if command == "create_org":
                    return _human_create_org(self, conn, params, user)
                raise ApiError(ACCESS_DENIED,
                               "Local web identity reserved for list_orgs and create_org")
            if command in _HUMAN_HANDLERS:
                # Commands reserved for human accounts (SPEC-WEB): management
                # des organisations et lecture de contenu de l'organisation.
                if row is None or row["principal_type"] != "human":
                    raise ApiError(ACCESS_DENIED, "Command reserved for human accounts")
                data = _HUMAN_HANDLERS[command](self, conn, params, user)
                self._audit_action(conn, command, params, actor=user, org_name=_org_of(conn, user))
                return data
            handler = _AGENT_HANDLERS[command]
            data = handler(self, conn, params, user)
            self._audit_action(conn, command, params, actor=user, org_name=_org_of(conn, user))
            return data

    def _audit_action(
        self,
        conn: sqlite3.Connection,
        command: str,
        params: dict,
        *,
        actor: str,
        org_name: str,
    ) -> None:
        """Registers write commands not already audited by their
        handler (F11). Pure reads are not audited (they do not
        change any state); coordination commands audit
        themselves in their transaction."""
        if command not in _AUDITED_COMMANDS:
            return
        target = params.get(_TARGET_FIELD.get(command, "")) if _TARGET_FIELD.get(command) else None
        if isinstance(target, str):
            target = target.lower()
        with db.begin_immediate(conn):
            _audit(
                conn,
                organization_name=org_name,
                at=now_utc(),
                actor_username=actor,
                command=command,
                target_type="target",
                target_username=target,
                outcome="ok",
            )

    # ------------------------------------------------------------------
    # Cache d'authentification (F1)
    # ------------------------------------------------------------------
    def _cached_password_ok(self, key: str, password_hash: str, password: str) -> bool:
        """Verifies a password, with temporary success caching.

        The key is the principal name (usernames and organizations share
        the same cache, namespaced). The entry is only valid
        if (a) the stored hash matches the current DB hash — any
        password rotation invalidates the entry without explicit action —
        and (b) the provided password digest matches the one of
        the cached authentication: a DIFFERENT password never benefits
        from a fresh entry. Failures are never cached
        (rate limiting remains the only arbiter of failed attempts).
        """
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._auth_cache_lock:
            entry = self._auth_cache.get(key)
            if (
                entry is not None
                and entry[0] == password_hash
                and entry[1] == digest
                and entry[2] > now
            ):
                return True
        # Calcul Argon2id hors du verrou : il ne doit jamais bloquer les
        # other threads for ~255 ms (Argon2id semaphore already in place).
        ok = verify_password(password_hash, password)
        if ok:
            with self._auth_cache_lock:
                if len(self._auth_cache) >= _MAX_AUTH_CACHE_ENTRIES:
                    self._prune_auth_cache()
                self._auth_cache[key] = (
                    password_hash,
                    digest,
                    now + self.config.auth_cache_ttl_seconds,
                )
        return ok

    def _prune_auth_cache(self) -> None:
        """Purges expired entries (called under lock)."""
        now = time.monotonic()
        expired = [k for k, entry in self._auth_cache.items() if entry[2] <= now]
        for key in expired:
            del self._auth_cache[key]

    # ------------------------------------------------------------------
    # Authentication and rate limiting
    # ------------------------------------------------------------------
    def _authenticate(self, conn: sqlite3.Connection, name_raw: Any, password_raw: Any) -> str:
        window = self.config.auth_window_seconds
        maximum = self.config.auth_max_failures
        username = name_raw.lower()
        authfail.prune(conn, window)
        if authfail.count_recent(conn, username, window) >= maximum:
            raise ApiError(AUTH_FAILED, "Too many failed attempts, try again later")
        # System identity of the local web interface: the local trust
        # token (0600 file in the run dir) is the only accepted proof.
        if username == _WEB_LOCAL:
            if self.web_token_matches(password_raw):
                authfail.clear(conn, username)
                return _WEB_LOCAL
            authfail.record(conn, username)
            raise ApiError(AUTH_FAILED, "Identifiants invalides")
        row = accounts.get(conn, username)
        if row is None or row["status"] != "active":
            verify_dummy(password_raw)  # constant timing (anti-enumeration)
            authfail.record(conn, username)
            raise ApiError(AUTH_FAILED, "Identifiants invalides")
        org_row = organizations.get(conn, row["organization_name"])
        if org_row is None or not bool(org_row["enabled"]):
            # Organization deactivated or not found (SPEC-WEB §4.3):
            # aucun compte de l'organisation ne s'authentifie.
            verify_dummy(password_raw)
            authfail.record(conn, username)
            raise ApiError(AUTH_FAILED, "Identifiants invalides")
        if row["principal_type"] == "human":
            # Compte humain (SPEC-WEB §5.2) : le mot de passe est celui de
            # the organization, never copied — the verification is delegated to the
            # hash de l'organisation. Le jeton de confiance local (interface
            # web) is accepted as a replacement (selection login).
            if not (self._cached_password_ok(username, org_row["password_hash"], password_raw)
                    or self.web_token_matches(password_raw)):
                authfail.record(conn, username)
                raise ApiError(AUTH_FAILED, "Identifiants invalides")
            authfail.clear(conn, username)
            return username
        if not self._cached_password_ok(username, row["password_hash"], password_raw):
            authfail.record(conn, username)
            raise ApiError(AUTH_FAILED, "Identifiants invalides")
        authfail.clear(conn, username)
        return username

    def _authenticate_organization(
        self, conn: sqlite3.Connection, name_raw: Any, password_raw: Any
    ) -> str:
        """Authenticates an organization (rate-limit key ``org:<name>``).

        Failures are counted separately from usernames (``org:`` prefix):
        une organisation et un agent homonymes ne partagent pas leur budget
        of failures. Control order (section 3.3):

        * une organisation existante avec un mauvais mot de passe ->
          ``AUTH_FAILED`` ;
        * a username (agent account) used as identity
          d'organisation -> ``ACCESS_DENIED`` : un agent ne peut pas appeler
          une commande d'organisation ;
        * an unknown name -> ``AUTH_FAILED`` (no existence revelation).
        """
        window = self.config.auth_window_seconds
        maximum = self.config.auth_max_failures
        org_name = name_raw.lower()
        key = f"org:{org_name}"
        authfail.prune(conn, window)
        if authfail.count_recent(conn, key, window) >= maximum:
            raise ApiError(AUTH_FAILED, "Too many failed attempts, try again later")
        row = organizations.get(conn, org_name)
        if row is not None:
            if not bool(row["enabled"]):
                # Deactivated organization (SPEC-WEB §4.3): any
                # authentication fails, data intact.
                verify_dummy(password_raw)
                authfail.record(conn, key)
                raise ApiError(AUTH_FAILED, "Identifiants invalides")
            if not (self._cached_password_ok(key, row["password_hash"], password_raw)
                    or self.web_token_matches(password_raw)):
                authfail.record(conn, key)
                raise ApiError(AUTH_FAILED, "Identifiants invalides")
            authfail.clear(conn, key)
            return org_name
        if accounts.get(conn, org_name) is not None:
            # Un agent ne peut pas s'authentifier comme organisation.
            raise ApiError(ACCESS_DENIED, "Command reserved for organizations")
        verify_dummy(password_raw)  # constant timing (anti-enumeration)
        authfail.record(conn, key)
        raise ApiError(AUTH_FAILED, "Identifiants invalides")

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    def _pagination(
        self,
        params: dict,
        username: str,
        command: str,
        sort: str,
        filters: dict,
    ) -> tuple[tuple[str, ...] | None, str]:
        cursor = params.get("cursor")
        if cursor is not None:
            payload = decode_cursor(self.cursor_secret, cursor)
            validate_cursor_binding(
                payload, command=command, username=username, sort=sort, filters=filters
            )
            boundary = payload["boundary"]
            last_value = payload.get("last")
            last = tuple(last_value) if last_value else None
            return last, boundary
        return None, now_utc()

    def _next_cursor(
        self,
        username: str,
        command: str,
        sort: str,
        filters: dict,
        boundary: str,
        last: tuple[str, ...],
    ) -> str:
        payload = build_payload(
            username=username,
            command=command,
            sort=sort,
            filters=filters,
            boundary=boundary,
            last=list(last),
        )
        return encode_cursor(self.cursor_secret, payload)


# ===========================================================================
# Commandes d'organisation
# ===========================================================================


def _org_require_member(
    conn: sqlite3.Connection, username: str, org_name: str
) -> sqlite3.Row:
    """Returns the ``username`` account if it belongs to ``org_name``.

    A missing account or one belonging to another organization causes
    ``USER_NOT_FOUND`` (no existence revelation, section 3.4).
    """
    row = accounts.get(conn, username)
    if row is None or row["organization_name"] != org_name:
        raise ApiError(USER_NOT_FOUND)
    return row


def _org_create_agent(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    # The Argon2id hash (expensive) runs outside the transaction:
    # the write lock is not held during the computation.
    if is_reserved_human_username(p["username"]):
        raise ApiError(INVALID_ARGUMENT, "This name is reserved for the organization's human account")
    if p["principal_type"] == "human":
        # SPEC-WEB §5: human accounts are created automatically with
        # leur organisation (jamais par create_agent).
        raise ApiError(INVALID_ARGUMENT, "Human accounts are created automatically")
    password_hash = hash_password(p["password"])
    with db.begin_immediate(conn):
        if accounts.get(conn, p["username"]) is not None:
            raise ApiError(USERNAME_ALREADY_EXISTS)
        accounts.insert(
            conn,
            p["username"],
            password_hash,
            "active",
            p["description"],
            org_name,
            p["can_see_org_agents"],
            p["principal_type"],
        )
    return {
        "username": p["username"],
        "status": "active",
        "description": p["description"],
        "organization_name": org_name,
        "can_see_org_agents": p["can_see_org_agents"],
        "principal_type": p["principal_type"],
    }


def _org_deactivate_agent(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    with db.begin_immediate(conn):
        row = _org_require_member(conn, p["username"], org_name)
        if row["principal_type"] == "human":
            # SPEC-WEB §5.5: the human account cannot be deactivated
            # individuellement (le gel se fait au niveau de l'organisation).
            raise ApiError(ACCESS_DENIED, "The human account cannot be deactivated")
        if row["status"] == "disabled":
            return {"username": p["username"], "status": "disabled"}
        accounts.set_status(conn, p["username"], "disabled")
    return {"username": p["username"], "status": "disabled"}


def _org_reactivate_agent(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    with db.begin_immediate(conn):
        row = _org_require_member(conn, p["username"], org_name)
        if row["status"] == "active":
            return {"username": p["username"], "status": "active"}
        accounts.set_status(conn, p["username"], "active")
    return {"username": p["username"], "status": "active"}


def _org_change_password(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    # The human account password is delegated to the organization's
    # (SPEC-WEB §5.2): it has no password of its own to change.
    member = _org_require_member(conn, p["username"], org_name)
    if member["principal_type"] == "human":
        raise ApiError(ACCESS_DENIED, "The human account has no password of its own")
    password_hash = hash_password(p["new_password"])  # outside the transaction (expensive Argon2)
    with db.begin_immediate(conn):
        accounts.set_password_hash(conn, p["username"], password_hash)
    return {"username": p["username"], "status": member["status"]}


def _org_set_visibility(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    with db.begin_immediate(conn):
        _org_require_member(conn, p["username"], org_name)
        accounts.set_visibility(conn, p["username"], p["can_see_org_agents"])
    return {"username": p["username"], "can_see_org_agents": p["can_see_org_agents"]}


def _org_get_agents(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    """Paginated list of the organization's agents (section 13.2)."""
    limit = p["limit"]
    filters: dict = {}
    last, boundary = service._pagination(p, org_name, "get_org_agents", _SORT_USERNAME, filters)
    with db.begin_read(conn):
        rows = accounts.list_by_org(
            conn, org_name, limit + 1, last[0] if last else None, boundary
        )
        reputations = _reputation_counts_many(
            conn, [r["username"] for r in rows]
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    agents = [
        {
            "username": row["username"],
            "description": row["description"],
            "status": row["status"],
            "can_see_org_agents": bool(row["can_see_org_agents"]),
            "principal_type": row["principal_type"],
            # Detailed reputation visible to the organization (SPEC.txt F16).
            "reputation": reputations[row["username"]],
        }
        for row in rows
    ]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            org_name, "get_org_agents", _SORT_USERNAME, filters, boundary,
            (rows[-1]["username"], ""),
        )
    return {"agents": agents, "next_cursor": next_cursor}


def _org_set_policy(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    with db.begin_immediate(conn):
        organizations.update_policies(
            conn, org_name, p["allow_incoming_external"], p["allow_outgoing_external"]
        )
    return {
        "organization_name": org_name,
        "allow_incoming_external": p["allow_incoming_external"],
        "allow_outgoing_external": p["allow_outgoing_external"],
    }


def _org_get_policy(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    with db.begin_read(conn):
        row = organizations.get(conn, org_name)
    assert row is not None  # authenticated organization, therefore existing
    return {
        "organization_name": org_name,
        "allow_incoming_external": bool(row["allow_incoming_external"]),
        "allow_outgoing_external": bool(row["allow_outgoing_external"]),
    }


def _org_change_organization_password(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    password_hash = hash_password(p["new_password"])  # outside the transaction (expensive Argon2)
    with db.begin_immediate(conn):
        organizations.update_password(conn, org_name, password_hash)
    return {"organization_name": org_name}


def _org_change_agent_description(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Modifie la description publique d'an agent of theorganisation
    (SPEC-WEB §4, « modifier un agent »). Le compte humain n'est pas
    modifiable: its description is self-managed."""
    with db.begin_immediate(conn):
        member = _org_require_member(conn, p["username"], org_name)
        if member["principal_type"] == "human":
            raise ApiError(ACCESS_DENIED, "The human account cannot be modified")
        conn.execute(
            "UPDATE accounts SET description = ? WHERE username = ?",
            (p["description"], p["username"]),
        )
    return {"username": p["username"], "description": p["description"]}


# ===========================================================================
# Commandes des comptes humains (SPEC-WEB) : gestion des organisations et
# content reading. Authentication: human account (my_*_auth), checked
# contre le mot de passe de son organisation (§5.2).
# ===========================================================================


def _human_list_orgs(service: Service, conn: sqlite3.Connection,
                     p: dict | None = None, *, web_local: bool = False) -> dict:
    """Lists the organizations (selection login screen, SPEC-WEB
    D5 amended). Reserved for the local web identity and human accounts.

    For the local web identity, only ACTIVE organizations are
    listed (no state revelation: only what is usable is offered).
    joignable). Un compte humain peut demander ``include_disabled=true``
    (administration locale, SPEC_CLI ``org list --all``) : les
    deactivated organizations are then listed in a separate
    ``disabled`` field — never mixed with the active ones.
    """
    include_disabled = bool(p.get("include_disabled")) if p else False
    rows = conn.execute(
        "SELECT organization_name FROM organizations WHERE enabled = 1 "
        "ORDER BY organization_name"
    ).fetchall()
    result: dict = {
        "organizations": [{"organization_name": r[0]} for r in rows],
    }
    if include_disabled and not web_local:
        disabled = conn.execute(
            "SELECT organization_name FROM organizations WHERE enabled = 0 "
            "ORDER BY organization_name"
        ).fetchall()
        if disabled:
            result["disabled"] = [{"organization_name": r[0]} for r in disabled]
    return result


def _human_create_org(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Creates an organization from a human account (SPEC-WEB §4/§7.1).

    The first organization is always created locally
    (``synapse-init-org``); subsequent ones can be created by a human.
    The organization gets its human account in the same transaction
    (password delegated to the organization's, never copied)."""
    org_name = p["organization_name"]
    human_name = human_username_for(org_name)
    human_hash = human_password_sentinel()  # never verified (delegated to the org)
    with db.begin_immediate(conn):
        if organizations.get(conn, org_name) is not None:
            raise ApiError(INVALID_ARGUMENT, "Organization name already used")
        if accounts.get(conn, human_name) is not None:
            raise ApiError(INVALID_ARGUMENT, "Human account name already used")
        organizations.insert(conn, org_name, hash_password(p["organization_password"]))
        accounts.insert(
            conn,
            human_name,
            human_hash,
            "active",
            f"Human account of the organization {org_name} (web access)",
            org_name,
            can_see_org_agents=True,  # superviseur : annuaire et recherche
            principal_type="human",
        )
    return {"organization_name": org_name, "human_username": human_name}


def _human_disable_org(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Deactivates the caller's organization (SPEC-WEB §4/§7.2).

    Isolation: a human only deactivates THEIR OWN organization. Freeze
    reversible (data intact); reactivation is a local procedure
    locale (``synapse-init-org --enable``)."""
    me_org = _org_of(conn, me)
    target = p["organization_name"]
    if target != me_org:
        raise ApiError(ACCESS_DENIED, "A human only manages their own organization")
    with db.begin_immediate(conn):
        row = organizations.get(conn, target)
        if row is None:
            raise ApiError(INVALID_ARGUMENT, "Organisation unknowne")
        if not bool(row["enabled"]):
            raise ApiError(INVALID_ARGUMENT, "The organization is already deactivated")
        conn.execute(
            "UPDATE organizations SET enabled = 0 WHERE organization_name = ?", (target,)
        )
    return {"organization_name": target, "enabled": False}


def _human_list_org_conversations(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    """Paginated list of the organization's conversations (SPEC-WEB §2/§7.3).

    Metadata only (participants, volume, last exchange, unread
    pour l'appelant) ; le contenu est servi par ``get_org_conversation``.
    A conversation appears as soon as at least one participant belongs to
    the organization (external exchanges are therefore visible, R2.1)."""
    org_name = _org_of(conn, me)
    limit = p["limit"]
    filters: dict[str, Any] = {}
    last, boundary = service._pagination(p, me, "list_org_conversations", _SORT_DESC, filters)
    having = "HAVING MAX(m.created_at) <= ?"
    params: list[Any] = [boundary]
    if last is not None:
        having += (
            " AND (MAX(m.created_at) < ? OR "
            "(MAX(m.created_at) = ? AND c.conversation_id < ?))"
        )
        params.extend([last[0], last[0], last[1]])
    params.append(limit + 1)
    with db.begin_read(conn):
        rows = conn.execute(
            "SELECT c.conversation_id, c.key, "
            "COUNT(m.message_id) AS message_count, "
            "MAX(m.created_at) AS last_message_at, "
            "COALESCE(SUM(CASE WHEN m.recipient_username = ? AND m.read_at IS NULL "
            "THEN 1 ELSE 0 END), 0) AS unread_count "
            "FROM conversations c "
            "JOIN messages m ON m.conversation_id = c.conversation_id "
            "JOIN accounts a_s ON a_s.username = m.sender_username "
            "JOIN accounts a_r ON a_r.username = m.recipient_username "
            "WHERE (a_s.organization_name = ? OR a_r.organization_name = ?) "
            "GROUP BY c.conversation_id, c.key "
            + having
            + " ORDER BY MAX(m.created_at) DESC, c.conversation_id DESC LIMIT ?",
            [me, org_name, org_name] + params,
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    conversations = [
        {
            "conversation_id": r["conversation_id"],
            "participants": [u for u in r["key"].split(":") if u],
            "message_count": int(r["message_count"]),
            "unread_count": int(r["unread_count"]),
            "last_message_at": r["last_message_at"],
        }
        for r in rows
    ]
    next_cursor = None
    if has_more:
        last_row = rows[-1]
        next_cursor = service._next_cursor(
            me, "list_org_conversations", _SORT_DESC, filters, boundary,
            (last_row["last_message_at"], last_row["conversation_id"]),
        )
    return {"conversations": conversations, "next_cursor": next_cursor}


def _human_get_org_conversation(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    """Lecture d'a conversation of theorganisation, contenu compris
    (SPEC-WEB §2/§7.4).

    Authorization: at least one participant belongs to the organization of
    l'appelant, sinon ``CONVERSATION_NOT_FOUND`` (non-divulgation). La
    content reading is traced (audit F11, R2.6)."""
    org_name = _org_of(conn, me)
    conv_id = p["conversation_id"]
    limit = p["limit"]
    with db.begin_read(conn):
        if conn.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ?", (conv_id,)
        ).fetchone() is None:
            raise ApiError(CONVERSATION_NOT_FOUND, "Conversation not found")
        visible = conn.execute(
            "SELECT 1 FROM messages m "
            "WHERE m.conversation_id = ? AND ("
            "m.sender_username IN (SELECT username FROM accounts "
            "WHERE organization_name = ?) OR "
            "m.recipient_username IN (SELECT username FROM accounts "
            "WHERE organization_name = ?)) LIMIT 1",
            (conv_id, org_name, org_name),
        ).fetchone()
        if visible is None:
            raise ApiError(CONVERSATION_NOT_FOUND, "Conversation not found")
    filters: dict[str, Any] = {"conversation_id": conv_id}
    last, boundary = service._pagination(p, me, "get_org_conversation", _SORT_ASC, filters)
    clauses = ["conversation_id = ?", "created_at <= ?"]
    params: list[Any] = [conv_id, boundary]
    if last is not None:
        last_created, last_id = last
        clauses.append("(created_at > ? OR (created_at = ? AND message_id > ?))")
        params.extend([last_created, last_created, last_id])
    params.append(limit + 1)
    with db.begin_read(conn):
        rows = conn.execute(
            "SELECT message_id, sender_username, recipient_username, content, "
            "business_reference, created_at, read_at "
            "FROM messages WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at ASC, message_id ASC LIMIT ?",
            params,
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    messages_out = [dict(r) for r in rows]
    next_cursor = None
    if has_more:
        last_row = rows[-1]
        next_cursor = service._next_cursor(
            me, "get_org_conversation", _SORT_ASC, filters, boundary,
            (last_row["created_at"], last_row["message_id"]),
        )
    return {"conversation_id": conv_id, "messages": messages_out, "next_cursor": next_cursor}


def _org_approve_agent_card(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Valide la carte d'an agent of theorganisation (SPEC.txt F2).

    The card must exist (``USER_NOT_FOUND`` otherwise) and belong to an
    agent of the authenticated organization. Validation is idempotent.
    """
    with db.begin_immediate(conn):
        _org_require_member(conn, p["username"], org_name)
        card = cards.get(conn, p["username"])
        if card is None:
            raise ApiError(USER_NOT_FOUND, "This agent has no card to validate")
        cards.approve(conn, username=p["username"], approved_by=org_name, approved_at=now_utc())
        row = cards.get(conn, p["username"])
    assert row is not None
    return {"username": p["username"], "validation_state": "approved"}


# ===========================================================================
# Commandes des agents
# ===========================================================================


def _get_my_organization(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Returns the authenticated agent's organization and its policies."""
    with db.begin_read(conn):
        row = accounts.get(conn, me)
        assert row is not None  # authenticated agent, therefore existing
        org = organizations.get(conn, row["organization_name"])
        assert org is not None  # FK guaranteed by the schema
        return {
            "organization_name": row["organization_name"],
            "allow_incoming_external": bool(org["allow_incoming_external"]),
            "allow_outgoing_external": bool(org["allow_outgoing_external"]),
        }


def _get_agent_description(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Retourne la description publique d'un compte (section 13.1).

    Read-only. The description and organization are public directory
    metadata: a deactivated account stays consultable, and no
    other information (hash, state) is exposed.
    """
    with db.begin_read(conn):
        row = accounts.get(conn, p["username"])
        if row is None:
            raise ApiError(USER_NOT_FOUND)
        return {
            "username": p["username"],
            "organization_name": row["organization_name"],
            "description": row["description"],
        }


def _list_org_agents(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Paginated list of usernames of the active agents of the
    agent's organization (section 13.3): reserved for authorized agents."""
    limit = p["limit"]
    filters: dict = {}
    last, boundary = service._pagination(p, me, "list_org_agents", _SORT_USERNAME, filters)
    with db.begin_read(conn):
        row = accounts.get(conn, me)
        assert row is not None  # authenticated agent, therefore existing
        if not row["can_see_org_agents"]:
            raise ApiError(ACCESS_DENIED, "Permission can_see_org_agents requise")
        rows = accounts.list_by_org(
            conn, row["organization_name"], limit + 1, last[0] if last else None, boundary,
            active_only=True,
            include_humans=False,  # l'annuaire des agents exclut le compte humain
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    usernames = [row["username"] for row in rows]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            me, "list_org_agents", _SORT_USERNAME, filters, boundary,
            (rows[-1]["username"], ""),
        )
    return {"usernames": usernames, "next_cursor": next_cursor}


def _help(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Returns the service's built-in documentation (section 14 of SPEC.txt).

    Read-only, without storage access: ``command_name`` has already been
    validated (``None`` for the full documentation, otherwise the exact name
    d'une commande existante).
    """
    return {"documentation": helpdoc.build_documentation(p["command_name"])}


def _agent_set_agent_card(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Declares or replaces the card of the authenticated agent (SPEC.txt F2).

    Any submission returns the card to the ``pending`` state (the previous
    version stays displayed until validated by the organization).
    """
    with db.begin_immediate(conn):
        cards.upsert(
            conn,
            username=me,
            capabilities=p["capabilities"],
            domain=p["domain"],
            model=p["model"],
            tools=p["tools"],
            sla=p["sla"],
            limits=p["limits"],
            estimated_cost=p["estimated_cost"],
            updated_at=now_utc(),
        )
        row = cards.get(conn, me)
    assert row is not None
    return cards.row_to_card_public(row)


def _agent_get_agent_card(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Retourne la carte d'un compte (SPEC.txt F2), lecture publique.

    Like the description: a deactivated account stays consultable, no
    other information (hash, state) is exposed. An account without a card
    returns an empty card (``validation_state`` null).
    """
    with db.begin_read(conn):
        row = accounts.get(conn, p["username"])
        if row is None:
            raise ApiError(USER_NOT_FOUND)
        card = cards.get(conn, p["username"])
        reputation = _reputation_summary(conn, p["username"], me)
    if card is None:
        out = {
            "username": p["username"],
            "capabilities": [],
            "domain": None,
            "model": None,
            "tools": [],
            "sla": None,
            "limits": None,
            "estimated_cost": None,
            "validation_state": None,
            "approved_by": None,
            "approved_at": None,
            "updated_at": None,
        }
    else:
        out = cards.row_to_card(card)
    # Reputation measured by the server (SPEC.txt F16), computed field of the
    # card: detail for oneself, qualitative mention for others.
    out["reputation"] = reputation
    return out


def _agent_find_agents(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    """Searches agents by capability in one's own organization (F3).

    Reserved for authorized agents (``can_see_org_agents``, consistent with
    ``list_org_agents`` et la contrainte 22 de SPEC.txt) : la recherche ne doit
    cannot bypass the username visibility control. Scope limited to
    la propre organisation (aucune fuite inter-organisations).
    """
    limit = p["limit"]
    filters = {
        "capability": p["capability"],
        "domain": p["domain"],
        "name_contains": p["name_contains"],
    }
    last, boundary = service._pagination(p, me, "find_agents", _SORT_USERNAME, filters)
    with db.begin_read(conn):
        row = accounts.get(conn, me)
        assert row is not None  # authenticated agent, therefore existing
        if not row["can_see_org_agents"]:
            raise ApiError(ACCESS_DENIED, "Permission can_see_org_agents requise")
        rows = cards.search(
            conn,
            org_name=row["organization_name"],
            capability=filters["capability"],
            domain=filters["domain"],
            name_contains=filters["name_contains"],
            boundary=boundary,
            last_username=last[0] if last else None,
            limit=limit,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    agents = [cards.row_to_card(r) for r in rows]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            me, "find_agents", _SORT_USERNAME, filters, boundary,
            (rows[-1]["username"], ""),
        )
    return {"agents": agents, "next_cursor": next_cursor}


def _send_message(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    recipient = p["recipient_username"]
    content = p["message"]
    if recipient == me:
        raise ApiError(INVALID_ARGUMENT, "The recipient must be different from the sender")
    with db.begin_immediate(conn):
        sender = accounts.get(conn, me)
        assert sender is not None  # authenticated agent, therefore existing
        sender_org = organizations.get(conn, sender["organization_name"])
        assert sender_org is not None  # FK guaranteed by the schema
        # Idempotent retrieval before any policy: an already
        # validated message is returned as-is, even if a policy changed
        # depuis (section 6.1).
        existing = messages.find_message_by_client_id(conn, me, p["client_message_id"])
        if existing is not None:
            if (
                existing["recipient_username"] == recipient
                and existing["content"] == content
                and existing["business_reference"] == p.get("business_reference")
            ):
                return messages.row_to_message(existing)
            messages.raise_message_already_exists()
        _check_message_budget(conn, me)
        # Politiques de communication externe (section 6.2 et 6.3).
        outgoing_blocked = not sender_org["allow_outgoing_external"]
        recip = accounts.get(conn, recipient)
        # R4.3: a recipient whose organization is deactivated is
        # treated as a deactivated account (reversible freeze) — same
        # treatment: non-disclosure, incoming sends refused.
        recip_org_enabled = True
        if recip is not None:
            recip_org = organizations.get(conn, recip["organization_name"])
            recip_org_enabled = bool(recip_org["enabled"]) if recip_org is not None else False
        if recip is None or recip["status"] != "active" or not recip_org_enabled:
            if outgoing_blocked:
                # A closed organization does not reveal whether a recipient
                # exists externally or not: the same error for both cases.
                raise ApiError(POLICY_DENIED)
            raise ApiError(RECIPIENT_NOT_FOUND)
        if recip["organization_name"] != sender["organization_name"]:
            if outgoing_blocked:
                raise ApiError(POLICY_DENIED)
            recip_org = organizations.get(conn, recip["organization_name"])
            assert recip_org is not None  # FK guaranteed by the schema
            if not recip_org["allow_incoming_external"]:
                raise ApiError(POLICY_DENIED)
        # Message insertion (the conversation, the initial status, the
        # idempotency key and the reply states are created or updated
        # in the same transaction).
        created_at = now_utc()
        conversation_id = messages.fetch_or_create_conversation(
            conn, messages.conversation_key(me, recipient), created_at
        )
        message_id = messages.new_uuid()
        try:
            messages.insert_message(
                conn,
                message_id=message_id,
                conversation_id=conversation_id,
                client_message_id=p["client_message_id"],
                sender_username=me,
                recipient_username=recipient,
                content=content,
                created_at=created_at,
                business_reference=p.get("business_reference"),
            )
        except sqlite3.IntegrityError:
            # Idempotency race: another concurrent send committed.
            existing = messages.find_message_by_client_id(conn, me, p["client_message_id"])
            if (
                existing is not None
                and existing["recipient_username"] == recipient
                and existing["content"] == content
                and existing["business_reference"] == p.get("business_reference")
            ):
                return messages.row_to_message(existing)
            messages.raise_message_already_exists()
        # Reply states in the same transaction: the recipient's
        # marking is undone; the sender goes back to no_reply_needed.
        messages.set_no_reply(conn, conversation_id, recipient, None, None)
        messages.set_no_reply(conn, conversation_id, me, None, None)
        row = messages.get_message_by_id(conn, message_id)
        assert row is not None  # inserted in the same transaction
        return messages.row_to_message(row)


def _get_messages(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    limit = p["limit"]
    filters = {
        "status": p["status"],
        "sender": p["sender_username"],
        "conversation_id": p["conversation_id"],
    }
    last, boundary = service._pagination(p, me, "get_messages", _SORT_DESC, filters)
    with db.begin_read(conn):
        rows = queries.message_page(
            conn,
            username=me,
            boundary=boundary,
            status=filters["status"],
            sender=filters["sender"],
            conversation_id=filters["conversation_id"],
            last=last,
            limit=limit,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    messages_out = [queries_row_to_message_as_of(r, boundary) for r in rows]
    next_cursor = None
    if has_more:
        last_row = rows[-1]
        next_cursor = service._next_cursor(
            me, "get_messages", _SORT_DESC, filters, boundary,
            (last_row["created_at"], last_row["message_id"]),
        )
    return {"messages": messages_out, "next_cursor": next_cursor}


def _get_conversation(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    other = p["other_username"]
    if other == me:
        raise ApiError(INVALID_ARGUMENT, "other_username must be different from the authenticated agent")
    limit = p["limit"]
    filters = {"other_username": other}
    last, boundary = service._pagination(p, me, "get_conversation", _SORT_ASC, filters)
    with db.begin_read(conn):
        conv = messages.get_conversation_by_key(conn, messages.conversation_key(me, other))
        if conv is None:
            raise ApiError(CONVERSATION_NOT_FOUND)
        conversation_id = conv["conversation_id"]
        reply_status, _ = queries.reply_status(conn, conversation_id, me, boundary)
        rows = queries.conversation_page(
            conn,
            conversation_id=conversation_id,
            boundary=boundary,
            last=last,
            limit=limit,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    messages_out = [queries_row_to_message_as_of(r, boundary) for r in rows]
    next_cursor = None
    if has_more:
        last_row = rows[-1]
        next_cursor = service._next_cursor(
            me, "get_conversation", _SORT_ASC, filters, boundary,
            (last_row["created_at"], last_row["message_id"]),
        )
    return {
        "conversation_id": conversation_id,
        "other_username": other,
        "reply_status": reply_status,
        "messages": messages_out,
        "next_cursor": next_cursor,
    }


def _read_message(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    with db.begin_immediate(conn):
        row = messages.get_message_by_id(conn, p["message_id"])
        if row is None or (row["sender_username"] != me and row["recipient_username"] != me):
            raise ApiError(MESSAGE_NOT_FOUND)
        if row["recipient_username"] == me:
            messages.mark_read_conditional(conn, p["message_id"], now_utc())
            row = messages.get_message_by_id(conn, p["message_id"])
            assert row is not None  # le message existe toujours
        # The reply state is recomputed (derived): no storage needed.
        return messages.row_to_message(row)


def _get_notifications(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    limit = p["limit"]
    filters: dict = {}
    last, boundary = service._pagination(p, me, "get_notifications", _SORT_DESC, filters)
    with db.begin_read(conn):
        unread = queries.unread_by_sender(conn, me, boundary)
        items = queries.notification_page(
            conn, username=me, boundary=boundary, last=last, limit=limit
        )
    has_more = len(items) > limit
    items = items[:limit]
    next_cursor = None
    if has_more:
        last_item = items[-1]
        next_cursor = service._next_cursor(
            me, "get_notifications", _SORT_DESC, filters, boundary,
            (last_item["last_received_at"], last_item["conversation_id"]),
        )
    return {"unread_by_sender": unread, "needs_reply": items, "next_cursor": next_cursor}


def _mark_conversation_no_reply(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    with db.begin_immediate(conn):
        conv = messages.get_conversation_by_id(conn, p["conversation_id"])
        if conv is None:
            raise ApiError(INVALID_ARGUMENT, "Conversation not found or without a received message")
        last = queries.last_received_message(conn, conv["conversation_id"], me)
        if last is None:
            raise ApiError(INVALID_ARGUMENT, "Conversation not found or without a received message")
        messages.set_no_reply(conn, conv["conversation_id"], me, last["message_id"], now_utc())
    return {
        "conversation_id": conv["conversation_id"],
        "reply_status": "no_reply_needed",
        "no_reply_for_message_id": last["message_id"],
    }


# ===========================================================================
# Tasks, work queues, approvals, events (SPEC.txt F5-F10)
# ===========================================================================


def _audit(
    conn: sqlite3.Connection,
    *,
    organization_name: str,
    at: str,
    actor_username: str,
    command: str,
    target_type: str | None = None,
    target_username: str | None = None,
    outcome: str = "ok",
) -> None:
    audit.append(
        conn,
        organization_name=organization_name,
        at=at,
        actor_username=actor_username,
        command=command,
        target_type=target_type,
        target_username=target_username,
        outcome=outcome,
    )


def _org_of(conn: sqlite3.Connection, username: str) -> str:
    row = accounts.get(conn, username)
    assert row is not None  # authenticated principal
    return row["organization_name"]


def _task_visible_or_404(
    conn: sqlite3.Connection, task_id: str, me: str, *, allow_delegation: bool = False
) -> tuple[sqlite3.Row, bool]:
    """Task visible by ``me`` (creator or assignee), otherwise ``TASK_NOT_FOUND``
    (non-disclosure: an inaccessible task is indistinguishable from a task
    nonexistent). With ``allow_delegation``, an active delegation (SPEC.txt F17)
    allows reading and state changes; the second returned element
    indicates whether access goes through a delegation."""
    row = tasks.get(conn, task_id)
    if row is not None and (row["creator_username"] == me or row["assignee_username"] == me):
        return row, False
    if allow_delegation and row is not None:
        active = conn.execute(
            "SELECT 1 FROM delegations WHERE delegatee_username = ? AND task_id = ? "
            "AND expires_at > ?",
            (me, task_id, now_utc()),
        ).fetchone()
        if active is not None:
            return row, True
    raise ApiError(TASK_NOT_FOUND)


def _require_active_account(conn: sqlite3.Connection, username: str) -> None:
    row = accounts.get(conn, username)
    if row is None:
        raise ApiError(USER_NOT_FOUND)
    if row["status"] != "active":
        raise ApiError(RECIPIENT_NOT_FOUND, "The account must be active")


def _check_task_budget(conn: sqlite3.Connection, assignee: str) -> None:
    """Active tasks budget (F9): if a limit is defined for
    the assignee and reached, creation/transfer is refused."""
    budget = conn.execute(
        "SELECT max_active_tasks FROM agent_budgets WHERE username = ?", (assignee,)
    ).fetchone()
    if budget is None or budget["max_active_tasks"] is None:
        return
    if tasks.active_count(conn, assignee) >= budget["max_active_tasks"]:
        raise ApiError(QUOTA_EXCEEDED, "Active task budget exceeded")


def _check_message_budget(conn: sqlite3.Connection, sender: str) -> None:
    """Message budget (F9): maximum number of messages sent per hour."""
    budget = conn.execute(
        "SELECT max_messages_per_hour FROM agent_budgets WHERE username = ?", (sender,)
    ).fetchone()
    if budget is None or budget["max_messages_per_hour"] is None:
        return
    since = now_utc_offset(3600)
    if tasks.messages_in_hour(conn, sender, since) >= budget["max_messages_per_hour"]:
        raise ApiError(QUOTA_EXCEEDED, "Budget de messages horaire atteint")


def _check_escalations(
    conn: sqlite3.Connection, org_name: str, now: str, default_retention: int
) -> None:
    """Automatic escalation (F9): tasks late or failing since the
    configured threshold are transferred to the designated agent, with event and
    audit. Called in the write transaction, on the organization of
    the current assignee."""
    policy = conn.execute(
        "SELECT * FROM org_escalation_policy WHERE organization_name = ?", (org_name,)
    ).fetchone()
    if policy is None or not policy["enabled"]:
        return
    escalate_to = policy["escalate_to_username"]
    target = accounts.get(conn, escalate_to)
    if target is None or target["status"] != "active":
        return  # cible indisponible : pas d'escalade (aucune erreur silencieuse)
    due_before = now_utc_offset(policy["due_after_seconds"])
    failed_before = now_utc_offset(policy["failed_after_seconds"])
    for row in tasks.due_tasks_to_escalate(
        conn,
        organization_name=org_name,
        exclude_username=escalate_to,
        due_before=due_before,
        failed_before=failed_before,
    ):
        task_id = row["task_id"]
        tasks.set_assignee(conn, task_id, escalate_to, now)
        tasks.add_event(conn, task_id, "escalated", escalate_to, "automatic escalation", now)
        task_dict = {"task_id": task_id, "creator_username": row["creator_username"],
                     "assignee_username": escalate_to}
        events.append_for_task(conn, task=task_dict, event_type="task.escalated",
                               by_username=escalate_to, note="automatic escalation", at=now,
                               retention_days=_event_retention_days(
                                   conn, org_name, default_retention))
        _audit(conn, organization_name=org_name, at=now, actor_username=escalate_to,
               command="escalate_task", target_type="task", target_username=task_id,
               outcome="escalated")


# F9 guardrail: maximum depth of a task's dependency chain
# (avoids degenerate subtask loops, lesson #1 of multi-agent
# deployments). Exceeding it -> QUOTA_EXCEEDED.
MAX_DEPENDENCY_DEPTH = 8


def _dependency_depth(
    conn: sqlite3.Connection, task_ids: list[str], max_depth: int
) -> int:
    """Maximum depth of the dependency chain starting from the given
    tasks (bounded BFS on ``task_dependencies``)."""
    seen: set[str] = set(task_ids)
    frontier = list(task_ids)
    depth = 0
    while frontier and depth <= max_depth:
        placeholders = ", ".join("?" for _ in frontier)
        rows = conn.execute(
            f"SELECT depends_on_task_id FROM task_dependencies "
            f"WHERE task_id IN ({placeholders})",
            frontier,
        ).fetchall()
        frontier = [
            r["depends_on_task_id"]
            for r in rows
            if r["depends_on_task_id"] not in seen
        ]
        seen.update(frontier)
        depth += 1
    return depth


def _event_retention_days(
    conn: sqlite3.Connection, org_name: str, default: int
) -> int:
    """Event retention of the organization (F10): value from the
    ``org_settings`` table, otherwise the default (server config)."""
    row = conn.execute(
        "SELECT event_retention_days FROM org_settings WHERE organization_name = ?",
        (org_name,),
    ).fetchone()
    return int(row["event_retention_days"]) if row is not None else default


def _agent_create_task(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        org = _org_of(conn, me)
        if p["client_task_id"] is not None:
            existing = tasks.get_by_client_key(conn, me, p["client_task_id"])
            if existing is not None:
                if (
                    existing["assignee_username"] == p["assignee_username"]
                    and existing["title"] == p["title"]
                ):
                    _audit(conn, organization_name=org, at=at, actor_username=me,
                           command="create_task", target_type="task",
                           target_username=existing["task_id"], outcome="idempotent")
                    return tasks.row_to_task(conn, existing)
                raise ApiError(
                    INVALID_ARGUMENT,
                    "client_task_id already used for another task",
                )
        _require_active_account(conn, p["assignee_username"])
        _check_task_budget(conn, p["assignee_username"])
        for dep in p["depends_on"]:
            if tasks.get(conn, dep) is None:
                raise ApiError(INVALID_ARGUMENT, f"The dependent task {dep} does not exist")
        if _dependency_depth(conn, p["depends_on"], MAX_DEPENDENCY_DEPTH) > MAX_DEPENDENCY_DEPTH:
            raise ApiError(QUOTA_EXCEEDED, "Maximum dependency depth exceeded")
        task_id = messages.new_uuid()
        tasks.insert(
            conn,
            task_id=task_id,
            client_task_id=p["client_task_id"],
            title=p["title"],
            description=p["description"],
            creator_username=me,
            assignee_username=p["assignee_username"],
            priority=p["priority"],
            due_at=p["due_at"],
            business_reference=p["business_reference"],
            created_at=at,
        )
        if p["depends_on"]:
            tasks.replace_dependencies(conn, task_id, p["depends_on"])
        tasks.add_event(conn, task_id, "created", me, None, at)
        row = tasks.get(conn, task_id)
        assert row is not None
        task = tasks.row_to_task(conn, row)
        events.append_for_task(conn, task=task, event_type="task.created",
                               by_username=me, note=None, at=at,
                               retention_days=_event_retention_days(
                                   conn, org, service.config.event_retention_days))
        _audit(conn, organization_name=org, at=at, actor_username=me,
               command="create_task", target_type="task", target_username=task_id)
        _check_escalations(conn, _org_of(conn, p["assignee_username"]), at,
                           service.config.event_retention_days)
        return task


def _agent_get_task(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    with db.begin_read(conn):
        row, _delegated = _task_visible_or_404(conn, p["task_id"], me, allow_delegation=True)
        return tasks.row_to_task(conn, row)


def _agent_list_tasks(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    limit = p["limit"]
    filters = {
        "assignee": p["assignee_username"],
        "state": p["state"],
        "priority": p["priority"],
        "due_before": p["due_before"],
    }
    last, boundary = service._pagination(p, me, "list_tasks", "task_asc", filters)
    with db.begin_read(conn):
        rows = tasks.list_visible(
            conn,
            me=me,
            assignee_filter=filters["assignee"],
            state_filter=filters["state"],
            priority_filter=filters["priority"],
            due_before=filters["due_before"],
            boundary=boundary,
            last=last,
            limit=limit,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = []
    for r in rows:
        with db.begin_read(conn):
            out.append(tasks.row_to_task(conn, r))
    next_cursor = None
    if has_more:
        last_row = rows[-1]
        next_cursor = service._next_cursor(
            me, "list_tasks", "task_asc", filters, boundary,
            (last_row["created_at"], last_row["task_id"]),
        )
    return {"tasks": out, "next_cursor": next_cursor}


def _agent_update_task_state(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        row, delegated = _task_visible_or_404(conn, p["task_id"], me, allow_delegation=True)
        org = _org_of(conn, me)
        current = row["state"]
        new_state = p["new_state"]
        tasks.ensure_transition(current, new_state)
        if new_state == tasks.STATE_IN_PROGRESS and not tasks.dependencies_met(conn, p["task_id"]):
            raise ApiError(TASK_DEPENDENCY_NOT_MET)
        result = p["result"] if new_state in (tasks.STATE_COMPLETED, tasks.STATE_FAILED) else None
        tasks.set_state(conn, p["task_id"], new_state, result, at)
        tasks.add_event(conn, p["task_id"], f"state_changed:{current}->{new_state}", me, None, at)
        updated = tasks.get(conn, p["task_id"])
        assert updated is not None
        task = tasks.row_to_task(conn, updated)
        events.append_for_task(conn, task=task, event_type="task.state_changed",
                               by_username=me, note=None, at=at,
                               retention_days=_event_retention_days(
                                   conn, org, service.config.event_retention_days))
        _audit(conn, organization_name=org, at=at, actor_username=me,
               command="update_task_state", target_type="task",
               target_username=p["task_id"],
               outcome=new_state if not delegated else f"{new_state}:delegated")
        _check_escalations(conn, org, at, service.config.event_retention_days)
        return task


def _agent_transfer_task(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        row, _ = _task_visible_or_404(conn, p["task_id"], me)
        org = _org_of(conn, me)
        _require_active_account(conn, p["assignee_username"])
        _check_task_budget(conn, p["assignee_username"])
        if row["state"] in tasks.TERMINAL_STATES:
            raise ApiError(TASK_STATE_INVALID, "A completed task cannot be transferred")
        tasks.set_assignee(conn, p["task_id"], p["assignee_username"], at)
        tasks.add_event(conn, p["task_id"], "transferred", me, p["note"], at)
        updated = tasks.get(conn, p["task_id"])
        assert updated is not None
        task = tasks.row_to_task(conn, updated)
        events.append_for_task(conn, task=task, event_type="task.transferred",
                               by_username=me, note=p["note"], at=at,
                               retention_days=_event_retention_days(
                                   conn, org, service.config.event_retention_days))
        _audit(conn, organization_name=org, at=at, actor_username=me,
               command="transfer_task", target_type="task",
               target_username=p["task_id"], outcome=p["assignee_username"])
        _check_escalations(conn, _org_of(conn, p["assignee_username"]), at,
                           service.config.event_retention_days)
        return task


def _agent_request_approval(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        row, _ = _task_visible_or_404(conn, p["task_id"], me)
        org = _org_of(conn, me)
        if row["state"] in tasks.TERMINAL_STATES or row["state"] == tasks.STATE_PENDING_APPROVAL:
            raise ApiError(TASK_STATE_INVALID)
        _require_active_account(conn, p["approver_username"])
        tasks.set_approver(conn, p["task_id"], p["approver_username"], at)
        tasks.set_state(conn, p["task_id"], tasks.STATE_PENDING_APPROVAL, None, at)
        tasks.add_event(conn, p["task_id"], "approval_requested", me,
                        f"approbateur: {p['approver_username']}", at)
        updated = tasks.get(conn, p["task_id"])
        assert updated is not None
        task = tasks.row_to_task(conn, updated)
        events.append_for_task(conn, task=task, event_type="task.approval_requested",
                               by_username=me, note=None, at=at,
                               retention_days=_event_retention_days(
                                   conn, org, service.config.event_retention_days))
        _audit(conn, organization_name=org, at=at, actor_username=me,
               command="request_approval", target_type="task",
               target_username=p["task_id"], outcome=p["approver_username"])
        return task


def _agent_approve_task(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        row = tasks.get(conn, p["task_id"])
        if row is None or row["approver_username"] != me:
            raise ApiError(TASK_NOT_FOUND)  # non-divulgation : invisible pour un non-approbateur
        org = _org_of(conn, me)
        if row["state"] != tasks.STATE_PENDING_APPROVAL:
            raise ApiError(TASK_STATE_INVALID)
        tasks.set_state(conn, p["task_id"], tasks.STATE_COMPLETED, row["result"], at)
        tasks.add_event(conn, p["task_id"], "approved", me, None, at)
        updated = tasks.get(conn, p["task_id"])
        assert updated is not None
        task = tasks.row_to_task(conn, updated)
        events.append_for_task(conn, task=task, event_type="task.approved",
                               by_username=me, note=None, at=at,
                               retention_days=_event_retention_days(
                                   conn, org, service.config.event_retention_days))
        _audit(conn, organization_name=org, at=at, actor_username=me,
               command="approve_task", target_type="task", target_username=p["task_id"])
        return task


def _agent_reject_task(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        row = tasks.get(conn, p["task_id"])
        if row is None or row["approver_username"] != me:
            raise ApiError(TASK_NOT_FOUND)
        org = _org_of(conn, me)
        if row["state"] != tasks.STATE_PENDING_APPROVAL:
            raise ApiError(TASK_STATE_INVALID)
        tasks.set_state(conn, p["task_id"], tasks.STATE_IN_PROGRESS, None, at)
        tasks.add_event(conn, p["task_id"], "rejected", me, p["reason"], at)
        updated = tasks.get(conn, p["task_id"])
        assert updated is not None
        task = tasks.row_to_task(conn, updated)
        events.append_for_task(conn, task=task, event_type="task.rejected",
                               by_username=me, note=p["reason"], at=at,
                               retention_days=_event_retention_days(
                                   conn, org, service.config.event_retention_days))
        _audit(conn, organization_name=org, at=at, actor_username=me,
               command="reject_task", target_type="task", target_username=p["task_id"],
               outcome="rejected")
        return task


def _agent_get_my_work(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    limit = p["limit"]
    filters: dict = {}
    last, boundary = service._pagination(p, me, "get_my_work", "work_asc", filters)
    with db.begin_read(conn):
        rows = tasks.list_work(
            conn, me=me, boundary=boundary, last=last, limit=limit
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = []
    for r in rows:
        with db.begin_read(conn):
            out.append(tasks.row_to_task(conn, r))
    next_cursor = None
    if has_more:
        last_row = rows[-1]
        next_cursor = service._next_cursor(
            me, "get_my_work", "work_asc", filters, boundary,
            (last_row["due_at"] or tasks.NO_DUE_AT,
             last_row["created_at"], last_row["task_id"]),
        )
    return {"work_items": out, "next_cursor": next_cursor}


def _agent_get_events(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    limit = p["limit"]
    types = frozenset(p["types"]) if p["types"] else None
    filters = {"types": p["types"]}
    last, boundary = service._pagination(p, me, "get_events", "seq_asc", filters)
    last_seq = int(last[0]) if last else None
    # Append-only journal: the ``seq`` cursor is enough for stability;
    # la borne temporelle du snapshot n'a pas de sens pour un polling
    # (it freezes events created after the first read).
    with db.begin_read(conn):
        rows = events.page(
            conn,
            principal=me,
            types=types,
            boundary=_EVENTS_NO_BOUNDARY,
            last_seq=last_seq,
            limit=limit,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = [events.row_to_event(r) for r in rows]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            me, "get_events", "seq_asc", filters, boundary,
            (str(rows[-1]["seq"]), ""),
        )
    return {"events": out, "next_cursor": next_cursor}


def _org_set_event_retention_days(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Configurable retention of consultable events (SPEC.txt F10)."""
    at = now_utc()
    with db.begin_immediate(conn):
        conn.execute(
            "INSERT INTO org_settings (organization_name, event_retention_days) "
            "VALUES (?, ?) "
            "ON CONFLICT(organization_name) DO UPDATE SET "
            "event_retention_days = excluded.event_retention_days",
            (org_name, p["days"]),
        )
        _audit(conn, organization_name=org_name, at=at, actor_username=org_name,
               command="set_event_retention_days", target_type="organization",
               target_username=org_name, outcome="ok")
    return {"event_retention_days": p["days"]}


def _org_set_escalation_policy(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        _org_require_member(conn, p["escalate_to_username"], org_name)
        conn.execute(
            "INSERT INTO org_escalation_policy (organization_name, enabled, "
            "due_after_seconds, failed_after_seconds, escalate_to_username) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(organization_name) DO UPDATE SET enabled = excluded.enabled, "
            "due_after_seconds = excluded.due_after_seconds, "
            "failed_after_seconds = excluded.failed_after_seconds, "
            "escalate_to_username = excluded.escalate_to_username",
            (org_name, int(p["enabled"]), p["due_after_seconds"],
             p["failed_after_seconds"], p["escalate_to_username"]),
        )
        _audit(conn, organization_name=org_name, at=at, actor_username=org_name,
               command="set_escalation_policy", target_type="organization",
               target_username=org_name, outcome="ok")
    return {
        "enabled": p["enabled"],
        "due_after_seconds": p["due_after_seconds"],
        "failed_after_seconds": p["failed_after_seconds"],
        "escalate_to_username": p["escalate_to_username"],
    }


def _org_get_escalation_policy(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Lecture de la politique d'escalation of theorganisation (SPEC_CLI
    ``policy escalation``, lecture). Retourne la politique courante, ou
    the default state (disabled) if it was never configured."""
    row = conn.execute(
        "SELECT enabled, due_after_seconds, failed_after_seconds, "
        "escalate_to_username FROM org_escalation_policy WHERE organization_name = ?",
        (org_name,),
    ).fetchone()
    if row is None:
        return {
            "organization_name": org_name,
            "enabled": False,
            "due_after_seconds": None,
            "failed_after_seconds": None,
            "escalate_to_username": None,
        }
    return {
        "organization_name": org_name,
        "enabled": bool(row["enabled"]),
        "due_after_seconds": row["due_after_seconds"],
        "failed_after_seconds": row["failed_after_seconds"],
        "escalate_to_username": row["escalate_to_username"],
    }


def _org_set_agent_budget(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        _org_require_member(conn, p["username"], org_name)
        if p["max_active_tasks"] is None and p["max_messages_per_hour"] is None:
            conn.execute("DELETE FROM agent_budgets WHERE username = ?", (p["username"],))
        else:
            conn.execute(
                "INSERT INTO agent_budgets (username, max_active_tasks, "
                "max_messages_per_hour) VALUES (?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET "
                "max_active_tasks = excluded.max_active_tasks, "
                "max_messages_per_hour = excluded.max_messages_per_hour",
                (p["username"], p["max_active_tasks"], p["max_messages_per_hour"]),
            )
        _audit(conn, organization_name=org_name, at=at, actor_username=org_name,
               command="set_agent_budget", target_type="agent",
               target_username=p["username"], outcome="ok")
    return {
        "username": p["username"],
        "max_active_tasks": p["max_active_tasks"],
        "max_messages_per_hour": p["max_messages_per_hour"],
    }


def _org_create_department(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Creates a department of the organization (SPEC.txt F13)."""
    at = now_utc()
    with db.begin_immediate(conn):
        exists = conn.execute(
            "SELECT 1 FROM departments WHERE organization_name = ? AND department_name = ?",
            (org_name, p["department_name"]),
        ).fetchone()
        if exists is not None:
            raise ApiError(INVALID_ARGUMENT, "This department already exists")
        conn.execute(
            "INSERT INTO departments (organization_name, department_name, created_at) "
            "VALUES (?, ?, ?)",
            (org_name, p["department_name"], at),
        )
    return {"department_name": p["department_name"], "organization_name": org_name}


def _org_set_agent_department(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Attaches an agent of the organization to a department with a fixed role
    (SPEC.txt F13/F14). The department must belong to the same organization."""
    at = now_utc()
    with db.begin_immediate(conn):
        _org_require_member(conn, p["username"], org_name)
        dept = conn.execute(
            "SELECT 1 FROM departments WHERE organization_name = ? AND department_name = ?",
            (org_name, p["department_name"]),
        ).fetchone()
        if dept is None:
            raise ApiError(USER_NOT_FOUND, "Department unknown in this organization")
        conn.execute(
            "INSERT INTO memberships (username, organization_name, department_name, "
            "role, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(username) DO UPDATE SET department_name = excluded.department_name, "
            "role = excluded.role, created_at = excluded.created_at",
            (p["username"], org_name, p["department_name"], p["role"], at),
        )
    return {
        "username": p["username"],
        "department_name": p["department_name"],
        "role": p["role"],
    }


def _structure_by_department(
    conn: sqlite3.Connection, org_name: str
) -> dict[str, list[dict]]:
    """Departments of the organization with their members and roles."""
    departments = conn.execute(
        "SELECT department_name FROM departments WHERE organization_name = ? "
        "ORDER BY department_name",
        (org_name,),
    ).fetchall()
    members = conn.execute(
        "SELECT username, department_name, role FROM memberships "
        "WHERE organization_name = ? ORDER BY username",
        (org_name,),
    ).fetchall()
    by_dept: dict[str, list[dict]] = {}
    for d in departments:
        by_dept[d["department_name"]] = []
    for m in members:
        by_dept.setdefault(m["department_name"], []).append(
            {"username": m["username"], "role": m["role"]}
        )
    return by_dept


def _org_get_org_structure(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Returns the organization structure (SPEC.txt F13): departments
    with their members and roles, and unattached agents. Read-only."""
    with db.begin_read(conn):
        by_dept = _structure_by_department(conn, org_name)
        all_agents = accounts.list_by_org(conn, org_name, limit=10_000)
    member_names = {m["username"] for members in by_dept.values() for m in members}
    unassigned = [a["username"] for a in all_agents if a["username"] not in member_names]
    return {
        "organization_name": org_name,
        "departments": [
            {"department_name": name, "members": by_dept[name]}
            for name in sorted(by_dept)
        ],
        "unassigned_agents": unassigned,
    }


def _agent_list_department_tasks(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    """Active tasks of the agents of a department (SPEC.txt F14): reserved for the
    manager of that department (fixed role). Never reveals message
    content — only tasks (coordination metadata)."""
    limit = p["limit"]
    filters = {"department_name": p["department_name"]}
    last, boundary = service._pagination(p, me, "list_department_tasks", "task_asc", filters)
    with db.begin_read(conn):
        membership = conn.execute(
            "SELECT role FROM memberships WHERE username = ? AND department_name = ?",
            (me, p["department_name"]),
        ).fetchone()
        if membership is None or membership["role"] != "manager":
            raise ApiError(ACCESS_DENIED, "Manager role required for this department")
        # The scope is limited to one's own organization: the subquery
        # filters by organization_name, otherwise a same-named department of another
        # autre organisation would expose its tasks to the manager (isolation
        # inter-organisations, contrainte 3 de SPEC.txt).
        org = _org_of(conn, me)
        rows = conn.execute(
            "SELECT task_id, title, assignee_username, state, priority, due_at, "
            "created_at, updated_at FROM tasks "
            "WHERE assignee_username IN "
            "(SELECT username FROM memberships WHERE department_name = ? "
            " AND organization_name = ?) "
            "AND state IN ('submitted', 'in_progress', 'pending_approval') "
            "AND created_at <= ? "
            "AND (? IS NULL OR (created_at > ? OR (created_at = ? AND task_id > ?))) "
            "ORDER BY created_at ASC, task_id ASC LIMIT ?",
            (
                p["department_name"],
                org,
                boundary,
                last[0] if last else None,
                last[0] if last else None,
                last[0] if last else None,
                last[1] if last else None,
                limit + 1,
            ),
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    tasks_out = [dict(r) for r in rows]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            me, "list_department_tasks", "task_asc", filters, boundary,
            (rows[-1]["created_at"], rows[-1]["task_id"]),
        )
    return {"department_name": p["department_name"], "tasks": tasks_out,
            "next_cursor": next_cursor}


def _org_get_audit(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    """Journal d'audit de l'organisation (SPEC.txt F11) : actions des agents et
    de l'organisation, sans contenu. Filtres : depuis une date, par acteur,
    par commande. Append-only (aucune modification possible)."""
    limit = p["limit"]
    filters = {
        "since": p["since"],
        "actor": p["actor_username"],
        "command": p["command"],
    }
    last, boundary = service._pagination(p, org_name, "get_org_audit", "id_asc", filters)
    last_id = int(last[0]) if last else None
    with db.begin_read(conn):
        rows = audit.page(
            conn,
            organization_name=org_name,
            since=filters["since"],
            actor_filter=filters["actor"],
            command_filter=filters["command"],
            boundary=boundary,
            last_id=last_id,
            limit=limit,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    entries = [audit.row_to_entry(r) for r in rows]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            org_name, "get_org_audit", "id_asc", filters, boundary,
            (str(rows[-1]["id"]), ""),
        )
    return {"entries": entries, "next_cursor": next_cursor}


def _org_get_metrics(service: Service, conn: sqlite3.Connection, p: dict, org_name: str) -> dict:
    """Organization metrics (SPEC.txt F12): headcount, tasks by state,
    recent activity. No content data."""
    with db.begin_read(conn):
        total_agents = accounts.count_by_org(conn, org_name)
        active_agents = conn.execute(
            "SELECT COUNT(*) AS n FROM accounts WHERE organization_name = ? AND status = 'active'",
            (org_name,),
        ).fetchone()["n"]
        tasks_by_state = {
            row["state"]: row["n"]
            for row in conn.execute(
                "SELECT state, COUNT(*) AS n FROM tasks "
                "JOIN accounts ON accounts.username = tasks.assignee_username "
                "WHERE accounts.organization_name = ? GROUP BY state",
                (org_name,),
            ).fetchall()
        }
        messages_hour = conn.execute(
            "SELECT COUNT(*) AS n FROM messages "
            "JOIN accounts ON accounts.username = messages.sender_username "
            "WHERE accounts.organization_name = ? AND messages.created_at > ?",
            (org_name, now_utc_offset(3600)),
        ).fetchone()["n"]
    return {
        "organization_name": org_name,
        "total_agents": total_agents,
        "active_agents": active_agents,
        "tasks_by_state": tasks_by_state,
        "messages_last_hour": messages_hour,
    }


def _org_get_server_status(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Server state (SPEC.txt F12): process counters, without business
    data. Accessible to any authenticated organization (no secrets)."""
    uptime = int(time.monotonic() - service._started_at)
    with service._requests_lock:
        requests = service._requests_total
    return {
        "api_version": "v2",
        "commands_count": len(COMMAND_SPECS),
        "requests_total": requests,
        "uptime_seconds": uptime,
        "max_concurrent_connections": 64,
    }


# ===========================================================================
# Groups (F15), reputation (F16), delegation (F17)
# ===========================================================================


def _group_require_member(conn: sqlite3.Connection, group_id: str, me: str) -> sqlite3.Row:
    """Groupe dont ``me`` est membre, sinon ``GROUP_NOT_FOUND`` (non-divulgation :
    un groupe inaccessible est indiscernable d'un groupe inexistant)."""
    group = conn.execute(
        "SELECT group_id, name, created_by, created_at FROM groups WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if group is None:
        raise ApiError(GROUP_NOT_FOUND)
    member = conn.execute(
        "SELECT 1 FROM group_members WHERE group_id = ? AND username = ?",
        (group_id, me),
    ).fetchone()
    if member is None:
        raise ApiError(GROUP_NOT_FOUND)
    return group


def _external_comm_allowed(conn: sqlite3.Connection, me: str, other: str) -> bool:
    """True if an exchange from ``me`` to ``other`` (another organization)
    respects both organizations' policies: outgoing of the sender
    et entrante du destinataire (section 6.2 de SPEC.txt). La communication
    internal is always allowed.
    """
    me_row = accounts.get(conn, me)
    other_row = accounts.get(conn, other)
    if me_row is None or other_row is None:
        return False
    if me_row["organization_name"] == other_row["organization_name"]:
        return True
    me_org = organizations.get(conn, me_row["organization_name"])
    other_org = organizations.get(conn, other_row["organization_name"])
    if me_org is None or other_org is None:
        return False
    return bool(me_org["allow_outgoing_external"] and other_org["allow_incoming_external"])


def _agent_create_group(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        group_id = messages.new_uuid()
        conn.execute(
            "INSERT INTO groups (group_id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
            (group_id, p["name"], me, at),
        )
        conn.execute(
            "INSERT INTO group_members (group_id, username, added_by, added_at) VALUES (?, ?, ?, ?)",
            (group_id, me, me, at),
        )
    return {"group_id": group_id, "name": p["name"], "created_by": me, "created_at": at}


def _agent_add_group_member(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        _group_require_member(conn, p["group_id"], me)
        _require_active_account(conn, p["username"])
        # Un groupe ne contourne pas les politiques de communication externe :
        # adding a member from another organization is subject to the same
        # rules as a send (outgoing of the adder, incoming of the org of the
        # membre), sinon l'isolation par politiques serait neutralisable.
        if not _external_comm_allowed(conn, me, p["username"]):
            raise ApiError(
                POLICY_DENIED,
                "External communication denied by the organization policy",
            )
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, username, added_by, added_at) "
            "VALUES (?, ?, ?, ?)",
            (p["group_id"], p["username"], me, at),
        )
    return {"group_id": p["group_id"], "username": p["username"]}


def _agent_remove_group_member(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    with db.begin_immediate(conn):
        group = _group_require_member(conn, p["group_id"], me)
        # Only the group creator removes another member; a member can
        # always leave by themselves (SPEC.txt F15: "a participant leaves
        # le groupe »). Sans cette restriction, tout membre pourrait exclure
        # the others — including the creator — from their own channel.
        if me != group["created_by"] and me != p["username"]:
            raise ApiError(ACCESS_DENIED, "Only the group creator removes a member")
        conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND username = ?",
            (p["group_id"], p["username"]),
        )
    return {"group_id": p["group_id"], "username": p["username"]}


def _agent_send_group_message(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        _group_require_member(conn, p["group_id"], me)
        if p["client_message_id"] is not None:
            existing = conn.execute(
                "SELECT message_id FROM group_messages WHERE sender_username = ? "
                "AND client_message_id = ?",
                (me, p["client_message_id"]),
            ).fetchone()
            if existing is not None:
                row = conn.execute(
                    "SELECT * FROM group_messages WHERE message_id = ?",
                    (existing["message_id"],),
                ).fetchone()
                return {
                    "message_id": row["message_id"],
                    "group_id": row["group_id"],
                    "sender_username": row["sender_username"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                }
        # Budget de messages (F9) : s'applique aussi aux messages de groupe
        # (the quota covers sent messages, across all channels). The
        # idempotent retrieval above stays priority: an already-validated
        # message is returned even if the budget is reached.
        _check_message_budget(conn, me)
        # Politiques de communication externe : un message de groupe ne doit
        # cannot bypass the policies — checked for each member of a
        # autre organisation, au moment de l'envoi (les messages existants
        # restent accessibles, section 6.3 de SPEC.txt).
        for member_row in conn.execute(
            "SELECT username FROM group_members WHERE group_id = ?", (p["group_id"],)
        ).fetchall():
            if member_row["username"] != me and not _external_comm_allowed(
                conn, me, member_row["username"]
            ):
                raise ApiError(
                    POLICY_DENIED,
                    "External communication denied by the organization policy",
                )
        message_id = messages.new_uuid()
        conn.execute(
            "INSERT INTO group_messages (message_id, group_id, client_message_id, "
            "sender_username, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, p["group_id"], p["client_message_id"], me, p["message"], at),
        )
    return {
        "message_id": message_id,
        "group_id": p["group_id"],
        "sender_username": me,
        "content": p["message"],
        "created_at": at,
    }


def _agent_get_group_messages(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    limit = p["limit"]
    filters = {"group_id": p["group_id"]}
    last, boundary = service._pagination(p, me, "get_group_messages", _SORT_DESC, filters)
    with db.begin_read(conn):
        _group_require_member(conn, p["group_id"], me)
        rows = conn.execute(
            "SELECT message_id, group_id, sender_username, content, created_at "
            "FROM group_messages WHERE group_id = ? AND created_at <= ? "
            "AND (? IS NULL OR (created_at < ? OR (created_at = ? AND message_id < ?))) "
            "ORDER BY created_at DESC, message_id DESC LIMIT ?",
            (
                p["group_id"],
                boundary,
                last[0] if last else None,
                last[0] if last else None,
                last[0] if last else None,
                last[1] if last else None,
                limit + 1,
            ),
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = [dict(r) for r in rows]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            me, "get_group_messages", _SORT_DESC, filters, boundary,
            (rows[-1]["created_at"], rows[-1]["message_id"]),
        )
    return {"group_id": p["group_id"], "messages": out, "next_cursor": next_cursor}


def _agent_get_group_members(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    with db.begin_read(conn):
        _group_require_member(conn, p["group_id"], me)
        members = conn.execute(
            "SELECT username FROM group_members WHERE group_id = ? ORDER BY added_at, username",
            (p["group_id"],),
        ).fetchall()
    return {"group_id": p["group_id"], "members": [m["username"] for m in members]}


def _agent_list_my_groups(service: Service, conn: sqlite3.Connection, p: dict, me: str) -> dict:
    limit = p["limit"]
    filters: dict = {}
    last, boundary = service._pagination(p, me, "list_my_groups", _SORT_ASC, filters)
    with db.begin_read(conn):
        rows = conn.execute(
            "SELECT g.group_id, g.name, g.created_by, g.created_at, "
            "(SELECT COUNT(*) FROM group_members m WHERE m.group_id = g.group_id) AS member_count "
            "FROM groups g JOIN group_members gm ON gm.group_id = g.group_id "
            "WHERE gm.username = ? AND g.created_at <= ? "
            "AND (? IS NULL OR (g.created_at > ? OR (g.created_at = ? AND g.group_id > ?))) "
            "ORDER BY g.created_at ASC, g.group_id ASC LIMIT ?",
            (
                me,
                boundary,
                last[0] if last else None,
                last[0] if last else None,
                last[0] if last else None,
                last[1] if last else None,
                limit + 1,
            ),
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = [dict(r) for r in rows]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            me, "list_my_groups", _SORT_ASC, filters, boundary,
            (rows[-1]["created_at"], rows[-1]["group_id"]),
        )
    return {"groups": out, "next_cursor": next_cursor}


def _reputation_counts_many(
    conn: sqlite3.Connection, usernames: list[str]
) -> dict[str, dict]:
    """Reputation accounts (F16) for several agents, in a single
    grouped request. Never declarative: everything derives from tasks."""
    if not usernames:
        return {}
    placeholders = ", ".join("?" for _ in usernames)
    rows = conn.execute(
        "SELECT assignee_username, state, COUNT(*) AS n FROM tasks "
        f"WHERE assignee_username IN ({placeholders}) "
        "GROUP BY assignee_username, state",
        usernames,
    ).fetchall()
    out = {
        u: {"completed": 0, "failed": 0, "canceled": 0, "active": 0,
            "completion_rate": None}
        for u in usernames
    }
    for row in rows:
        user = row["assignee_username"]
        state = row["state"]
        if state == "completed":
            out[user]["completed"] = row["n"]
        elif state == "failed":
            out[user]["failed"] = row["n"]
        elif state == "canceled":
            out[user]["canceled"] = row["n"]
        else:
            out[user]["active"] += row["n"]
    for detail in out.values():
        done = detail["completed"] + detail["failed"]
        if done:
            detail["completion_rate"] = round(detail["completed"] / done, 4)
    return out


def _reputation_summary(
    conn: sqlite3.Connection, username: str, viewer: str
) -> dict:
    """Reputation of an agent (F16): detail for oneself, qualitative
    qualitative pour les autres."""
    detail = _reputation_counts_many(conn, [username])[username]
    if username == viewer:
        return {"username": username, **detail}
    rate = detail["completion_rate"]
    if rate is None:
        qualitative = "unknown"
    elif rate >= 0.9:
        qualitative = "excellent"
    elif rate >= 0.7:
        qualitative = "good"
    elif rate >= 0.5:
        qualitative = "average"
    else:
        qualitative = "poor"
    return {"username": username, "qualitative": qualitative}


def _agent_get_agent_reputation(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    """Reputation measured by the server (SPEC.txt F16): statistics of the
    agent's completed tasks. Detailed figures for oneself, qualitative mention
    for others. Never declarative."""
    with db.begin_read(conn):
        if accounts.get(conn, p["username"]) is None:
            raise ApiError(USER_NOT_FOUND)
        return _reputation_summary(conn, p["username"], me)


def _agent_create_delegation(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        row, _ = _task_visible_or_404(conn, p["task_id"], me)
        _require_active_account(conn, p["delegatee_username"])
        if p["expires_at"] <= at:
            raise ApiError(INVALID_ARGUMENT, "expires_at must be in the future")
        conn.execute(
            "INSERT INTO delegations (delegator_username, delegatee_username, task_id, "
            "expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (me, p["delegatee_username"], p["task_id"], p["expires_at"], at),
        )
        org = _org_of(conn, me)
        _audit(conn, organization_name=org, at=at, actor_username=me,
               command="create_delegation", target_type="task",
               target_username=p["task_id"], outcome=p["delegatee_username"])
    return {
        "task_id": p["task_id"],
        "delegatee_username": p["delegatee_username"],
        "expires_at": p["expires_at"],
        "created_at": at,
    }


def _agent_revoke_delegation(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    at = now_utc()
    with db.begin_immediate(conn):
        row, _ = _task_visible_or_404(conn, p["task_id"], me)
        deleted = conn.execute(
            "DELETE FROM delegations WHERE delegator_username = ? AND task_id = ? "
            "AND delegatee_username = ?",
            (me, p["task_id"], p["delegatee_username"]),
        )
        org = _org_of(conn, me)
        _audit(conn, organization_name=org, at=at, actor_username=me,
               command="revoke_delegation", target_type="task",
               target_username=p["task_id"], outcome=p["delegatee_username"])
    return {"task_id": p["task_id"], "delegatee_username": p["delegatee_username"],
            "revoked": deleted.rowcount > 0}


def _agent_get_my_delegations(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    limit = p["limit"]
    filters: dict = {}
    last, boundary = service._pagination(p, me, "get_my_delegations", "id_asc", filters)
    last_id = int(last[0]) if last else None
    with db.begin_read(conn):
        rows = conn.execute(
            "SELECT id, delegator_username, task_id, expires_at, created_at "
            "FROM delegations WHERE delegatee_username = ? AND expires_at > ? AND id > ? "
            "ORDER BY id ASC LIMIT ?",
            (me, now_utc(), last_id if last_id is not None else 0, limit + 1),
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = [dict(r) for r in rows]
    next_cursor = None
    if has_more:
        next_cursor = service._next_cursor(
            me, "get_my_delegations", "id_asc", filters, boundary,
            (str(rows[-1]["id"]), ""),
        )
    return {"delegations": out, "next_cursor": next_cursor}


def _org_create_observer_account(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Creates an observer account (SPEC.txt F18): read-only, intended for
    the web interface. Write commands are refused to it (dispatch).
    C'is a READ-ONLY account (principal_type 'agent') : jamais un compte
    human — except explicit marking, auth does not delegate to the org."""
    with db.begin_immediate(conn):
        existing = accounts.get(conn, p["observer_name"])
        if existing is not None:
            raise ApiError(USERNAME_ALREADY_EXISTS)
        password_hash = hash_password(p["password"])
        accounts.insert(
            conn,
            p["observer_name"],
            password_hash,
            "active",
            p["description"],
            org_name,
            can_see_org_agents=True,
            principal_type="agent",
        )
        conn.execute(
            "UPDATE accounts SET is_observer = 1 WHERE username = ?",
            (p["observer_name"],),
        )
    return {
        "observer_name": p["observer_name"],
        "status": "active",
        "organization_name": org_name,
        "read_only": True,
    }


def _org_revoke_observer_account(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Deactivates an observer account (SPEC.txt F18)."""
    with db.begin_immediate(conn):
        _org_require_member(conn, p["observer_name"], org_name)
        conn.execute(
            "UPDATE accounts SET status = 'disabled' WHERE username = ?",
            (p["observer_name"],),
        )
    return {"observer_name": p["observer_name"], "status": "disabled"}


def _org_list_observers(
    service: Service, conn: sqlite3.Connection, p: dict, org_name: str
) -> dict:
    """Lists the observer accounts of the organization (SPEC.txt F18)."""
    with db.begin_read(conn):
        rows = conn.execute(
            "SELECT username, description, status, created_at FROM accounts "
            "WHERE organization_name = ? AND is_observer = 1 ORDER BY username",
            (org_name,),
        ).fetchall()
    return {"observers": [dict(r) for r in rows]}


def _agent_get_org_snapshot(
    service: Service, conn: sqlite3.Connection, p: dict, me: str
) -> dict:
    """Aggregated view of the organization for an observer or human account
    (SPEC.txt F18, SPEC-WEB §1): directory, tasks by state, structure,
    recent audit and metrics — never message content. Reserved
    aux comptes observers et humains de l'organisation."""
    with db.begin_read(conn):
        row = accounts.get(conn, me)
        assert row is not None
        if not (bool(row["is_observer"]) or row["principal_type"] == "human"):
            raise ApiError(ACCESS_DENIED, "Compte observer ou humain requis")
        org = row["organization_name"]
        agents = conn.execute(
            "SELECT username, description, status, principal_type, is_observer "
            "FROM accounts WHERE organization_name = ? ORDER BY username",
            (org,),
        ).fetchall()
        tasks_by_state = {
            r["state"]: r["n"]
            for r in conn.execute(
                "SELECT state, COUNT(*) AS n FROM tasks "
                "JOIN accounts ON accounts.username = tasks.assignee_username "
                "WHERE accounts.organization_name = ? GROUP BY state",
                (org,),
            ).fetchall()
        }
        departments = [
            {"department_name": d, "members": members}
            for d, members in _structure_by_department(conn, org).items()
        ]
        recent_audit = conn.execute(
            "SELECT id, at, actor_username, command, outcome FROM audit_log "
            "WHERE organization_name = ? ORDER BY id DESC LIMIT 20",
            (org,),
        ).fetchall()
        messages_hour = conn.execute(
            "SELECT COUNT(*) AS n FROM messages "
            "JOIN accounts ON accounts.username = messages.sender_username "
            "WHERE accounts.organization_name = ? AND messages.created_at > ?",
            (org, now_utc_offset(3600)),
        ).fetchone()["n"]
        # Internal communication flows (metadata only — never
        # content): agent pairs, volume, last exchange, unread. External
        # exchanges are only visible through volumes.
        conversations = conn.execute(
            "SELECT MIN(m.sender_username, m.recipient_username) AS a, "
            "       MAX(m.sender_username, m.recipient_username) AS b, "
            "       COUNT(*) AS message_count, MAX(m.created_at) AS last_at, "
            "       SUM(CASE WHEN m.read_at IS NULL THEN 1 ELSE 0 END) AS unread_count "
            "FROM messages m "
            "JOIN accounts AS senders ON senders.username = m.sender_username "
            "JOIN accounts AS recipients ON recipients.username = m.recipient_username "
            "WHERE senders.organization_name = ? AND recipients.organization_name = ? "
            "GROUP BY m.conversation_id ORDER BY last_at DESC LIMIT 300",
            (org, org),
        ).fetchall()
        # Organization tasks as metadata (without title, description,
        # result or business reference — treated as content, F4/F5).
        tasks = conn.execute(
            "SELECT t.task_id, t.state, t.priority, t.creator_username, "
            "       t.assignee_username, t.approver_username, t.due_at, "
            "       t.created_at, t.updated_at "
            "FROM tasks t "
            "JOIN accounts AS creators ON creators.username = t.creator_username "
            "JOIN accounts AS assignees ON assignees.username = t.assignee_username "
            "WHERE creators.organization_name = ? OR assignees.organization_name = ? "
            "ORDER BY t.updated_at DESC LIMIT 300",
            (org, org),
        ).fetchall()
    return {
        "organization_name": org,
        "agents": [dict(a) for a in agents],
        "tasks_by_state": tasks_by_state,
        "departments": departments,
        "recent_audit": [dict(e) for e in recent_audit],
        "messages_last_hour": messages_hour,
        "conversations": [dict(c) for c in conversations],
        "tasks": [dict(t) for t in tasks],
    }


def queries_row_to_message_as_of(row: sqlite3.Row, boundary: str) -> dict:
    return messages.row_to_message_as_of(row, boundary)


_ORG_HANDLERS: dict[str, Callable[..., dict]] = {
    "create_agent": _org_create_agent,
    "deactivate_agent": _org_deactivate_agent,
    "reactivate_agent": _org_reactivate_agent,
    "change_agent_password": _org_change_password,
    "set_agent_visibility": _org_set_visibility,
    "get_org_agents": _org_get_agents,
    "set_organization_policy": _org_set_policy,
    "get_organization_policy": _org_get_policy,
    "change_organization_password": _org_change_organization_password,
    "change_agent_description": _org_change_agent_description,
    "approve_agent_card": _org_approve_agent_card,
    "set_escalation_policy": _org_set_escalation_policy,
    "get_escalation_policy": _org_get_escalation_policy,
    "set_agent_budget": _org_set_agent_budget,
    "create_department": _org_create_department,
    "set_agent_department": _org_set_agent_department,
    "get_org_structure": _org_get_org_structure,
    "get_org_audit": _org_get_audit,
    "get_org_metrics": _org_get_metrics,
    "get_server_status": _org_get_server_status,
    "create_observer_account": _org_create_observer_account,
    "revoke_observer_account": _org_revoke_observer_account,
    "list_observers": _org_list_observers,
    "set_event_retention_days": _org_set_event_retention_days,
}

# Commands reserved for human accounts (SPEC-WEB): management des
# organisations et lecture de contenu de l'organisation.
_HUMAN_HANDLERS: dict[str, Callable[..., dict]] = {
    "create_org": _human_create_org,
    "disable_org": _human_disable_org,
    "list_org_conversations": _human_list_org_conversations,
    "get_org_conversation": _human_get_org_conversation,
}

_AGENT_HANDLERS: dict[str, Callable[..., dict]] = {
    "get_my_organization": _get_my_organization,
    "get_agent_description": _get_agent_description,
    "list_org_agents": _list_org_agents,
    "help": _help,
    "send_message": _send_message,
    "get_messages": _get_messages,
    "get_conversation": _get_conversation,
    "read_message": _read_message,
    "get_notifications": _get_notifications,
    "mark_conversation_no_reply": _mark_conversation_no_reply,
    "set_agent_card": _agent_set_agent_card,
    "get_agent_card": _agent_get_agent_card,
    "find_agents": _agent_find_agents,
    "create_task": _agent_create_task,
    "get_task": _agent_get_task,
    "list_tasks": _agent_list_tasks,
    "update_task_state": _agent_update_task_state,
    "transfer_task": _agent_transfer_task,
    "request_approval": _agent_request_approval,
    "approve_task": _agent_approve_task,
    "reject_task": _agent_reject_task,
    "get_my_work": _agent_get_my_work,
    "get_events": _agent_get_events,
    "list_department_tasks": _agent_list_department_tasks,
    "create_group": _agent_create_group,
    "add_group_member": _agent_add_group_member,
    "remove_group_member": _agent_remove_group_member,
    "send_group_message": _agent_send_group_message,
    "get_group_messages": _agent_get_group_messages,
    "get_group_members": _agent_get_group_members,
    "list_my_groups": _agent_list_my_groups,
    "get_agent_reputation": _agent_get_agent_reputation,
    "create_delegation": _agent_create_delegation,
    "revoke_delegation": _agent_revoke_delegation,
    "get_my_delegations": _agent_get_my_delegations,
    "get_org_snapshot": _agent_get_org_snapshot,
}
