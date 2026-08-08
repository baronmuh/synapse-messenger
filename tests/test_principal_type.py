"""F19 — Human accounts (SPEC-WEB §5).

Since SPEC-WEB, the human account is a SYSTEM account: auto-created with
its organization (password delegated to the organization's, never
copied), not creatable via create_agent, visible as organization
metadata (get_org_agents, snapshot), able to authenticate and
to act (approver, messaging).
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError

from .conftest import ORG_NAME, ORG_PASSWORD

HUMAN = f"{ORG_NAME}_humain"


def test_create_agent_cannot_create_human(fx):
    """create_agent refuses principal_type='human': human accounts
    are created automatically with their organization (SPEC-WEB §5)."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent(
            "denise", "motdepasse-denise-1", "Ressources humaines",
            ORG_NAME, ORG_PASSWORD, principal_type="human",
        )
    assert exc.value.code == "INVALID_ARGUMENT"


def test_default_principal_type_is_agent(fx):
    result = fx.client.create_agent(
        "eric", "motdepasse-eric-1", "Agent par défaut", ORG_NAME, ORG_PASSWORD
    )
    assert result["principal_type"] == "agent"


def test_human_visible_in_org_listing(fx):
    """The auto-created human account appears in get_org_agents with
    principal_type 'human'."""
    listing = fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD)
    agents = {a["username"]: a for a in listing["agents"]}
    assert agents[HUMAN]["principal_type"] == "human"


def test_invalid_principal_type_rejected(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent(
            "eric", "motdepasse-eric-1", "desc", ORG_NAME, ORG_PASSWORD,
            principal_type="robot",
        )
    assert exc.value.code == "INVALID_ARGUMENT"


def test_human_can_authenticate_and_approve(fx):
    """The human account is a normal account: it authenticates (with
    ITS organization's password, delegation) and can act as an
    approver (SPEC.txt F8/F19)."""
    tid = fx.client.create_task("leave request", HUMAN, "alice",
                                "motdepasse-alice-1")["task_id"]
    fx.client.update_task_state(tid, "in_progress", HUMAN, ORG_PASSWORD)
    fx.client.request_approval(tid, HUMAN, "alice", "motdepasse-alice-1")
    fx.client.approve_task(tid, HUMAN, ORG_PASSWORD)
    from .conftest import make_server

    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        task = server2.client.get_task(tid, "alice", "motdepasse-alice-1")
        assert task["state"] == "completed"
    finally:
        server2.stop()
