"""Independent audit of the SPEC.txt specification — edge cases and failure
scenarios complementary to the existing suite.

This module modifies no existing file: it only drives the service via the
socket (Client) or via raw JSON requests.

Covered sections (SPEC.txt specification):
* request envelope (section 2): non-object payloads, wrong types;
* parameter validation (sections 5, 6, 7): limit, status, filters,
  UUIDv1/v3, content, client_message_id;
* idempotency (section 6): recipient in a different case, replay after
  deactivation/reactivation, NFC equivalence;
* reply states (sections 10, 12): marking on a read message, send by the
  sender after marking, conversation with no received message;
* stable pagination (section 9): limit-100 page, different cursor and
  limit, cross-command cursor, combined filters, frozen unread_by_sender;
* persistence (sections 1, 15): bidirectional conversations, reply
  states, no_reply marking, unread_by_sender;
* errors (sections 5, 7): reading a third party's message, other_username
  case, special passwords;
* side-effect-free behaviors (sections 7, 11).
"""

from __future__ import annotations

import json

import pytest

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

INVALID_ARGUMENT = "INVALID_ARGUMENT"
MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"
CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
MESSAGE_ALREADY_EXISTS = "MESSAGE_ALREADY_EXISTS"
RECIPIENT_NOT_FOUND = "RECIPIENT_NOT_FOUND"
CAROL = "carol"
CAROL_PASSWORD = "motdepasse-carol-1"


def _create_carol(fx) -> None:
    fx.client.create_agent(CAROL,  CAROL_PASSWORD, "Test agent",  ORG_NAME, ORG_PASSWORD)


def _conv(fx, me: str, password: str, other: str) -> dict:
    return fx.client.get_conversation(other, me, password)


def _assert_code(fn, code: str):
    with pytest.raises(ApiClientError) as exc:
        fn()
    assert exc.value.code == code
    return exc.value


# ---------------------------------------------------------------------------
# Envelope (section 2)
# ---------------------------------------------------------------------------


