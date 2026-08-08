"""F3 — Agent search by capability (`find_agents`).

Covers the filters (capability, domain, name), the
`can_see_org_agents` permission, inter-organization isolation, pagination and
edge cases (no card, disabled agent).
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import ACCESS_DENIED, INVALID_ARGUMENT

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


def _give_visibility(fx, username: str, password: str) -> None:
    fx.client.set_agent_visibility(username, True, ORG_NAME, ORG_PASSWORD)


def test_find_by_capability(fx):
    _give_visibility(fx, ALICE, ALICE_PASSWORD)
    fx.client.set_agent_card(["comptabilite", "reporting"], ALICE, ALICE_PASSWORD)
    fx.client.set_agent_card(["marketing"], BOB, BOB_PASSWORD)
    result = fx.client.find_agents(ALICE, ALICE_PASSWORD, capability="compta")
    usernames = [a["username"] for a in result["agents"]]
    assert usernames == [ALICE]
    # case-insensitive substring
    result = fx.client.find_agents(ALICE, ALICE_PASSWORD, capability="COMPTA")
    assert [a["username"] for a in result["agents"]] == [ALICE]


def test_find_by_domain_and_name(fx):
    _give_visibility(fx, ALICE, ALICE_PASSWORD)
    fx.client.set_agent_card(["a"], ALICE, ALICE_PASSWORD, domain="finance")
    fx.client.set_agent_card(["b"], BOB, BOB_PASSWORD, domain="marketing")
    by_domain = fx.client.find_agents(ALICE, ALICE_PASSWORD, domain="fin")
    assert [a["username"] for a in by_domain["agents"]] == [ALICE]
    by_name = fx.client.find_agents(ALICE, ALICE_PASSWORD, name_contains="bob")
    assert [a["username"] for a in by_name["agents"]] == [BOB]


def test_find_requires_can_see_org_agents(fx):
    fx.client.set_agent_card(["comptabilite"], ALICE, ALICE_PASSWORD)
    # alice lacks can_see_org_agents (default false) -> ACCESS_DENIED
    with pytest.raises(ApiClientError) as exc:
        fx.client.find_agents(ALICE, ALICE_PASSWORD, capability="comptabilite")
    assert exc.value.code == ACCESS_DENIED


def test_find_only_own_organization(fx):
    from synapse.install import create_organization

    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("carol", "motdepasse-carol-1", "desc", ORG2_NAME, ORG2_PASSWORD)
    fx.client.set_agent_card(["comptabilite"], ALICE, ALICE_PASSWORD)
    fx.client.set_agent_card(["comptabilite"], BOB, BOB_PASSWORD)
    # carol has the same capability but belongs to org2
    fx.client.set_agent_card(["comptabilite"], "carol", "motdepasse-carol-1")
    _give_visibility(fx, ALICE, ALICE_PASSWORD)
    result = fx.client.find_agents(ALICE, ALICE_PASSWORD, capability="comptabilite")
    usernames = [a["username"] for a in result["agents"]]
    assert usernames == [ALICE, BOB]  # carol (other org) absent


def test_find_excludes_disabled_and_cardless(fx):
    _give_visibility(fx, ALICE, ALICE_PASSWORD)
    fx.client.set_agent_card(["comptabilite"], ALICE, ALICE_PASSWORD)
    # bob: no card -> excluded; carol disabled with a card -> excluded
    fx.create_agent("carol", "motdepasse-carol-1")
    fx.client.set_agent_card(["comptabilite"], "carol", "motdepasse-carol-1")
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    result = fx.client.find_agents(ALICE, ALICE_PASSWORD, capability="comptabilite")
    assert [a["username"] for a in result["agents"]] == [ALICE]


def test_find_pagination(fx):
    _give_visibility(fx, ALICE, ALICE_PASSWORD)
    fx.create_agent("dana", "motdepasse-dana-1")
    fx.create_agent("eve", "motdepasse-eve-1")
    for name in (ALICE, BOB, "dana", "eve"):
        fx.client.set_agent_card(["capacite-commune"], name, _password_for(name))
    page1 = fx.client.find_agents(ALICE, ALICE_PASSWORD, capability="commune", limit=2)
    assert len(page1["agents"]) == 2
    assert page1["next_cursor"] is not None
    page2 = fx.client.find_agents(
        ALICE, ALICE_PASSWORD, capability="commune", limit=2, cursor=page1["next_cursor"]
    )
    all_names = [a["username"] for a in page1["agents"]] + [
        a["username"] for a in page2["agents"]
    ]
    assert sorted(all_names) == sorted([ALICE, BOB, "dana", "eve"])
    assert page2["next_cursor"] is None


def _password_for(name: str) -> str:
    return {
        "alice": ALICE_PASSWORD,
        "bob": BOB_PASSWORD,
        "dana": "motdepasse-dana-1",
        "eve": "motdepasse-eve-1",
    }[name]


def test_find_validation(fx):
    _give_visibility(fx, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.find_agents(ALICE, ALICE_PASSWORD, capability="x" * 129)
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.find_agents(ALICE, ALICE_PASSWORD, limit=101)
    assert exc.value.code == INVALID_ARGUMENT
