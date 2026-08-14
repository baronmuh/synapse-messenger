"""F5 — Tasks: full lifecycle (creation, states, transitions,
dependencies, idempotency, visibility, persistence).

Covers the state machine, forbidden transitions, non-disclosure,
edge cases, and survival across restart.
"""

from __future__ import annotations

import uuid

import pytest

from synapse.client import ApiClientError
from synapse.errors import (
    INVALID_ARGUMENT,
    RECIPIENT_NOT_FOUND,
    TASK_DEPENDENCY_NOT_MET,
    TASK_NOT_FOUND,
    TASK_STATE_INVALID,
    USER_NOT_FOUND,
)

from .conftest import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
    make_server,
)

DUE = "2026-08-10T12:00:00.000Z"


def test_create_task_shape(fx):
    task = fx.client.create_task("Analyser incident 4711", BOB, ALICE, ALICE_PASSWORD,
                                 description="Incident de facturation", priority="high",
                                 due_at=DUE, business_reference="incident-4711",
                                 client_task_id="op-1")
    assert task["state"] == "submitted"
    assert task["creator_username"] == ALICE
    assert task["assignee_username"] == BOB
    assert task["priority"] == "high"
    assert task["due_at"] == DUE
    assert task["business_reference"] == "incident-4711"
    assert task["client_task_id"] == "op-1"
    assert task["depends_on"] == []
    assert task["history"][0]["event"] == "created"
    assert uuid.UUID(task["task_id"]).version == 4


