"""Tests of organizations and external communication policies
(sections 3 and 6 of SPEC.txt): the four-direction matrix, isolation,
non-disclosure, directory, agent's organization, inter-organization
notifications."""

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
)

ACCESS_DENIED = "ACCESS_DENIED"
AUTH_FAILED = "AUTH_FAILED"
POLICY_DENIED = "POLICY_DENIED"
INVALID_ARGUMENT = "INVALID_ARGUMENT"


@pytest.fixture()
def two_orgs(fx):
    """root_org (alice, bob) + second_org (dave), both closed by
    default."""
    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("dave", "motdepasse-dave-1", "Agent dave", ORG2_NAME, ORG2_PASSWORD)
    return fx


# ---------------------------------------------------------------------------
# get_my_organization
# ---------------------------------------------------------------------------


def test_get_my_organization(fx):
    data = fx.client.get_my_organization(ALICE, ALICE_PASSWORD)
    assert data == {
        "organization_name": ORG_NAME,
        "allow_incoming_external": False,
        "allow_outgoing_external": False,
    }


def test_get_my_organization_reflects_policy_changes(fx):
    fx.client.set_organization_policy(True, False, ORG_NAME, ORG_PASSWORD)
    data = fx.client.get_my_organization(ALICE, ALICE_PASSWORD)
    assert data["allow_incoming_external"] is True
    assert data["allow_outgoing_external"] is False


