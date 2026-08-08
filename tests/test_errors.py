"""Uniform response/error tests (section 5) and message immutability."""

from __future__ import annotations

import json

import pytest

from synapse.client import ApiClientError

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD

ALL_ERROR_CODES = {
    "AUTH_FAILED",
    "ACCESS_DENIED",
    "INVALID_ARGUMENT",
    "UNKNOWN_COMMAND",
    "USERNAME_ALREADY_EXISTS",
    "USER_NOT_FOUND",
    "RECIPIENT_NOT_FOUND",
    "MESSAGE_NOT_FOUND",
    "CONVERSATION_NOT_FOUND",
    "MESSAGE_ALREADY_EXISTS",
    "POLICY_DENIED",
    "TASK_NOT_FOUND",
    "TASK_STATE_INVALID",
    "TASK_DEPENDENCY_NOT_MET",
    "QUOTA_EXCEEDED",
    "GROUP_NOT_FOUND",
    "INTERNAL_ERROR",
}


def test_success_envelope_shape(fx):
    response = fx.client.request("get_notifications", {
        "my_name_auth": ALICE, "my_password_auth": ALICE_PASSWORD,
        "limit": 50, "cursor": None,
    })
    # request() returns data; the envelope shape is checked via raw socket
    import socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(fx.config.socket_path)
        sock.sendall(
            json.dumps({
                "api_version": "v2",
                "command": "get_notifications",
                "parameters": {
                    "my_name_auth": ALICE,
                    "my_password_auth": ALICE_PASSWORD,
                    "limit": 50,
                    "cursor": None,
                },
            }).encode() + b"\n"
        )
        sock.shutdown(socket.SHUT_WR)
        raw = b"".join(iter(lambda: sock.recv(65536), b""))
    finally:
        sock.close()
    envelope = json.loads(raw)
    assert set(envelope.keys()) == {"success", "data", "error"}
    assert envelope["success"] is True
    assert envelope["error"] is None
    assert isinstance(envelope["data"], dict)


