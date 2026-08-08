"""Consultable events (F10): append-only journal per principal.

Each business event (task created, state changed, transfer, approval,
escalation) is recorded for each concerned principal (creator and assignee)
in the same transaction as the action. Events never contain
message content nor description.
"""

from __future__ import annotations

import sqlite3
from typing import Any

EVENT_TYPES = frozenset(
    {
        "task.created",
        "task.state_changed",
        "task.transferred",
        "task.approval_requested",
        "task.approved",
        "task.rejected",
        "task.escalated",
    }
)


def append(
    conn: sqlite3.Connection,
    *,
    principal: str,
    event_type: str,
    ref_id: str | None,
    by_username: str | None,
    at: str,
    retention_days: int | None = None,
) -> None:
    if retention_days is not None:
        purge_old(conn, retention_days, at)
    conn.execute(
        "INSERT INTO events (principal, event_type, ref_id, by_username, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (principal, event_type, ref_id, by_username, at),
    )


def purge_old(conn: sqlite3.Connection, retention_days: int, at: str) -> None:
    """Configurable retention (SPEC.txt F10): purges events older
    than ``retention_days``, never messages. Bounded by the index
    ``idx_events_at``; synchronous at write (no async process)."""
    from ..validation import now_utc_offset

    cutoff = now_utc_offset(86400.0 * retention_days)
    conn.execute("DELETE FROM events WHERE at < ?", (cutoff,))


def append_for_task(
    conn: sqlite3.Connection,
    *,
    task: Any,
    event_type: str,
    by_username: str,
    note: str | None,
    at: str,
    retention_days: int | None = None,
) -> None:
    """Records the event for the creator and assignee of the task
    (deduplicated if identical). The task is passed as a
    dict (``row_to_task``) — the current creator/assignee is in it."""
    principals = {task["creator_username"], task["assignee_username"]}
    for principal in principals:
        append(
            conn,
            principal=principal,
            event_type=event_type,
            ref_id=task["task_id"],
            by_username=by_username,
            at=at,
            retention_days=retention_days,
        )


def page(
    conn: sqlite3.Connection,
    *,
    principal: str,
    types: frozenset[str] | None,
    boundary: str,
    last_seq: int | None,
    limit: int,
) -> list[sqlite3.Row]:
    clauses = ["principal = ?", "at <= ?"]
    args: list[Any] = [principal, boundary]
    if types:
        placeholders = ", ".join("?" for _ in types)
        clauses.append(f"event_type IN ({placeholders})")
        args.extend(sorted(types))
    if last_seq is not None:
        clauses.append("seq > ?")
        args.append(last_seq)
    args.append(limit + 1)
    return conn.execute(
        "SELECT seq, event_type, ref_id, by_username, at FROM events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY seq ASC LIMIT ?",
        args,
    ).fetchall()


def row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "event_type": row["event_type"],
        "ref_id": row["ref_id"],
        "by_username": row["by_username"],
        "at": row["at"],
    }