def test_envelope_empty_parameters_object(fx, raw_socket_client):
    """parameters = {} (no fields): INVALID_ARGUMENT."""
    resp = raw_socket_client(
        json.dumps({"api_version": "v2", "command": "get_messages", "parameters": {}}) + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


@pytest.mark.parametrize("api_version", [1, 1.5, True])
def test_envelope_numeric_api_version(fx, raw_socket_client, api_version):
    """Numeric/boolean api_version: INVALID_ARGUMENT (must be the string 'v1')."""
    resp = raw_socket_client(
        json.dumps(
            {"api_version": api_version, "command": "get_notifications", "parameters": {}}
        )
        + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


@pytest.mark.parametrize("parameters", [[], "texte", 42, None])
def test_envelope_parameters_not_object(fx, raw_socket_client, parameters):
    """parameters = list/string/number/null: INVALID_ARGUMENT (object required)."""
    resp = raw_socket_client(
        json.dumps({"api_version": "v2", "command": "get_notifications", "parameters": parameters})
        + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


@pytest.mark.parametrize("command", [["get_notifications"], {"x": 1}, 42])
def test_envelope_command_not_string(fx, raw_socket_client, command):
    """command = list/object/number: INVALID_ARGUMENT (not INTERNAL_ERROR)."""
    resp = raw_socket_client(
        json.dumps({"api_version": "v2", "command": command, "parameters": {}}) + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


@pytest.mark.parametrize("payload", [[1, 2, 3], "texte", 42, None])
def test_envelope_top_level_not_object(fx, raw_socket_client, payload):
    """Scalar or array JSON at the top level: INVALID_ARGUMENT."""
    resp = raw_socket_client(json.dumps(payload) + "\n")
    assert resp["error"]["code"] == INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# Parameter validation (sections 5, 6, 7)
# ---------------------------------------------------------------------------


def test_limit_float_rejected(fx):
    """limit = 50.0 (float): INVALID_ARGUMENT — only a JSON integer is accepted."""
    _assert_code(
        lambda: fx.client.get_messages(ALICE, ALICE_PASSWORD, limit=50.0), INVALID_ARGUMENT
    )


def test_limit_string_rejected(fx):
    """limit = "50" (string): INVALID_ARGUMENT."""
    _assert_code(
        lambda: fx.client.get_messages(ALICE, ALICE_PASSWORD, limit="50"), INVALID_ARGUMENT
    )


@pytest.mark.parametrize("status", ["READ", "Unread", "READ ", " read"])
def test_status_case_sensitive(fx, status):
    """status is case-sensitive: only exact 'read'/'unread' is accepted."""
    _assert_code(
        lambda: fx.client.get_messages(ALICE, ALICE_PASSWORD, status=status), INVALID_ARGUMENT
    )


@pytest.mark.parametrize("sender", ["a!", "ab", "x", "bad name", ""])
def test_sender_filter_invalid_short(fx, sender):
    """Invalid sender_username filter (too short or forbidden character):
    INVALID_ARGUMENT, whatever the actual recipient."""
    _assert_code(
        lambda: fx.client.get_messages(ALICE, ALICE_PASSWORD, sender_username=sender),
        INVALID_ARGUMENT,
    )


def test_sender_filter_unknown_user_empty(fx):
    """Valid but unknown sender filter: empty list, no error (no leak)."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-aud-sf-1")
    data = fx.client.get_messages(BOB, BOB_PASSWORD, sender_username="ghost")
    assert data == {"messages": [], "next_cursor": None}


@pytest.mark.parametrize(
    "uuidv", ["6ba7b810-9dad-11d1-80b4-00c04fd430c8", "3d813cbb-47fb-32ba-91df-831e1593ac29"]
)
def test_conversation_id_uuid_v1_v3_rejected(fx, uuidv):
    """Only a UUIDv4 is accepted: UUIDv1/UUIDv3 in a filter → INVALID_ARGUMENT."""
    _assert_code(
        lambda: fx.client.get_messages(ALICE, ALICE_PASSWORD, conversation_id=uuidv),
        INVALID_ARGUMENT,
    )


def test_read_message_uuid_v1_rejected(fx):
    """read_message with a UUIDv1 message_id: INVALID_ARGUMENT."""
    _assert_code(
        lambda: fx.client.read_message("6ba7b810-9dad-11d1-80b4-00c04fd430c8", ALICE, ALICE_PASSWORD),
        INVALID_ARGUMENT,
    )


def test_message_whitespace_only_rejected(fx):
    """Content made only of whitespace (including non-breaking spaces) after
    cleaning: INVALID_ARGUMENT via the API."""
    for content in ("   ", "\u00a0\u00a0", " \t "):
        _assert_code(
            lambda c=content: fx.client.send_message(
                BOB, c, "cmid-aud-ws-1", ALICE, ALICE_PASSWORD
            ),
            INVALID_ARGUMENT,
        )


def test_content_exactly_one_code_point(fx):
    """Content of exactly 1 code point (including a composed NFC form): valid."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "a", "cmid-aud-1cp-1")
    assert m1["content"] == "a"
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "e\u0301", "cmid-aud-1cp-2")  # NFC -> é
    assert m2["content"] == "\u00e9"
    assert len(m2["content"]) == 1


def test_content_10000_code_points_astral(fx):
    """Exactly 10,000 code points counted in code points (not in UTF-8
    bytes): 10,000 astral emojis (4 bytes each) are accepted."""
    content = "\U0001f600" * 10_000
    sent = fx.send(ALICE, ALICE_PASSWORD, BOB, content, "cmid-aud-10k-1")
    assert len(sent["content"]) == 10_000
    _assert_code(
        lambda: fx.client.send_message(
            BOB, "\U0001f600" * 10_001, "cmid-aud-10k-2", ALICE, ALICE_PASSWORD
        ),
        INVALID_ARGUMENT,
    )


def test_client_message_id_128_chars_exact(fx):
    """client_message_id of exactly 128 characters: accepted and kept intact;
    129: INVALID_ARGUMENT."""
    cmid = "x" * 128
    sent = fx.send(ALICE, ALICE_PASSWORD, BOB, "long id", cmid)
    assert sent["client_message_id"] == cmid
    _assert_code(
        lambda: fx.client.send_message(BOB, "long id", "x" * 129, ALICE, ALICE_PASSWORD),
        INVALID_ARGUMENT,
    )


# ---------------------------------------------------------------------------
# Idempotency (section 6)
# ---------------------------------------------------------------------------


def test_idempotence_recipient_case_insensitive(fx):
    """Same client_message_id + same content with the recipient in a different
    case (BOB then bob): the same message is returned (recipient normalized)."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, "BOB", "salut", "cmid-aud-rc-1")
    m2 = fx.send(ALICE, ALICE_PASSWORD, "bob", "salut", "cmid-aud-rc-1")
    assert m2["message_id"] == m1["message_id"]
    assert m2["recipient_username"] == BOB
    # a content variant with the recipient in a different case remains
    # an idempotency conflict (MESSAGE_ALREADY_EXISTS)
    _assert_code(
        lambda: fx.client.send_message("BOB", "autre", "cmid-aud-rc-1", ALICE, ALICE_PASSWORD),
        MESSAGE_ALREADY_EXISTS,
    )


def test_idempotence_replay_after_deactivate_reactivate(fx):
    """Replaying the same client_message_id after a deactivation /
    reactivation cycle of the recipient returns the already-created message."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "salut", "cmid-aud-replay-1")
    fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    fx.client.reactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    m2 = fx.client.send_message(BOB, "salut", "cmid-aud-replay-1", ALICE, ALICE_PASSWORD)
    assert m2["message_id"] == m1["message_id"]
    assert m2["content"] == "salut"


def test_idempotence_nfc_equivalent_content(fx):
    """Two NFC-equivalent Unicode forms (e+accent vs é) with the same
    client_message_id: same message (content is compared after NFC)."""
    first = fx.send(ALICE, ALICE_PASSWORD, BOB, "e\u0301tude", "cmid-aud-nfc-1")
    second = fx.send(ALICE, ALICE_PASSWORD, BOB, "\u00e9tude", "cmid-aud-nfc-1")
    assert second["message_id"] == first["message_id"]
    assert second["content"] == "\u00e9tude"


# ---------------------------------------------------------------------------
# Reply states (sections 10, 12)
# ---------------------------------------------------------------------------


def test_state_mark_after_read_then_new_message_read(fx):
    """no_reply marking on a READ message, then a new message read:
    the state goes back to needs_reply (the marking is cancelled by the new
    message)."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question 1", "cmid-aud-st-1")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "needs_reply"

    marked = fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    assert marked["no_reply_for_message_id"] == m1["message_id"]
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "no_reply_needed"

    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question 2", "cmid-aud-st-2")
    # new unread message: not needs_reply
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "no_reply_needed"
    fx.client.read_message(m2["message_id"], BOB, BOB_PASSWORD)
    # the marking does not cover m2 -> needs_reply
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "needs_reply"


def test_state_sender_send_after_marking(fx):
    """After a no_reply marking by the recipient, a new send from the
    sender cancels the marking: no_reply_needed while the new message is
    unread, then needs_reply after reading."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question", "cmid-aud-st-3")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "no_reply_needed"

    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "suite", "cmid-aud-st-4")
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "no_reply_needed"
    fx.client.read_message(m2["message_id"], BOB, BOB_PASSWORD)
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "needs_reply"


def test_state_sent_only_never_needs_reply(fx):
    """A conversation where the agent only has SENT messages: never
    needs_reply for it, never listed in its notifications — even after
    the other agent has read and must reply."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "bonjour", "cmid-aud-so-1")
    assert _conv(fx, ALICE, ALICE_PASSWORD, BOB)["reply_status"] == "no_reply_needed"
    notif = fx.client.get_notifications(ALICE, ALICE_PASSWORD)
    assert notif["needs_reply"] == []
    assert notif["unread_by_sender"] == {}

    # bob reads the message: bob must reply, but alice is never listed
    m = fx.client.get_messages(BOB, BOB_PASSWORD)["messages"][0]
    fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)
    assert len(fx.client.get_notifications(BOB, BOB_PASSWORD)["needs_reply"]) == 1
    notif = fx.client.get_notifications(ALICE, ALICE_PASSWORD)
    assert notif["needs_reply"] == []
    assert notif["unread_by_sender"] == {}


# ---------------------------------------------------------------------------
# Stable pagination (section 9)
# ---------------------------------------------------------------------------


def test_pagination_limit_100_single_full_page(fx):
    """Exactly 100 messages: a single full page, no cursor, no
    loss and no duplicates."""
    for i in range(100):
        fx.send(ALICE, ALICE_PASSWORD, BOB, f"m-{i:03d}", f"cmid-aud-100-{i:03d}")
    page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=100)
    assert len(page["messages"]) == 100
    assert page["next_cursor"] is None
    ids = [m["message_id"] for m in page["messages"]]
    assert len(set(ids)) == 100


def test_pagination_limit_100_multi_page_no_dupes(fx):
    """150 messages paginated by 100: two pages, 150 unique messages."""
    for i in range(150):
        fx.send(ALICE, ALICE_PASSWORD, BOB, f"m-{i:03d}", f"cmid-aud-150-{i:03d}")
    seen = []
    cursor = None
    while True:
        page = fx.client.get_messages(BOB, BOB_PASSWORD, limit=100, cursor=cursor)
        seen.extend(m["message_id"] for m in page["messages"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 150
    assert len(set(seen)) == 150


def test_cursor_reused_with_different_limit(fx):
    """A cursor can be reused with a different limit: only the
    sorting/filters are bound, not the page size."""
    for i in range(5):
        fx.send(ALICE, ALICE_PASSWORD, BOB, f"m{i}", f"cmid-aud-cl-{i}")
    page1 = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    assert len(page1["messages"]) == 2
    page2 = fx.client.get_messages(BOB, BOB_PASSWORD, limit=100, cursor=page1["next_cursor"])
    assert len(page2["messages"]) == 3
    assert page2["next_cursor"] is None
    seen = [m["message_id"] for m in page1["messages"] + page2["messages"]]
    assert len(set(seen)) == 5


def test_cursor_conversation_reused_with_different_limit(fx):
    """Same for get_conversation (ascending sort)."""
    for i in range(3):
        fx.send(ALICE, ALICE_PASSWORD, BOB, f"m{i}", f"cmid-aud-cc-{i}")
    conv1 = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD, limit=2)
    assert len(conv1["messages"]) == 2
    conv2 = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD, limit=100, cursor=conv1["next_cursor"])
    assert len(conv2["messages"]) == 1
    assert conv2["next_cursor"] is None
    seen = [m["message_id"] for m in conv1["messages"] + conv2["messages"]]
    assert len(set(seen)) == 3


def test_cursor_get_conversation_reused_in_get_messages_rejected(fx):
    """A get_conversation cursor reused in get_messages (another
    command, another sort): INVALID_ARGUMENT."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-aud-xc-1")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-aud-xc-2")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD, limit=1)
    assert conv["next_cursor"] is not None
    _assert_code(
        lambda: fx.client.get_messages(BOB, BOB_PASSWORD, cursor=conv["next_cursor"]),
        INVALID_ARGUMENT,
    )


