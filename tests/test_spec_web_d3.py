"""SPEC-WEB D3 tests — Organization and agent management.

create_org (humans only, org + human account atomic),
disable_org (full authentication freeze, data intact),
enable_org (LOCAL procedure: synapse-init-org --enable / install.enable_organization),
human-account guardrails (reserved suffix, not deactivatable, no
own password, description not editable).
"""

from __future__ import annotations

import sqlite3

import pytest

from synapse.client import ApiClientError
from synapse.install import enable_organization
from tests.conftest import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
)

HUMAN = f"{ORG_NAME}_humain"
NEW_ORG = "org_tiers"
NEW_PASSWORD = "motdepasse-org-tiers-1"


@pytest.fixture()
def human(fx):
    return HUMAN


# ---------------------------------------------------------------------------
# create_org
# ---------------------------------------------------------------------------


def test_d3_create_org_by_human(fx, human):
    """A human creates an organization: org + human account atomic,
    the new org's human account authenticates with ITS OWN password."""
    data = fx.client.create_org(NEW_ORG, NEW_PASSWORD, human, ORG_PASSWORD)
    assert data["organization_name"] == NEW_ORG
    assert data["human_username"] == f"{NEW_ORG}_humain"
    # the new human authenticates with the NEW org's password
    info = fx.client.get_my_organization(f"{NEW_ORG}_humain", NEW_PASSWORD)
    assert info["organization_name"] == NEW_ORG
    # and NOT with root_org's (correct delegation)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_my_organization(f"{NEW_ORG}_humain", ORG_PASSWORD)
    assert exc.value.code == "AUTH_FAILED"


def test_d3_create_org_requires_human(fx, human):
    """An agent cannot create an organization (the web is humans only)."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_org(NEW_ORG, NEW_PASSWORD, ALICE, ALICE_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d3_create_org_duplicate(fx, human):
    fx.client.create_org(NEW_ORG, NEW_PASSWORD, human, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_org(NEW_ORG, NEW_PASSWORD, human, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_d3_create_org_bad_password(fx, human):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_org(NEW_ORG, "court", human, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_d3_create_org_audited(fx, human):
    """create_org is traced in the audit log."""
    fx.client.create_org(NEW_ORG, NEW_PASSWORD, human, ORG_PASSWORD)
    audit = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD)
    rows = [e for e in audit["entries"] if e["command"] == "create_org"]
    assert rows and rows[-1]["actor_username"] == HUMAN


def test_d3_create_org_first_org_is_local(fx, human):
    """Even a human cannot create the first organization: root_org
    already exists (local creation) — the duplicate is rejected."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_org(ORG_NAME, ORG_PASSWORD, human, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# disable_org / enable_org
# ---------------------------------------------------------------------------


