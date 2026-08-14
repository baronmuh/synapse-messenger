"""Tasks (F5-F9): persistent coordination objects with a lifecycle.

State transition logic (state machine) lives in the service; this
module provides low-level access: insertion, reading, update,
history, dependencies, budget and search. All functions receive
a connection placed in the transaction wanted by the caller.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..errors import ApiError, TASK_STATE_INVALID

# State machine states (SPEC.txt F5/F8).
STATE_SUBMITTED = "submitted"
STATE_IN_PROGRESS = "in_progress"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"
STATE_PENDING_APPROVAL = "pending_approval"

TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_FAILED, STATE_CANCELED})
ACTIVE_STATES = frozenset(
    {STATE_SUBMITTED, STATE_IN_PROGRESS, STATE_PENDING_APPROVAL}
)


def active_states_sql() -> str:
    """SQL ``IN (... )`` fragment listing the active task states.

    Rendered from ``ACTIVE_STATES`` (sorted for determinism — the order
    inside an ``IN`` clause never affects results), so the active-state
    filter is defined once instead of being repeated as literals in every
    query.
    """
    return "(" + ", ".join(f"'{s}'" for s in sorted(ACTIVE_STATES)) + ")"

# Transitions allowed by ``update_task_state`` (approvals excluded:
# they go through request_approval/approve/reject).
_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_SUBMITTED: frozenset({STATE_IN_PROGRESS, STATE_CANCELED}),
    STATE_IN_PROGRESS: frozenset({STATE_COMPLETED, STATE_FAILED, STATE_CANCELED}),
}

_TASK_FIELDS = (
    "task_id, client_task_id, title, description, creator_username, "
    "assignee_username, state, priority, due_at, business_reference, result, "
    "approver_username, created_at, updated_at"
)


def get(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_TASK_FIELDS} FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()


def get_by_client_key(
    conn: sqlite3.Connection, creator_username: str, client_task_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_TASK_FIELDS} FROM tasks "
        "WHERE creator_username = ? AND client_task_id = ?",
        (creator_username, client_task_id),
    ).fetchone()


def insert(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    client_task_id: str | None,
    title: str,
    description: str | None,
    creator_username: str,
    assignee_username: str,
    priority: str,
    due_at: str | None,
    business_reference: str | None,
    created_at: str,
) -> None:
    conn.execute(
        f"INSERT INTO tasks ({_TASK_FIELDS}) VALUES "
        "(?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?, NULL, NULL, ?, ?)",
        (
            task_id,
            client_task_id,
            title,
            description,
            creator_username,
            assignee_username,
            priority,
            due_at,
            business_reference,
            created_at,
            created_at,
        ),
    )


def set_state(
    conn: sqlite3.Connection, task_id: str, state: str, result: str | None, at: str
) -> None:
    conn.execute(
        "UPDATE tasks SET state = ?, result = ?, updated_at = ? WHERE task_id = ?",
        (state, result, at, task_id),
    )


def set_assignee(
    conn: sqlite3.Connection, task_id: str, assignee_username: str, at: str
) -> None:
    conn.execute(
        "UPDATE tasks SET assignee_username = ?, updated_at = ? WHERE task_id = ?",
        (assignee_username, at, task_id),
    )


def set_approver(
    conn: sqlite3.Connection, task_id: str, approver_username: str, at: str
) -> None:
    conn.execute(
        "UPDATE tasks SET approver_username = ?, updated_at = ? WHERE task_id = ?",
        (approver_username, at, task_id),
    )


def replace_dependencies(
    conn: sqlite3.Connection, task_id: str, depends_on: list[str]
) -> None:
    conn.execute("DELETE FROM task_dependencies WHERE task_id = ?", (task_id,))
    for dep in depends_on:
        conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
            (task_id, dep),
        )


def get_dependencies(conn: sqlite3.Connection, task_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ? "
        "ORDER BY depends_on_task_id",
        (task_id,),
    ).fetchall()
    return [r["depends_on_task_id"] for r in rows]


def dependencies_met(conn: sqlite3.Connection, task_id: str) -> bool:
    """True if all the task dependencies are in the ``completed`` state."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM task_dependencies d JOIN tasks t "
        "ON t.task_id = d.depends_on_task_id "
        "WHERE d.task_id = ? AND t.state <> 'completed'",
        (task_id,),
    ).fetchone()
    return row["n"] == 0


def add_event(
    conn: sqlite3.Connection, task_id: str, event: str, by_username: str,
    note: str | None, at: str, hlc: str,
) -> None:
    conn.execute(
        "INSERT INTO task_events (task_id, event, by_username, note, at, hlc) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, event, by_username, note, at, hlc),
    )


def get_history(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT event, by_username, note, at FROM task_events "
        "WHERE task_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    return [
        {"event": r["event"], "by": r["by_username"], "note": r["note"], "at": r["at"]}
        for r in rows
    ]


def active_count(conn: sqlite3.Connection, username: str) -> int:
    """Number of active tasks (submitted/in_progress/pending_approval) of an agent."""
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM tasks WHERE assignee_username = ? "
        f"AND state IN {active_states_sql()}",
        (username,),
    ).fetchone()
    return row["n"]


def messages_in_hour(conn: sqlite3.Connection, username: str, since: str) -> int:
    """Number of messages sent by an agent since ``since`` (budget F9).

    Counts both direct messages AND group messages: the quota
    "messages sent per period" (SPEC.txt F9) covers all channels, so
    a budgeted agent could not bypass the limit via groups.
    """
    direct = conn.execute(
        "SELECT COUNT(*) AS n FROM messages "
        "WHERE sender_username = ? AND created_at > ?",
        (username, since),
    ).fetchone()["n"]
    group = conn.execute(
        "SELECT COUNT(*) AS n FROM group_messages "
        "WHERE sender_username = ? AND created_at > ?",
        (username, since),
    ).fetchone()["n"]
    return int(direct) + int(group)


