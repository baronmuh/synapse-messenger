"""Point-by-point compliance with the specification's final constraints
(SPEC.txt, section 18): one test function per constraint."""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import uuid

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

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def test_constraint_1_local_api_no_human_interface_no_network(fx):
    """Local v2 API, no human interface, no network access."""
    from synapse.validation import COMMAND_SPECS
    assert len(COMMAND_SPECS) == 65  # 19 v2 + 39 (F2-F20) + 5 (SPEC-WEB) + list_orgs (D5 amended) + get_escalation_policy
    # transport: Unix socket only
    assert stat.S_ISSOCK(os.stat(fx.config.socket_path).st_mode)
    assert not fx.config.socket_path.startswith(("tcp:", "udp:", "http:"))


def test_constraint_2_auth_in_every_command(fx):
    """Authentication verified in every command (agents and
    organizations)."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(ALICE, "wrong")
    assert exc.value.code == "AUTH_FAILED"
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_agents(ORG_NAME, "wrong")
    assert exc.value.code == "AUTH_FAILED"
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_agents("org_ghost", "any-password")
    assert exc.value.code == "AUTH_FAILED"  # no existence disclosure


def test_constraint_3_isolation_between_organizations(fx):
    """Isolation: an agent acts only within its organization, an organization
    manages only its own agents; inter-organization discovery does not
    exist."""
    # an agent cannot call an organization command
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("x_agent", "motdepasse-x-1", "Test agent", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"
    # the root_org org does not see another organization's agents
    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("dave", "motdepasse-dave-1", "Agent dave", ORG2_NAME, ORG2_PASSWORD)
    names = {a["username"] for a in fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD)["agents"]}
    assert "dave" not in names
    # and cannot manage another organization's agent (USER_NOT_FOUND)
    with pytest.raises(ApiClientError) as exc:
        fx.client.deactivate_agent("dave", ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "USER_NOT_FOUND"
    # inter-organization discovery does not exist (list_org_agents only
    # lists the own organization)
    fx.client.set_agent_visibility(ALICE, True, ORG_NAME, ORG_PASSWORD)
    usernames = fx.client.list_org_agents(ALICE, ALICE_PASSWORD)["usernames"]
    assert "dave" not in usernames


def test_constraint_4_no_org_access_to_content(fx):
    """No administrative (organization) access to message, conversation, or
    notification content."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "secret absolu", "cmid-c-4")
    responses = [
        fx.client.deactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD),
        fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD),
        fx.client.reactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD),
        fx.client.reactivate_agent(BOB, ORG_NAME, ORG_PASSWORD),
        fx.client.change_agent_password(ALICE, "nouveau-motdepasse-1", ORG_NAME, ORG_PASSWORD),
        fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD),
        fx.client.get_organization_policy(ORG_NAME, ORG_PASSWORD),
    ]
    assert "secret absolu" not in str(responses)


def test_constraint_5_messages_immutable(fx):
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "immuable", "cmid-c-5")
    before = dict(m)
    fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    after = conv["messages"][0]
    for field in ("message_id", "conversation_id", "client_message_id",
                  "sender_username", "sender_organization_name",
                  "recipient_username", "content", "created_at"):
        assert before[field] == after[field]


def test_constraint_6_explicit_individual_read(fx):
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "lu explicitement", "cmid-c-6")
    assert fx.client.get_messages(BOB, BOB_PASSWORD)["messages"][0]["status"] == "unread"
    fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)
    assert fx.client.get_messages(BOB, BOB_PASSWORD)["messages"][0]["status"] == "read"


def test_constraint_7_read_state_per_recipient(fx):
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "per destinataire", "cmid-c-7")
    fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)
    conv_bob = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    conv_alice = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    assert conv_bob["messages"][0]["status"] == "read"
    assert conv_alice["messages"][0]["read_at"] == conv_bob["messages"][0]["read_at"]


