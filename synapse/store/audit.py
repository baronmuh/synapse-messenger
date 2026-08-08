"""Organizational audit (F11): immutable journal of actions, without content.

Every command executed by an organization agent is recorded in the
same transaction: actor, command, target, outcome. The journal is
append-only (no modification nor deletion) and never contains the
content of messages, descriptions or cards.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def append(
    conn: sqlite3.Connection,
    *,
    organization_name: str,
    at: str,
    actor_username: str,
    command: str,
    target_type: str | None,
    target_username: str | None,
    outcome: str,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (organization_name, at, actor_username, command, "
        "target_type, target_username, outcome) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            organization_name,
            at,
            actor_username,
            command,
            target_type,
            target_username,
            outcome,
        ),
    )


def page(
    conn: sqlite3.Connection,
    *,
    organization_name: str,
    since: str | None,
    actor_filter: str | None,
    command_filter: str | None,
    boundary: str,
    last_id: int | None,
    limit: int,
) -> list[sqlite3.Row]:
    clauses = ["organization_name = ?", "at <= ?"]
    args: list[Any] = [organization_name, boundary]
    if since is not None:
        clauses.append("at >= ?")
        args.append(since)
    if actor_filter is not None:
        clauses.append("actor_username = ?")
        args.append(actor_filter)
    if command_filter is not None:
        clauses.append("command = ?")
        args.append(command_filter)
    if last_id is not None:
        clauses.append("id > ?")
        args.append(last_id)
    args.append(limit + 1)
    return conn.execute(
        "SELECT id, at, actor_username, command, target_type, target_username, "
        "outcome FROM audit_log WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id ASC LIMIT ?",
        args,
    ).fetchall()


def row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "at": row["at"],
        "actor_username": row["actor_username"],
        "command": row["command"],
        "target_type": row["target_type"],
        "target_username": row["target_username"],
        "outcome": row["outcome"],
    }
