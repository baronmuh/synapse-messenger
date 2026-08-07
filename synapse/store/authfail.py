"""Authentication failure journal (sliding window).

Specification limit: at most ``auth_max_failures`` failures
for a username in a sliding window of
``auth_window_seconds``; beyond that, attempts are refused until
the window empties. Refusals are not re-logged (otherwise the
blocage serait permanent).
"""

from __future__ import annotations

import sqlite3

from ..validation import now_utc, now_utc_offset


def prune(conn: sqlite3.Connection, window_seconds: int) -> None:
    """Deletes records older than the window (hygiene)."""
    cutoff = now_utc_offset(window_seconds)
    conn.execute(
        "DELETE FROM auth_failures WHERE attempted_at < ?",
        (cutoff,),
    )


def count_recent(conn: sqlite3.Connection, username: str, window_seconds: int) -> int:
    """Counts the failures of the username in the sliding window."""
    cutoff = now_utc_offset(window_seconds)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM auth_failures WHERE username = ? AND attempted_at >= ?",
        (username, cutoff),
    ).fetchone()
    return int(row["n"])


def record(conn: sqlite3.Connection, username: str) -> None:
    conn.execute(
        "INSERT INTO auth_failures (username, attempted_at) VALUES (?, ?)",
        (username, now_utc()),
    )


def clear(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("DELETE FROM auth_failures WHERE username = ?", (username,))