def test_pagination_get_messages_combined_filters(fx):
    """Pagination with combined status + sender filters: each page only
    contains conforming messages, with no duplicates or loss."""
    _create_carol(fx)
    sent_a = []
    for i in range(5):
        sent_a.append(fx.send(ALICE, ALICE_PASSWORD, BOB, f"a{i}", f"cmid-aud-cf-a{i}"))
    for i in range(3):
        fx.client.send_message(BOB, f"c{i}", f"cmid-aud-cf-c{i}", CAROL, CAROL_PASSWORD)
    # bob reads a1 and a2: only a3..a5 remain unread for alice
    fx.client.read_message(sent_a[1]["message_id"], BOB, BOB_PASSWORD)
    fx.client.read_message(sent_a[2]["message_id"], BOB, BOB_PASSWORD)

    seen = []
    cursor = None
    while True:
        page = fx.client.get_messages(
            BOB, BOB_PASSWORD, status="unread", sender_username="alice", limit=2, cursor=cursor
        )
        for m in page["messages"]:
            assert m["status"] == "unread"
            assert m["sender_username"] == ALICE
            seen.append(m["message_id"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 3
    assert len(set(seen)) == 3
    expected = {sent_a[3]["message_id"], sent_a[4]["message_id"], sent_a[0]["message_id"]}
    assert set(seen) == expected


def test_notifications_unread_by_sender_frozen_across_pages(fx):
    """unread_by_sender is frozen at the snapshot boundary: page 2 returns
    exactly the same counts as page 1, even if the real state changed
    between the two pages."""
    _create_carol(fx)
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un1", "cmid-aud-fz-1")
    m2 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un2", "cmid-aud-fz-2")
    m3 = fx.send(ALICE, ALICE_PASSWORD, BOB, "lu", "cmid-aud-fz-3")
    c1 = fx.client.send_message(BOB, "de carol", "cmid-aud-fz-4", CAROL, CAROL_PASSWORD)
    # two needs_reply conversations: carol (last received, most recent)
    # then alice; m1/m2 remain unread for bob
    fx.client.read_message(m3["message_id"], BOB, BOB_PASSWORD)
    fx.client.read_message(c1["message_id"], BOB, BOB_PASSWORD)

    page1 = fx.client.get_notifications(BOB, BOB_PASSWORD, limit=1)
    assert page1["next_cursor"] is not None
    assert page1["unread_by_sender"] == {ALICE: 2}
    assert [i["other_username"] for i in page1["needs_reply"]] == [CAROL]

    # real state change between the two pages: bob reads m1
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert fx.client.get_notifications(BOB, BOB_PASSWORD)["unread_by_sender"] == {ALICE: 1}

    page2 = fx.client.get_notifications(BOB, BOB_PASSWORD, limit=1, cursor=page1["next_cursor"])
    assert page2["unread_by_sender"] == page1["unread_by_sender"] == {ALICE: 2}
    assert [i["other_username"] for i in page2["needs_reply"]] == [ALICE]
    # the alice conversation stays listed needs_reply (frozen at the boundary)
    assert page2["needs_reply"][0]["unread_count"] == 2
    assert page2["needs_reply"][0]["conversation_id"] == m3["conversation_id"]


# ---------------------------------------------------------------------------
# Persistence (sections 1, 15)
# ---------------------------------------------------------------------------


def test_restart_bidirectional_conversation_same_id(fx):
    """After a restart, a bidirectional conversation still exists,
    keeps its identifier and its messages in both directions."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "a->b", "cmid-aud-pr-1")
    m2 = fx.send(BOB, BOB_PASSWORD, ALICE, "b->a", "cmid-aud-pr-2")
    conv_id = m1["conversation_id"]
    assert m2["conversation_id"] == conv_id

    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        c2 = server2.client
        conv_bob = c2.get_conversation(ALICE, BOB, BOB_PASSWORD)
        assert conv_bob["conversation_id"] == conv_id
        assert [m["message_id"] for m in conv_bob["messages"]] == [
            m1["message_id"],
            m2["message_id"],
        ]
        conv_alice = c2.get_conversation(BOB, ALICE, ALICE_PASSWORD)
        assert conv_alice["conversation_id"] == conv_id
        assert len(conv_alice["messages"]) == 2
        assert {m["sender_username"] for m in conv_alice["messages"]} == {ALICE, BOB}
    finally:
        server2.stop()


def test_restart_preserves_reply_and_no_reply_states(fx):
    """Reply states (including a no_reply marking) survive the
    restart, on both sides of the conversation."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question", "cmid-aud-pr-3")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "needs_reply"
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    # alice has received nothing: her state stays no_reply_needed
    assert _conv(fx, ALICE, ALICE_PASSWORD, BOB)["reply_status"] == "no_reply_needed"

    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        c2 = server2.client
        # marking preserved: bob is not needs_reply after restart
        assert c2.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "no_reply_needed"
        assert c2.get_notifications(BOB, BOB_PASSWORD)["needs_reply"] == []
        assert c2.get_conversation(BOB, ALICE, ALICE_PASSWORD)["reply_status"] == "no_reply_needed"
        # the marking is bound to m1: reading m1 (old message) changes nothing
        c2.read_message(m1["message_id"], BOB, BOB_PASSWORD)
        assert c2.get_conversation(ALICE, BOB, BOB_PASSWORD)["reply_status"] == "no_reply_needed"
    finally:
        server2.stop()


def test_restart_preserves_unread_by_sender(fx):
    """The unread_by_sender counters survive the restart."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un1", "cmid-aud-pr-4")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un2", "cmid-aud-pr-5")
    fx.send(BOB, BOB_PASSWORD, ALICE, "reponse", "cmid-aud-pr-6")
    assert fx.client.get_notifications(BOB, BOB_PASSWORD)["unread_by_sender"] == {ALICE: 2}
    assert fx.client.get_notifications(ALICE, ALICE_PASSWORD)["unread_by_sender"] == {BOB: 1}

    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        c2 = server2.client
        assert c2.get_notifications(BOB, BOB_PASSWORD)["unread_by_sender"] == {ALICE: 2}
        assert c2.get_notifications(ALICE, ALICE_PASSWORD)["unread_by_sender"] == {BOB: 1}
    finally:
        server2.stop()


# ---------------------------------------------------------------------------
# Errors (sections 5, 7)
# ---------------------------------------------------------------------------


def test_read_message_third_party_other_conversation(fx):
    """An agent cannot read a message from another pair's conversation,
    even if it knows one of the participants: MESSAGE_NOT_FOUND
    (no existence leak)."""
    _create_carol(fx)
    m = fx.client.send_message(BOB, "secret carol->bob", "cmid-aud-3p-1", CAROL, CAROL_PASSWORD)
    # alice is in conversation with bob, but not with carol: the message
    # carol->bob remains invisible to her
    _assert_code(
        lambda: fx.client.read_message(m["message_id"], ALICE, ALICE_PASSWORD),
        MESSAGE_NOT_FOUND,
    )
    # and bob cannot read a message for which he is neither the sender nor
    # the recipient (message alice->carol)
    m2 = fx.client.send_message(CAROL, "secret alice->carol", "cmid-aud-3p-2", ALICE, ALICE_PASSWORD)
    _assert_code(
        lambda: fx.client.read_message(m2["message_id"], BOB, BOB_PASSWORD),
        MESSAGE_NOT_FOUND,
    )


def test_get_conversation_other_username_case_variant(fx):
    """other_username is normalized: a different case works and returns the
    normalized name; an unknown user in a different case still yields
    CONVERSATION_NOT_FOUND."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-aud-cs-1")
    conv = fx.client.get_conversation("BOB", ALICE, ALICE_PASSWORD)
    assert conv["other_username"] == BOB
    conv2 = fx.client.get_conversation("ALICE", BOB, BOB_PASSWORD)
    assert conv2["other_username"] == ALICE
    assert conv["conversation_id"] == conv2["conversation_id"]
    _assert_code(
        lambda: fx.client.get_conversation("GHOST", ALICE, ALICE_PASSWORD),
        CONVERSATION_NOT_FOUND,
    )


