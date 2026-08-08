"""Access to agent accounts (read, creation, states, organization)."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..validation import now_utc

_ACCOUNT_FIELDS = (
    "username, password_hash, status, description, organization_name, "
    "can_see_org_agents, created_at, principal_type, is_observer"
)


def get(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    row = conn.execute(f"SELECT {_ACCOUNT_FIELDS} FROM accounts WHERE username = ?", (username,)).fetchone()
    return row


def insert(
    conn: sqlite3.Connection,
    username: str,
    password_hash: str,
    status: str,
    description: str,
    organization_name: str,
    can_see_org_agents: bool = False,
    principal_type: str = "agent",
) -> None:
    # is_observer is never provided here: the column default (0) applies;
    # observer accounts are marked afterwards by create_observer_account.
    conn.execute(
        "INSERT INTO accounts (username, password_hash, status, description, "
        "organization_name, can_see_org_agents, created_at, principal_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            username,
            password_hash,
            status,
            description,
            organization_name,
            1 if can_see_org_agents else 0,
            now_utc(),
            principal_type,
        ),
    )


def set_status(conn: sqlite3.Connection, username: str, status: str) -> None:
    conn.execute("UPDATE accounts SET status = ? WHERE username = ?", (status, username))


def set_password_hash(conn: sqlite3.Connection, username: str, password_hash: str) -> None:
    conn.execute("UPDATE accounts SET password_hash = ? WHERE username = ?", (password_hash, username))


def set_visibility(conn: sqlite3.Connection, username: str, can_see_org_agents: bool) -> None:
    conn.execute(
        "UPDATE accounts SET can_see_org_agents = ? WHERE username = ?",
        (1 if can_see_org_agents else 0, username),
    )


def any_account_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
    return row is not None


def list_by_org(
    conn: sqlite3.Connection,
    organization_name: str,
    limit: int,
    after_username: str | None = None,
    boundary: str | None = None,
    active_only: bool = False,
    include_humans: bool = True,
) -> list[sqlite3.Row]:
    """Lists the agents of an organization, sorted by ascending ``username``.

    Cursor pagination: ``after_username`` excludes already-seen names
    (usernames are unique and the list is sorted, so pagination is
    deterministic). ``boundary`` freezes the snapshot (accounts created
    after the first page do not appear); ``active_only`` restricts
    the list to active accounts (agent visibility permission);
    ``include_humans=False`` excludes the human account (the
    agents directory — SPEC-WEB §5.4).
    """
    clauses = ["organization_name = ?"]
    params: list[Any] = [organization_name]
    if not include_humans:
        clauses.append("principal_type != 'human'")
    if boundary is not None:
        clauses.append("created_at <= ?")
        params.append(boundary)
    if active_only:
        clauses.append("status = 'active'")
    if after_username is not None:
        clauses.append("username > ?")
        params.append(after_username)
    params.append(limit)
    rows = conn.execute(
        f"SELECT {_ACCOUNT_FIELDS} FROM accounts "
        f"WHERE {' AND '.join(clauses)} ORDER BY username ASC LIMIT ?",
        params,
    ).fetchall()
    return rows


def count_by_org(conn: sqlite3.Connection, organization_name: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM accounts WHERE organization_name = ?",
        (organization_name,),
    ).fetchone()
    return int(row["n"])


def account_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "username": row["username"],
        "password_hash": row["password_hash"],
        "status": row["status"],
        "description": row["description"],
        "organization_name": row["organization_name"],
        "can_see_org_agents": bool(row["can_see_org_agents"]),
        "created_at": row["created_at"],
    }
