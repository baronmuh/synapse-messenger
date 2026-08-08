"""Persistence and restart tests (sections 1, 15): accounts,
messages, conversations, statuses, dates, and idempotency identifiers
survive a service restart."""

from __future__ import annotations

import pytest

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, make_server


def test_data_survives_restart(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "persistant", "cmid-per-1")
    m2 = fx.send(BOB, BOB_PASSWORD, ALICE, "reply", "cmid-per-2")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)

    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        c2 = server2.client
        # accounts preserved (with their role)
        assert c2.create_agent("carol",  "motdepasse-carol-1", "Agent de test",  ORG_NAME, ORG_PASSWORD)["username"] == "carol"
        # messages and statuses preserved
        inbox = c2.get_messages(BOB, BOB_PASSWORD)
        assert len(inbox["messages"]) == 1
        assert inbox["messages"][0]["content"] == "persistant"
        assert inbox["messages"][0]["status"] == "read"
        # conversation and reply states preserved
        conv = c2.get_conversation(ALICE, BOB, BOB_PASSWORD)
        assert conv["reply_status"] == "no_reply_needed"
        assert len(conv["messages"]) == 2
        # idempotency preserved after restart
        again = c2.send_message(BOB, "persistant", "cmid-per-1", ALICE, ALICE_PASSWORD)
        assert again["message_id"] == m1["message_id"]
        # the original timestamp is preserved (no regeneration)
        assert again["created_at"] == m1["created_at"]
    finally:
        server2.stop()


def test_read_state_persists(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "lu", "cmid-per-3")
    read_at = fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)["read_at"]
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        conv = server2.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
        assert conv["messages"][0]["read_at"] == read_at
    finally:
        server2.stop()


def test_disabled_state_persists(fx):
    from synapse.client import ApiClientError
    fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        with pytest.raises(ApiClientError) as exc:
            server2.client.get_messages(BOB, BOB_PASSWORD)
        assert exc.value.code == "AUTH_FAILED"
        # reactivation possible after restart
        server2.client.reactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
        assert server2.client.get_messages(BOB, BOB_PASSWORD) == {"messages": [], "next_cursor": None}
    finally:
        server2.stop()


def test_lock_released_after_stop(fx):
    """The lock is released on shutdown: a second server can start."""
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        assert server2.client.get_messages(ALICE, ALICE_PASSWORD) == {
            "messages": [],
            "next_cursor": None,
        }
    finally:
        server2.stop()
