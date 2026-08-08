"""Tests for the agent description and the get_agent_description command
(SPEC.txt section 3): validation, permissions, normalization, persistence,
backups, logs, and CLI."""

from __future__ import annotations

import json
import os

import pytest

from synapse.client import ApiClientError
from synapse.errors import INVALID_ARGUMENT, USER_NOT_FOUND

from .conftest import (
    ORG_NAME,
    ORG_PASSWORD,
    ALICE,
    ALICE_DESCRIPTION,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    make_server,
)

DESC_CAROL = "Agent carol: code review specialist"
DESC_MARKER = "DescriptionUniqueNonJournalisableXYZ"


# ---------------------------------------------------------------------------
# Creation: the description is mandatory and normalized
# ---------------------------------------------------------------------------


def test_create_agent_requires_description(fx):
    """A missing description is rejected (mandatory parameter)."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("create_agent", {
            "username": "carol",
            "password": "motdepasse-carol-1",
            "organization_name_auth": ORG_NAME,
            "organization_password_auth": ORG_PASSWORD,
        })
    assert exc.value.code == INVALID_ARGUMENT


def test_create_agent_normalizes_description(fx):
    """The description is normalized (trim + NFC) before storage."""
    composed = "Agent composé : ééé"  # already NFC
    decomposed = "Agent composé : e\u0301e\u0301e\u0301"  # NFD -> NFC
    data = fx.client.create_agent("carol", "motdepasse-carol-1", decomposed, ORG_NAME, ORG_PASSWORD)
    assert data["description"] == composed
    desc = fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
    assert desc["description"] == composed
    # trim also applies
    data = fx.client.create_agent("dave", "motdepasse-dave-1", "  agent dave  ", ORG_NAME, ORG_PASSWORD)
    assert data["description"] == "agent dave"


def test_create_agent_rejects_bad_descriptions(fx):
    """Empty, too long, or control-character description: rejected."""
    for bad in ("   ", "x" * 501, "ligne\ncoupée", "\t"):
        with pytest.raises(ApiClientError) as exc:
            fx.client.create_agent("dave", "motdepasse-dave-1", bad, ORG_NAME, ORG_PASSWORD)
        assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("dave", "motdepasse-dave-1", 42, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT
    # none of these creations succeeded
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_description("dave", ALICE, ALICE_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_description_length_boundaries(fx):
    """Exact bound: 500 code points accepted, 501 rejected."""
    data = fx.client.create_agent("carol", "motdepasse-carol-1", "c" * 500, ORG_NAME, ORG_PASSWORD)
    assert data["description"] == "c" * 500
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("dave", "motdepasse-dave-1", "c" * 501, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# get_agent_description: public read
# ---------------------------------------------------------------------------


def test_get_agent_description_success(fx):
    fx.client.create_agent("carol", "motdepasse-carol-1", DESC_CAROL, ORG_NAME, ORG_PASSWORD)
    desc = fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
    assert desc == {"username": "carol", "organization_name": ORG_NAME, "description": DESC_CAROL}


def test_get_agent_description_normalizes_username(fx):
    fx.client.create_agent("carol", "motdepasse-carol-1", DESC_CAROL, ORG_NAME, ORG_PASSWORD)
    desc = fx.client.get_agent_description("CAROL", ALICE, ALICE_PASSWORD)
    assert desc["username"] == "carol"  # name normalized in the response


def test_get_agent_description_unknown_user(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_description("ghost", ALICE, ALICE_PASSWORD)
    assert exc.value.code == USER_NOT_FOUND


def test_agent_organization_is_public_directory_metadata(fx):
    """An agent's description and organization are public: alice sees bob's
    organization (directory metadata)."""
    desc = fx.client.get_agent_description(BOB, ALICE, ALICE_PASSWORD)
    assert desc == {"username": BOB, "organization_name": ORG_NAME, "description": "Test agent bob: sends and receives messages"}


def test_get_agent_description_self_lookup(fx):
    desc = fx.client.get_agent_description(ALICE, ALICE, ALICE_PASSWORD)
    assert desc["description"] == "Test agent alice: sends and receives messages"


def test_get_agent_description_disabled_account_still_visible(fx):
    """A disabled account's description remains public metadata."""
    fx.client.create_agent("carol", "motdepasse-carol-1", DESC_CAROL, ORG_NAME, ORG_PASSWORD)
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    desc = fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
    assert desc == {"username": "carol", "organization_name": ORG_NAME, "description": DESC_CAROL}


