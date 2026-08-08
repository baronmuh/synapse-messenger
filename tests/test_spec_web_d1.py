"""SPEC-WEB D1 tests — Conversations with content (option B).

The organization's human reads internal AND external exchanges with their
content (list_org_conversations: metadata; get_org_conversation:
full messages), and can reply from their human account. Agents have no
access to organization content; a human from another organization does not
see conversations that do not touch their own (CONVERSATION_NOT_FOUND,
non-disclosure). Content reads are traced (audit F11).
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from tests.conftest import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    ORG2_NAME,
    ORG2_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
)

HUMAN = f"{ORG_NAME}_humain"
HUMAN2 = f"{ORG2_NAME}_humain"


@pytest.fixture()
def human(fx):
    """root_org's human is auto-created with the organization."""
    assert fx.client.get_my_organization(HUMAN, ORG_PASSWORD)["organization_name"] == ORG_NAME
    return HUMAN


def _ensure_org2(fx, human):
    """second_org is created by root_org's human (create_org, D3)."""
    try:
        fx.client.create_org(ORG2_NAME, ORG2_PASSWORD, human, ORG_PASSWORD)
    except ApiClientError as exc:
        if exc.code != "INVALID_ARGUMENT":
            raise
    fx.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)


def _seed_messages(fx, human):
    """Three conversations: internal (human<->alice), alice<->bob,
    and an external one (alice -> second_org's agent)."""
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.send(HUMAN, ORG_PASSWORD, ALICE, "hello alice", "cmid-d1-1")
    fx.send(ALICE, ALICE_PASSWORD, human, "hello human", "cmid-d1-2")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "interne a-b", "cmid-d1-3")
    _ensure_org2(fx, human)
    fx.create_agent("agent2", ORG2_PASSWORD + "x", "Agent de second_org", ORG2_NAME, ORG2_PASSWORD)
    fx.send(ALICE, ALICE_PASSWORD, "agent2", "message externe", "cmid-d1-4")


# ---------------------------------------------------------------------------
# list_org_conversations
# ---------------------------------------------------------------------------


def test_d1_list_conversations_org_scope(fx, human):
    """All conversations touching the organization appear
    (internal and external), with metadata only."""
    _seed_messages(fx, human)
    data = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    convs = {tuple(sorted(c["participants"])): c for c in data["conversations"]}
    # internal human<->alice
    assert ("alice", HUMAN) in convs
    c = convs[("alice", HUMAN)]
    assert c["message_count"] == 2
    assert c["unread_count"] == 1  # "hello human" (alice -> human) unread
    assert c["last_message_at"]
    assert "content" not in c  # metadata only in the list
    # internal alice<->bob
    assert ("alice", BOB) in convs
    # external alice->agent2 visible
    assert ("agent2", "alice") in convs or ("alice", "agent2") in convs


def test_d1_list_requires_human(fx, human):
    """An agent cannot list the organization's conversations."""
    _seed_messages(fx, human)
    with pytest.raises(ApiClientError) as exc:
        fx.client.list_org_conversations(ALICE, ALICE_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d1_list_foreign_human_scope(fx, human):
    """second_org's human only sees THEIR conversations (not root_org's
    purely internal ones)."""
    _seed_messages(fx, human)
    fx.send(ALICE, ALICE_PASSWORD, "agent2", "message externe", "cmid-d1-5")
    data = fx.client.list_org_conversations(HUMAN2, ORG2_PASSWORD, limit=100)
    participants = {tuple(sorted(c["participants"])) for c in data["conversations"]}
    assert ("agent2", "alice") in participants  # external conversation visible
    assert ("alice", BOB) not in participants  # internal to root_org: invisible


def test_d1_list_paginated(fx, human):
    """Cursor pagination: stable, disjoint pages."""
    for i in range(3):
        fx.send(human, ORG_PASSWORD, ALICE, f"msg a{i}", f"cmid-d1pa-{i}")
        fx.send(human, ORG_PASSWORD, BOB, f"msg b{i}", f"cmid-d1pb-{i}")
    page1 = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=1)
    assert len(page1["conversations"]) == 1
    assert page1["next_cursor"], "une page suivante doit exister"
    page2 = fx.client.list_org_conversations(
        HUMAN, ORG_PASSWORD, limit=1, cursor=page1["next_cursor"])
    assert len(page2["conversations"]) == 1
    ids = {c["conversation_id"] for c in page1["conversations"] + page2["conversations"]}
    assert len(ids) == 2  # pages disjointes


# ---------------------------------------------------------------------------
# get_org_conversation (content)
# ---------------------------------------------------------------------------


def test_d1_read_content(fx, human):
    """The human reads the full message content (option B)."""
    _seed_messages(fx, human)
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    conv = next(c for c in convs["conversations"] if HUMAN in c["participants"])
    data = fx.client.get_org_conversation(conv["conversation_id"], HUMAN, ORG_PASSWORD)
    contents = {m["content"] for m in data["messages"]}
    assert "hello alice" in contents
    assert "hello human" in contents
    for m in data["messages"]:
        assert m["sender_username"] and m["recipient_username"] and m["created_at"]


