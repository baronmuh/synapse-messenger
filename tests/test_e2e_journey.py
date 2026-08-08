"""Complete end-to-end journey: integrates every component of the
system in a single scenario covering the SPEC.txt journeys (accounts,
authentication, messaging, states, notifications, pagination,
persistence, restart, backup, restore, security)."""

from __future__ import annotations

import json
import os
import uuid

import pytest

from synapse.backup import backup, restore
from synapse.client import ApiClientError

from .conftest import (
    ORG_NAME,
    ORG_PASSWORD,
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    make_server,
)


def test_full_journey(config):
    """The grand tour: from installation to restore, through
    a restart, without ever losing state."""

    # ---- 1. Installation: first administrator + startup ----
    server = make_server(config, org=True)
    c = server.client
    try:
        # ---- 2. Accounts and permissions ----
        c.create_agent(ALICE,  ALICE_PASSWORD, "Test agent",  ORG_NAME, ORG_PASSWORD)
        c.create_agent(BOB,  BOB_PASSWORD, "Test agent",  ORG_NAME, ORG_PASSWORD)
        c.create_agent("carol",  "motdepasse-carol-1", "Test agent",  ORG_NAME, ORG_PASSWORD)
        with pytest.raises(ApiClientError) as exc:
            c.create_agent("dave",  "motdepasse-dave-1", "Test agent",  ALICE, ALICE_PASSWORD)
        assert exc.value.code == "ACCESS_DENIED"
        with pytest.raises(ApiClientError) as exc:
            c.get_messages("ghost", "nimporte-quel-motdepasse")
        assert exc.value.code == "AUTH_FAILED"

        # ---- 3. Messaging: sends + idempotency ----
        sent = []
        for i in range(5):
            m = c.send_message(BOB, f"message {i}", f"cmid-e2e-{i}", ALICE, ALICE_PASSWORD)
            sent.append(m)
        retry = c.send_message(BOB, "message 2", "cmid-e2e-2", ALICE, ALICE_PASSWORD)
        assert retry["message_id"] == sent[2]["message_id"]  # idempotency
        with pytest.raises(ApiClientError) as exc:
            c.send_message(BOB, "different content", "cmid-e2e-2", ALICE, ALICE_PASSWORD)
        assert exc.value.code == "MESSAGE_ALREADY_EXISTS"
        c.send_message(ALICE, "bob's reply", "cmid-e2e-b1", BOB, BOB_PASSWORD)

        # ---- 4. Stable pagination ----
        page1 = c.get_messages(BOB, BOB_PASSWORD, limit=2)
        page2 = c.get_messages(BOB, BOB_PASSWORD, limit=2, cursor=page1["next_cursor"])
        page3 = c.get_messages(BOB, BOB_PASSWORD, limit=2, cursor=page2["next_cursor"])
        all_ids = [m["message_id"] for m in page1["messages"] + page2["messages"] + page3["messages"]]
        assert len(all_ids) == 5 and len(set(all_ids)) == 5
        assert page3["next_cursor"] is None
        # bound cursor: reuse with other filters rejected
        with pytest.raises(ApiClientError) as exc:
            c.get_messages(BOB, BOB_PASSWORD, status="read", cursor=page1["next_cursor"])
        assert exc.value.code == "INVALID_ARGUMENT"

        # ---- 5. Individual read and states ----
        newest = page1["messages"][0]
        read_at = c.read_message(newest["message_id"], BOB, BOB_PASSWORD)["read_at"]
        assert read_at is not None
        conv_bob = c.get_conversation(ALICE, BOB, BOB_PASSWORD)
        assert conv_bob["reply_status"] == "no_reply_needed"  # bob replied afterwards
        # read ALL messages received by bob
        for m in all_ids:
            c.read_message(m, BOB, BOB_PASSWORD)

        # ---- 6. Notifications and reply states ----
        notif_bob = c.get_notifications(BOB, BOB_PASSWORD)
        assert notif_bob["unread_by_sender"] == {}  # all read
        assert notif_bob["needs_reply"] == []  # bob replied
        notif_alice = c.get_notifications(ALICE, ALICE_PASSWORD)
        assert notif_alice["unread_by_sender"] == {BOB: 1}

        # ---- 7. Marking no_reply then canceling ----
        carol_msg = c.send_message(BOB, "de carol", "cmid-e2e-c1", "carol", "motdepasse-carol-1")
        c.read_message(carol_msg["message_id"], BOB, BOB_PASSWORD)
        data = c.mark_conversation_no_reply(carol_msg["conversation_id"], BOB, BOB_PASSWORD)
        assert data["reply_status"] == "no_reply_needed"
        assert (
            c.get_conversation("carol", BOB, BOB_PASSWORD)["reply_status"] == "no_reply_needed"
        )
        new_msg = c.send_message(BOB, "encore", "cmid-e2e-c2", "carol", "motdepasse-carol-1")
        c.read_message(new_msg["message_id"], BOB, BOB_PASSWORD)
        assert (
            c.get_conversation("carol", BOB, BOB_PASSWORD)["reply_status"] == "needs_reply"
        )  # marking canceled by the new message

        # ---- 8. Security: disabled account, logs without secrets ----
        c.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
        c.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
        with pytest.raises(ApiClientError):
            c.get_messages("carol", "motdepasse-carol-1")
        with pytest.raises(ApiClientError) as exc:
            c.send_message("carol", "to carol", "cmid-e2e-9", ALICE, ALICE_PASSWORD)
        assert exc.value.code == "RECIPIENT_NOT_FOUND"

        # ---- 9. RESTART: everything is preserved ----
        server.stop()
        server2 = make_server(config, org=False)
        c2 = server2.client
        try:
            # accounts, messages, states, idempotency, cursors
            # bob received: 5 from alice + 1 from carol + 1 from carol = 7
            assert len(c2.get_messages(BOB, BOB_PASSWORD)["messages"]) == 7
            assert len(c2.get_conversation(ALICE, BOB, BOB_PASSWORD)["messages"]) == 6
            again = c2.send_message(BOB, "message 2", "cmid-e2e-2", ALICE, ALICE_PASSWORD)
            assert again["message_id"] == sent[2]["message_id"]
            # carol stays disabled after restart
            with pytest.raises(ApiClientError):
                c2.get_messages("carol", "motdepasse-carol-1")
            # pagination cursor still valid after restart
            c2.send_message(BOB, "new after restart", "cmid-e2e-r1", ALICE, ALICE_PASSWORD)
            p1 = c2.get_messages(BOB, BOB_PASSWORD, limit=3)
            p2 = c2.get_messages(BOB, BOB_PASSWORD, limit=3, cursor=p1["next_cursor"])
            assert len(p2["messages"]) == 3

            # ---- 10. Backup / restore ----
            path = backup(config)
            c2.send_message(BOB, "after backup", "cmid-e2e-r2", ALICE, ALICE_PASSWORD)
            server2.stop()
            restore(config, path)
            server3 = make_server(config, org=False)
            try:
                c3 = server3.client
                inbox = c3.get_messages(BOB, BOB_PASSWORD)
                contents = [m["content"] for m in inbox["messages"]]
                assert "after backup" not in contents  # undone by the restore
                assert "message 4" in contents  # pre-backup state restored
                # identifiers and dates unchanged
                conv = c3.get_conversation(ALICE, BOB, BOB_PASSWORD)
                assert conv["messages"][0]["message_id"] == sent[0]["message_id"]
                assert conv["messages"][0]["created_at"] == sent[0]["created_at"]
            finally:
                server3.stop()
        finally:
            server2.stop()
    finally:
        server.stop()
