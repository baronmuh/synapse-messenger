"""Regression tests: end-to-end scenarios covering the whole
specification, to detect any cross-feature regression between
features."""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD


def test_full_lifecycle(fx):
    """Full lifecycle: accounts, sending, reading, replying, notifications,
    deactivation, reactivation."""
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    # alice writes to bob, bob reads then replies
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "Hello Bob", "cmid-rg-1")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "needs_reply"
    )
    m2 = fx.send(BOB, BOB_PASSWORD, ALICE, "Hello Alice", "cmid-rg-2")
    assert m1["conversation_id"] == m2["conversation_id"]
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "no_reply_needed"
    )

    # alice's notifications: 1 unread message from bob
    notif = fx.client.get_notifications(ALICE, ALICE_PASSWORD)
    assert notif["unread_by_sender"] == {BOB: 1}

    # deactivation: alice can no longer do anything, her data remains
    fx.client.deactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError):
        fx.client.get_messages(ALICE, ALICE_PASSWORD)
    # can bob still write? no: the recipient is deactivated
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message(ALICE, "still there?", "cmid-rg-4", BOB, BOB_PASSWORD)
    assert exc.value.code == "RECIPIENT_NOT_FOUND"
    fx.client.reactivate_agent(ALICE, ORG_NAME, ORG_PASSWORD)

    # everything is intact after reactivation
    conv = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    assert len(conv["messages"]) == 2


def test_idempotent_retry_after_error(fx):
    """Retrying a send after an error: never a duplicate (section 14)."""
    first = fx.send(ALICE, ALICE_PASSWORD, BOB, "unique", "cmid-rg-5")
    for _ in range(3):
        again = fx.send(ALICE, ALICE_PASSWORD, BOB, "unique", "cmid-rg-5")
        assert again["message_id"] == first["message_id"]
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert len(inbox["messages"]) == 1


def test_read_at_propagates_to_sender(fx):
    """read_at (first read) is visible to both the recipient and the sender."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "read receipt", "cmid-rg-6")
    read_at = fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)["read_at"]
    conv = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    assert conv["messages"][0]["read_at"] == read_at
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert inbox["messages"][0]["read_at"] == read_at


def test_multiple_conversations_and_filters(fx):
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Agent de test",  ORG_NAME, ORG_PASSWORD)
    m_a = fx.send(ALICE, ALICE_PASSWORD, BOB, "de alice", "cmid-rg-7")
    m_c = fx.client.send_message(BOB, "de carol", "cmid-rg-8", "carol", "motdepasse-carol-1")
    fx.client.read_message(m_c["message_id"], BOB, BOB_PASSWORD)
    # filter by sender
    from_alice = fx.client.get_messages(BOB, BOB_PASSWORD, sender_username=ALICE)
    assert [m["message_id"] for m in from_alice["messages"]] == [m_a["message_id"]]
    # filter by status
    unread = fx.client.get_messages(BOB, BOB_PASSWORD, status="unread")
    assert [m["message_id"] for m in unread["messages"]] == [m_a["message_id"]]
    # filter by conversation
    conv_bob_alice = fx.client.get_messages(BOB, BOB_PASSWORD, conversation_id=m_a["conversation_id"])
    assert [m["message_id"] for m in conv_bob_alice["messages"]] == [m_a["message_id"]]


def test_username_case_variants_same_account(fx):
    """Alice, ALICE, alice all refer to the same account."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "casse", "cmid-rg-9")
    inbox = fx.client.get_messages("ALICE", ALICE_PASSWORD)
    assert len(inbox["messages"]) == 0  # alice received nothing
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert inbox["messages"][0]["sender_username"] == ALICE


def test_conversation_reply_status_after_mark_then_new_message(fx):
    """Mark no_reply, then a new message: the mark is cancelled."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-rg-10")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"]
        == "no_reply_needed"
    )
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-rg-11")
    fx.client.read_message(m2["message_id"], BOB, BOB_PASSWORD)
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"]
        == "needs_reply"
    )
