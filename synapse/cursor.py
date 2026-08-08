"""Opaque, signed pagination cursors.

A cursor is an opaque string containing:
* a JSON payload (version, command, agent, filters, sort, snapshot
  bound, last position);
* an HMAC-SHA256 signature of the payload.

The service verifies the signature and the cursor binding to the current
request (command, agent, filters, ordering). Any invalid, forged or
out-of-context cursor causes ``INVALID_ARGUMENT``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from .errors import ApiError, INVALID_ARGUMENT

CURSOR_VERSION = 1


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64url(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    try:
        return base64.urlsafe_b64decode(token + padding)
    except (ValueError, TypeError) as exc:
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor") from exc


def encode_cursor(secret_key: bytes, payload: dict) -> str:
    """Encodes and signs a cursor payload."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body_b64 = _b64url(body)
    signature = hmac.new(secret_key, body_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{body_b64}.{_b64url(signature)}"


def decode_cursor(secret_key: bytes, cursor: str) -> dict:
    """Decodes and verifies the signature of a cursor.

    Raises ``ApiError(INVALID_ARGUMENT)`` if the format or signature are
    invalid.
    """
    if not isinstance(cursor, str):
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor")
    try:
        body_b64, signature_b64 = cursor.split(".", 1)
    except ValueError as exc:
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor") from exc
    expected = hmac.new(secret_key, body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _unb64url(signature_b64)
    except ApiError:
        raise
    if not hmac.compare_digest(expected, provided):
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor")
    try:
        payload = json.loads(_unb64url(body_b64))
    except (ValueError, ApiError) as exc:
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor") from exc
    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor")
    return payload


def build_payload(
    *,
    command: str,
    username: str,
    boundary: str,
    sort: str,
    last: list | None = None,
    filters: dict | None = None,
) -> dict:
    """Builds the canonical payload of a cursor."""
    payload: dict[str, Any] = {
        "v": CURSOR_VERSION,
        "cmd": command,
        "user": username,
        "boundary": boundary,
        "sort": sort,
    }
    if filters is not None:
        payload["filters"] = filters
    if last is not None:
        payload["last"] = last
    return payload


def validate_cursor_binding(
    payload: dict,
    *,
    command: str,
    username: str,
    sort: str,
    filters: dict | None = None,
) -> None:
    """Verifies that the cursor is bound to the current request.

    A reuse with another command, another agent, other
    filters or another sort causes ``INVALID_ARGUMENT``.
    """
    if payload.get("cmd") != command:
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor for this command")
    if payload.get("user") != username:
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor for this agent")
    if payload.get("sort") != sort:
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor for this sort")
    if payload.get("filters") != (filters or {}):
        raise ApiError(INVALID_ARGUMENT, "Invalid cursor for these filters")