def test_create_agent_spaces_only_password_valid(fx):
    """A password made only of spaces (12 printable characters, spaces are
    allowed by the specification) is valid: the account is created and
    authenticates."""
    data = fx.client.create_agent("dave",  " " * 12, "Test agent",  ORG_NAME, ORG_PASSWORD)
    assert data == {"username": "dave", "status": "active",
                    "description": "Test agent",
                    "organization_name": ORG_NAME,
                    "can_see_org_agents": False,
                    "principal_type": "agent"}
    assert fx.client.get_messages("dave", " " * 12) == {"messages": [], "next_cursor": None}


def test_create_agent_tab_password_rejected(fx):
    """A password containing a tab (control character): INVALID_ARGUMENT,
    even if the length is sufficient."""
    _assert_code(
        lambda: fx.client.create_agent("erin",  "abcdefghijkl\t", "Test agent",  ORG_NAME, ORG_PASSWORD),
        INVALID_ARGUMENT,
    )
    _assert_code(
        lambda: fx.client.create_agent("frank",  "\t" * 12, "Test agent",  ORG_NAME, ORG_PASSWORD),
        INVALID_ARGUMENT,
    )


def test_uppercase_uuid_read_and_mark(fx):
    """UUIDs are accepted case-insensitively in read_message and
    mark_conversation_no_reply."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-aud-u-1")
    read = fx.client.read_message(m1["message_id"].upper(), BOB, BOB_PASSWORD)
    assert read["message_id"] == m1["message_id"]
    marked = fx.client.mark_conversation_no_reply(
        m1["conversation_id"].upper(), BOB, BOB_PASSWORD
    )
    assert marked["conversation_id"] == m1["conversation_id"]


# ---------------------------------------------------------------------------
# Side-effect-free behaviors (sections 7, 11) and isolation
# ---------------------------------------------------------------------------


def test_get_messages_side_effect_free(fx):
    """get_messages (with filters and pagination) modifies no state:
    conversation and notifications are identical before/after."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-aud-sf-1")
    fx.send(BOB, BOB_PASSWORD, ALICE, "deux", "cmid-aud-sf-2")

    conv_before = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    notif_before = fx.client.get_notifications(BOB, BOB_PASSWORD)

    fx.client.get_messages(BOB, BOB_PASSWORD, status="unread", limit=1)
    fx.client.get_messages(BOB, BOB_PASSWORD, status="read")
    fx.client.get_messages(BOB, BOB_PASSWORD, sender_username="alice")
    fx.client.get_messages(BOB, BOB_PASSWORD, limit=1)

    conv_after = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    notif_after = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert conv_after == conv_before
    assert notif_after == notif_before


