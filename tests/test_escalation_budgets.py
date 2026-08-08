"""F9 — Automatic escalation and budget guardrails (active tasks, messages
per hour). Covers the organization rules, refusals (QUOTA_EXCEEDED), and
edge cases (unavailable target).
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import QUOTA_EXCEEDED, USER_NOT_FOUND

from .conftest import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
)


def test_active_task_budget(fx):
    fx.client.set_agent_budget(BOB, ORG_NAME, ORG_PASSWORD, max_active_tasks=1)
    fx.client.create_task("First", BOB, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("Seconde", BOB, ALICE, ALICE_PASSWORD)
    assert exc.value.code == QUOTA_EXCEEDED
    # a completed task frees the quota
    work = fx.client.get_my_work(BOB, BOB_PASSWORD)
    fx.client.update_task_state(work["work_items"][0]["task_id"], "in_progress", BOB, BOB_PASSWORD)
    fx.client.update_task_state(work["work_items"][0]["task_id"], "completed", BOB, BOB_PASSWORD,
                                result="ok")
    fx.client.create_task("After release", BOB, ALICE, ALICE_PASSWORD)


def test_transfer_respects_target_budget(fx):
    fx.create_agent("carol", "motdepasse-carol-1")
    fx.client.set_agent_budget("carol", ORG_NAME, ORG_PASSWORD, max_active_tasks=1)
    t1 = fx.client.create_task("To transfer", BOB, ALICE, ALICE_PASSWORD)
    fx.client.create_task("Occupe carol", "carol", ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.transfer_task(t1["task_id"], "carol", ALICE, ALICE_PASSWORD)
    assert exc.value.code == QUOTA_EXCEEDED


def test_message_budget(fx):
    fx.client.set_agent_budget(ALICE, ORG_NAME, ORG_PASSWORD, max_messages_per_hour=2)
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-bud-1")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-bud-2")
    with pytest.raises(ApiClientError) as exc:
        fx.send(ALICE, ALICE_PASSWORD, BOB, "trois", "cmid-bud-3")
    assert exc.value.code == QUOTA_EXCEEDED


def test_message_budget_idempotent_retry_not_charged(fx):
    fx.client.set_agent_budget(ALICE, ORG_NAME, ORG_PASSWORD, max_messages_per_hour=1)
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-bud-idem")
    # the idempotent resend does not consume the budget
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-bud-idem")
    with pytest.raises(ApiClientError) as exc:
        fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-bud-2")
    assert exc.value.code == QUOTA_EXCEEDED


def test_budget_removed_when_both_null(fx):
    fx.client.set_agent_budget(BOB, ORG_NAME, ORG_PASSWORD, max_active_tasks=1)
    fx.client.create_task("A", BOB, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError):
        fx.client.create_task("B", BOB, ALICE, ALICE_PASSWORD)
    fx.client.set_agent_budget(BOB, ORG_NAME, ORG_PASSWORD, max_active_tasks=None,
                               max_messages_per_hour=None)
    fx.client.create_task("C", BOB, ALICE, ALICE_PASSWORD)  # no more limit


def test_escalation_due_task(fx):
    fx.client.set_escalation_policy(True, 1, 1, ALICE, ORG_NAME, ORG_PASSWORD)
    # task with past due date assigned to bob
    fx.client.create_task("En retard", BOB, ALICE, ALICE_PASSWORD,
                          due_at="2020-01-01T00:00:00.000Z")
    # the next write triggers escalation to alice
    fx.client.create_task("Trigger", BOB, ALICE, ALICE_PASSWORD)
    escalated = fx.client.list_tasks(ALICE, ALICE_PASSWORD)
    assert any(t["title"] == "En retard" and t["assignee_username"] == ALICE
               for t in escalated["tasks"])
    # escalation event emitted for alice
    events = fx.client.get_events(ALICE, ALICE_PASSWORD, types=["task.escalated"])
    assert len(events["events"]) == 1


def test_escalation_disabled_by_default(fx):
    fx.client.create_task("En retard", BOB, ALICE, ALICE_PASSWORD,
                          due_at="2020-01-01T00:00:00.000Z")
    fx.client.create_task("Trigger", BOB, ALICE, ALICE_PASSWORD)
    # without an enabled policy, no escalation
    assert fx.client.get_events(ALICE, ALICE_PASSWORD, types=["task.escalated"])["events"] == []


def test_escalation_target_must_be_member(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_escalation_policy(True, 60, 60, "ghost", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_escalation_skipped_when_target_disabled(fx):
    fx.create_agent("carol", "motdepasse-carol-1")
    fx.client.set_escalation_policy(True, 1, 1, "carol", ORG_NAME, ORG_PASSWORD)
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    late = fx.client.create_task("En retard", BOB, ALICE, ALICE_PASSWORD,
                                 due_at="2020-01-01T00:00:00.000Z")
    fx.client.create_task("Trigger", BOB, ALICE, ALICE_PASSWORD)
    # no silent escalation to a disabled account: the task stays with bob
    got = fx.client.get_task(late["task_id"], ALICE, ALICE_PASSWORD)
    assert got["assignee_username"] == BOB
    assert fx.client.get_events(ALICE, ALICE_PASSWORD, types=["task.escalated"])["events"] == []


def test_escalation_thresholds_reject_null_and_zero(fx):
    """Escalation thresholds are integers >= 1 (AUDIT-003): `null` (which
    would become 0 → immediate escalation of all tasks) and 0 are
    rejected by validation."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_escalation_policy(True, None, 60, ALICE, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_escalation_policy(True, 60, None, ALICE, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_escalation_policy(True, 0, 60, ALICE, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_escalation_policy(True, 60, 0, ALICE, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
    # a valid value stays accepted
    fx.client.set_escalation_policy(True, 60, 60, ALICE, ORG_NAME, ORG_PASSWORD)