def test_get_my_organization_auth_failure(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_my_organization(ALICE, "mauvais")
    assert exc.value.code == AUTH_FAILED


# ---------------------------------------------------------------------------
# Policies: read and write
# ---------------------------------------------------------------------------


def test_organization_policy_default_closed(fx):
    policy = fx.client.get_organization_policy(ORG_NAME, ORG_PASSWORD)
    assert policy == {
        "organization_name": ORG_NAME,
        "allow_incoming_external": False,
        "allow_outgoing_external": False,
    }


def test_set_organization_policy(fx):
    data = fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    assert data == {
        "organization_name": ORG_NAME,
        "allow_incoming_external": True,
        "allow_outgoing_external": True,
    }
    policy = fx.client.get_organization_policy(ORG_NAME, ORG_PASSWORD)
    assert policy["allow_incoming_external"] is True
    assert policy["allow_outgoing_external"] is True
    # close again
    fx.client.set_organization_policy(False, False, ORG_NAME, ORG_PASSWORD)
    policy = fx.client.get_organization_policy(ORG_NAME, ORG_PASSWORD)
    assert policy["allow_incoming_external"] is False


def test_set_organization_policy_invalid_type(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("set_organization_policy", {
            "allow_incoming_external": "oui",
            "allow_outgoing_external": False,
            "organization_name_auth": ORG_NAME,
            "organization_password_auth": ORG_PASSWORD,
        })
    assert exc.value.code == INVALID_ARGUMENT


def test_organization_policy_requires_org_auth(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_organization_policy(True, True, ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED


# ---------------------------------------------------------------------------
# Matrix of the four communication directions
# ---------------------------------------------------------------------------


def test_direction_internal_always_allowed(two_orgs):
    """Internal to the organization: always allowed, whatever the state
    of the policies."""
    fx = two_orgs
    sent = fx.send(ALICE, ALICE_PASSWORD, BOB, "interne", "cmid-dir-1")
    assert sent["sender_organization_name"] == ORG_NAME
    assert fx.client.get_messages(BOB, BOB_PASSWORD)["messages"][0]["content"] == "interne"


def test_direction_internal_to_external_blocked_by_default(two_orgs):
    """Internal -> external: refused when the outgoing policy is closed."""
    with pytest.raises(ApiClientError) as exc:
        two_orgs.client.send_message("dave", "sortant", "cmid-dir-2", ALICE, ALICE_PASSWORD)
    assert exc.value.code == POLICY_DENIED


def test_direction_external_to_internal_blocked_by_default(two_orgs):
    """External -> internal: refused when the incoming policy is closed."""
    with pytest.raises(ApiClientError) as exc:
        two_orgs.client.send_message(ALICE, "entrant", "cmid-dir-3", "dave", "motdepasse-dave-1")
    assert exc.value.code == POLICY_DENIED


def test_direction_external_to_external_unrelated(two_orgs):
    """Two agents external to each other: each one's policy governs
    its own outgoing traffic; here dave (closed) cannot write to a third party."""
    create_organization(two_orgs.config, "org_tiers", "motdepasse-org-tiers-1", "motdepasse-org-tiers-1")
    two_orgs.client.create_agent("erin", "motdepasse-erin-1", "Agent erin", "org_tiers", "motdepasse-org-tiers-1")
    with pytest.raises(ApiClientError) as exc:
        two_orgs.client.send_message("erin", "hello", "cmid-dir-4", "dave", "motdepasse-dave-1")
    assert exc.value.code == POLICY_DENIED


def test_direction_inter_org_requires_both_policies(two_orgs):
    """Between two organizations: both policies must allow it."""
    fx = two_orgs
    # only root_org's outgoing open: refusal on the recipient side
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "essai", "cmid-dir-5", ALICE, ALICE_PASSWORD)
    assert exc.value.code == POLICY_DENIED
    # second_org's incoming open too: success
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    sent = fx.client.send_message("dave", "inter-org", "cmid-dir-6", ALICE, ALICE_PASSWORD)
    assert sent["sender_organization_name"] == ORG_NAME
    # and the dave -> alice reply works (policies still open)
    reply = fx.client.send_message(ALICE, "reply", "cmid-dir-7", "dave", "motdepasse-dave-1")
    assert reply["recipient_username"] == ALICE


def test_direction_asymmetric_policies(two_orgs):
    """Asymmetry: an organization can receive without sending."""
    fx = two_orgs
    fx.client.set_organization_policy(True, False, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(False, True, ORG2_NAME, ORG2_PASSWORD)
    # dave (outgoing allowed) -> alice (incoming allowed): OK
    fx.client.send_message(ALICE, "vers alice", "cmid-dir-8", "dave", "motdepasse-dave-1")
    # alice (outgoing refused) -> dave: POLICY_DENIED
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "vers dave", "cmid-dir-9", ALICE, ALICE_PASSWORD)
    assert exc.value.code == POLICY_DENIED


# ---------------------------------------------------------------------------
# Non-disclosure and check order
# ---------------------------------------------------------------------------


def test_closed_org_does_not_reveal_recipient_existence(two_orgs):
    """A closed organization returns POLICY_DENIED before any recipient
    lookup: existing and nonexistent recipients are
    indistinguishable."""
    fx = two_orgs
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "x", "cmid-nd-1", ALICE, ALICE_PASSWORD)
    assert exc.value.code == POLICY_DENIED
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("ghost", "x", "cmid-nd-2", ALICE, ALICE_PASSWORD)
    assert exc.value.code == POLICY_DENIED


def test_open_org_keeps_recipient_not_found(two_orgs):
    """Open organization: a nonexistent or deactivated recipient still yields
    RECIPIENT_NOT_FOUND (v1 semantics preserved)."""
    fx = two_orgs
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("ghost", "x", "cmid-nd-3", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "RECIPIENT_NOT_FOUND"
    # deactivated recipient
    fx.client.deactivate_agent("dave", ORG2_NAME, ORG2_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "x", "cmid-nd-4", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "RECIPIENT_NOT_FOUND"


def test_policy_change_does_not_affect_existing_messages(two_orgs):
    """A policy change never affects existing messages:
    the conversation stays readable on both sides."""
    fx = two_orgs
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    fx.client.send_message("dave", "avant fermeture", "cmid-nd-5", ALICE, ALICE_PASSWORD)
    # close both organizations again
    fx.client.set_organization_policy(False, False, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(False, False, ORG2_NAME, ORG2_PASSWORD)
    # existing messages stay readable on both sides
    inbox_dave = fx.client.get_messages("dave", "motdepasse-dave-1")
    assert "avant fermeture" in [m["content"] for m in inbox_dave["messages"]]
    conv = fx.client.get_conversation(ALICE, "dave", "motdepasse-dave-1")
    assert len(conv["messages"]) == 1
    # but no new message is possible anymore
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "after closing", "cmid-nd-6", ALICE, ALICE_PASSWORD)
    assert exc.value.code == POLICY_DENIED


def test_idempotence_survives_policy_change(two_orgs):
    """Idempotent retrieval returns the already-created message even if a
    policy has changed since (section 6.1)."""
    fx = two_orgs
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    first = fx.client.send_message("dave", "idempotent", "cmid-nd-7", ALICE, ALICE_PASSWORD)
    fx.client.set_organization_policy(False, False, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(False, False, ORG2_NAME, ORG2_PASSWORD)
    second = fx.client.send_message("dave", "idempotent", "cmid-nd-7", ALICE, ALICE_PASSWORD)
    assert second["message_id"] == first["message_id"]


# ---------------------------------------------------------------------------
# Directory and visibility
# ---------------------------------------------------------------------------


def test_list_org_agents_requires_permission(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.list_org_agents(ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED


def test_list_org_agents_with_permission(fx):
    fx.client.set_agent_visibility(ALICE, True, ORG_NAME, ORG_PASSWORD)
    data = fx.client.list_org_agents(ALICE, ALICE_PASSWORD)
    assert set(data["usernames"]) == {ALICE, BOB}
    assert data["next_cursor"] is None


def test_list_org_agents_only_own_org(two_orgs):
    fx = two_orgs
    fx.client.set_agent_visibility(ALICE, True, ORG_NAME, ORG_PASSWORD)
    usernames = fx.client.list_org_agents(ALICE, ALICE_PASSWORD)["usernames"]
    assert "dave" not in usernames  # another organization
    fx.client.set_agent_visibility("dave", True, ORG2_NAME, ORG2_PASSWORD)
    usernames = fx.client.list_org_agents("dave", "motdepasse-dave-1")["usernames"]
    assert usernames == ["dave"]


def test_list_org_agents_paginated(fx):
    for i in range(5):
        fx.client.create_agent(f"agent{i:02d}", "motdepasse-agent-1", "Agent", ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_visibility(ALICE, True, ORG_NAME, ORG_PASSWORD)
    seen = []
    cursor = None
    while True:
        page = fx.client.list_org_agents(ALICE, ALICE_PASSWORD, limit=3, cursor=cursor)
        seen += page["usernames"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 7 and len(set(seen)) == 7
    assert seen == sorted(seen)
    # cursor bound to the command: unusable elsewhere
    page1 = fx.client.list_org_agents(ALICE, ALICE_PASSWORD, limit=3)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD, cursor=page1["next_cursor"])
    assert exc.value.code == INVALID_ARGUMENT


def test_get_agent_description_reveals_org(two_orgs):
    """An agent's organization is a public directory metadata."""
    desc = two_orgs.client.get_agent_description("dave", ALICE, ALICE_PASSWORD)
    assert desc["organization_name"] == ORG2_NAME


def test_sender_organization_name_on_messages(two_orgs):
    """Each message exposes the sender's organization at the time of
    sending."""
    fx = two_orgs
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    sent = fx.client.send_message("dave", "inter-org", "cmid-org-1", ALICE, ALICE_PASSWORD)
    assert sent["sender_organization_name"] == ORG_NAME
    # also visible on the recipient side
    msg = fx.client.get_messages("dave", "motdepasse-dave-1")["messages"][0]
    assert msg["sender_organization_name"] == ORG_NAME


def test_notifications_include_other_organization(two_orgs):
    """Notifications expose the other agent's organization."""
    fx = two_orgs
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    sent = fx.client.send_message("dave", "notif inter-org", "cmid-notif-1", ALICE, ALICE_PASSWORD)
    fx.client.read_message(sent["message_id"], "dave", "motdepasse-dave-1")
    notif = fx.client.get_notifications("dave", "motdepasse-dave-1")
    items = notif["needs_reply"]
    assert len(items) == 1
    assert items[0]["other_username"] == ALICE
    assert items[0]["other_organization_name"] == ORG_NAME


def test_internal_notifications_include_own_org(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "interne", "cmid-notif-2")
    m = fx.client.get_messages(BOB, BOB_PASSWORD)["messages"][0]
    fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)
    notif = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert notif["needs_reply"][0]["other_organization_name"] == ORG_NAME
