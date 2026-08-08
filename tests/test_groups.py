"""F15 — Multi-agent groups: creation, members, messages, isolation, and
non-disclosure.
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import GROUP_NOT_FOUND

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD


def _make_group(fx) -> str:
    return fx.client.create_group("incident-4711", ALICE, ALICE_PASSWORD)["group_id"]


def test_group_lifecycle(fx):
    gid = _make_group(fx)
    fx.client.add_group_member(gid, BOB, ALICE, ALICE_PASSWORD)
    members = fx.client.get_group_members(gid, ALICE, ALICE_PASSWORD)
    assert set(members["members"]) == {ALICE, BOB}
    # alice's groups
    groups = fx.client.list_my_groups(ALICE, ALICE_PASSWORD)["groups"]
    assert [g["group_id"] for g in groups] == [gid]
    assert groups[0]["member_count"] == 2
    # bob's groups too
    assert fx.client.list_my_groups(BOB, BOB_PASSWORD)["groups"]


def test_group_messages(fx):
    gid = _make_group(fx)
    fx.client.add_group_member(gid, BOB, ALICE, ALICE_PASSWORD)
    sent = fx.client.send_group_message(gid, "Situation under control", ALICE, ALICE_PASSWORD)
    fx.client.send_group_message(gid, "Received", BOB, BOB_PASSWORD)
    messages = fx.client.get_group_messages(gid, ALICE, ALICE_PASSWORD)["messages"]
    # most recent first
    assert [m["sender_username"] for m in messages] == [BOB, ALICE]
    assert sent["content"] == "Situation under control"
    assert all(set(m) >= {"message_id", "group_id", "sender_username", "content", "created_at"}
               for m in messages)


def test_group_message_idempotency(fx):
    gid = _make_group(fx)
    first = fx.client.send_group_message(gid, "duplicated", ALICE, ALICE_PASSWORD,
                                         client_message_id="gm-dup")
    second = fx.client.send_group_message(gid, "duplicated", ALICE, ALICE_PASSWORD,
                                          client_message_id="gm-dup")
    assert first["message_id"] == second["message_id"]
    messages = fx.client.get_group_messages(gid, ALICE, ALICE_PASSWORD)["messages"]
    assert len(messages) == 1


def test_non_member_cannot_read_or_write(fx):
    gid = _make_group(fx)
    # bob is not a member: the group is invisible to him
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_group_messages(gid, BOB, BOB_PASSWORD)
    assert exc.value.code == GROUP_NOT_FOUND
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_group_message(gid, "intrusion", BOB, BOB_PASSWORD)
    assert exc.value.code == GROUP_NOT_FOUND
    with pytest.raises(ApiClientError) as exc:
        fx.client.add_group_member(gid, BOB, BOB, BOB_PASSWORD)
    assert exc.value.code == GROUP_NOT_FOUND


def test_removed_member_loses_access(fx):
    gid = _make_group(fx)
    fx.client.add_group_member(gid, BOB, ALICE, ALICE_PASSWORD)
    fx.client.remove_group_member(gid, BOB, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_group_members(gid, BOB, BOB_PASSWORD)
    assert exc.value.code == GROUP_NOT_FOUND
    # the group's messages are preserved (no data deletion)
    messages = fx.client.get_group_messages(gid, ALICE, ALICE_PASSWORD)["messages"]
    assert messages == []


def test_unknown_group_id(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_group_members("11111111-1111-4111-8111-111111111111",
                                    ALICE, ALICE_PASSWORD)
    assert exc.value.code == GROUP_NOT_FOUND


def test_group_messages_pagination(fx):
    gid = _make_group(fx)
    for i in range(3):
        fx.client.send_group_message(gid, f"m{i}", ALICE, ALICE_PASSWORD)
    page1 = fx.client.get_group_messages(gid, ALICE, ALICE_PASSWORD, limit=2)
    assert len(page1["messages"]) == 2
    page2 = fx.client.get_group_messages(gid, ALICE, ALICE_PASSWORD,
                                         cursor=page1["next_cursor"])
    assert len(page2["messages"]) == 1
    ids = {m["message_id"] for m in page1["messages"]}
    assert page2["messages"][0]["message_id"] not in ids


def test_groups_persist_across_restart(fx):
    from .conftest import make_server

    gid = _make_group(fx)
    fx.client.send_group_message(gid, "kept", ALICE, ALICE_PASSWORD)
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        messages = server2.client.get_group_messages(gid, ALICE, ALICE_PASSWORD)["messages"]
        assert messages[0]["content"] == "kept"
    finally:
        server2.stop()
