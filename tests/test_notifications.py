"""Tests for notifications (section 11) and the "no reply needed" marking
(section 12)."""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD

INVALID_ARGUMENT = "INVALID_ARGUMENT"


def test_notifications_shape(fx):
    data = fx.client.get_notifications(ALICE, ALICE_PASSWORD)
    assert set(data.keys()) == {"unread_by_sender", "needs_reply", "next_cursor"}
    assert data["unread_by_sender"] == {}
    assert data["needs_reply"] == []
    assert data["next_cursor"] is None


def test_unread_by_sender_counts(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nt-1")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-nt-2")
    fx.send(BOB, BOB_PASSWORD, ALICE, "trois", "cmid-nt-3")
    notif_bob = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert notif_bob["unread_by_sender"] == {ALICE: 2}
    notif_alice = fx.client.get_notifications(ALICE, ALICE_PASSWORD)
    assert notif_alice["unread_by_sender"] == {BOB: 1}


def test_unread_by_sender_resets_after_read(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nt-4")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-nt-5")
    notif = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert notif["unread_by_sender"] == {ALICE: 2}
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    notif = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert notif["unread_by_sender"] == {ALICE: 1}


def test_notifications_do_not_modify_status(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nt-6")
    fx.client.get_notifications(BOB, BOB_PASSWORD)
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert inbox["messages"][0]["status"] == "unread"


def test_needs_reply_item_shape(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nt-7")
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-nt-8")
    # read the last received message (m2): needs_reply, with m1 still unread
    fx.client.read_message(m2["message_id"], BOB, BOB_PASSWORD)
    notif = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert len(notif["needs_reply"]) == 1
    item = notif["needs_reply"][0]
    assert set(item.keys()) == {
        "conversation_id",
        "other_username",
        "other_organization_name",
        "unread_count",
        "last_received_at",
    }
    assert item["other_username"] == ALICE
    assert item["unread_count"] == 1  # one of the two messages is still unread
    assert item["last_received_at"].endswith("Z")


def test_needs_reply_only_when_last_received_read(fx):
    """No needs_reply while the last received message is unread."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nt-9")
    notif = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert notif["needs_reply"] == []


def test_needs_reply_cleared_by_own_reply(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question", "cmid-nt-10")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert len(fx.client.get_notifications(BOB, BOB_PASSWORD)["needs_reply"]) == 1
    fx.send(BOB, BOB_PASSWORD, ALICE, "reply", "cmid-nt-11")
    assert fx.client.get_notifications(BOB, BOB_PASSWORD)["needs_reply"] == []


def test_needs_reply_sorted_by_last_received_desc(fx):
    """Sorting: descending date of the last received message."""
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Agent de test",  ORG_NAME, ORG_PASSWORD)
    m_carol = fx.client.send_message(BOB, "de carol", "cmid-nt-12", "carol", "motdepasse-carol-1")
    m_alice = fx.send(ALICE, ALICE_PASSWORD, BOB, "d'alice", "cmid-nt-13")
    fx.client.read_message(m_alice["message_id"], BOB, BOB_PASSWORD)
    fx.client.read_message(m_carol["message_id"], BOB, BOB_PASSWORD)
    notif = fx.client.get_notifications(BOB, BOB_PASSWORD)
    dates = [item["last_received_at"] for item in notif["needs_reply"]]
    assert dates == sorted(dates, reverse=True)


def test_needs_reply_per_agent(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "bonjour", "cmid-nt-14")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    notif_bob = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert [i["other_username"] for i in notif_bob["needs_reply"]] == [ALICE]
    notif_alice = fx.client.get_notifications(ALICE, ALICE_PASSWORD)
    assert notif_alice["needs_reply"] == []


# ---------------------------------------------------------------------------
# mark_conversation_no_reply
# ---------------------------------------------------------------------------


def test_mark_no_reply_response_shape(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nr-1")
    conv_id = m1["conversation_id"]
    data = fx.client.mark_conversation_no_reply(conv_id, BOB, BOB_PASSWORD)
    assert set(data.keys()) == {
        "conversation_id",
        "reply_status",
        "no_reply_for_message_id",
    }
    assert data["conversation_id"] == conv_id
    assert data["reply_status"] == "no_reply_needed"
    assert data["no_reply_for_message_id"] == m1["message_id"]


def test_mark_no_reply_requires_received_message(fx):
    """Nonexistent conversation or no received message: INVALID_ARGUMENT."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.mark_conversation_no_reply(
            "00000000-0000-4000-8000-000000000000", BOB, BOB_PASSWORD
        )
    assert exc.value.code == INVALID_ARGUMENT
    # existing conversation but no message received by the agent
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nr-2")
    conv_id = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["conversation_id"]
    with pytest.raises(ApiClientError) as exc:
        fx.client.mark_conversation_no_reply(conv_id, ALICE, ALICE_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_mark_no_reply_clears_needs_reply(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question", "cmid-nr-3")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "needs_reply"
    )
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"
    assert fx.client.get_notifications(BOB, BOB_PASSWORD)["needs_reply"] == []


def test_mark_no_reply_idempotent(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nr-4")
    conv_id = m1["conversation_id"]
    first = fx.client.mark_conversation_no_reply(conv_id, BOB, BOB_PASSWORD)
    second = fx.client.mark_conversation_no_reply(conv_id, BOB, BOB_PASSWORD)
    assert first == second


def test_mark_no_reply_targets_last_received(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nr-5")
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-nr-6")
    data = fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    assert data["no_reply_for_message_id"] == m2["message_id"]  # the latest received


def test_mark_no_reply_new_message_cancels_marking(fx):
    """A newly received message cancels the marking."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nr-7")
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    fx.send(ALICE, ALICE_PASSWORD, BOB, "nouveau", "cmid-nr-8")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    # the new message is unread: no needs_reply; after reading it, the
    # marking no longer covers this new message -> needs_reply
    assert conv["reply_status"] == "no_reply_needed"
    messages = fx.client.get_messages(BOB, BOB_PASSWORD)["messages"]
    newest = messages[0]
    fx.client.read_message(newest["message_id"], BOB, BOB_PASSWORD)
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "needs_reply"


def test_mark_no_reply_does_not_change_read_status(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nr-9")
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert inbox["messages"][0]["status"] == "unread"
    assert inbox["messages"][0]["read_at"] is None


def test_mark_no_reply_does_not_affect_other_agent(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-nr-10")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    conv_alice = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    assert conv_alice["reply_status"] == "no_reply_needed"  # unchanged (already no_reply)
    # and alice received no new message
    assert fx.client.get_notifications(ALICE, ALICE_PASSWORD)["needs_reply"] == []


def test_mark_no_reply_does_not_reveal_other_conversations(fx):
    """Marking another pair's conversation: INVALID_ARGUMENT, without
    revealing its existence."""
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Agent de test",  ORG_NAME, ORG_PASSWORD)
    m1 = fx.client.send_message(BOB, "secret", "cmid-nr-11", "carol", "motdepasse-carol-1")
    conv_id = m1["conversation_id"]
    with pytest.raises(ApiClientError) as exc:
        fx.client.mark_conversation_no_reply(conv_id, ALICE, ALICE_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT
