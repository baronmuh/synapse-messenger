"""SPEC.txt v3 compliance (audit additions): event retention
(F10), dependency depth (F9), reputation in the card and for the
organization (F16).
"""

from __future__ import annotations

import sqlite3

import pytest

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ORG_NAME, ORG_PASSWORD


def _insert_old_event(fx, principal: str, days_ago: int) -> None:
    """Insert an old event directly into the database (the service
    always timestamps just now — the purge must catch up with it)."""
    from synapse.validation import now_utc_offset

    at = now_utc_offset(86400.0 * days_ago)
    with sqlite3.connect(fx.config.storage_dir + "/synapse.db") as conn:
        conn.execute(
            "INSERT INTO events (principal, event_type, ref_id, by_username, at) "
            "VALUES (?, 'task.created', 'ancien', ?, ?)",
            (principal, "x", at),
        )


def test_event_retention_purges_old_events(fx):
    """set_event_retention_days: events older than the retention
    are purged on the next write; recent ones remain."""
    fx.client.create_task("T1", BOB, ALICE, ALICE_PASSWORD)
    _insert_old_event(fx, ALICE, days_ago=400)
    # default retention: 90 days -> the old event is still there
    events = fx.client.get_events(ALICE, ALICE_PASSWORD)["events"]
    assert any(e["ref_id"] == "ancien" for e in events)
    # retention of 30 days then an event write triggers the purge
    fx.client.set_event_retention_days(30, ORG_NAME, ORG_PASSWORD)
    fx.client.update_task_state(
        fx.client.create_task("T2", BOB, ALICE, ALICE_PASSWORD)["task_id"],
        "in_progress", BOB, BOB_PASSWORD,
    )
    events = fx.client.get_events(ALICE, ALICE_PASSWORD)["events"]
    assert not any(e["ref_id"] == "ancien" for e in events)


def test_event_retention_validation(fx):
    with pytest.raises(Exception) as exc:
        fx.client.set_event_retention_days(0, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
    with pytest.raises(Exception) as exc:
        fx.client.set_event_retention_days(4000, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_dependency_depth_guard(fx):
    """F9 guardrail: a dependency chain deeper than the bound
    (8) is refused (QUOTA_EXCEEDED)."""
    previous = None
    for i in range(10):
        params = {}
        if previous is not None:
            params["depends_on"] = [previous]
        if i == 9:
            with pytest.raises(Exception) as exc:
                fx.client.create_task(
                    f"T{i}", BOB, ALICE, ALICE_PASSWORD, **params)
            assert exc.value.code == "QUOTA_EXCEEDED"
        else:
            task = fx.client.create_task(
                f"T{i}", BOB, ALICE, ALICE_PASSWORD, **params)
            previous = task["task_id"]


def test_reputation_in_card(fx):
    """F16: the card exposes reputation — detailed for oneself, a
    qualitative mention for others."""
    for result in ("ok", "ok", "ko"):
        tid = fx.client.create_task("T", BOB, ALICE, ALICE_PASSWORD)["task_id"]
        fx.client.update_task_state(tid, "in_progress", BOB, BOB_PASSWORD)
        fx.client.update_task_state(
            tid, "completed" if result == "ok" else "failed", BOB, BOB_PASSWORD,
            result=result,
        )
    own = fx.client.get_agent_card(BOB, BOB, BOB_PASSWORD)
    assert own["reputation"]["completed"] == 2
    assert own["reputation"]["failed"] == 1
    assert own["reputation"]["completion_rate"] == pytest.approx(2 / 3, abs=1e-3)
    other = fx.client.get_agent_card(BOB, ALICE, ALICE_PASSWORD)
    assert "qualitative" in other["reputation"] and other["reputation"]["qualitative"] == "average"


def test_reputation_in_org_agents(fx):
    """F16: the organization sees per-agent detailed figures."""
    tid = fx.client.create_task("T", BOB, ALICE, ALICE_PASSWORD)["task_id"]
    fx.client.update_task_state(tid, "in_progress", BOB, BOB_PASSWORD)
    fx.client.update_task_state(tid, "completed", BOB, BOB_PASSWORD, result="ok")
    data = fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD)
    by_name = {agent["username"]: agent for agent in data["agents"]}
    assert by_name[BOB]["reputation"]["completed"] == 1
    assert by_name[BOB]["reputation"]["completion_rate"] == 1.0