def test_get_notifications_side_effect_free(fx):
    """get_notifications modifies neither the messages nor the reply states."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question", "cmid-aud-sn-1")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "needs_reply"

    before = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    n1 = fx.client.get_notifications(BOB, BOB_PASSWORD)
    n2 = fx.client.get_notifications(BOB, BOB_PASSWORD)
    assert n1 == n2
    assert n1["unread_by_sender"] == {}
    assert [i["other_username"] for i in n1["needs_reply"]] == [ALICE]
    after = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert after == before
    # the message stays unread for the recipient
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert inbox["messages"][0]["status"] == "read"  # read before, unchanged


def test_send_message_does_not_touch_other_conversation_state(fx):
    """Sending a message in a conversation does not modify the sender's
    reply state in ANOTHER conversation."""
    _create_carol(fx)
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "question", "cmid-aud-oc-1")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "needs_reply"

    # bob sends to carol: the bob<->alice state must stay needs_reply
    fx.client.send_message(CAROL, "pour carol", "cmid-aud-oc-2", BOB, BOB_PASSWORD)
    assert _conv(fx, BOB, BOB_PASSWORD, ALICE)["reply_status"] == "needs_reply"
    # and the bob<->carol conversation is distinct
    conv_bc = fx.client.get_conversation(CAROL, BOB, BOB_PASSWORD)
    assert conv_bc["conversation_id"] != m1["conversation_id"]


def test_two_pairs_distinct_conversations_and_isolated_content(fx):
    """Two pairs sharing an agent (alice-bob and bob-carol) have two
    distinct conversations, each containing only its own messages."""
    _create_carol(fx)
    m_ab = fx.send(ALICE, ALICE_PASSWORD, BOB, "alice->bob", "cmid-aud-2p-1")
    m_bc = fx.send(BOB, BOB_PASSWORD, CAROL, "bob->carol", "cmid-aud-2p-2")
    assert m_ab["conversation_id"] != m_bc["conversation_id"]

    conv_ab = fx.client.get_conversation(BOB, ALICE, ALICE_PASSWORD)
    conv_bc = fx.client.get_conversation(CAROL, BOB, BOB_PASSWORD)
    assert [m["message_id"] for m in conv_ab["messages"]] == [m_ab["message_id"]]
    assert [m["message_id"] for m in conv_bc["messages"]] == [m_bc["message_id"]]
    # the isolation is reciprocal: alice never sees the bob-carol
    # conversation, even when requesting it via its other participant
    _assert_code(
        lambda: fx.client.get_conversation(CAROL, ALICE, ALICE_PASSWORD),
        CONVERSATION_NOT_FOUND,
    )


# ---------------------------------------------------------------------------
# Explicit spec contract: optional parameters set to null
# ---------------------------------------------------------------------------


def test_limit_null_defaults_to_50(fx):
    """Section 5: "Optional parameters are always present with null when
    unused. Default values are ... limit=50". A conforming client therefore
    sends limit=null; the service must apply the default 50 and return the
    page normally.
    (OBSERVED BUG: INTERNAL_ERROR instead of the default value.)"""
    for i in range(3):
        fx.send(ALICE, ALICE_PASSWORD, BOB, f"m{i}", f"cmid-aud-nl-{i}")
    data = fx.client.get_messages(BOB, BOB_PASSWORD, limit=None)
    assert len(data["messages"]) == 3
    assert data["next_cursor"] is None


def test_limit_null_defaults_to_50_conversation(fx):
    """Same contract for get_conversation: limit=null -> 50."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-aud-nlc-1")
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD, limit=None)
    assert len(conv["messages"]) == 1


def test_limit_null_defaults_to_50_notifications(fx):
    """Same contract for get_notifications: limit=null -> 50."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-aud-nln-1")
    data = fx.client.get_notifications(BOB, BOB_PASSWORD, limit=None)
    assert set(data.keys()) == {"unread_by_sender", "needs_reply", "next_cursor"}
