"""F1 — Authentication verification cache.

Verifies that the cache temporarily remembers successful authentications
(a single Argon2id verification for close-in-time commands), that failures
are never cached, that password rotation invalidates the entry, that the
TTL expires, and that agent/organization namespaces remain separate.
"""

from __future__ import annotations

import dataclasses
import time

import pytest

import synapse.service as service_module

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


def _install_spy(monkeypatch) -> list:
    """Counts calls to Argon2id verification (the hasher stays fast)."""
    calls: list = []
    real = service_module.verify_password

    def spy(password_hash: str, password: str) -> bool:
        calls.append(1)
        return real(password_hash, password)

    monkeypatch.setattr(service_module, "verify_password", spy)
    return calls


def test_cache_avoids_repeated_verification(fx, monkeypatch):
    calls = _install_spy(monkeypatch)
    for _ in range(3):
        assert fx.client.get_my_organization(ALICE, ALICE_PASSWORD)["organization_name"] == ORG_NAME
    assert len(calls) == 1  # a single verification for 3 close-in-time commands


def test_cache_invalidated_after_password_change(fx, monkeypatch):
    # warms the organization cache (change_agent_password's org auth must be
    # served by the cache, not by a counted verification)
    fx.client.get_organization_policy(ORG_NAME, ORG_PASSWORD)
    calls = _install_spy(monkeypatch)
    fx.client.get_my_organization(ALICE, ALICE_PASSWORD)
    assert len(calls) == 1
    # password rotation by the organization: the hash changes in the DB
    fx.client.change_agent_password(ALICE, "nouveau-motdepasse-alice-2", ORG_NAME, ORG_PASSWORD)
    fx.client.get_my_organization(ALICE, "nouveau-motdepasse-alice-2")
    assert len(calls) == 2  # the new hash does not match the cached entry


def test_failures_never_cached(fx, monkeypatch):
    calls = _install_spy(monkeypatch)
    for _ in range(2):
        with pytest.raises(Exception):
            fx.client.get_my_organization(ALICE, "mauvais-mot-de-passe-1")
    assert len(calls) == 2  # each failure pays the verification


def test_cache_ttl_expiry(fx, monkeypatch):
    cfg = dataclasses.replace(fx.config, auth_cache_ttl_seconds=0.05)
    fx.server.stop()
    server = make_server(cfg, org=False)
    try:
        calls = _install_spy(monkeypatch)
        server.client.get_my_organization(ALICE, ALICE_PASSWORD)
        server.client.get_my_organization(ALICE, ALICE_PASSWORD)
        assert len(calls) == 1  # within the TTL window
        time.sleep(0.12)
        server.client.get_my_organization(ALICE, ALICE_PASSWORD)
        assert len(calls) == 2  # outside the window: re-verification
    finally:
        server.stop()


def test_agent_and_org_namespaces_separate(fx, monkeypatch):
    """An agent's key with the same name as an organization does not share the cache.

    Note: the fixture already authenticates ``root_org`` (agent creation) —
    that organization's cache is therefore warm. A virgin organization
    (never authenticated) is used to measure the separation.
    """
    from synapse.install import create_organization

    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    calls = _install_spy(monkeypatch)
    fx.client.get_my_organization(ALICE, ALICE_PASSWORD)  # agent: key "alice"
    fx.client.get_organization_policy(ORG2_NAME, ORG2_PASSWORD)  # org: key "org:second_org"
    fx.client.get_organization_policy(ORG2_NAME, ORG2_PASSWORD)  # org cache
    fx.client.get_my_organization(ALICE, ALICE_PASSWORD)  # agent cache
    assert len(calls) == 2  # 1 (agent) + 1 (virgin org), then cache for each


def test_two_agents_have_separate_entries(fx, monkeypatch):
    calls = _install_spy(monkeypatch)
    fx.client.get_my_organization(ALICE, ALICE_PASSWORD)
    fx.client.get_my_organization(BOB, BOB_PASSWORD)
    fx.client.get_my_organization(ALICE, ALICE_PASSWORD)
    fx.client.get_my_organization(BOB, BOB_PASSWORD)
    assert len(calls) == 2  # one verification per agent, then cache for each