def test_get_agent_description_read_only(fx):
    """The command modifies nothing: two calls give the same result and no
    state is changed."""
    fx.client.create_agent("carol", "motdepasse-carol-1", DESC_CAROL, ORG_NAME, ORG_PASSWORD)
    before = fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
    again = fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
    assert before == again
    # alice's notifications are unchanged (no side effects)
    assert fx.client.get_notifications(ALICE, ALICE_PASSWORD)["needs_reply"] == []


def test_get_agent_description_auth_failures(fx):
    """Invalid credentials or unknown account: AUTH_FAILED."""
    from synapse.errors import AUTH_FAILED
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_description(ALICE, ALICE, "mauvais-motdepasse")
    assert exc.value.code == AUTH_FAILED
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_description(ALICE, "ghost", "nimporte-quel-motdepasse")
    assert exc.value.code == AUTH_FAILED


def test_get_agent_description_disabled_caller_denied(fx):
    from synapse.errors import AUTH_FAILED
    fx.client.create_agent("carol", "motdepasse-carol-1", DESC_CAROL, ORG_NAME, ORG_PASSWORD)
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_description(ALICE, "carol", "motdepasse-carol-1")
    assert exc.value.code == AUTH_FAILED


def test_get_agent_description_invalid_username(fx):
    """A malformed name is rejected by validation."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_description("bad name!", ALICE, ALICE_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_get_agent_description_never_logged(fx):
    """The description never appears in the logs."""
    fx.client.create_agent("carol", "motdepasse-carol-1", DESC_MARKER, ORG_NAME, ORG_PASSWORD)
    fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
    logs = []
    for name in os.listdir(fx.config.log_dir):
        path = os.path.join(fx.config.log_dir, name)
        if os.path.isfile(path):
            logs.append(open(path, encoding="utf-8").read())
    joined = "\n".join(logs)
    assert DESC_MARKER not in joined
    assert "motdepasse-carol-1" not in joined


# ---------------------------------------------------------------------------
# Immutability and lifecycle
# ---------------------------------------------------------------------------


def test_description_immutable_across_lifecycle(fx):
    """No command modifies the description (password, deactivation,
    reactivation)."""
    fx.client.create_agent("carol", "motdepasse-carol-1", DESC_CAROL, ORG_NAME, ORG_PASSWORD)
    fx.client.change_agent_password("carol", "nouveau-motdepasse-carol", ORG_NAME, ORG_PASSWORD)
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    fx.client.reactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    desc = fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
    assert desc["description"] == DESC_CAROL


def test_description_persists_across_restart(config):
    """The description survives a server restart."""
    server = make_server(config, org=True)
    try:
        server.client.create_agent("carol", "motdepasse-carol-1", DESC_CAROL, ORG_NAME, ORG_PASSWORD)
        server.client.create_agent(ALICE, ALICE_PASSWORD, "Agent alice", ORG_NAME, ORG_PASSWORD)
    finally:
        server.stop()
    server2 = make_server(config, org=False)
    try:
        desc = server2.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
        assert desc["description"] == DESC_CAROL
    finally:
        server2.stop()


def test_description_preserved_by_backup_restore(fx, config):
    """Backup and restore preserve the descriptions."""
    from synapse.backup import backup, restore
    fx.client.create_agent("carol", "motdepasse-carol-1", DESC_CAROL, ORG_NAME, ORG_PASSWORD)
    path = backup(config)
    fx.server.stop()
    restore(config, path)
    server = make_server(config, org=False)
    try:
        desc = server.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)
        assert desc["description"] == DESC_CAROL
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_creates_with_descriptions(config):
    """Concurrent creations with distinct descriptions stay consistent
    (each account keeps its own)."""
    import threading

    server = make_server(config, org=True)
    try:
        server.client.create_agent(ALICE, ALICE_PASSWORD, "Agent alice", ORG_NAME, ORG_PASSWORD)
        errors = []

        def create(i):
            try:
                server.client.create_agent(
                    f"agent{i:02d}", f"motdepasse-agent{i}-1", f"Description of agent {i}",
                    ORG_NAME, ORG_PASSWORD,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        for i in range(8):
            desc = server.client.get_agent_description(f"agent{i:02d}", ALICE, ALICE_PASSWORD)
            assert desc["description"] == f"Description of agent {i}"
    finally:
        server.stop()

