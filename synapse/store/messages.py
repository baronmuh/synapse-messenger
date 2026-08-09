"""Messages, conversations and reply states (low-level access).

All functions receive a connection already placed in the
transaction wanted by the caller (``begin_immediate`` for writes).
Business logic (validation, idempotency, authorization) lives in the
service.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from ..errors import ApiError, MESSAGE_ALREADY_EXISTS, MESSAGE_ALREADY_EXISTS_CLIENT_ID

_MESSAGE_FIELDS = (
    "message_id, conversation_id, client_message_id, sender_username, "
    "recipient_username, content, business_reference, created_at, read_at"
)

# Read fields: includes the sender organization at the time of
# reading (metadata stored on the account, never rewritten on the message).
_MESSAGE_READ_FIELDS = (
    "message_id, conversation_id, client_message_id, sender_username, "
    "recipient_username, content, business_reference, created_at, read_at, "
    "(SELECT a.organization_name FROM accounts a "
    " WHERE a.username = messages.sender_username) AS sender_organization_name"
)

MESSAGE_SCHEMA_KEYS = (
    "message_id",
    "conversation_id",
    "client_message_id",
    "sender_username",
    "sender_organization_name",
    "recipient_username",
    "content",
    "business_reference",
    "created_at",
    "status",
    "read_at",
)


def new_uuid() -> str:
    return str(uuid.uuid4())


def conversation_key(username_a: str, username_b: str) -> str:
    """Logical key of a conversation: ``min(a,b) + ":" + max(a,b)``."""
    low, high = sorted((username_a, username_b))
    return f"{low}:{high}"


def get_conversation_by_key(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT conversation_id, key, created_at FROM conversations WHERE key = ?",
        (key,),
    ).fetchone()


def get_conversation_by_id(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT conversation_id, key, created_at FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()


def fetch_or_create_conversation(
    conn: sqlite3.Connection, key: str, created_at: str
) -> str:
    """Returns the existing conversation or creates it.

    The uniqueness constraint on ``key`` guarantees one conversation per
    pair even with simultaneous sends: on collision, the
    existing conversation is re-read.
    """
    row = get_conversation_by_key(conn, key)
    if row is not None:
        return row["conversation_id"]
    conversation_id = new_uuid()
    try:
        conn.execute(
            "INSERT INTO conversations (conversation_id, key, created_at) VALUES (?, ?, ?)",
            (conversation_id, key, created_at),
        )
        return conversation_id
    except sqlite3.IntegrityError:
        row = get_conversation_by_key(conn, key)
        if row is None:
            raise
        return row["conversation_id"]


def insert_message(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    conversation_id: str,
    client_message_id: str,
    sender_username: str,
    recipient_username: str,
    content: str,
    created_at: str,
    business_reference: str | None = None,
) -> None:
    conn.execute(
        f"INSERT INTO messages ({_MESSAGE_FIELDS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            message_id,
            conversation_id,
            client_message_id,
            sender_username,
            recipient_username,
            content,
            business_reference,
            created_at,
        ),
    )


def find_message_by_client_id(
    conn: sqlite3.Connection, sender_username: str, client_message_id: str
) -> sqlite3.Row | None:
    """Finds the existing message for the idempotency key (sender, client id)."""
    return conn.execute(
        f"SELECT {_MESSAGE_READ_FIELDS} FROM messages "
        "WHERE sender_username = ? AND client_message_id = ?",
        (sender_username, client_message_id),
    ).fetchone()


def get_message_by_id(conn: sqlite3.Connection, message_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_MESSAGE_READ_FIELDS} FROM messages WHERE message_id = ?",
        (message_id,),
    ).fetchone()


def mark_read_conditional(conn: sqlite3.Connection, message_id: str, read_at: str) -> None:
    """Marks a message read only if ``read_at`` is still NULL.

    Concurrent reads all return the same first-read date
    (that of the first committed transaction).
    """
    conn.execute(
        "UPDATE messages SET read_at = ? WHERE message_id = ? AND read_at IS NULL",
        (read_at, message_id),
    )


# ---------------------------------------------------------------------------
# Reply states (no_reply_for_message_id / no_reply_marked_at)
# ---------------------------------------------------------------------------


def set_no_reply(
    conn: sqlite3.Connection,
    conversation_id: str,
    username: str,
    no_reply_for_message_id: str | None,
    no_reply_marked_at: str | None,
) -> None:
    """Upserts the reply state of a participant."""
    conn.execute(
        "INSERT INTO reply_state (conversation_id, username, no_reply_for_message_id, no_reply_marked_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(conversation_id, username) DO UPDATE SET "
        "no_reply_for_message_id = excluded.no_reply_for_message_id, "
        "no_reply_marked_at = excluded.no_reply_marked_at",
        (conversation_id, username, no_reply_for_message_id, no_reply_marked_at),
    )


def get_no_reply(conn: sqlite3.Connection, conversation_id: str, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT no_reply_for_message_id, no_reply_marked_at FROM reply_state "
        "WHERE conversation_id = ? AND username = ?",
        (conversation_id, username),
    ).fetchone()


# ---------------------------------------------------------------------------
# Row transformation
# ---------------------------------------------------------------------------


def row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    """Turns a row into a message JSON object (current status)."""
    read_at = row["read_at"]
    return {
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "client_message_id": row["client_message_id"],
        "sender_username": row["sender_username"],
        "sender_organization_name": row["sender_organization_name"],
        "recipient_username": row["recipient_username"],
        "content": row["content"],
        "business_reference": row["business_reference"],
        "created_at": row["created_at"],
        "status": "read" if read_at is not None else "unread",
        "read_at": read_at,
    }


def row_to_message_as_of(row: sqlite3.Row, boundary: str) -> dict[str, Any]:
    """Transforms a row into a JSON object, status frozen at ``boundary``.

    A message read after the boundary is shown as unread in a pagination
    already started (stable snapshot).
    """
    read_at = row["read_at"]
    read_eff = read_at if (read_at is not None and read_at <= boundary) else None
    result = row_to_message(row)
    result["read_at"] = read_eff
    result["status"] = "read" if read_eff is not None else "unread"
    return result


def raise_message_already_exists() -> None:
    raise ApiError(MESSAGE_ALREADY_EXISTS, MESSAGE_ALREADY_EXISTS_CLIENT_ID)