def test_full_lifecycle(fx):
    task = fx.client.create_task("Complete task", BOB, ALICE, ALICE_PASSWORD)
    tid = task["task_id"]
    # submitted -> in_progress -> completed (with result)
    fx.client.update_task_state(tid, "in_progress", BOB, BOB_PASSWORD)
    done = fx.client.update_task_state(tid, "completed", BOB, BOB_PASSWORD,
                                       result="Processed successfully")
    assert done["state"] == "completed"
    assert done["result"] == "Processed successfully"
    # terminal states: no transition possible
    with pytest.raises(ApiClientError) as exc:
        fx.client.update_task_state(tid, "in_progress", BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_STATE_INVALID


def test_failed_state_requires_result(fx):
    task = fx.client.create_task("Fail", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    failed = fx.client.update_task_state(task["task_id"], "failed", BOB, BOB_PASSWORD,
                                         result="Impossible: missing data")
    assert failed["state"] == "failed"
    assert failed["result"] == "Impossible: missing data"


def test_cancel_from_submitted_and_in_progress(fx):
    t1 = fx.client.create_task("Cancel early", BOB, ALICE, ALICE_PASSWORD)
    assert fx.client.update_task_state(t1["task_id"], "canceled", ALICE, ALICE_PASSWORD)["state"] == "canceled"
    t2 = fx.client.create_task("Cancel late", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(t2["task_id"], "in_progress", BOB, BOB_PASSWORD)
    assert fx.client.update_task_state(t2["task_id"], "canceled", BOB, BOB_PASSWORD)["state"] == "canceled"


def test_invalid_transition(fx):
    task = fx.client.create_task("Forbidden transition", BOB, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.update_task_state(task["task_id"], "completed", BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_STATE_INVALID


def test_creator_and_assignee_can_update(fx):
    task = fx.client.create_task("Double access", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "in_progress", ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "completed", BOB, BOB_PASSWORD, result="ok")
    assert fx.client.get_task(task["task_id"], ALICE, ALICE_PASSWORD)["state"] == "completed"


def test_task_invisible_to_others(fx):
    fx.create_agent("carol", "motdepasse-carol-1")
    task = fx.client.create_task("Secret", BOB, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_task(task["task_id"], "carol", "motdepasse-carol-1")
    assert exc.value.code == TASK_NOT_FOUND
    with pytest.raises(ApiClientError) as exc:
        fx.client.update_task_state(task["task_id"], "canceled", "carol", "motdepasse-carol-1")
    assert exc.value.code == TASK_NOT_FOUND
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_task("00000000-0000-4000-8000-000000000000", ALICE, ALICE_PASSWORD)
    assert exc.value.code == TASK_NOT_FOUND


def test_dependencies_block_in_progress(fx):
    dep = fx.client.create_task("Dependency", BOB, ALICE, ALICE_PASSWORD)
    task = fx.client.create_task("Depends on the other", BOB, ALICE, ALICE_PASSWORD,
                                 depends_on=[dep["task_id"]])
    with pytest.raises(ApiClientError) as exc:
        fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_DEPENDENCY_NOT_MET
    # a completed dependency unblocks
    fx.client.update_task_state(dep["task_id"], "in_progress", BOB, BOB_PASSWORD)
    fx.client.update_task_state(dep["task_id"], "completed", BOB, BOB_PASSWORD, result="fait")
    fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    assert fx.client.get_task(task["task_id"], ALICE, ALICE_PASSWORD)["state"] == "in_progress"


def test_unknown_dependency_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("Bad dependency", BOB, ALICE, ALICE_PASSWORD,
                              depends_on=["00000000-0000-4000-8000-000000000000"])
    assert exc.value.code == INVALID_ARGUMENT


def test_assignee_must_exist_and_be_active(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("Ghost", "ghost", ALICE, ALICE_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND
    fx.create_agent("carol", "motdepasse-carol-1")
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("Deactivated", "carol", ALICE, ALICE_PASSWORD)
    assert exc.value.code == RECIPIENT_NOT_FOUND


def test_client_task_id_idempotent(fx):
    first = fx.client.create_task("Idempotent", BOB, ALICE, ALICE_PASSWORD,
                                  client_task_id="cle-1")
    second = fx.client.create_task("Idempotent", BOB, ALICE, ALICE_PASSWORD,
                                   client_task_id="cle-1")
    assert second["task_id"] == first["task_id"]
    # same key for another task -> INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("Other title", BOB, ALICE, ALICE_PASSWORD, client_task_id="cle-1")
    assert exc.value.code == INVALID_ARGUMENT


def test_list_tasks_filters(fx):
    fx.client.create_task("Une", BOB, ALICE, ALICE_PASSWORD, priority="high")
    fx.client.create_task("Deux", BOB, ALICE, ALICE_PASSWORD)
    t3 = fx.client.create_task("Trois", BOB, BOB, BOB_PASSWORD)
    # alice sees her creations (including those assigned to bob); bob sees his own plus the ones assigned to him
    assert len(fx.client.list_tasks(ALICE, ALICE_PASSWORD)["tasks"]) == 2
    assert len(fx.client.list_tasks(BOB, BOB_PASSWORD)["tasks"]) == 3
    # filter by state
    fx.client.update_task_state(t3["task_id"], "in_progress", BOB, BOB_PASSWORD)
    listed = fx.client.list_tasks(BOB, BOB_PASSWORD, state="in_progress")
    assert [t["task_id"] for t in listed["tasks"]] == [t3["task_id"]]
    # filter by assignee (restricted to visibility)
    listed = fx.client.list_tasks(ALICE, ALICE_PASSWORD, assignee_username=BOB)
    assert len(listed["tasks"]) == 2
    # filter by priority
    listed = fx.client.list_tasks(ALICE, ALICE_PASSWORD, priority="high")
    assert [t["title"] for t in listed["tasks"]] == ["Une"]
    # filter by maximum due date (no task has a due date)
    listed = fx.client.list_tasks(ALICE, ALICE_PASSWORD,
                                  due_before="2020-01-01T00:00:00.000Z")
    assert listed["tasks"] == []


def test_transfer_task(fx):
    fx.create_agent("carol", "motdepasse-carol-1")
    task = fx.client.create_task("Transfer", BOB, ALICE, ALICE_PASSWORD)
    moved = fx.client.transfer_task(task["task_id"], "carol", ALICE, ALICE_PASSWORD,
                                    note="Support hands over")
    assert moved["assignee_username"] == "carol"
    assert moved["history"][-1]["event"] == "transferred"
    assert moved["history"][-1]["note"] == "Support hands over"
    # the former assignee is no longer an actor
    with pytest.raises(ApiClientError) as exc:
        fx.client.update_task_state(task["task_id"], "canceled", BOB, BOB_PASSWORD)
    assert exc.value.code == TASK_NOT_FOUND


def test_transfer_terminated_task_forbidden(fx):
    task = fx.client.create_task("Completed", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    fx.client.update_task_state(task["task_id"], "completed", BOB, BOB_PASSWORD, result="ok")
    with pytest.raises(ApiClientError) as exc:
        fx.client.transfer_task(task["task_id"], BOB, ALICE, ALICE_PASSWORD)
    assert exc.value.code == TASK_STATE_INVALID


def test_task_persists_across_restart(fx):
    task = fx.client.create_task("Persistent", BOB, ALICE, ALICE_PASSWORD,
                                 client_task_id="persist-1")
    fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        got = server2.client.get_task(task["task_id"], ALICE, ALICE_PASSWORD)
        assert got["state"] == "in_progress"
        assert got["history"][-1]["event"].startswith("state_changed")
    finally:
        server2.stop()


def test_task_validation(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("", BOB, ALICE, ALICE_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("x" * 201, BOB, ALICE, ALICE_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("Prio", BOB, ALICE, ALICE_PASSWORD, priority="urgent")
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("Date", BOB, ALICE, ALICE_PASSWORD, due_at="12/08/2026")
    assert exc.value.code == INVALID_ARGUMENT


def test_task_assignable_cross_organization(fx):
    """Cross-org task assignment now respects external-communication
    policies, exactly like messaging (permissions audit F1 HIGH fix).
    A closed org can no longer push a task to a foreign agent."""
    from .conftest import ORG2_NAME, ORG2_PASSWORD, create_organization

    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("dave", "motdepasse-dave-1", "Agent dave", ORG2_NAME, ORG2_PASSWORD)
    # Both orgs closed by default → task blocked
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task(
            "Cross-organization task", "dave", ALICE, ALICE_PASSWORD,
            business_reference="ref-x-org",
        )
    assert exc.value.code == "POLICY_DENIED"
    # Open both sides → task becomes possible
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    task = fx.client.create_task(
        "Cross-organization task", "dave", ALICE, ALICE_PASSWORD,
        business_reference="ref-x-org",
    )
    assert task["assignee_username"] == "dave"
    # dave sees and updates the task; alice can still consult it
    got = fx.client.get_task(task["task_id"], "dave", "motdepasse-dave-1")
    assert got["state"] == "submitted"
    fx.client.update_task_state(task["task_id"], "in_progress", "dave", "motdepasse-dave-1")
    assert (
        fx.client.get_task(task["task_id"], ALICE, ALICE_PASSWORD)["state"]
        == "in_progress"
    )
    # visibility stays limited to the two parties (non-disclosure)
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_task(task["task_id"], "carol", "motdepasse-carol-1")
    assert exc.value.code == TASK_NOT_FOUND
