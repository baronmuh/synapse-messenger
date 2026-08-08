"""Conversation tests (section 8) and reply states (section 10)."""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD

INVALID_ARGUMENT = "INVALID_ARGUMENT"
CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"


def test_get_conversation_shape(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-cv-1")
    fx.send(BOB, BOB_PASSWORD, ALICE, "deux", "cmid-cv-2")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert set(conv.keys()) == {
        "conversation_id",
        "other_username",
        "reply_status",
        "messages",
        "next_cursor",
    }
    assert conv["other_username"] == ALICE
    assert conv["reply_status"] in ("needs_reply", "no_reply_needed")
    assert conv["next_cursor"] is None


def test_get_conversation_chronological_ascending(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-cv-3")
    m2 = fx.send(BOB, BOB_PASSWORD, ALICE, "deux", "cmid-cv-4")
    m3 = fx.send(ALICE, ALICE_PASSWORD, BOB, "trois", "cmid-cv-5")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert [m["message_id"] for m in conv["messages"]] == [
        m1["message_id"],
        m2["message_id"],
        m3["message_id"],
    ]
    timestamps = [m["created_at"] for m in conv["messages"]]
    assert timestamps == sorted(timestamps)


def test_get_conversation_no_exchange(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert exc.value.code == CONVERSATION_NOT_FOUND


def test_get_conversation_other_unknown_user(fx):
    """Nonexistent other agent: no conversation possible, no leak."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation("ghost", ALICE, ALICE_PASSWORD)
    assert exc.value.code == CONVERSATION_NOT_FOUND


def test_get_conversation_other_must_differ(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation(BOB, BOB, BOB_PASSWORD)
    assert exc.value.code == INVALID_ARGUMENT


def test_get_conversation_does_not_mark_read(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-cv-6")
    fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert inbox["messages"][0]["status"] == "unread"


def test_get_conversation_status_is_per_recipient(fx):
    """status/read_at concern the recipient; the sender sees the date the
    recipient read the message."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-cv-7")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    conv_bob = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    conv_alice = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    # same message, seen from both sides: read_at = date read by bob
    assert conv_bob["messages"][0]["status"] == "read"
    assert conv_bob["messages"][0]["read_at"] == conv_alice["messages"][0]["read_at"]
    assert conv_alice["messages"][0]["read_at"] is not None


def test_get_conversation_requires_participation(fx):
    """A third-party agent cannot see other agents' conversation."""
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Test agent",  ORG_NAME, ORG_PASSWORD)
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-cv-8")
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation(BOB, "carol", "motdepasse-carol-1")
    assert exc.value.code == CONVERSATION_NOT_FOUND


# ---------------------------------------------------------------------------
# Reply states (section 10)
# ---------------------------------------------------------------------------


def test_reply_state_no_reply_needed_without_received(fx):
    """A conversation without received messages can be no_reply_needed but
    never needs_reply."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "bonjour", "cmid-rs-1")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"  # bob has received nothing


def test_reply_state_unread_last_message_not_needs_reply(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "bonjour", "cmid-rs-2")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"  # latest received, unread


def test_reply_state_needs_reply_after_read(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "bonjour", "cmid-rs-3")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "needs_reply"


def test_reply_state_own_reply_clears_needs_reply(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question", "cmid-rs-4")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "needs_reply"
    )
    fx.send(BOB, BOB_PASSWORD, ALICE, "reply", "cmid-rs-5")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"  # bob replied afterwards


def test_reply_state_new_unread_message_clears_needs_reply(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question", "cmid-rs-6")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert (
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "needs_reply"
    )
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "suite", "cmid-rs-7")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"  # new unread message


def test_reply_state_per_participant(fx):
    """The state is per participant."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "bonjour", "cmid-rs-8")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    conv_bob = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    conv_alice = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    assert conv_bob["reply_status"] == "needs_reply"
    assert conv_alice["reply_status"] == "no_reply_needed"


def test_reply_state_cannot_be_forced_directly(fx):
    """needs_reply is computed, never forced: sending never creates
    needs_reply for the sender."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "bonjour", "cmid-rs-9")
    conv_alice = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    assert conv_alice["reply_status"] == "no_reply_needed"
