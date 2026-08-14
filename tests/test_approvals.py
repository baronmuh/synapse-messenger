"""F8 — Approvals: request, approval, rejection, permissions, and
non-disclosure.
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import (
    RECIPIENT_NOT_FOUND,
    TASK_NOT_FOUND,
    TASK_STATE_INVALID,
    USER_NOT_FOUND,
)

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ORG_NAME, ORG_PASSWORD


def _task_ready(fx) -> str:
    task = fx.client.create_task("Validation", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    return task["task_id"]


def test_approval_flow(fx):
    tid = _task_ready(fx)
    pending = fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    assert pending["state"] == "pending_approval"
    assert pending["approver_username"] == ALICE
    # the approver validates -> completed (the result is preserved)
    done = fx.client.approve_task(tid, ALICE, ALICE_PASSWORD)
    assert done["state"] == "completed"
    assert done["history"][-1]["event"] == "approved"


def test_rejection_returns_to_in_progress(fx):
    tid = _task_ready(fx)
    fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    rejected = fx.client.reject_task(tid, ALICE, ALICE_PASSWORD,
                                     reason="Incomplete scope")
    assert rejected["state"] == "in_progress"
    assert rejected["history"][-1]["event"] == "rejected"
    assert rejected["history"][-1]["note"] == "Incomplete scope"
    # the task can go back into validation
    fx.client.update_task_state(tid, "completed", BOB, BOB_PASSWORD, result="fixed")


def test_non_approver_cannot_approve(fx):
    tid = _task_ready(fx)
    fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    # bob is not the designated approver: task invisible (non-disclosure)
    with pytest.raises(ApiClientError) as exc:
        fx.client.approve_task(tid, BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_NOT_FOUND
    with pytest.raises(ApiClientError) as exc:
        fx.client.reject_task(tid, BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_NOT_FOUND


def test_approve_non_pending_task_invalid(fx):
    tid = _task_ready(fx)
    fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    fx.client.approve_task(tid, ALICE, ALICE_PASSWORD)
    # the task is done: re-approving (still approver) is invalid
    with pytest.raises(ApiClientError) as exc:
        fx.client.approve_task(tid, ALICE, ALICE_PASSWORD)
    assert exc.value.code == TASK_STATE_INVALID


def test_request_approval_unknown_approver(fx):
    tid = _task_ready(fx)
    with pytest.raises(ApiClientError) as exc:
        fx.client.request_approval(tid, "ghost", BOB, BOB_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND
    fx.create_agent("carol", "motdepasse-carol-1")
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.request_approval(tid, "carol", BOB, BOB_PASSWORD)
    assert exc.value.code == RECIPIENT_NOT_FOUND


def test_request_approval_on_terminated_task_invalid(fx):
    tid = _task_ready(fx)
    fx.client.update_task_state(tid, "completed", BOB, BOB_PASSWORD, result="fait")
    with pytest.raises(ApiClientError) as exc:
        fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_STATE_INVALID


def test_double_request_approval_invalid(fx):
    tid = _task_ready(fx)
    fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_STATE_INVALID


def test_approval_persists_across_restart(fx):
    from .conftest import make_server

    tid = _task_ready(fx)
    fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        got = server2.client.get_task(tid, ALICE, ALICE_PASSWORD)
        assert got["state"] == "pending_approval"
        assert got["approver_username"] == ALICE
    finally:
        server2.stop()


def test_reject_non_pending_task_invalid(fx):
    """Rejecting a task that is no longer pending (already approved) is an
    invalid transition, even for the designated approver."""
    tid = _task_ready(fx)
    fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    fx.client.approve_task(tid, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.reject_task(tid, ALICE, ALICE_PASSWORD, reason="trop tard")
    assert exc.value.code == "TASK_STATE_INVALID"


def test_request_approval_self_approval_rejected(fx):
    """F8 HITL guardrail: an agent cannot designate themselves as the
    approver of their own task (otherwise approval is a no-op and the
    human-in-the-loop control is bypassed)."""
    tid = _task_ready(fx)
    with pytest.raises(ApiClientError) as exc:
        fx.client.request_approval(tid, ALICE, ALICE, ALICE_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
    # The task is unchanged (still in_progress, no approver designated).
    task = fx.client.get_task(tid, BOB, BOB_PASSWORD)
    assert task["state"] == "in_progress"
    assert task.get("approver_username") is None