def list_visible(
    conn: sqlite3.Connection,
    *,
    me: str,
    assignee_filter: str | None,
    state_filter: str | None,
    priority_filter: str | None,
    due_before: str | None,
    boundary: str,
    last: tuple[str, ...] | None,
    limit: int,
) -> list[sqlite3.Row]:
    """Tasks visible by ``me`` (creator or assignee), sorted by
    ``created_at`` then ``task_id`` (stable order)."""
    clauses = [
        "(creator_username = ? OR assignee_username = ?)",
        "created_at <= ?",
    ]
    args: list[Any] = [me, me, boundary]
    if assignee_filter is not None:
        clauses.append("assignee_username = ?")
        args.append(assignee_filter)
    if state_filter is not None:
        clauses.append("state = ?")
        args.append(state_filter)
    if priority_filter is not None:
        clauses.append("priority = ?")
        args.append(priority_filter)
    if due_before is not None:
        clauses.append("due_at IS NOT NULL AND due_at < ?")
        args.append(due_before)
    if last is not None:
        clauses.append("(created_at > ? OR (created_at = ? AND task_id > ?))")
        args.extend([last[0], last[0], last[1]])
    args.append(limit + 1)
    rows = conn.execute(
        f"SELECT {_TASK_FIELDS} FROM tasks WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at ASC, task_id ASC LIMIT ?",
        args,
    ).fetchall()
    return rows


# Sentinel value for sorting tasks without a due date (NULLS LAST):
# it must stay identical in the pagination clause and the cursor
# encoding (otherwise the pagination re-scans tasks without a due_at).
NO_DUE_AT = "9999"


def list_work(
    conn: sqlite3.Connection,
    *,
    me: str,
    boundary: str,
    last: tuple[str, str, str] | None,
    limit: int,
) -> list[sqlite3.Row]:
    """Work queue of ``me`` (F6): assigned active tasks + pending
    approvals where ``me`` is the approver. Sorted by ``due_at`` (without
    due last), then ``created_at``, then ``task_id``."""
    clauses = [
        f"((assignee_username = ? AND state IN {active_states_sql()}) "
        "OR (approver_username = ? AND state = 'pending_approval'))",
        "created_at <= ?",
    ]
    args: list[Any] = [me, me, boundary]
    if last is not None:
        # last = (due_at or '9999', created_at, task_id); stable pagination
        # on the sort (due_at NULLS LAST, created_at, task_id).
        clauses.append(
            f"(COALESCE(due_at, '{NO_DUE_AT}') > ? OR (COALESCE(due_at, '{NO_DUE_AT}') = ? AND created_at > ?) "
            f"OR (COALESCE(due_at, '{NO_DUE_AT}') = ? AND created_at = ? AND task_id > ?))"
        )
        args.extend([last[0], last[0], last[1], last[0], last[1], last[2]])
    args.append(limit + 1)
    rows = conn.execute(
        f"SELECT {_TASK_FIELDS} FROM tasks WHERE "
        + " AND ".join(clauses)
        + f" ORDER BY COALESCE(due_at, '{NO_DUE_AT}') ASC, created_at ASC, task_id ASC LIMIT ?",
        args,
    ).fetchall()
    return rows


def due_tasks_to_escalate(
    conn: sqlite3.Connection,
    *,
    organization_name: str,
    exclude_username: str,
    due_before: str,
    failed_before: str,
) -> list[sqlite3.Row]:
    """Unfinished tasks of an organization to escalate (F9): due date
    exceeded by ``due_after_seconds`` (via ``due_before``) or failure older than
    ``failed_after_seconds`` (via ``failed_before``), excluding tasks already
    assigned to the escalation target."""
    return conn.execute(
        f"SELECT {', '.join('tasks.' + f for f in _TASK_FIELDS.split(', '))} "
        "FROM tasks JOIN accounts "
        "ON accounts.username = tasks.assignee_username "
        "WHERE accounts.organization_name = ? AND tasks.assignee_username <> ? "
        "AND tasks.state IN ('submitted', 'in_progress') "
        "AND ((tasks.due_at IS NOT NULL AND tasks.due_at < ?) OR "
        "(tasks.state = 'in_progress' AND tasks.updated_at < ?))",
        (organization_name, exclude_username, due_before, failed_before),
    ).fetchall()


def row_to_task(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Transforms a row into a task JSON object (with history and
    dependencies). To be called in the same transaction as the read."""
    return {
        "task_id": row["task_id"],
        "client_task_id": row["client_task_id"],
        "title": row["title"],
        "description": row["description"],
        "creator_username": row["creator_username"],
        "assignee_username": row["assignee_username"],
        "state": row["state"],
        "priority": row["priority"],
        "due_at": row["due_at"],
        "business_reference": row["business_reference"],
        "result": row["result"],
        "approver_username": row["approver_username"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "depends_on": get_dependencies(conn, row["task_id"]),
        "history": get_history(conn, row["task_id"]),
    }


def ensure_transition(from_state: str, to_state: str) -> None:
    """Validates an ``update_task_state`` transition (terminal states and
    ``pending_approval`` are outside the machine: TASK_STATE_INVALID)."""
    allowed = _TRANSITIONS.get(from_state)
    if allowed is None or to_state not in allowed:
        raise ApiError(TASK_STATE_INVALID)
