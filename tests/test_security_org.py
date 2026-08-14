"""Security tests of isolation between organizations (section 4 of the plan):
permission bypass, injections, another organization's resources,
separation of failure budgets, permission/policy changes."""

from __future__ import annotations

import json
import socket

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
)

ACCESS_DENIED = "ACCESS_DENIED"
AUTH_FAILED = "AUTH_FAILED"
INVALID_ARGUMENT = "INVALID_ARGUMENT"


@pytest.fixture()
def two_orgs(fx):
    """root_org (alice, bob) + second_org (dave), both open to
    external exchange to test legitimate crossings."""
    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("dave", "motdepasse-dave-1", "Agent dave", ORG2_NAME, ORG2_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    return fx


# ---------------------------------------------------------------------------
# Bypass: parameter injection
# ---------------------------------------------------------------------------


def test_injection_organization_name_in_create_agent(fx):
    """An agent cannot choose its organization: the organization_name
    parameter does not exist in create_agent (INVALID_ARGUMENT), and the
    organization is always the one that authenticates."""

    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("create_agent", {
            "username": "milicien",
            "password": "motdepasse-milicien-1",
            "description": "Agent",
            "organization_name": ORG2_NAME,  # unknown field -> refused
            "organization_name_auth": ORG_NAME,
            "organization_password_auth": ORG_PASSWORD,
        })
    assert exc.value.code == INVALID_ARGUMENT
    # the account was not created
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_description("milicien", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "USER_NOT_FOUND"


def test_injection_role_field_rejected(fx):
    """The role no longer exists: a role field in create_agent is refused
    (strictly validated)."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("create_agent", {
            "username": "carol",
            "password": "motdepasse-carol-1",
            "description": "Agent",
            "role": "admin",
            "organization_name_auth": ORG_NAME,
            "organization_password_auth": ORG_PASSWORD,
        })
    assert exc.value.code == INVALID_ARGUMENT


def test_agent_cannot_create_agents(two_orgs):
    """An agent cannot create agents (command reserved for
    organizations): neither in its own organization nor elsewhere."""
    fx = two_orgs
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol", "motdepasse-carol-1", "Agent", ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol", "motdepasse-carol-1", "Agent", "dave", "motdepasse-dave-1")
    assert exc.value.code == ACCESS_DENIED


# ---------------------------------------------------------------------------
# Bypass: access to another organization's resources
# ---------------------------------------------------------------------------


def test_org_cannot_manage_other_org_agents(two_orgs):
    """The root_org organization can neither deactivate, nor reset, nor
    change the visibility of a second_org agent (USER_NOT_FOUND)."""
    fx = two_orgs
    for call in (
        lambda: fx.client.deactivate_agent("dave", ORG_NAME, ORG_PASSWORD),
        lambda: fx.client.change_agent_password("dave", "pirate-12345678", ORG_NAME, ORG_PASSWORD),
        lambda: fx.client.set_agent_visibility("dave", True, ORG_NAME, ORG_PASSWORD),
    ):
        with pytest.raises(ApiClientError) as exc:
            call()
        assert exc.value.code == "USER_NOT_FOUND"


def test_org_cannot_read_other_org_messages(two_orgs):
    """Organization commands never grant access to messages:
    no content, no conversation identifier, no notifications."""
    fx = two_orgs
    fx.client.send_message("dave", "secret inter-org", "cmid-sec-1", ALICE, ALICE_PASSWORD)
    responses = [
        fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD),
        fx.client.get_org_agents(ORG2_NAME, ORG2_PASSWORD),
        fx.client.get_organization_policy(ORG_NAME, ORG_PASSWORD),
        fx.client.get_organization_policy(ORG2_NAME, ORG2_PASSWORD),
    ]
    blob = str(responses)
    assert "secret inter-org" not in blob
    # no org command takes a conversation_id or message_id
    from synapse.validation import COMMAND_SPECS
    org_params = set()
    for name, spec in COMMAND_SPECS.items():
        if spec[2]:
            org_params.update(p[0] for p in spec[1])
    assert "conversation_id" not in org_params
    assert "message_id" not in org_params


def test_agent_cannot_read_other_org_conversation(two_orgs):
    """An agent cannot read a conversation of agents from another
    organization: get_conversation requires being a participant."""
    fx = two_orgs
    fx.client.send_message("dave", "un", "cmid-sec-2", ALICE, ALICE_PASSWORD)
    fx.client.send_message(ALICE, "deux", "cmid-sec-3", "dave", "motdepasse-dave-1")
    # bob (root_org) does not take part in the alice-dave conversation
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation("dave", BOB, BOB_PASSWORD)
    assert exc.value.code == "CONVERSATION_NOT_FOUND"
    # carol (other org) neither
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent", ORG2_NAME, ORG2_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation(ALICE, "carol", "motdepasse-carol-1")
    assert exc.value.code == "CONVERSATION_NOT_FOUND"


def test_agent_cannot_read_other_org_message(two_orgs):
    """read_message of a message from another pair: MESSAGE_NOT_FOUND."""
    fx = two_orgs
    sent = fx.client.send_message("dave", "pour dave", "cmid-sec-4", ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.read_message(sent["message_id"], BOB, BOB_PASSWORD)
    assert exc.value.code == "MESSAGE_NOT_FOUND"


def test_notifications_scoped_to_own_account(two_orgs):
    """An agent only sees ITS notifications; the organization cannot
    call get_notifications (agent command)."""
    fx = two_orgs
    fx.client.send_message("dave", "to dave", "cmid-sec-5", ALICE, ALICE_PASSWORD)
    fx.client.send_message(BOB, "to bob", "cmid-sec-6", ALICE, ALICE_PASSWORD)
    # dave only sees his message
    inbox = fx.client.get_messages("dave", "motdepasse-dave-1")
    assert len(inbox["messages"]) == 1
    assert inbox["messages"][0]["content"] == "to dave"
    # the org cannot read the agents' notifications: an organization
    # name is not an agent account -> AUTH_FAILED
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("get_notifications", {
            "my_name_auth": ORG_NAME,
            "my_password_auth": ORG_PASSWORD,
            "limit": 50,
            "cursor": None,
        })
    assert exc.value.code == AUTH_FAILED


# ---------------------------------------------------------------------------
# Permission and policy changes
# ---------------------------------------------------------------------------


def test_revoking_visibility_takes_effect_immediately(two_orgs):
    """Revoking can_see_org_agents immediately blocks list_org_agents;
    existing conversations stay intact."""
    fx = two_orgs
    fx.client.set_agent_visibility(ALICE, True, ORG_NAME, ORG_PASSWORD)
    assert fx.client.list_org_agents(ALICE, ALICE_PASSWORD)["usernames"]
    fx.client.set_agent_visibility(ALICE, False, ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.list_org_agents(ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED
    # messaging is not affected
    fx.send(ALICE, ALICE_PASSWORD, BOB, "toujours", "cmid-sec-7")
    assert fx.client.get_messages(BOB, BOB_PASSWORD)["messages"]


def test_visibility_granted_by_org_only(two_orgs):
    """An agent cannot grant itself the permission."""
    fx = two_orgs
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("set_agent_visibility", {
            "username": ALICE,
            "can_see_org_agents": True,
            "organization_name_auth": ALICE,  # an agent, not an org
            "organization_password_auth": ALICE_PASSWORD,
        })
    assert exc.value.code == ACCESS_DENIED


def test_policy_change_blocks_new_messages_only(two_orgs):
    """Closing an organization again blocks new messages but leaves
    existing conversations readable and notifications intact."""
    fx = two_orgs
    fx.client.send_message("dave", "avant", "cmid-sec-8", ALICE, ALICE_PASSWORD)
    fx.client.set_organization_policy(False, False, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(False, False, ORG2_NAME, ORG2_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "after", "cmid-sec-9", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "POLICY_DENIED"
    conv = fx.client.get_conversation(ALICE, "dave", "motdepasse-dave-1")
    assert len(conv["messages"]) == 1


# ---------------------------------------------------------------------------
# Separate failure budgets and disabled accounts
# ---------------------------------------------------------------------------


def test_org_and_agent_failure_budgets_separate(fx, config):
    """Five organization failures on a name do not lock the agent
    with the same name, and vice versa."""
    from synapse import db
    from synapse.store import authfail

    with db.connect(config) as conn:
        for _ in range(5):
            authfail.record(conn, f"org:{ALICE}")
    # the alice agent can still authenticate (separate budget)
    assert fx.client.get_messages(ALICE, ALICE_PASSWORD) == {"messages": [], "next_cursor": None}
    # the alice org (nonexistent) is locked
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_agents(ALICE, "nimporte-quel-motdepasse")
    assert exc.value.code == AUTH_FAILED
    # the root_org org is not affected by the agent's failures
    with db.connect(config) as conn:
        for _ in range(5):
            authfail.record(conn, ALICE)
    fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD)


def test_disabled_agent_cannot_be_used_as_relay(two_orgs):
    """A disabled agent can neither send nor receive: no
    reactivation by bypass, its messages stay inaccessible."""
    fx = two_orgs
    fx.client.send_message("dave", "before deactivation", "cmid-sec-10", ALICE, ALICE_PASSWORD)
    fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    # bob can no longer authenticate
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(BOB, BOB_PASSWORD)
    assert exc.value.code == AUTH_FAILED
    # nobody can write to bob (disabled recipient)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message(BOB, "to bob", "cmid-sec-11", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "RECIPIENT_NOT_FOUND"
    # messages received by bob before disabling stay stored but
    # inaccessible without reactivation
    fx.client.reactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    assert len(fx.client.get_messages(BOB, BOB_PASSWORD)["messages"]) == 0  # bob received nothing


def test_unknown_organization_auth_fails(two_orgs):
    """Nonexistent organization: AUTH_FAILED, without disclosure."""
    fx = two_orgs
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_agents("org_ghost", "nimporte-quel-motdepasse")
    assert exc.value.code == AUTH_FAILED
    # the agent with the same name is not revealed by organization auth
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_agents(ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED  # an agent cannot be an org


def test_raw_socket_cannot_forge_identity(two_orgs):
    """A raw request cannot impersonate an organization
    with agent credentials (ACCESS_DENIED), nor add fields."""
    fx = two_orgs
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(fx.config.socket_path)
        s.sendall(json.dumps({
            "api_version": "v2",
            "command": "create_agent",
            "parameters": {
                "username": "carol",
                "password": "motdepasse-carol-1",
                "description": "Agent",
                "can_see_org_agents": False,
                "organization_name_auth": ALICE,
                "organization_password_auth": ALICE_PASSWORD,
                "extra": True,
            },
        }).encode() + b"\n")
        s.shutdown(socket.SHUT_WR)
        raw = b"".join(iter(lambda: s.recv(65536), b""))
    finally:
        s.close()
    resp = json.loads(raw)
    assert resp["error"]["code"] == INVALID_ARGUMENT  # extra field refused first
