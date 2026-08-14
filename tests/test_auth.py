"""Authentication tests: credentials, disabled accounts, rate
limiting (sections 3 and 4 of the specification)."""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD

AUTH_FAILED = "AUTH_FAILED"


def test_authentication_success(fx):
    data = fx.client.get_messages(ALICE, ALICE_PASSWORD)
    assert data == {"messages": [], "next_cursor": None}


def test_authentication_wrong_password(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(ALICE, "mauvais-mot-de-passe")
    assert exc.value.code == AUTH_FAILED


def test_authentication_unknown_user(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages("ghost", "nimporte-quel-mot-de-passe")
    assert exc.value.code == AUTH_FAILED


def test_authentication_case_insensitive_username(fx):
    data = fx.client.get_messages("ALICE", ALICE_PASSWORD)
    assert data == {"messages": [], "next_cursor": None}


def test_authentication_no_business_data_on_failure(fx):
    """A failed authentication must not read or modify anything."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "message secret", "cmid-auth-1")
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(BOB, "mauvais")
    assert exc.value.code == AUTH_FAILED
    # no business data leaked through the error message
    assert "secret" not in exc.value.message
    # the state is intact
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert len(inbox["messages"]) == 1


def test_disabled_account_cannot_authenticate(fx):
    fx.client.deactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(ALICE, ALICE_PASSWORD)
    assert exc.value.code == AUTH_FAILED
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message(BOB, "hello", "cmid-dis-1", ALICE, ALICE_PASSWORD)
    assert exc.value.code == AUTH_FAILED


def test_disabled_account_reactivation_restores_access(fx):
    fx.client.deactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError):
        fx.client.get_messages(ALICE, ALICE_PASSWORD)
    fx.client.reactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD)
    data = fx.client.get_messages(ALICE, ALICE_PASSWORD)
    assert data == {"messages": [], "next_cursor": None}


def test_disabled_account_data_preserved(fx):
    """A disabled account's data is preserved along with its statuses."""
    sent = fx.send(ALICE, ALICE_PASSWORD, BOB, "conserve-moi", "cmid-pres-1")
    fx.client.read_message(sent["message_id"], BOB, BOB_PASSWORD)
    fx.client.deactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD)
    fx.client.reactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD)
    conv = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    assert len(conv["messages"]) == 1
    assert conv["messages"][0]["content"] == "conserve-moi"
    assert conv["messages"][0]["status"] == "read"  # status unchanged


def test_org_authentication_required(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol",  "motdepasse-carol-1", "Agent de test",  ORG_NAME, "mauvais-motdepasse")
    assert exc.value.code == AUTH_FAILED


def test_rate_limit_blocks_after_five_failures(fx):
    """5 failures allowed, the 6th attempt is rejected."""
    for i in range(5):
        with pytest.raises(ApiClientError) as exc:
            fx.client.get_messages(ALICE, f"mauvais-{i}")
        assert exc.value.code == AUTH_FAILED
    # 6th attempt: refused (still AUTH_FAILED)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(ALICE, ALICE_PASSWORD)  # even the correct password
    assert exc.value.code == AUTH_FAILED


def test_rate_limit_window_is_per_username(fx):
    """Rate limiting is per username."""
    for i in range(5):
        with pytest.raises(ApiClientError):
            fx.client.get_messages(ALICE, f"mauvais-{i}")
    # bob is not affected by alice's failures
    data = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert data == {"messages": [], "next_cursor": None}


def test_successful_auth_resets_failure_counter(config):
    # cache disabled (TTL 0): the test isolates the rate-limit mechanism from
    # the authentication cache (SPEC.txt §19.1 amendment)
    from dataclasses import replace

    from .conftest import make_server

    cfg = replace(config, auth_cache_ttl_seconds=0.0)
    server = make_server(cfg, org=True)
    try:
        server.create_agent(ALICE, ALICE_PASSWORD)
        for i in range(4):
            with pytest.raises(ApiClientError):
                server.client.get_messages(ALICE, f"mauvais-{i}")
        # a successful authentication resets the counter to zero
        server.client.get_messages(ALICE, ALICE_PASSWORD)
        with pytest.raises(ApiClientError) as exc:
            server.client.get_messages(ALICE, "encore-mauvais")
        # 1st failure after reset: accepted (not blocked)
        assert exc.value.code == AUTH_FAILED
    finally:
        server.stop()


def test_rate_limit_unknown_usernames_also_counted(fx):
    for i in range(5):
        with pytest.raises(ApiClientError):
            fx.client.get_messages("ghost", f"mauvais-{i}")
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages("ghost", "nimporte")
    assert exc.value.code == AUTH_FAILED


def test_rate_limit_does_not_block_other_users(fx):
    for i in range(5):
        with pytest.raises(ApiClientError):
            fx.client.get_messages("ghost", f"mauvais-{i}")
    data = fx.client.get_messages(ALICE, ALICE_PASSWORD)
    assert data == {"messages": [], "next_cursor": None}


def test_org_auth_agent_name_runs_dummy_verification(fx, monkeypatch):
    """An agent name used as an organization identity must go through the
    dummy Argon2id verification (constant timing) before the
    ACCESS_DENIED — otherwise the agent account existence is revealed
    by a fast response (anti-enumeration, section 3.3)."""
    import synapse.service as service_mod
    from synapse.db import connect
    from synapse.errors import ACCESS_DENIED as ACCESS_DENIED_CODE, ApiError

    calls = []

    def _counting_dummy(_password):
        calls.append(True)

    monkeypatch.setattr(service_mod, "verify_dummy", _counting_dummy)
    with connect(fx.config) as conn:
        with pytest.raises(ApiError) as exc:
            fx.server.service._authenticate_organization(
                conn, ALICE, "nimporte-quel-mot-de-passe"
            )
    assert exc.value.code == ACCESS_DENIED_CODE
    assert calls, "verify_dummy must be called on the agent-name path"
