"""Tests for stable pagination (section 9): opaque signed cursors,
snapshot bound, command/agent/filters/sorting binding."""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD

INVALID_ARGUMENT = "INVALID_ARGUMENT"


def _seed_many(fx, n=10, sender=ALICE, recipient=BOB, prefix="cmid-pg-"):
    ids = []
    for i in range(n):
        m = fx.send(sender, ALICE_PASSWORD if sender == ALICE else BOB_PASSWORD,
                    recipient, f"message-{i}", f"{prefix}{i}")
        ids.append(m["message_id"])
    return ids


def test_get_messages_pagination_all_pages(fx):
    _seed_many(fx, 10)
    seen = []
    cursor = None
    while True:
        page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=3, cursor=cursor)
        seen.extend(m["message_id"] for m in page["messages"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 10
    assert len(set(seen)) == 10  # no duplicates


def test_get_messages_pagination_order_desc(fx):
    _seed_many(fx, 10)
    seen = []
    cursor = None
    while True:
        page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=3, cursor=cursor)
        seen.extend(m["created_at"] for m in page["messages"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(seen, reverse=True)


def test_pagination_last_page_null_cursor(fx):
    _seed_many(fx, 4)
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=4)
    assert page["next_cursor"] is None
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=3)
    assert page["next_cursor"] is not None


def test_cursor_snapshot_boundary_freeze(fx):
    """Messages created after the first page do not appear in the already
    started pagination; a new request includes them."""
    _seed_many(fx, 4)
    page1 = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    cursor = page1["next_cursor"]
    fx.send(ALICE, ALICE_PASSWORD, BOB, "after the boundary", "cmid-pg-fresh")
    page2 = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2, cursor=cursor)
    contents = [m["content"] for m in page2["messages"]]
    assert "after the boundary" not in contents
    fresh = fx.client.get_messages(BOB, BOB_PASSWORD, limit=50)
    assert fresh["messages"][0]["content"] == "after the boundary"


def test_cursor_status_frozen(fx):
    """A status changing after the first page is frozen in the pagination."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-pg-st-1")
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-pg-st-2")
    page1 = fx.client.get_messages(BOB, BOB_PASSWORD, status="unread", limit=1)
    assert page1["messages"][0]["message_id"] == m2["message_id"]
    cursor = page1["next_cursor"]
    # bob reads m2 after the first page
    fx.client.read_message(m2["message_id"], BOB, BOB_PASSWORD)
    page2 = fx.client.get_messages(BOB, BOB_PASSWORD, status="unread", limit=1, cursor=cursor)
    # m1 stays in page 2, still presented as unread (frozen at the bound)
    assert page2["messages"][0]["message_id"] == m1["message_id"]
    assert page2["messages"][0]["status"] == "unread"


def test_get_conversation_pagination(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-pgc-1")
    fx.send(BOB, BOB_PASSWORD, ALICE, "deux", "cmid-pgc-2")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "trois", "cmid-pgc-3")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD, limit=2)
    assert len(conv["messages"]) == 2
    assert conv["next_cursor"] is not None
    conv2 = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD, limit=2, cursor=conv["next_cursor"])
    assert len(conv2["messages"]) == 1
    assert conv2["next_cursor"] is None
    all_ids = [m["message_id"] for m in conv["messages"] + conv2["messages"]]
    assert len(set(all_ids)) == 3


def test_get_notifications_pagination(fx):
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Agent de test",  ORG_NAME, ORG_PASSWORD)
    ids = []
    for i in range(5):
        m = fx.send(ALICE, ALICE_PASSWORD, BOB, f"a{i}", f"cmid-pgn-a{i}")
        ids.append(m)
    for i in range(3):
        m = fx.client.send_message(BOB, f"c{i}", f"cmid-pgn-c{i}", "carol", "motdepasse-carol-1")
        ids.append(m)
    # read everything to trigger needs_reply on each conversation
    for m in ids:
        fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)
    seen = []
    cursor = None
    while True:
        page = fx.client.get_notifications(BOB, BOB_PASSWORD, limit=1, cursor=cursor)
        seen.extend(item["conversation_id"] for item in page["needs_reply"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 2  # deux conversations distinctes
    assert len(set(seen)) == 2


def test_cursor_rejected_with_wrong_command(fx):
    _seed_many(fx, 4)
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_notifications(BOB, BOB_PASSWORD, cursor=page["next_cursor"])
    assert exc.value.code == INVALID_ARGUMENT


def test_cursor_rejected_with_wrong_agent(fx):
    _seed_many(fx, 4)
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(ALICE, ALICE_PASSWORD, cursor=page["next_cursor"])
    assert exc.value.code == INVALID_ARGUMENT


def test_cursor_rejected_with_wrong_filter(fx):
    _seed_many(fx, 4)
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(BOB, BOB_PASSWORD, status="read", cursor=page["next_cursor"])
    assert exc.value.code == INVALID_ARGUMENT


def test_cursor_rejected_with_wrong_conversation_filter(fx):
    _seed_many(fx, 4)
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(
            BOB, BOB_PASSWORD,
            conversation_id=page["messages"][0]["conversation_id"],
            cursor=page["next_cursor"],
        )
    assert exc.value.code == INVALID_ARGUMENT


def test_cursor_rejected_with_wrong_conversation_command(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-pgx-1")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-pgx-2")
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=1)
    assert page["next_cursor"] is not None
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD, cursor=page["next_cursor"])
    assert exc.value.code == INVALID_ARGUMENT


def test_tampered_cursor_rejected(fx):
    _seed_many(fx, 4)
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    cursor = page["next_cursor"]
    tampered = cursor[:-2] + ("A" if cursor[-1] != "A" else "B")
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(BOB, BOB_PASSWORD, cursor=tampered)
    assert exc.value.code == INVALID_ARGUMENT


def test_garbage_cursor_rejected(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages(BOB, BOB_PASSWORD, cursor="pas-un-curseur")
    assert exc.value.code == INVALID_ARGUMENT


def test_cursor_after_restart_still_valid(fx):
    """The signing key persists: a cursor survives a restart."""
    from .conftest import make_server
    _seed_many(fx, 4)
    page1 = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    cursor = page1["next_cursor"]
    fx.server.stop()
    server2 = make_server(fx.config, org=False)  # same storage
    try:
        page2 = server2.client.get_messages(BOB, BOB_PASSWORD, limit=2, cursor=cursor)
        assert len(page2["messages"]) == 2
    finally:
        server2.stop()


def test_cursor_opaque_does_not_leak(fx):
    """The cursor is opaque: it contains no message content."""
    _seed_many(fx, 4)
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    cursor = page["next_cursor"] or ""
    assert "message-" not in cursor