def test_error_envelope_shape(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(ALICE, "wrong")
    assert exc.value.code == "AUTH_FAILED"
    assert isinstance(exc.value.message, str)


def test_all_error_codes_reachable(fx, config):
    """Each of the specification's error codes is reachable."""
    import socket as socket_module

    def raw(payload: dict) -> dict:
        s = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        try:
            s.connect(fx.config.socket_path)
            s.sendall(json.dumps(payload).encode() + b"\n")
            s.shutdown(socket_module.SHUT_WR)
            return json.loads(b"".join(iter(lambda: s.recv(65536), b"")))
        finally:
            s.close()

    base = {"api_version": "v2", "parameters": {}}
    seen = set()

    seen.add(raw({**base, "command": "bogus_command"})["error"]["code"])  # UNKNOWN_COMMAND
    seen.add(raw({**base, "command": "get_notifications", "parameters": {"x": 1}})["error"]["code"])  # INVALID_ARGUMENT
    seen.add(raw({
        "api_version": "v2",
        "command": "get_messages",
        "parameters": {"my_name_auth": ALICE, "my_password_auth": "wrong",
                       "status": None, "sender_username": None, "conversation_id": None,
                       "limit": 50, "cursor": None},
    })["error"]["code"])  # AUTH_FAILED

    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent("carol",  "motdepasse-carol-1", "Agent de test",  ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # ACCESS_DENIED

    with pytest.raises(ApiClientError) as exc:
        fx.client.create_agent(ALICE,  "motdepasse-carol-1", "Agent de test",  ORG_NAME, ORG_PASSWORD)
    seen.add(exc.value.code)  # USERNAME_ALREADY_EXISTS

    with pytest.raises(ApiClientError) as exc:
        fx.client.deactivate_agent("ghost", ORG_NAME, ORG_PASSWORD)
    seen.add(exc.value.code)  # USER_NOT_FOUND

    # root_org opens its outgoing policy: a nonexistent recipient
    # becomes RECIPIENT_NOT_FOUND again (v1 semantics preserved)
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("ghost", "hello", "cmid-err-1", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # RECIPIENT_NOT_FOUND

    with pytest.raises(ApiClientError) as exc:
        fx.client.read_message("00000000-0000-4000-8000-000000000000", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # MESSAGE_NOT_FOUND

    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation("ghost", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # CONVERSATION_NOT_FOUND

    fx.send(ALICE, ALICE_PASSWORD, BOB, "premier", "cmid-err-2")
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message(BOB, "different content", "cmid-err-2", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # MESSAGE_ALREADY_EXISTS

    # POLICY_DENIED: external send refused by the policies (both test
    # organizations are closed by default)
    from .conftest import ORG2_NAME, ORG2_PASSWORD, create_organization
    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("dave", "motdepasse-dave-1", "Agent dave", ORG2_NAME, ORG2_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("dave", "hello", "cmid-err-policy", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # POLICY_DENIED

    # INTERNAL_ERROR: direct simulator via the service
    from synapse.service import Service
    service = Service(fx.config)
    response, _ = service.process(b"{broken json")
    assert response["error"]["code"] == "INVALID_ARGUMENT"  # not INTERNAL

    # --- v3 codes (SPEC.txt §19.4 amendment) ---

    # TASK_NOT_FOUND: nonexistent task (non-disclosure).
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_task("00000000-0000-4000-8000-000000000000", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # TASK_NOT_FOUND

    # TASK_STATE_INVALID: forbidden transition (completed is terminal).
    task = fx.client.create_task("Terminal task", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "in_progress", ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "completed", ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.update_task_state(task["task_id"], "in_progress", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # TASK_STATE_INVALID

    # TASK_DEPENDENCY_NOT_MET: moving to in_progress with an unfinished
    # dependency.
    dep = fx.client.create_task("Dependency", BOB, ALICE, ALICE_PASSWORD)
    child = fx.client.create_task(
        "Enfant", BOB, ALICE, ALICE_PASSWORD, depends_on=[dep["task_id"]])
    with pytest.raises(ApiClientError) as exc:
        fx.client.update_task_state(child["task_id"], "in_progress", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # TASK_DEPENDENCY_NOT_MET

    # QUOTA_EXCEEDED: active-task budget exceeded.
    fx.client.create_task("Occupies the quota", BOB, ALICE, ALICE_PASSWORD)
    fx.client.set_agent_budget(BOB, ORG_NAME, ORG_PASSWORD, max_active_tasks=1)
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task("Budget plein", BOB, ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # QUOTA_EXCEEDED
    fx.client.set_agent_budget(BOB, ORG_NAME, ORG_PASSWORD,
                               max_active_tasks=None, max_messages_per_hour=None)

    # GROUP_NOT_FOUND: group outside membership (non-disclosure).
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_group_messages(
            "00000000-0000-4000-8000-000000000000", ALICE, ALICE_PASSWORD)
    seen.add(exc.value.code)  # GROUP_NOT_FOUND

    assert seen == ALL_ERROR_CODES - {"INTERNAL_ERROR"}


def test_error_message_is_informative_only(fx):
    """error.message is informative; only error.code is authoritative."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(ALICE, "wrong")
    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.message  # non-empty, localizable


def test_messages_are_immutable(fx):
    """No command modifies or deletes a message."""
    sent = fx.send(ALICE, ALICE_PASSWORD, BOB, "immuable", "cmid-imm-1")
    snapshot = dict(sent)
    fx.client.read_message(sent["message_id"], BOB, BOB_PASSWORD)
    fx.client.get_messages(ALICE, ALICE_PASSWORD)
    fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    fx.client.get_notifications(ALICE, ALICE_PASSWORD)
    fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    fx.client.reactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    # only read_at/status (recipient-specific) can change
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    message = conv["messages"][0]
    assert message["message_id"] == snapshot["message_id"]
    assert message["conversation_id"] == snapshot["conversation_id"]
    assert message["client_message_id"] == snapshot["client_message_id"]
    assert message["sender_username"] == snapshot["sender_username"]
    assert message["recipient_username"] == snapshot["recipient_username"]
    assert message["content"] == snapshot["content"]
    assert message["created_at"] == snapshot["created_at"]
    assert message["status"] == "read"  # the only legal change
    assert message["read_at"] is not None


def test_no_deletion_command_exists(fx):
    """The API exposes no deletion: the command list is exact
    (65 commands — SPEC.txt F2/F3/F5-F20 + SPEC-WEB §20 + D5 amended)."""
    from synapse.validation import COMMAND_SPECS
    assert set(COMMAND_SPECS.keys()) == {
        "create_agent",
        "deactivate_agent",
        "reactivate_agent",
        "change_agent_password",
        "set_agent_visibility",
        "get_org_agents",
        "set_organization_policy",
        "get_organization_policy",
        "change_organization_password",
        "get_my_organization",
        "get_agent_description",
        "list_org_agents",
        "send_message",
        "get_messages",
        "get_conversation",
        "read_message",
        "get_notifications",
        "mark_conversation_no_reply",
        "help",
        "set_agent_card",
        "get_agent_card",
        "approve_agent_card",
        "find_agents",
        "create_task",
        "get_task",
        "list_tasks",
        "update_task_state",
        "transfer_task",
        "request_approval",
        "approve_task",
        "reject_task",
        "get_my_work",
        "get_events",
        "set_escalation_policy",
        "get_escalation_policy",
        "set_agent_budget",
        "set_event_retention_days",
        "create_department",
        "set_agent_department",
        "get_org_structure",
        "list_department_tasks",
        "get_org_audit",
        "get_org_metrics",
        "get_server_status",
        "create_group",
        "add_group_member",
        "remove_group_member",
        "send_group_message",
        "get_group_messages",
        "get_group_members",
        "list_my_groups",
        "get_agent_reputation",
        "create_delegation",
        "revoke_delegation",
        "get_my_delegations",
        "create_observer_account",
        "revoke_observer_account",
        "list_observers",
        "get_org_snapshot",
        # SPEC-WEB (§20): human accounts, organization management
        "create_org",
        "disable_org",
        "list_org_conversations",
        "get_org_conversation",
        "change_agent_description",
        # SPEC-WEB D5 amended: login by selection
        "list_orgs",
    }


def test_success_data_never_null(fx):
    """A successful command always returns a conforming data object."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "hello", "cmid-null-1")
    data = fx.client.mark_conversation_no_reply(
        fx.client.get_messages(BOB, BOB_PASSWORD)["messages"][0]["conversation_id"],
        BOB,
        BOB_PASSWORD,
    )
    assert isinstance(data, dict)
    assert data["reply_status"] == "no_reply_needed"