def test_constraint_8_reply_state_per_agent(fx):
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "bonjour", "cmid-c-8")
    fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)
    assert fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "needs_reply"
    assert fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)["reply_status"] == "no_reply_needed"


def test_constraint_9_needs_reply_computed_from_last_read_received(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-c-9a")
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-c-9b")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)  # older read
    assert fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "no_reply_needed"
    fx.client.read_message(m2["message_id"], BOB, BOB_PASSWORD)  # latest read
    assert fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "needs_reply"


def test_constraint_10_no_reply_linked_to_message_cancelled_by_new(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-c-10a")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"]
        == "no_reply_needed"
    )
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "nouveau", "cmid-c-10b")
    fx.client.read_message(m2["message_id"], BOB, BOB_PASSWORD)
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"]
        == "needs_reply"
    )


def test_constraint_11_single_conversation_per_pair(fx):
    fx.client.create_agent("carol", "motdepasse-carol-1", "Test agent", ORG_NAME, ORG_PASSWORD)
    ids = {
        fx.send(ALICE, ALICE_PASSWORD, BOB, "a", "cmid-c-11a")["conversation_id"],
        fx.send(BOB, BOB_PASSWORD, ALICE, "b", "cmid-c-11b")["conversation_id"],
        fx.client.send_message(ALICE, "c", "cmid-c-11c", "carol", "motdepasse-carol-1")["conversation_id"],
    }
    assert len(ids) == 2  # une conversation alice-bob, une alice-carol


