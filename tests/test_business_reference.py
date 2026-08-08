"""F4 — Business correlation: `business_reference` on messages.

Covers validation, persistence, retrieval, extended idempotency
and edge cases (absence, length, control characters, unknown
fields).
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import INVALID_ARGUMENT, MESSAGE_ALREADY_EXISTS

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, make_server


def test_send_with_business_reference_roundtrip(fx):
    sent = fx.send(
        ALICE, ALICE_PASSWORD, BOB, "Monthly report", "cmid-ref-1",
        business_reference="facture-2026-08",
    )
    assert sent["business_reference"] == "facture-2026-08"
    # visible in the recipient's conversation
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["messages"][0]["business_reference"] == "facture-2026-08"
    # and in the sender's messages
    msgs = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert msgs["messages"][0]["business_reference"] == "facture-2026-08"


def test_send_without_business_reference_is_null(fx):
    sent = fx.send(ALICE, ALICE_PASSWORD, BOB, "Without reference", "cmid-ref-2")
    assert sent["business_reference"] is None


def test_business_reference_normalized(fx):
    # NFC + trimming of leading/trailing spaces
    sent = fx.send(
        ALICE, ALICE_PASSWORD, BOB, "Message", "cmid-ref-3",
        business_reference="  opé-42  ",
    )
    assert sent["business_reference"] == "opé-42"


def test_business_reference_too_long_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.send(
            ALICE, ALICE_PASSWORD, BOB, "Message", "cmid-ref-4",
            business_reference="x" * 129,
        )
    assert exc.value.code == INVALID_ARGUMENT


def test_business_reference_control_characters_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.send(
            ALICE, ALICE_PASSWORD, BOB, "Message", "cmid-ref-5",
            business_reference="op\x00é",
        )
    assert exc.value.code == INVALID_ARGUMENT


def test_business_reference_empty_after_strip_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.send(
            ALICE, ALICE_PASSWORD, BOB, "Message", "cmid-ref-6",
            business_reference="   ",
        )
    assert exc.value.code == INVALID_ARGUMENT


def test_business_reference_wrong_type_invalid(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.send(
            ALICE, ALICE_PASSWORD, BOB, "Message", "cmid-ref-7",
            business_reference=42,
        )
    assert exc.value.code == INVALID_ARGUMENT


def test_idempotence_same_reference_returns_existing(fx):
    first = fx.send(
        ALICE, ALICE_PASSWORD, BOB, "Doublon", "cmid-ref-dup",
        business_reference="ref-identique",
    )
    second = fx.send(
        ALICE, ALICE_PASSWORD, BOB, "Doublon", "cmid-ref-dup",
        business_reference="ref-identique",
    )
    assert second["message_id"] == first["message_id"]


def test_idempotence_different_reference_conflicts(fx):
    fx.send(
        ALICE, ALICE_PASSWORD, BOB, "Doublon", "cmid-ref-dup2",
        business_reference="ref-a",
    )
    with pytest.raises(ApiClientError) as exc:
        fx.send(
            ALICE, ALICE_PASSWORD, BOB, "Doublon", "cmid-ref-dup2",
            business_reference="ref-b",
        )
    assert exc.value.code == MESSAGE_ALREADY_EXISTS


def test_idempotence_reference_absent_vs_present_conflicts(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "Doublon", "cmid-ref-dup3")
    with pytest.raises(ApiClientError) as exc:
        fx.send(
            ALICE, ALICE_PASSWORD, BOB, "Doublon", "cmid-ref-dup3",
            business_reference="ref-c",
        )
    assert exc.value.code == MESSAGE_ALREADY_EXISTS


def test_business_reference_persists_across_restart(fx):
    sent = fx.send(
        ALICE, ALICE_PASSWORD, BOB, "Persistant", "cmid-ref-persist",
        business_reference="op-7",
    )
    fx.server.stop()
    server2 = make_server(fx.config, org=False)
    try:
        msgs = server2.client.get_messages(BOB, BOB_PASSWORD)
        msg = next(m for m in msgs["messages"] if m["message_id"] == sent["message_id"])
        assert msg["business_reference"] == "op-7"
    finally:
        server2.stop()
