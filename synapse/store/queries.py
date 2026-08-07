"""Read queries: stable pagination, notifications, reply states.

All paginated reads are frozen at a ``boundary`` (read
snapshot) : seuls les messages avec ``created_at <= boundary`` sont
boundary), and the read status is evaluated as it was at the boundary
(``read_at`` NULL or after the boundary = unread at the boundary).

Pagination is "keyset" style: the position is encoded in the
curseur sous la forme ``(created_at, message_id)`` (ou
``(last_received_at, conversation_id)`` pour les notifications) et la page
next page resumes exactly after that position with the same ordering.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..store.messages import _MESSAGE_READ_FIELDS, get_no_reply

# Descending order: get_messages and get_notifications.
# Tri croissant : get_conversation.
SORT_DESC = "desc"
SORT_ASC = "asc"


# ---------------------------------------------------------------------------
# Reply state (computed, never stored directly)
# ---------------------------------------------------------------------------


def reply_status(
    conn: sqlite3.Connection,
    conversation_id: str,
    username: str,
    boundary: str,
) -> tuple[str, str | None]:
    """Computes the reply state of a participant, frozen at ``boundary``.

    ``needs_reply`` si et seulement si :
      1. a received message exists (at the boundary);
      2. it was sent by the other agent;
      3. it is read (at the boundary);
      4. no message sent by the current agent is later than it;
      5. il n'est pas couvert par un marquage ``no_reply_needed``.
    """
    row = conn.execute(
        "SELECT message_id, sender_username, created_at, read_at FROM messages "
        "WHERE conversation_id = ? AND recipient_username = ? AND created_at <= ? "
        "ORDER BY created_at DESC, message_id DESC LIMIT 1",
        (conversation_id, username, boundary),
    ).fetchone()
    if row is None:
        return "no_reply_needed", None
    last_id = row["message_id"]
    # Rule 2: sent by the other agent (always true in a two-party
    # conversation, kept defensively).
    if row["sender_username"] == username:
        return "no_reply_needed", last_id
    # Rule 3: read at the boundary.
    if row["read_at"] is None or row["read_at"] > boundary:
        return "no_reply_needed", last_id
    # Rule 4: no own message later (at the boundary).
    later = conn.execute(
        "SELECT 1 FROM messages "
        "WHERE conversation_id = ? AND sender_username = ? AND created_at <= ? "
        "AND (created_at > ? OR (created_at = ? AND message_id > ?)) LIMIT 1",
        (conversation_id, username, boundary, row["created_at"], row["created_at"], last_id),
    ).fetchone()
    if later is not None:
        return "no_reply_needed", last_id
    # Rule 5: no_reply_needed marking on this precise message.
    mark = get_no_reply(conn, conversation_id, username)
    if mark is not None and mark["no_reply_for_message_id"] == last_id:
        return "no_reply_needed", last_id
    return "needs_reply", last_id


def last_received_message(
    conn: sqlite3.Connection, conversation_id: str, username: str
) -> sqlite3.Row | None:
    """Last message received by ``username`` in the conversation (real time)."""
    return conn.execute(
        "SELECT message_id FROM messages "
        "WHERE conversation_id = ? AND recipient_username = ? "
        "ORDER BY created_at DESC, message_id DESC LIMIT 1",
        (conversation_id, username),
    ).fetchone()


# ---------------------------------------------------------------------------
# get_messages (descending order, messages received by the agent)
# ---------------------------------------------------------------------------


def message_page(
    conn: sqlite3.Connection,
    *,
    username: str,
    boundary: str,
    status: str | None,
    sender: str | None,
    conversation_id: str | None,
    last: tuple[str, ...] | None,
    limit: int,
) -> list[sqlite3.Row]:
    """Page of ``get_messages``: up to ``limit`` rows, plus one if the
    page suivante existe (l'appelant teste ``len > limit``)."""
    clauses = ["recipient_username = ?", "created_at <= ?"]
    params: list[Any] = [username, boundary]
    if status == "unread":
        clauses.append("(read_at IS NULL OR read_at > ?)")
        params.append(boundary)
    elif status == "read":
        clauses.append("(read_at IS NOT NULL AND read_at <= ?)")
        params.append(boundary)
    if sender is not None:
        clauses.append("sender_username = ?")
        params.append(sender)
    if conversation_id is not None:
        clauses.append("conversation_id = ?")
        params.append(conversation_id)
    if last is not None:
        last_created, last_id = last
        clauses.append("(created_at < ? OR (created_at = ? AND message_id < ?))")
        params.extend([last_created, last_created, last_id])
    params.append(limit + 1)
    query = (
        f"SELECT {_MESSAGE_READ_FIELDS} FROM messages "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC, message_id DESC LIMIT ?"
    )
    return conn.execute(query, params).fetchall()


# ---------------------------------------------------------------------------
# get_conversation (tri croissant)
# ---------------------------------------------------------------------------


def conversation_page(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    boundary: str,
    last: tuple[str, ...] | None,
    limit: int,
) -> list[sqlite3.Row]:
    """Page de ``get_conversation`` : ordre chronologique croissant."""
    clauses = ["conversation_id = ?", "created_at <= ?"]
    params: list[Any] = [conversation_id, boundary]
    if last is not None:
        last_created, last_id = last
        clauses.append("(created_at > ? OR (created_at = ? AND message_id > ?))")
        params.extend([last_created, last_created, last_id])
    params.append(limit + 1)
    query = (
        f"SELECT {_MESSAGE_READ_FIELDS} FROM messages "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at ASC, message_id ASC LIMIT ?"
    )
    return conn.execute(query, params).fetchall()


# ---------------------------------------------------------------------------
# get_notifications
# ---------------------------------------------------------------------------


def unread_by_sender(conn: sqlite3.Connection, username: str, boundary: str) -> dict[str, int]:
    """Unread message counts by sender, frozen at ``boundary``."""
    rows = conn.execute(
        "SELECT sender_username, COUNT(*) AS n FROM messages "
        "WHERE recipient_username = ? AND created_at <= ? "
        "AND (read_at IS NULL OR read_at > ?) "
        "GROUP BY sender_username",
        (username, boundary, boundary),
    ).fetchall()
    return {row["sender_username"]: int(row["n"]) for row in rows}


def notification_page(
    conn: sqlite3.Connection,
    *,
    username: str,
    boundary: str,
    last: tuple[str, ...] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Page of ``needs_reply`` conversations, frozen at ``boundary``.

    Ordering: descending date of the last received message, then conversation_id
    descending. The ``needs_reply`` condition is evaluated **in SQL** (before
    ``LIMIT``): a conversation whose last received message is unread does not
    consume a page slot and never makes a conversation disappear
    that requires a reply.
    """
    clauses = [
        "l.rn = 1",
        # Rule 3: the last received message is read at the boundary.
        "l.read_at IS NOT NULL AND l.read_at <= ?",
        # Rule 4: no message sent by the current agent is later
        # than the last received message (at the boundary).
        "NOT EXISTS ("
        "SELECT 1 FROM messages x "
        "WHERE x.conversation_id = l.conversation_id AND x.sender_username = ? "
        "AND x.created_at <= ? "
        "AND (x.created_at > l.created_at "
        "OR (x.created_at = l.created_at AND x.message_id > l.message_id)))",
        # Rule 5: no no_reply_needed marking on this precise message.
        "(r.no_reply_for_message_id IS NULL OR r.no_reply_for_message_id != l.message_id)",
    ]
    if last is not None:
        last_received_at, last_conversation_id = last
        clauses.append(
            "(l.created_at < ? OR (l.created_at = ? AND l.conversation_id < ?))"
        )
    query = f"""
        WITH lastrec AS (
            SELECT conversation_id, message_id, sender_username, created_at, read_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY conversation_id
                       ORDER BY created_at DESC, message_id DESC
                   ) AS rn
            FROM messages
            WHERE recipient_username = ? AND created_at <= ?
        )
        SELECT l.conversation_id,
               l.message_id            AS last_message_id,
               l.sender_username       AS last_sender,
               l.created_at            AS last_received_at,
               l.read_at               AS last_read_at,
               c.key                   AS conv_key,
               r.no_reply_for_message_id,
               (SELECT a.organization_name FROM accounts a
                 WHERE a.username = l.sender_username) AS other_organization_name,
               (SELECT COUNT(*) FROM messages m
                WHERE m.conversation_id = l.conversation_id
                  AND m.recipient_username = ?
                  AND m.created_at <= ?
                  AND (m.read_at IS NULL OR m.read_at > ?)) AS unread_count
        FROM lastrec l
        JOIN conversations c ON c.conversation_id = l.conversation_id
        LEFT JOIN reply_state r
               ON r.conversation_id = l.conversation_id AND r.username = ?
        WHERE {' AND '.join(clauses)}
        ORDER BY l.created_at DESC, l.conversation_id DESC
        LIMIT ?
    """
    # Parameters in order of appearance of '?' in the query.
    params: list[Any] = [
        username, boundary,  # lastrec
        username, boundary, boundary,  # unread_count
        username,  # reply_state
        boundary,  # rule 3
        username, boundary,  # rule 4
    ]
    if last is not None:
        last_received_at, last_conversation_id = last
        params.extend([last_received_at, last_received_at, last_conversation_id])
    params.append(limit + 1)

    rows = conn.execute(query, params).fetchall()
    return [
        {
            "conversation_id": row["conversation_id"],
            "other_username": _other_participant(row["conv_key"], username),
            "other_organization_name": row["other_organization_name"],
            "unread_count": int(row["unread_count"]),
            "last_received_at": row["last_received_at"],
        }
        for row in rows
    ]


def _other_participant(conv_key: str, username: str) -> str:
    low, high = conv_key.split(":", 1)
    return high if low == username else low
