"""F2 — Agent cards: declaration, reading, validation by the organization.

Covers the full lifecycle (submission → pending → approval →
re-submission → re-pending), field validation, visibility,
inter-organization isolation, and persistence.
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import (
    INVALID_ARGUMENT,
    USER_NOT_FOUND,
)

from .conftest import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    ORG2_NAME,
    ORG2_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
    make_server,
)

CARD = {
    "capabilities": ["comptabilite", "reporting"],
    "domain": "finance",
    "model": "demo-model-2",
    "tools": ["tableur", "api-facturation"],
    "sla": "reponse sous 1 heure",
    "limits": "10 operations par jour",
    "estimated_cost": "0.01 EUR",
}


def test_set_and_get_card_roundtrip(fx):
    card = fx.client.set_agent_card(
        CARD["capabilities"], ALICE, ALICE_PASSWORD,
        domain=CARD["domain"], model=CARD["model"], tools=CARD["tools"],
        sla=CARD["sla"], limits=CARD["limits"], estimated_cost=CARD["estimated_cost"],
    )
    assert card["username"] == ALICE
    assert card["validation_state"] == "pending"
    assert card["approved_by"] is None
    got = fx.client.get_agent_card(ALICE, BOB, BOB_PASSWORD)
    assert got["capabilities"] == ["comptabilite", "reporting"]
    assert got["domain"] == "finance"
    assert got["model"] == "demo-model-2"
    assert got["tools"] == ["tableur", "api-facturation"]
    assert got["sla"] == "reponse sous 1 heure"
    assert got["limits"] == "10 operations par jour"
    assert got["estimated_cost"] == "0.01 EUR"
    assert got["validation_state"] == "pending"


def test_approval_flow(fx):
    fx.client.set_agent_card(
        CARD["capabilities"], ALICE, ALICE_PASSWORD,
        domain=CARD["domain"], model=CARD["model"], tools=CARD["tools"],
        sla=CARD["sla"], limits=CARD["limits"], estimated_cost=CARD["estimated_cost"],
    )
    result = fx.client.approve_agent_card(ALICE, ORG_NAME, ORG_PASSWORD)
    assert result["validation_state"] == "approved"
    got = fx.client.get_agent_card(ALICE, BOB, BOB_PASSWORD)
    assert got["validation_state"] == "approved"
    assert got["approved_by"] == ORG_NAME
    assert got["approved_at"] is not None
    # re-submission -> re-pending (the card stays displayed)
    fx.client.set_agent_card(["nouvelle-capacite"], ALICE, ALICE_PASSWORD)
    got = fx.client.get_agent_card(ALICE, BOB, BOB_PASSWORD)
    assert got["validation_state"] == "pending"
    assert got["approved_by"] is None


def test_card_without_account_not_found(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_card("ghost", ALICE, ALICE_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_card_absent_returns_empty(fx):
    card = fx.client.get_agent_card(BOB, ALICE, ALICE_PASSWORD)
    assert card["username"] == BOB
    assert card["capabilities"] == []
    assert card["validation_state"] is None


def test_capabilities_required(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.request(
            "set_agent_card",
            {"capabilities": [], "my_name_auth": ALICE, "my_password_auth": ALICE_PASSWORD},
        )
    assert exc.value.code == INVALID_ARGUMENT


def test_capabilities_deduplicated_and_normalized(fx):
    # case-insensitive deduplication (first occurrence kept) + strip
    card = fx.client.set_agent_card(
        ["Reporting", "reporting", "  Accounting  "], ALICE, ALICE_PASSWORD
    )
    assert card["capabilities"] == ["Reporting", "Accounting"]


def test_capability_too_long_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_agent_card(["x" * 65], ALICE, ALICE_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_too_many_capabilities_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.set_agent_card([f"cap-{i}" for i in range(51)], ALICE, ALICE_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_wrong_types_invalid(fx):
    with pytest.raises(ApiClientError):
        fx.client.set_agent_card(["a"], ALICE, ALICE_PASSWORD, domain=42)
    with pytest.raises(ApiClientError):
        fx.client.set_agent_card("pas-une-liste", ALICE, ALICE_PASSWORD)


def test_org_cannot_approve_foreign_agent(fx):
    # second organization + foreign agent
    from synapse.install import create_organization

    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("carol", "motdepasse-carol-1", "desc", ORG2_NAME, ORG2_PASSWORD)
    fx.client.set_agent_card(["compta"], ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.approve_agent_card(ALICE, ORG2_NAME, ORG2_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND  # alice is not a member of org2


def test_org_cannot_approve_cardless_agent(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.approve_agent_card(BOB, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_agent_cannot_set_another_agent_card(fx):
    """set_agent_card only applies to the caller (no target parameter exists)."""
    fx.client.set_agent_card(["a"], ALICE, ALICE_PASSWORD)
    # bob cannot modify alice's card: no target parameter exists
    got = fx.client.get_agent_card(ALICE, BOB, BOB_PASSWORD)
    assert got["capabilities"] == ["a"]


def test_card_unknown_account_approval(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.approve_agent_card("ghost", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_disabled_agent_card_still_readable(fx):
    fx.client.set_agent_card(["a"], ALICE, ALICE_PASSWORD)
    fx.client.deactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD)
    got = fx.client.get_agent_card(ALICE, BOB, BOB_PASSWORD)
    assert got["capabilities"] == ["a"]


def test_card_persists_across_restart(fx):
    fx.client.set_agent_card(
        CARD["capabilities"], ALICE, ALICE_PASSWORD,
        domain=CARD["domain"], model=CARD["model"], tools=CARD["tools"],
        sla=CARD["sla"], limits=CARD["limits"], estimated_cost=CARD["estimated_cost"],
    )
    fx.client.approve_agent_card(ALICE, ORG_NAME, ORG_PASSWORD)
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        got = server2.client.get_agent_card(ALICE, BOB, BOB_PASSWORD)
        assert got["validation_state"] == "approved"
        assert got["approved_by"] == ORG_NAME
    finally:
        server2.stop()
