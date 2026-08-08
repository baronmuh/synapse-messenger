"""Tests for get_messages and read_message (section 7): filters, sorting,
individual reads, recipient-specific statuses."""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD

MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"


def _seed(fx):
    """Create 3 alice->bob messages and 1 bob->alice message; return the ids."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "first", "cmid-msg-1")
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "second", "cmid-msg-2")
    m3 = fx.send(ALICE, ALICE_PASSWORD, BOB, "third", "cmid-msg-3")
    m4 = fx.send(BOB, BOB_PASSWORD, ALICE, "bob's reply", "cmid-msg-4")
    return m1, m2, m3, m4


def test_get_messages_only_received(fx):
    """An agent only sees its received messages, never its own sends."""
    m1, m2, m3, m4 = _seed(fx)
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert [m["message_id"] for m in inbox["messages"]] == [m3["message_id"], m2["message_id"], m1["message_id"]]
    inbox_alice = fx.client.get_messages(ALICE, ALICE_PASSWORD)
    assert [m["message_id"] for m in inbox_alice["messages"]] == [m4["message_id"]]


def test_get_messages_sorted_desc(fx):
    _seed(fx)
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    timestamps = [m["created_at"] for m in inbox["messages"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_get_messages_does_not_modify_status(fx):
    _seed(fx)
    fx.client.get_messages(BOB, BOB_PASSWORD)
    fx.client.get_messages(BOB, BOB_PASSWORD, status="unread")
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert all(m["status"] == "unread" for m in inbox["messages"])


def test_get_messages_status_filter(fx):
    m1, m2, m3, _ = _seed(fx)
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    unread = fx.client.get_messages(BOB, BOB_PASSWORD, status="unread")
    assert [m["message_id"] for m in unread["messages"]] == [m3["message_id"], m2["message_id"]]
    read = fx.client.get_messages(BOB, BOB_PASSWORD, status="read")
    assert [m["message_id"] for m in read["messages"]] == [m1["message_id"]]


def test_get_messages_sender_filter(fx):
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Test agent",  ORG_NAME, ORG_PASSWORD)
    fx.send(ALICE, ALICE_PASSWORD, BOB, "de alice", "cmid-sf-1")
    fx.client.send_message(BOB, "de carol", "cmid-sf-2", "carol", "motdepasse-carol-1")
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD, sender_username="alice")
    assert len(inbox["messages"]) == 1
    assert inbox["messages"][0]["sender_username"] == ALICE
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD, sender_username="carol")
    assert len(inbox["messages"]) == 1
    assert inbox["messages"][0]["sender_username"] == "carol"


def test_get_messages_conversation_filter(fx):
    m1, m2, m3, _ = _seed(fx)
    conv_id = m1["conversation_id"]
    # the three alice->bob messages share the same conversation
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD, conversation_id=conv_id)
    assert [m["message_id"] for m in inbox["messages"]] == [
        m3["message_id"], m2["message_id"], m1["message_id"],
    ]
    other = fx.client.get_messages(BOB, BOB_PASSWORD, conversation_id="00000000-0000-4000-8000-000000000000")
    assert other["messages"] == []


def test_get_messages_case_insensitive_uuid_filter(fx):
    m1, *_ = _seed(fx)
    upper = m1["conversation_id"].upper()
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD, conversation_id=upper)
    assert len(inbox["messages"]) == 3


def test_get_messages_default_limit(fx):
    _seed(fx)
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD, limit=50)
    assert len(inbox["messages"]) == 3


def test_get_messages_limit(fx):
    _seed(fx)
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    assert len(inbox["messages"]) == 2
    assert inbox["next_cursor"] is not None


def test_get_messages_other_agent_cannot_see(fx):
    """Alice cannot see the messages received by bob."""
    _seed(fx)
    inbox = fx.client.get_messages(ALICE, ALICE_PASSWORD)
    assert all(m["sender_username"] != ALICE or m["recipient_username"] == ALICE for m in inbox["messages"])
    assert all(m["recipient_username"] == ALICE for m in inbox["messages"])


# ---------------------------------------------------------------------------
# read_message
# ---------------------------------------------------------------------------


def test_read_message_marks_only_that_message(fx):
    m1, m2, _, _ = _seed(fx)
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    statuses = {m["message_id"]: m["status"] for m in inbox["messages"]}
    assert statuses[m1["message_id"]] == "read"
    assert statuses[m2["message_id"]] == "unread"


def test_read_message_sets_read_at_once(fx):
    m1, *_ = _seed(fx)
    first = fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    second = fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert first["read_at"] is not None
    assert first["read_at"] == second["read_at"]  # first-read date is stable


def test_read_message_idempotent(fx):
    m1, *_ = _seed(fx)
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    again = fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert again["status"] == "read"


def test_read_message_unknown_id(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.read_message("00000000-0000-4000-8000-000000000000", BOB, BOB_PASSWORD)
    assert exc.value.code == MESSAGE_NOT_FOUND


def test_read_message_inaccessible_hides_existence(fx):
    """An agent cannot read a message it neither sent nor received:
    MESSAGE_NOT_FOUND, without revealing the message's existence."""
    m1, *_ = _seed(fx)
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Test agent",  ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.read_message(m1["message_id"], "carol", "motdepasse-carol-1")
    assert exc.value.code == MESSAGE_NOT_FOUND


def test_read_message_by_sender_returns_without_marking(fx):
    """The sender may view the message without marking the recipient's read
    state (the status belongs to the recipient)."""
    m1, *_ = _seed(fx)
    result = fx.client.read_message(m1["message_id"], ALICE, ALICE_PASSWORD)
    assert result["message_id"] == m1["message_id"]
    assert result["status"] == "unread"  # bob has not read it
    assert result["read_at"] is None
    # bob is not marked read by alice's action
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert inbox["messages"][0]["status"] == "unread"


def test_read_at_visible_to_both(fx):
    m1, *_ = _seed(fx)
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    # visible to the recipient
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    read_message = next(m for m in inbox["messages"] if m["message_id"] == m1["message_id"])
    assert read_message["read_at"] is not None
    assert read_message["status"] == "read"
    # visible to the sender via the conversation
    conv = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    sender_view = next(m for m in conv["messages"] if m["message_id"] == m1["message_id"])
    assert sender_view["read_at"] is not None


def test_read_message_recomputes_reply_state(fx):
    """After reading the last received message, the state becomes needs_reply."""
    # only alice->bob messages: bob sent nothing after
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "first", "cmid-rs-re-1")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "second", "cmid-rs-re-2")
    m3 = fx.send(ALICE, ALICE_PASSWORD, BOB, "third", "cmid-rs-re-3")
    conv_id = m1["conversation_id"]
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"  # latest received is unread
    # reading an older message does not change the state (latest received stays unread)
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"
    # reading the latest received message triggers needs_reply
    fx.client.read_message(m3["message_id"], BOB, BOB_PASSWORD)
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "needs_reply"
    # alice received nothing new: her state stays no_reply_needed
    conv = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"


def test_read_message_concurrent_readers_same_date(fx):
    """Concurrent reads all return the same first-read date."""
    import threading

    m1, *_ = _seed(fx)
    results = []
    barrier = threading.Barrier(4)

    def reader():
        barrier.wait()
        results.append(fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)["read_at"])

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1
    assert results[0] is not None
