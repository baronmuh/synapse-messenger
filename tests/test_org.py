"""Tests for agent management by an organization (section 3.4):
creation, deactivation, reactivation, password change, visibility
permission, agent listing, organization password rotation, isolation
between organizations and non-disclosure."""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError

from .conftest import (
    ORG_NAME,
    ORG_PASSWORD,
    ORG2_NAME,
    ORG2_PASSWORD,
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    create_organization,
    make_server,
)

ACCESS_DENIED = "ACCESS_DENIED"
AUTH_FAILED = "AUTH_FAILED"
USERNAME_ALREADY_EXISTS = "USERNAME_ALREADY_EXISTS"
USER_NOT_FOUND = "USER_NOT_FOUND"
INVALID_ARGUMENT = "INVALID_ARGUMENT"


def test_create_agent_response(fx):
    data = fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    assert data == {
        "username": "carol",
        "status": "active",
        "description": "Agent carol",
        "organization_name": ORG_NAME,
        "can_see_org_agents": False,
        "principal_type": "agent",
    }


def test_create_agent_normalizes_username(fx):
    data = fx.client.create_agent("Carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    assert data["username"] == "carol"


def test_create_agent_duplicate_username(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("ALICE", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USERNAME_ALREADY_EXISTS


def test_create_agent_agent_caller_denied(fx):
    """An agent cannot call an organization command."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED


def test_create_agent_bad_username_format(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("bad name", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_create_agent_bad_password_format(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol", "court", "Agent carol", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_create_agent_bad_visibility_type(fx):
    """can_see_org_agents must be a strict JSON boolean."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("create_agent", {
            "username": "carol",
            "password": "motdepasse-carol-1",
            "description": "Agent carol",
            "can_see_org_agents": 1,
            "organization_name_auth": ORG_NAME,
            "organization_password_auth": ORG_PASSWORD,
        })
    assert exc.value.code == INVALID_ARGUMENT


def test_agent_created_in_calling_org(fx, config):
    """The organization is never a parameter: the agent is created in the
    organization that authenticates."""
    create_organization(config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG2_NAME, ORG2_PASSWORD)
    desc = fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
    assert desc["organization_name"] == ORG2_NAME


def test_deactivate_and_reactivate(fx):
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    data = fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    assert data == {"username": "carol", "status": "disabled"}
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages("carol", "motdepasse-carol-1")
    assert exc.value.code == AUTH_FAILED
    data = fx.client.reactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    assert data == {"username": "carol", "status": "active"}
    assert fx.client.get_messages("carol", "motdepasse-carol-1") == {
        "messages": [],
        "next_cursor": None,
    }


def test_deactivate_reactivate_idempotent(fx):
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    data = fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    assert data == {"username": "carol", "status": "disabled"}
    fx.client.reactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    data = fx.client.reactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    assert data == {"username": "carol", "status": "active"}


def test_deactivate_unknown_user(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.deactivate_agent("ghost", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_manage_agent_of_another_org_hidden(fx, config):
    """An agent from another organization is indistinguishable from a
    nonexistent account: USER_NOT_FOUND (non-disclosure, section 3.4)."""
    create_organization(config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG2_NAME, ORG2_PASSWORD)
    for call in (
        lambda: fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD),
        lambda: fx.client.reactivate_agent("carol", ORG_NAME, ORG_PASSWORD),
        lambda: fx.client.change_agent_password("carol", "nouveau-motdepasse-1", ORG_NAME, ORG_PASSWORD),
        lambda: fx.client.set_agent_visibility("carol", True, ORG_NAME, ORG_PASSWORD),
    ):
        with pytest.raises(ApiClientError) as exc:
            call()
        assert exc.value.code == USER_NOT_FOUND


def test_change_password(fx):
    data = fx.client.change_agent_password(ALICE, "nouveau-motdepasse-1", ORG_NAME, ORG_PASSWORD)
    assert data == {"username": "alice", "status": "active"}
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(ALICE, ALICE_PASSWORD)
    assert exc.value.code == AUTH_FAILED
    assert fx.client.get_messages(ALICE, "nouveau-motdepasse-1") == {
        "messages": [],
        "next_cursor": None,
    }


def test_change_password_unknown_user(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.change_agent_password("ghost", "nouveau-motdepasse-1", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_change_password_invalid_format(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.change_agent_password(ALICE, "court", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_set_agent_visibility(fx):
    data = fx.client.set_agent_visibility(ALICE, True, ORG_NAME, ORG_PASSWORD)
    assert data == {"username": ALICE, "can_see_org_agents": True}
    data = fx.client.set_agent_visibility(ALICE, False, ORG_NAME, ORG_PASSWORD)
    assert data == {"username": ALICE, "can_see_org_agents": False}


def test_org_commands_require_org_auth(fx):
    """An agent account on an organization command -> ACCESS_DENIED."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", BOB, BOB_PASSWORD)
    assert exc.value.code == ACCESS_DENIED
    # a nonexistent organization -> AUTH_FAILED (no disclosure)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", "org_ghost", "nimporte-quel-motdepasse")
    assert exc.value.code == AUTH_FAILED
    # wrong organization password -> AUTH_FAILED
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, "mauvais-motdepasse")
    assert exc.value.code == AUTH_FAILED


def test_org_cannot_read_message_content(fx):
    """No organization command exposes message content."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "contenu ultra-secret", "cmid-org-1")
    responses = [
        fx.client.get_agent_description(ALICE, ALICE, ALICE_PASSWORD),
        fx.client.deactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD),
        fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD),
        fx.client.reactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD),
        fx.client.change_agent_password(ALICE, "nouveau-motdepasse-1", ORG_NAME, ORG_PASSWORD),
        fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD),
        fx.client.get_organization_policy(ORG_NAME, ORG_PASSWORD),
    ]
    blob = str(responses)
    assert "ultra-secret" not in blob


def test_org_name_normalized_for_auth(fx):
    data = fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol",
                                  ORG_NAME.upper(), ORG_PASSWORD)
    assert data["username"] == "carol"


def test_get_org_agents_lists_all(fx):
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    data = fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD)
    usernames = {agent["username"] for agent in data["agents"]}
    # alice, bob, carol + the auto-created human account (SPEC-WEB §5)
    assert usernames == {ALICE, BOB, "carol", f"{ORG_NAME}_humain"}
    assert data["next_cursor"] is None
    by_name = {agent["username"]: agent for agent in data["agents"]}
    assert by_name["carol"] == {
        "username": "carol",
        "description": "Agent carol",
        "status": "active",
        "can_see_org_agents": False,
        "principal_type": "agent",
        # reputation measured by the server (F16): no results -> 0/None
        "reputation": {"completed": 0, "failed": 0, "canceled": 0, "active": 0,
                       "completion_rate": None},
    }
    # ascending sort by username
    assert [a["username"] for a in data["agents"]] == sorted(usernames)


