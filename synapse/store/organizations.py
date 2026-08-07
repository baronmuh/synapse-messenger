"""Access to the organization storage.

Une organisation est un principal administratif permanent : nom unique,
hash Argon2id de son mot de passe, politiques de communication externe.
It is never deactivated, deleted nor renamed (section 3.1 of the
specification).
"""

from __future__ import annotations

import sqlite3

from ..validation import now_utc

_FIELDS = (
    "organization_name, password_hash, allow_incoming_external, "
    "allow_outgoing_external, created_at, enabled"
)


def get(conn: sqlite3.Connection, organization_name: str) -> sqlite3.Row | None:
    row = conn.execute(
        f"SELECT {_FIELDS} FROM organizations WHERE organization_name = ?",
        (organization_name,),
    ).fetchone()
    return row


def insert(
    conn: sqlite3.Connection,
    organization_name: str,
    password_hash: str,
    allow_incoming_external: bool = False,
    allow_outgoing_external: bool = False,
) -> None:
    conn.execute(
        f"INSERT INTO organizations ({_FIELDS}) VALUES (?, ?, ?, ?, ?, 1)",
        (
            organization_name,
            password_hash,
            1 if allow_incoming_external else 0,
            1 if allow_outgoing_external else 0,
            now_utc(),
        ),
    )


def update_policies(
    conn: sqlite3.Connection,
    organization_name: str,
    allow_incoming_external: bool,
    allow_outgoing_external: bool,
) -> None:
    conn.execute(
        "UPDATE organizations SET allow_incoming_external = ?, "
        "allow_outgoing_external = ? WHERE organization_name = ?",
        (
            1 if allow_incoming_external else 0,
            1 if allow_outgoing_external else 0,
            organization_name,
        ),
    )


def update_password(conn: sqlite3.Connection, organization_name: str, password_hash: str) -> None:
    conn.execute(
        "UPDATE organizations SET password_hash = ? WHERE organization_name = ?",
        (password_hash, organization_name),
    )