def test_d3_disable_freezes_all_auth(fx, human):
    """Freeze: human, agents and organization can no longer authenticate;
    the data stays in the database (no deletion)."""
    fx.send(human, ORG_PASSWORD, ALICE, "before freeze", "cmid-d3-1")
    fx.client.disable_org(ORG_NAME, human, ORG_PASSWORD)
    for name, password in ((HUMAN, ORG_PASSWORD), (ALICE, ALICE_PASSWORD),
                           (BOB, BOB_PASSWORD)):
        with pytest.raises(ApiClientError) as exc:
            fx.client.get_my_organization(name, password)
        assert exc.value.code == "AUTH_FAILED", name
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_organization_policy(ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "AUTH_FAILED"
    # data intact (messages still in the database)
    conn = sqlite3.connect(fx.config.db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sender_username = ?", (HUMAN,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_d3_disable_only_own_org(fx, human):
    """Isolation: a human only disables THEIR OWN organization."""
    fx.client.create_org(NEW_ORG, NEW_PASSWORD, human, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.disable_org(NEW_ORG, human, ORG_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d3_disable_idempotent(fx, human):
    """After the freeze, even the owner can no longer authenticate: the
    freeze is absolute (the handler's INVALID_ARGUMENT path only serves
    a race between two concurrent freezes)."""
    fx.client.disable_org(ORG_NAME, human, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.disable_org(ORG_NAME, human, ORG_PASSWORD)
    assert exc.value.code == "AUTH_FAILED"


def test_d3_disable_requires_human(fx, human):
    with pytest.raises(ApiClientError) as exc:
        fx.client.disable_org(ORG_NAME, ALICE, ALICE_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d3_enable_is_local_procedure(fx, human, config):
    """enable_org does NOT exist as a network command: reactivation is a
    local procedure (install.enable_organization), proof of control
    through the organization's password."""
    fx.client.disable_org(ORG_NAME, human, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("enable_org", {"organization_name": ORG_NAME,
                                         "my_name_auth": HUMAN,
                                         "my_password_auth": ORG_PASSWORD})
    assert exc.value.code == "UNKNOWN_COMMAND"
    # local procedure: wrong password rejected
    with pytest.raises(ValueError) as exc:
        enable_organization(config, ORG_NAME, "wrong-password-0000")
    assert "incorrect" in str(exc.value).lower()
    # local procedure: correct proof of control -> reactivation
    assert enable_organization(config, ORG_NAME, ORG_PASSWORD) == ORG_NAME
    assert fx.client.get_my_organization(HUMAN, ORG_PASSWORD)["organization_name"] == ORG_NAME
    assert fx.client.get_my_organization(ALICE, ALICE_PASSWORD)["organization_name"] == ORG_NAME


def test_d3_enable_local_unknown_org(config):
    with pytest.raises(ValueError):
        enable_organization(config, "org_inexistante", ORG_PASSWORD)


def test_d3_enable_local_already_active(fx, config):
    with pytest.raises(ValueError) as exc:
        enable_organization(config, ORG_NAME, ORG_PASSWORD)
    assert "is not deactivated" in str(exc.value)


# ---------------------------------------------------------------------------
# Agent management: human-account guardrails
# ---------------------------------------------------------------------------


def test_d3_agent_reserved_human_suffix(fx):
    """The _humain suffix is reserved: create_agent rejects it."""
    for bad in (f"{ORG_NAME}_humain", "autre_humain", "x_humain_123"):
        with pytest.raises(ApiClientError) as exc:
            fx.client.create_agent(bad, ORG_PASSWORD + "x", "tentative",
                                   ORG_NAME, ORG_PASSWORD)
        assert exc.value.code == "INVALID_ARGUMENT", bad


def test_d3_agent_cannot_be_human(fx):
    """create_agent cannot create a human account (system auto-creation)."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("agent_h", ORG_PASSWORD + "x", "humain?",
                               ORG_NAME, ORG_PASSWORD, principal_type="human")
    assert exc.value.code == "INVALID_ARGUMENT"


def test_d3_human_not_deactivatable(fx, human):
    with pytest.raises(ApiClientError) as exc:
        fx.client.deactivate_agent(HUMAN, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d3_human_has_no_own_password(fx, human):
    with pytest.raises(ApiClientError) as exc:
        fx.client.change_agent_password(HUMAN, ORG_PASSWORD + "y",
                                        ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d3_human_description_not_editable(fx, human):
    with pytest.raises(ApiClientError) as exc:
        fx.client.change_agent_description(HUMAN, "new description",
                                           ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d3_change_agent_description(fx):
    """change_agent_description (org) modifies an agent's description."""
    fx.client.change_agent_description(ALICE, "Description updated",
                                       ORG_NAME, ORG_PASSWORD)
    desc = fx.client.get_agent_description(ALICE, ALICE, ALICE_PASSWORD)
    assert desc["description"] == "Description updated"
    # outside the organization -> USER_NOT_FOUND (non-disclosure)
    fx.client.create_org("org_b", ORG_PASSWORD + "z", f"{ORG_NAME}_humain", ORG_PASSWORD)
    fx.create_agent("agent_b", ORG_PASSWORD + "z", "B", "org_b", ORG_PASSWORD + "z")
    with pytest.raises(ApiClientError) as exc:
        fx.client.change_agent_description("agent_b", "x", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "USER_NOT_FOUND"