def test_get_org_agents_includes_disabled(fx):
    fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    data = fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD)
    by_name = {agent["username"]: agent for agent in data["agents"]}
    assert by_name[BOB]["status"] == "disabled"


def test_get_org_agents_paginated(fx):
    for i in range(5):
        fx.client.create_agent(f"agent{i:02d}", "motdepasse-agent-1", "Agent", ORG_NAME, ORG_PASSWORD)
    seen = []
    cursor = None
    while True:
        page = fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD, limit=3, cursor=cursor)
        seen += [a["username"] for a in page["agents"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    # alice, bob, the auto-created human and agent00..04
    assert len(seen) == 8 and len(set(seen)) == 8
    assert seen == sorted(seen)  # ascending sort by username


def test_change_organization_password(fx):
    data = fx.client.change_organization_password("nouveau-mot-de-passe-org", ORG_NAME, ORG_PASSWORD)
    assert data == {"organization_name": ORG_NAME}
    # the old password no longer works
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == AUTH_FAILED
    # the new one works
    fx.client.get_org_agents(ORG_NAME, "nouveau-mot-de-passe-org")
    # agents are not affected
    assert fx.client.get_messages(ALICE, ALICE_PASSWORD) == {"messages": [], "next_cursor": None}


def test_change_organization_password_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.change_organization_password("court", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_organization_permanent_no_deactivate_command(fx):
    """There is no deactivation or deletion command for organizations
    (constraint 24)."""
    from synapse.validation import COMMAND_SPECS
    commands = set(COMMAND_SPECS)
    assert "deactivate_organization" not in commands
    assert "delete_organization" not in commands


def test_create_organization_refuses_duplicate(config):
    from synapse.install import create_organization
    from synapse import db
    create_organization(config, ORG_NAME, ORG_PASSWORD, ORG_PASSWORD)
    with pytest.raises(ValueError):
        create_organization(config, ORG_NAME, "autre-motdepasse-123", "autre-motdepasse-123")
    # the original password is kept
    with db.connect(config) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM organizations WHERE organization_name = ?",
            (ORG_NAME,),
        ).fetchone()
        assert int(row["n"]) == 1