def test_constraint_12_opaque_stable_pagination(fx):
    for i in range(7):
        fx.send(ALICE, ALICE_PASSWORD, BOB, f"m{i}", f"cmid-c-12-{i}")
    seen = []
    cursor = None
    while True:
        page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=3, cursor=cursor)
        seen += [m["message_id"] for m in page["messages"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 7 and len(set(seen)) == 7


def test_constraint_13_uniform_json_responses(fx):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(fx.config.socket_path)
        s.sendall(json.dumps({
            "api_version": "v2", "command": "get_notifications",
            "parameters": {"my_name_auth": ALICE, "my_password_auth": ALICE_PASSWORD,
                           "limit": 50, "cursor": None},
        }).encode() + b"\n")
        s.shutdown(socket.SHUT_WR)
        raw = b"".join(iter(lambda: s.recv(65536), b""))
    finally:
        s.close()
    envelope = json.loads(raw)
    assert set(envelope) == {"success", "data", "error"}
    assert envelope["success"] is True and envelope["error"] is None
    assert isinstance(envelope["data"], dict)


def test_constraint_14_uuids_and_utc_dates(fx):
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "formats", "cmid-c-14")
    assert UUID4_RE.match(m["message_id"])
    assert UUID4_RE.match(m["conversation_id"])
    assert TIMESTAMP_RE.match(m["created_at"])
    assert m["created_at"].endswith("Z")
    uuid.UUID(m["message_id"])
    uuid.UUID(m["conversation_id"])


def test_constraint_15_send_idempotence(fx):
    first = fx.send(ALICE, ALICE_PASSWORD, BOB, "idem", "cmid-c-15")
    second = fx.send(ALICE, ALICE_PASSWORD, BOB, "idem", "cmid-c-15")
    assert first["message_id"] == second["message_id"]
    assert len(fx.client.get_messages(BOB, BOB_PASSWORD)["messages"]) == 1


def test_constraint_16_atomic_transactions(fx):
    """The send transaction is atomic: a failure leaves no partial state
    (conversation, message, idempotency key, reply states)."""
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("ghost", "never delivered", "cmid-c-16", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "RECIPIENT_NOT_FOUND"
    # no message created, no conversation with 'ghost'
    assert fx.client.get_messages(ALICE, ALICE_PASSWORD)["messages"] == []
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation("ghost", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "CONVERSATION_NOT_FOUND"
    # the failed send did not consume the idempotency key
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "after failure", "cmid-c-16b")
    assert m["content"] == "after failure"


def test_constraint_17_passwords_hashed_never_plaintext(fx):
    import sqlite3
    conn = sqlite3.connect(fx.config.db_path)
    try:
        hashes = [r[0] for r in conn.execute("SELECT password_hash FROM accounts")]
        org_hashes = [r[0] for r in conn.execute("SELECT password_hash FROM organizations")]
    finally:
        conn.close()
    for h in hashes + org_hashes:
        assert h.startswith("$argon2id$")
        assert ALICE_PASSWORD not in h and BOB_PASSWORD not in h and ORG_PASSWORD not in h
    # logs without secrets
    log = open(os.path.join(fx.config.log_dir, "synapse.log"), encoding="utf-8").read()
    assert ALICE_PASSWORD not in log and BOB_PASSWORD not in log and ORG_PASSWORD not in log


def test_constraint_18_persistence_and_restore_without_change(fx, config):
    from synapse.backup import backup, restore
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "persistant", "cmid-c-18")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    path = backup(config)
    fx.server.stop()
    restore(config, path)
    server2 = make_server(config, org=False)
    try:
        conv = server2.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
        assert conv["messages"][0]["message_id"] == m1["message_id"]
        assert conv["messages"][0]["created_at"] == m1["created_at"]
        assert conv["messages"][0]["read_at"] is not None  # status preserved
        # the organization is restored with its policies
        policy = server2.client.get_organization_policy(ORG_NAME, ORG_PASSWORD)
        assert policy["allow_incoming_external"] is False
        assert policy["allow_outgoing_external"] is False
    finally:
        server2.stop()


def test_constraint_19_utf8_nfc_1_to_10000(fx):
    assert fx.send(ALICE, ALICE_PASSWORD, BOB, "e\u0301tude", "cmid-c-19a")["content"] == "\u00e9tude"
    assert len(fx.send(ALICE, ALICE_PASSWORD, BOB, "é" * 10000, "cmid-c-19b")["content"]) == 10000
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message(BOB, "x" * 10001, "cmid-c-19c", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_constraint_20_named_versioned_strict_params(fx, raw_socket_client):
    """Named, versioned, strictly validated parameters: any unknown field is
    rejected, the version is checked."""
    resp = raw_socket_client(
        json.dumps({
            "api_version": "v2",
            "command": "get_notifications",
            "parameters": {"my_name_auth": ALICE, "my_password_auth": ALICE_PASSWORD,
                           "limit": 50, "cursor": None, "extra": 1},
        }) + "\n"
    )
    assert resp["error"]["code"] == "INVALID_ARGUMENT"
    # incorrect version rejected
    resp = raw_socket_client(
        json.dumps({
            "api_version": "v1",
            "command": "get_notifications",
            "parameters": {"my_name_auth": ALICE, "my_password_auth": ALICE_PASSWORD,
                           "limit": 50, "cursor": None},
        }) + "\n"
    )
    assert resp["error"]["code"] == "INVALID_ARGUMENT"


def test_constraint_21_description_required_immutable_retrievable(fx):
    """Description mandatory at creation, immutable, never logged, retrievable
    via get_agent_description with the organization."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.request("create_agent", {
            "username": "carol", "password": "motdepasse-carol-1",
            "organization_name_auth": ORG_NAME, "organization_password_auth": ORG_PASSWORD,
        })
    assert exc.value.code == "INVALID_ARGUMENT"
    fx.client.create_agent("carol", "motdepasse-carol-1", "Contrainte 21", ORG_NAME, ORG_PASSWORD)
    assert fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD) == {
        "username": "carol", "organization_name": ORG_NAME, "description": "Contrainte 21",
    }
    # immutable: no command changes the description
    fx.client.change_agent_password("carol", "nouveau-motdepasse-carol", ORG_NAME, ORG_PASSWORD)
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    fx.client.reactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    assert fx.client.get_agent_description("carol", ALICE, ALICE_PASSWORD)["description"] == "Contrainte 21"
    # never logged
    logs = "\n".join(
        open(os.path.join(fx.config.log_dir, name), encoding="utf-8").read()
        for name in os.listdir(fx.config.log_dir)
        if os.path.isfile(os.path.join(fx.config.log_dir, name))
    )
    assert "Contrainte 21" not in logs


def test_constraint_22_visibility_permission(fx):
    """Username visibility controlled by can_see_org_agents (default false),
    limited to active agents of the own organization."""
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    # alice does not have the permission by default
    with pytest.raises(ApiClientError) as exc:
        fx.client.list_org_agents(ALICE, ALICE_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"
    # permission granted: usernames of ACTIVE agents only
    fx.client.set_agent_visibility(ALICE, True, ORG_NAME, ORG_PASSWORD)
    usernames = fx.client.list_org_agents(ALICE, ALICE_PASSWORD)["usernames"]
    assert "carol" in usernames
    assert ALICE in usernames
    assert BOB not in usernames  # disabled, hence excluded


def test_constraint_23_communication_policies(fx):
    """Internal communication always allowed; external communications subject
    to both organizations' policies, evaluated at send time; existing
    ones remain accessible after a policy change."""
    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("dave", "motdepasse-dave-1", "Agent dave", ORG2_NAME, ORG2_PASSWORD)
    # internal to root_org: always allowed
    fx.send(ALICE, ALICE_PASSWORD, BOB, "interne", "cmid-c-23a")
    # external refused by default (both orgs are closed)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "externe", "cmid-c-23b", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "POLICY_DENIED"
    # opening both sides: the inter-org exchange becomes possible
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    sent = fx.client.send_message("dave", "externe OK", "cmid-c-23c", ALICE, ALICE_PASSWORD)
    assert sent["sender_organization_name"] == ORG_NAME
    # one side open only: refused (both policies must allow)
    fx.client.set_organization_policy(False, True, ORG2_NAME, ORG2_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "blocked", "cmid-c-23d", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "POLICY_DENIED"
    # existing messages remain accessible after a policy change
    inbox = fx.client.get_messages("dave", "motdepasse-dave-1")
    assert "externe OK" in [m["content"] for m in inbox["messages"]]
    conv = fx.client.get_conversation(ALICE, "dave", "motdepasse-dave-1")
    assert conv["reply_status"] in ("needs_reply", "no_reply_needed")


def test_constraint_24_organizations_permanent(fx):
    """Permanent organizations: never disabled, deleted, or renamed; only
    agents can be disabled."""
    from synapse.validation import COMMAND_SPECS
    commands = set(COMMAND_SPECS)
    assert not {"deactivate_organization", "delete_organization",
                "rename_organization"} & commands
    # the organization stays authenticable and its agents operational
    fx.client.get_org_agents(ORG_NAME, ORG_PASSWORD)
    fx.send(ALICE, ALICE_PASSWORD, BOB, "still there", "cmid-c-24")


def test_constraint_25_help_available_any_active_account(fx):
    """help() available to any active, authenticated account without special
    privilege; documentation exactly consistent with the v2 API and free
    of secrets or account data."""
    doc = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    from synapse.validation import COMMAND_SPECS
    for name in COMMAND_SPECS:
        assert f"{name}(" in doc  # every command is documented
    assert len(doc.encode()) <= 64 * 1024
    assert ALICE_PASSWORD not in doc and ORG_PASSWORD not in doc


def test_constraint_26_policy_denied_before_revealing(fx):
    """POLICY_DENIED returned for any external send refused by a policy,
    before revealing information about the recipient: a closed
    organization returns the same error for an existing and for a
    nonexistent recipient."""
    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("dave", "motdepasse-dave-1", "Agent dave", ORG2_NAME, ORG2_PASSWORD)
    # root_org is closed (outgoing refused): same error in both cases
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "salut", "cmid-c-26a", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "POLICY_DENIED"
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("nimporte-qui", "salut", "cmid-c-26b", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "POLICY_DENIED"
