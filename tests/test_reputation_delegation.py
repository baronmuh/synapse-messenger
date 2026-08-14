"""F16 (reputation) and F17 (controlled delegation): measurement, temporary
access, expiration, revocation and non-disclosure.
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import TASK_NOT_FOUND

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ORG_NAME, ORG_PASSWORD


def _task_for(fx, assignee: str) -> str:
    """Task created by alice, assigned to ``assignee``, moved to in_progress."""
    tid = fx.client.create_task("ready task", assignee, ALICE, ALICE_PASSWORD)["task_id"]
    fx.client.update_task_state(tid, "in_progress", assignee,
                                BOB_PASSWORD if assignee == BOB else ALICE_PASSWORD)
    return tid


def _future_utc(days: int = 1) -> str:
    from synapse.validation import now_utc_offset

    # now_utc_offset subtracts seconds: the future is a negative offset
    return now_utc_offset(-days * 86400)


# ---------------------------------------------------------------------------
# F16 — Reputation
# ---------------------------------------------------------------------------


def test_reputation_own_detailed(fx):
    tid = _task_for(fx, BOB)
    fx.client.update_task_state(tid, "completed", BOB, BOB_PASSWORD, result="fini")
    rep = fx.client.get_agent_reputation(BOB, BOB, BOB_PASSWORD)
    assert rep["completed"] == 1
    assert rep["failed"] == 0
    assert rep["completion_rate"] == 1.0
    assert "qualitative" not in rep


def test_reputation_other_qualitative(fx):
    tid = _task_for(fx, BOB)
    fx.client.update_task_state(tid, "completed", BOB, BOB_PASSWORD, result="fini")
    rep = fx.client.get_agent_reputation(BOB, ALICE, ALICE_PASSWORD)
    assert rep["qualitative"] == "excellent"
    assert "completed" not in rep  # the detailed figures stay private


def test_reputation_unknown_when_no_tasks(fx):
    rep = fx.client.get_agent_reputation(BOB, ALICE, ALICE_PASSWORD)
    assert rep["qualitative"] == "unknown"


def test_reputation_failed_agent(fx):
    tid = _task_for(fx, BOB)
    fx.client.update_task_state(tid, "failed", BOB, BOB_PASSWORD, result="failure")
    rep = fx.client.get_agent_reputation(BOB, ALICE, ALICE_PASSWORD)
    assert rep["qualitative"] == "poor"
    own = fx.client.get_agent_reputation(BOB, BOB, BOB_PASSWORD)
    assert own["completion_rate"] == 0.0


def test_reputation_unknown_username(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_reputation("ghost", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "USER_NOT_FOUND"


# ---------------------------------------------------------------------------
# F17 — Controlled delegation
# ---------------------------------------------------------------------------


def test_delegation_allows_state_change(fx):
    tid = _task_for(fx, ALICE)  # alice's task
    fx.client.create_delegation(tid, BOB, _future_utc(), ALICE, ALICE_PASSWORD)
    # bob can read and advance the task without being creator or assignee
    task = fx.client.get_task(tid, BOB, BOB_PASSWORD)
    assert task["task_id"] == tid
    updated = fx.client.update_task_state(tid, "completed", BOB, BOB_PASSWORD, result="fait")
    assert updated["state"] == "completed"
    # the audit logs the delegated action
    entries = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD)["entries"]
    assert any(e["command"] == "update_task_state" and "delegated" in e["outcome"]
               for e in entries)


def _insert_expired_delegation(fx, tid: str) -> None:
    """Insert an already-expired delegation ticket (the service rejects
    past expirations; such a ticket must never grant access)."""
    from synapse.db import connect

    with connect(fx.config) as conn:
        conn.execute(
            "INSERT INTO delegations (delegator_username, delegatee_username, task_id, "
            "expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (ALICE, BOB, tid, _future_utc(-1), _future_utc(0)),
        )


def test_delegation_expired_gives_no_access(fx):
    tid = _task_for(fx, ALICE)
    _insert_expired_delegation(fx, tid)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_task(tid, BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_NOT_FOUND
    with pytest.raises(ApiClientError) as exc:
        fx.client.update_task_state(tid, "completed", BOB, BOB_PASSWORD, result="x")
    assert exc.value.code == TASK_NOT_FOUND


def test_delegation_revoked_gives_no_access(fx):
    tid = _task_for(fx, ALICE)
    fx.client.create_delegation(tid, BOB, _future_utc(), ALICE, ALICE_PASSWORD)
    revoked = fx.client.revoke_delegation(tid, BOB, ALICE, ALICE_PASSWORD)
    assert revoked["revoked"] is True
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_task(tid, BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_NOT_FOUND


def test_delegation_cannot_transfer(fx):
    tid = _task_for(fx, ALICE)
    fx.create_agent("carol", "motdepasse-carol-1")
    fx.client.create_delegation(tid, BOB, _future_utc(), ALICE, ALICE_PASSWORD)
    # the delegatee cannot re-transfer
    with pytest.raises(ApiClientError) as exc:
        fx.client.transfer_task(tid, "carol", BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_NOT_FOUND


def test_delegation_requires_visible_task(fx):
    # alice's task: bob cannot see it and cannot delegate it
    tid = _task_for(fx, ALICE)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_delegation(tid, ALICE, _future_utc(), BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_NOT_FOUND


def test_my_delegations_lists_active_only(fx):
    tid = _task_for(fx, ALICE)
    fx.client.create_delegation(tid, BOB, _future_utc(), ALICE, ALICE_PASSWORD)
    _insert_expired_delegation(fx, tid)
    delegations = fx.client.get_my_delegations(BOB, BOB_PASSWORD)["delegations"]
    assert len(delegations) == 1
    assert delegations[0]["delegator_username"] == ALICE
    assert delegations[0]["task_id"] == tid


def test_delegation_rejects_past_expiration(fx):
    tid = _task_for(fx, ALICE)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_delegation(tid, BOB, _future_utc(-1), ALICE, ALICE_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
