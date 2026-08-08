"""Agent cards (F2): structured capabilities, validated by the organization.

A card is a structured extension of the description: capabilities,
domain, model, tools, SLA, limits and estimated cost. It is declared by
the agent and validated by its organization (``validation_state``). Any
modification after validation returns the card to the ``pending`` state; the
current card stays displayed in the meantime.

The lists (``capabilities``, ``tools``) are stored as JSON;
normalization (NFC, bounds, dedup) is done by the validation layer.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_CARD_FIELDS = (
    "username, capabilities, domain, model, tools, sla, limits, "
    "estimated_cost, validation_state, approved_by, approved_at, updated_at"
)


def get(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    """Returns the card of an agent, or ``None`` if it has none."""
    return conn.execute(
        f"SELECT {_CARD_FIELDS} FROM agent_cards WHERE username = ?",
        (username,),
    ).fetchone()


def upsert(
    conn: sqlite3.Connection,
    *,
    username: str,
    capabilities: list[str],
    domain: str | None,
    model: str | None,
    tools: list[str],
    sla: str | None,
    limits: str | None,
    estimated_cost: str | None,
    updated_at: str,
) -> None:
    """Creates or replaces the card. Any submission returns to ``pending``."""
    conn.execute(
        "INSERT INTO agent_cards (username, capabilities, domain, model, tools, "
        "sla, limits, estimated_cost, validation_state, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?) "
        "ON CONFLICT(username) DO UPDATE SET "
        "capabilities = excluded.capabilities, domain = excluded.domain, "
        "model = excluded.model, tools = excluded.tools, sla = excluded.sla, "
        "limits = excluded.limits, estimated_cost = excluded.estimated_cost, "
        "validation_state = 'pending', approved_by = NULL, approved_at = NULL, "
        "updated_at = excluded.updated_at",
        (
            username,
            json.dumps(capabilities, ensure_ascii=False),
            domain,
            model,
            json.dumps(tools, ensure_ascii=False),
            sla,
            limits,
            estimated_cost,
            updated_at,
        ),
    )


def approve(
    conn: sqlite3.Connection, *, username: str, approved_by: str, approved_at: str
) -> None:
    conn.execute(
        "UPDATE agent_cards SET validation_state = 'approved', "
        "approved_by = ?, approved_at = ? WHERE username = ?",
        (approved_by, approved_at, username),
    )


def search(
    conn: sqlite3.Connection,
    *,
    org_name: str,
    capability: str | None,
    domain: str | None,
    name_contains: str | None,
    boundary: str,
    last_username: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    """Paginated search in the cards of an organization's active agents.

    The scope is limited to one's own organization (no leak
    of usernames across organizations via the search). The filters are
    case-insensitive substrings on capabilities, domain and
    name. Agents without a card are excluded (a card is required to be
    discovered by capability).
    """
    clauses = [
        "c.username = a.username",
        "a.organization_name = ?",
        "a.status = 'active'",
        "c.username > ?",  # pagination by username (exclusive bound)
    ]
    args: list[Any] = [org_name, last_username if last_username is not None else ""]
    if capability:
        clauses.append("c.capabilities LIKE ?")
        args.append(f"%{capability}%")
    if domain:
        clauses.append("c.domain LIKE ?")
        args.append(f"%{domain}%")
    if name_contains:
        clauses.append("c.username LIKE ?")
        args.append(f"%{name_contains}%")
    args.append(limit + 1)
    sql = (
        "SELECT c.username, c.capabilities, c.domain, c.model, c.tools, "
        "c.sla, c.limits, c.estimated_cost, c.validation_state, "
        "c.approved_by, c.approved_at, c.updated_at, "
        "a.organization_name, a.description "
        "FROM agent_cards c JOIN accounts a ON a.username = c.username "
        "WHERE " + " AND ".join(clauses) + " ORDER BY c.username LIMIT ?"
    )
    return conn.execute(sql, args).fetchall()


def row_to_card(row: sqlite3.Row) -> dict[str, Any]:
    """Turns a row into an agent card JSON object.

    ``organization_name`` is only present for rows from the
    search (JOIN with accounts); single reads omit it
    (the card is public metadata like the description).
    """
    card: dict[str, Any] = {
        "username": row["username"],
        "capabilities": json.loads(row["capabilities"]),
        "domain": row["domain"],
        "model": row["model"],
        "tools": json.loads(row["tools"]) if row["tools"] is not None else [],
        "sla": row["sla"],
        "limits": row["limits"],
        "estimated_cost": row["estimated_cost"],
        "validation_state": row["validation_state"],
        "approved_by": row["approved_by"],
        "approved_at": row["approved_at"],
        "updated_at": row["updated_at"],
    }
    if "organization_name" in row.keys():
        card["organization_name"] = row["organization_name"]
    return card


def row_to_card_public(row: sqlite3.Row) -> dict[str, Any]:
    """Card without the organization (public read, like the description)."""
    card = row_to_card(row)
    card.pop("organization_name", None)
    return card
