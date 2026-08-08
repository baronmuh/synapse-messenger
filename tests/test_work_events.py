"""F6 (get_my_work) and F10 (get_events): the agent's work queue and
the queryable event journal.
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import INVALID_ARGUMENT

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ORG_NAME, ORG_PASSWORD


def test_my_work_lists_assigned_tasks(fx):
    fx.client.create_task("To process", BOB, ALICE, ALICE_PASSWORD)
    fx.client.create_task("In progress", BOB, ALICE, ALICE_PASSWORD)
    work = fx.client.get_my_work(BOB, BOB_PASSWORD)
    assert len(work["work_items"]) == 2
    assert all(t["assignee_username"] == BOB for t in work["work_items"])
    # completed tasks disappear from the queue
    fx.client.update_task_state(work["work_items"][0]["task_id"], "in_progress", BOB, BOB_PASSWORD)
    fx.client.update_task_state(work["work_items"][0]["task_id"], "completed", BOB, BOB_PASSWORD,
                                result="ok")
    remaining = fx.client.get_my_work(BOB, BOB_PASSWORD)
    assert len(remaining["work_items"]) == 1


def test_my_work_ordered_by_due_at(fx):
    t_late = fx.client.create_task("No due date", BOB, ALICE, ALICE_PASSWORD)
    t_soon = fx.client.create_task("Urgent", BOB, ALICE, ALICE_PASSWORD,
                                   due_at="2026-08-01T00:00:00.000Z")
    fx.client.create_task("Moins urgent", BOB, ALICE, ALICE_PASSWORD,
                          due_at="2026-09-01T00:00:00.000Z")
    work = fx.client.get_my_work(BOB, BOB_PASSWORD)
    ids = [t["task_id"] for t in work["work_items"]]
    assert ids[0] == t_soon["task_id"]
    assert t_late["task_id"] in ids[-2:]  # no due date goes last


def test_my_work_includes_pending_approvals(fx):
    task = fx.client.create_task("To validate", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    fx.client.request_approval(task["task_id"], ALICE, BOB, BOB_PASSWORD)
    # alice sees the pending approval in her queue
    work = fx.client.get_my_work(ALICE, ALICE_PASSWORD)
    assert [t["task_id"] for t in work["work_items"]] == [task["task_id"]]
    assert work["work_items"][0]["state"] == "pending_approval"


def test_events_are_emitted_for_creator_and_assignee(fx):
    task = fx.client.create_task("Events", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    # alice (creator) and bob (assignee) receive the events
    ev_a = fx.client.get_events(ALICE, ALICE_PASSWORD)["events"]
    ev_b = fx.client.get_events(BOB, BOB_PASSWORD)["events"]
    assert [e["event_type"] for e in ev_a] == ["task.created", "task.state_changed"]
    assert [e["event_type"] for e in ev_b] == ["task.created", "task.state_changed"]
    assert all(e["ref_id"] == task["task_id"] for e in ev_a)


def test_events_filtered_by_type(fx):
    fx.client.create_task("Filtre", BOB, ALICE, ALICE_PASSWORD)
    events = fx.client.get_events(ALICE, ALICE_PASSWORD, types=["task.state_changed"])
    assert events["events"] == []
    events = fx.client.get_events(ALICE, ALICE_PASSWORD, types=["task.created"])
    assert len(events["events"]) == 1


def test_events_unknown_type_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_events(ALICE, ALICE_PASSWORD, types=["bogus.type"])
    assert exc.value.code == INVALID_ARGUMENT


def test_events_do_not_leak_between_agents(fx):
    fx.create_agent("carol", "motdepasse-carol-1")
    fx.client.create_task("Confidentielle", BOB, ALICE, ALICE_PASSWORD)
    # carol (unrelated to the task) sees no events
    events = fx.client.get_events("carol", "motdepasse-carol-1")
    assert events["events"] == []


def test_events_pagination_with_cursor(fx):
    for i in range(3):
        fx.client.create_task(f"Task {i}", BOB, ALICE, ALICE_PASSWORD)
    page1 = fx.client.get_events(ALICE, ALICE_PASSWORD, limit=2)
    assert len(page1["events"]) == 2
    assert page1["next_cursor"] is not None
    page2 = fx.client.get_events(ALICE, ALICE_PASSWORD, limit=2, cursor=page1["next_cursor"])
    assert len(page2["events"]) == 1
    # smart polling: a new event appears via the cursor,
    # without re-delivering already seen ones
    fx.client.create_task("New task", BOB, ALICE, ALICE_PASSWORD)
    page3 = fx.client.get_events(ALICE, ALICE_PASSWORD, cursor=page1["next_cursor"])
    assert len(page3["events"]) == 2  # the 3rd event + the new one
    assert page3["events"][-1]["ref_id"] != page2["events"][0]["ref_id"]
