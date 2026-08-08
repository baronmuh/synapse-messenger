"""F13/F14 — Organizational structure (departments, fixed roles) and
permission manager: creation, attachment, structure, isolation and
manager access to the tasks of its department.
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import ACCESS_DENIED, INVALID_ARGUMENT, USER_NOT_FOUND

from .conftest import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    ORG2_NAME,
    ORG2_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
)


def test_department_lifecycle(fx):
    fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    fx.client.create_department("finance", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_department(ALICE, "support", "manager", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_department(BOB, "support", "employee", ORG_NAME, ORG_PASSWORD)
    structure = fx.client.get_org_structure(ORG_NAME, ORG_PASSWORD)
    depts = {d["department_name"]: d for d in structure["departments"]}
    assert set(depts) == {"support", "finance"}
    assert depts["support"]["members"] == [
        {"username": ALICE, "role": "manager"},
        {"username": BOB, "role": "employee"},
    ]
    # the auto-created human account is not assigned to any department
    assert structure["unassigned_agents"] == [f"{ORG_NAME}_humain"]


def test_duplicate_department_invalid(fx):
    fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_unknown_department_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_agent_department(ALICE, "ghost", "manager", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_foreign_agent_rejected(fx):
    from synapse.install import create_organization

    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("carol", "motdepasse-carol-1", "desc", ORG2_NAME, ORG2_PASSWORD)
    fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_agent_department("carol", "support", "manager", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_invalid_role_rejected(fx):
    fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_agent_department(ALICE, "support", "ceo", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_manager_lists_department_tasks(fx):
    fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_department(ALICE, "support", "manager", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_department(BOB, "support", "employee", ORG_NAME, ORG_PASSWORD)
    # bob (employee) is assigned two tasks
    fx.client.create_task("T1", BOB, ALICE, ALICE_PASSWORD)
    fx.client.create_task("T2", BOB, ALICE, ALICE_PASSWORD)
    fx.client.create_task("T3", BOB, BOB, BOB_PASSWORD)  # created by bob, assigned to bob
    result = fx.client.list_department_tasks("support", ALICE, ALICE_PASSWORD)
    assert len(result["tasks"]) == 3
    assert all(t["assignee_username"] == BOB for t in result["tasks"])


def test_non_manager_denied(fx):
    fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_department(BOB, "support", "employee", ORG_NAME, ORG_PASSWORD)
    fx.client.create_task("T1", BOB, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.list_department_tasks("support", BOB, BOB_PASSWORD)
    assert exc.value.code == ACCESS_DENIED


def test_other_department_manager_denied(fx):
    fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    fx.client.create_department("finance", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_department(ALICE, "finance", "manager", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_department(BOB, "support", "employee", ORG_NAME, ORG_PASSWORD)
    fx.client.create_task("T1", BOB, ALICE, ALICE_PASSWORD)
    # alice is the finance manager, not support
    with pytest.raises(ApiClientError) as exc:
        fx.client.list_department_tasks("support", ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED


def test_structure_persists_across_restart(fx):
    from .conftest import make_server

    fx.client.create_department("support", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_department(ALICE, "support", "manager", ORG_NAME, ORG_PASSWORD)
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        structure = server2.client.get_org_structure(ORG_NAME, ORG_PASSWORD)
        assert structure["departments"][0]["department_name"] == "support"
        assert structure["departments"][0]["members"] == [
            {"username": ALICE, "role": "manager"}
        ]
    finally:
        server2.stop()