def test_d1_read_agent_denied(fx, human):
    """An agent never reads organization content."""
    _seed_messages(fx, human)
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    conv = convs["conversations"][0]
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_conversation(conv["conversation_id"], ALICE, ALICE_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d1_read_foreign_conversation_not_found(fx, human):
    """A human from another organization only gets
    CONVERSATION_NOT_FOUND for a conversation outside their scope
    (no existence disclosure)."""
    _seed_messages(fx, human)
    fx.send(ALICE, ALICE_PASSWORD, BOB, "interne a-b", "cmid-d1-6")
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    internal = next(c for c in convs["conversations"]
                    if HUMAN not in c["participants"])
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_conversation(internal["conversation_id"], HUMAN2, ORG2_PASSWORD)
    assert exc.value.code == "CONVERSATION_NOT_FOUND"


def test_d1_read_unknown_conversation(fx, human):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_conversation(
            "6f1c4f1a-2b3e-4c5d-8e9f-0a1b2c3d4e5f", HUMAN, ORG_PASSWORD)
    assert exc.value.code == "CONVERSATION_NOT_FOUND"


def test_d1_read_paginated(fx, human):
    """Chronological content pagination (ascending order, cursor)."""
    for i in range(4):
        fx.send(human, ORG_PASSWORD, ALICE, f"m{i}", f"cmid-d1q-{i}")
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    conv = next(c for c in convs["conversations"] if HUMAN in c["participants"])
    page1 = fx.client.get_org_conversation(conv["conversation_id"], HUMAN, ORG_PASSWORD, limit=2)
    assert len(page1["messages"]) == 2
    assert page1["next_cursor"]
    page2 = fx.client.get_org_conversation(
        conv["conversation_id"], HUMAN, ORG_PASSWORD, limit=2, cursor=page1["next_cursor"])
    ids = {m["message_id"] for m in page1["messages"] + page2["messages"]}
    assert len(ids) == 4
    # global chronological order
    times = [m["created_at"] for m in page1["messages"] + page2["messages"]]
    assert times == sorted(times)


# ---------------------------------------------------------------------------
# Human reply + audit
# ---------------------------------------------------------------------------


def test_d1_human_replies(fx, human):
    """The human replies from their account: the agent reads the message
    and the conversation contains the reply."""
    fx.send(human, ORG_PASSWORD, ALICE, "question", "cmid-d1r-1")
    fx.send(ALICE, ALICE_PASSWORD, human, "agent reply", "cmid-d1r-2")
    fx.send(human, ORG_PASSWORD, ALICE, "human reply", "cmid-d1r-3")
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    conv = next(c for c in convs["conversations"] if HUMAN in c["participants"])
    data = fx.client.get_org_conversation(conv["conversation_id"], HUMAN, ORG_PASSWORD)
    contents = [m["content"] for m in data["messages"]]
    assert contents == ["question", "agent reply", "human reply"]


def test_d1_content_read_is_audited(fx, human):
    """Content reading is traced (audit F11, R2.6)."""
    _seed_messages(fx, human)
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    conv = convs["conversations"][0]
    fx.client.get_org_conversation(conv["conversation_id"], HUMAN, ORG_PASSWORD)
    audit = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD)
    rows = [e for e in audit["entries"]
            if e["command"] == "get_org_conversation"]
    assert rows, "content reading must appear in the journal"
    assert rows[-1]["actor_username"] == HUMAN


def test_d1_conversations_never_in_snapshot(fx, human):
    """The snapshot (polling) never contains content (R2.2)."""
    _seed_messages(fx, human)
    snap = fx.client.get_org_snapshot(HUMAN, ORG_PASSWORD)
    for c in snap["conversations"]:
        assert "content" not in c and "messages" not in c


def test_d1_read_paginated(fx, human):
    """C2.4 — A conversation's detail is paginated (limit/cursor):
    chronological order, no duplicates or loss."""
    _seed_messages(fx, human)
    for i in range(4):  # 6 messages au total dans la conversation humaine
        fx.send(human, ORG_PASSWORD, ALICE, f"msg {i}", f"cmid-pg-{i}")
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    conv = next(c for c in convs["conversations"] if HUMAN in c["participants"])
    first = fx.client.get_org_conversation(conv["conversation_id"], HUMAN,
                                           ORG_PASSWORD, limit=2)
    assert len(first["messages"]) == 2
    assert first["next_cursor"]
    second = fx.client.get_org_conversation(conv["conversation_id"], HUMAN,
                                            ORG_PASSWORD, limit=2,
                                            cursor=first["next_cursor"])
    ids = {m["message_id"] for m in first["messages"] + second["messages"]}
    assert len(ids) == len(first["messages"]) + len(second["messages"])
    # strict chronological order
    times = [m["created_at"] for m in first["messages"] + second["messages"]]
    assert times == sorted(times)


def test_d1_read_after_other_org_disabled(fx, human):
    """C2.1 — Conversation with a disabled organization: READING
    stays possible (history), sending is refused (frozen recipient)."""
    _ensure_org2(fx, human)
    fx.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    fx.client.create_org(ORG_NAME + "_gel", "motdepasse-gel-123", human,
                         ORG_PASSWORD)
    fx.client.create_agent("agent_gel", "motdepasse-gel-123", "jelly",
                           ORG_NAME + "_gel", "motdepasse-gel-123")
    fx.client.set_organization_policy(True, True, ORG_NAME + "_gel",
                                      "motdepasse-gel-123")
    fx.send(human, ORG_PASSWORD, "agent_gel", "before freeze", "cmid-gel-1")
    fx.client.disable_org(ORG_NAME + "_gel", f"{ORG_NAME}_gel_humain",
                          "motdepasse-gel-123")
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    conv = next(c for c in convs["conversations"] if "agent_gel" in c["participants"])
    data = fx.client.get_org_conversation(conv["conversation_id"], HUMAN,
                                          ORG_PASSWORD)
    assert [m["content"] for m in data["messages"]] == ["before freeze"]
    with pytest.raises(ApiClientError) as exc:
        fx.send(human, ORG_PASSWORD, "agent_gel", "after freeze", "cmid-gel-2")
    assert exc.value.code in ("RECIPIENT_NOT_FOUND", "AUTH_FAILED")
