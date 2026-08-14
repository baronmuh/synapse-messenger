"""Atomic tests of the validation module: error paths and edge cases
not covered by the integration tests."""

from __future__ import annotations

import json
import re

import pytest

from synapse.errors import INVALID_ARGUMENT, ApiError
from synapse.validation import (
    is_valid_username,
    normalize_content,
    normalize_uuid,
    normalize_username,
    now_utc,
    now_utc_offset,
    parse_json_request,
    validate_client_message_id,
    validate_envelope,
    validate_password,
)


def test_normalize_username_non_string_rejected():
    for bad in (None, 123, ["a"], b"abc"):
        with pytest.raises(ApiError) as exc:
            normalize_username(bad)
        assert exc.value.code == INVALID_ARGUMENT


def test_is_valid_username():
    assert is_valid_username("alice")
    assert is_valid_username("Alice-1")
    assert not is_valid_username("")
    assert not is_valid_username("ab")
    assert not is_valid_username("with space")
    assert not is_valid_username("accentué")
    assert not is_valid_username(None)


def test_validate_password_non_string_rejected():
    with pytest.raises(ApiError) as exc:
        validate_password(12345)
    assert exc.value.code == INVALID_ARGUMENT


def test_normalize_content_non_string_rejected():
    for bad in (None, 42, ["texte"], {"a": 1}):
        with pytest.raises(ApiError) as exc:
            normalize_content(bad)
        assert exc.value.code == INVALID_ARGUMENT


def test_validate_client_message_id_non_string_rejected():
    with pytest.raises(ApiError) as exc:
        validate_client_message_id(99)
    assert exc.value.code == INVALID_ARGUMENT


def test_normalize_uuid_non_string_rejected():
    with pytest.raises(ApiError) as exc:
        normalize_uuid(123, "test")
    assert exc.value.code == INVALID_ARGUMENT


def test_normalize_uuid_accepts_upper_and_lower():
    value = "ABCDEF00-1234-4123-8ABC-DEF012345678"
    assert normalize_uuid(value) == value.lower()


def test_normalize_uuid_rejects_non_v4():
    # UUID v1
    with pytest.raises(ApiError) as exc:
        normalize_uuid("550e8400-e29b-11d4-a716-446655440000")
    assert exc.value.code == INVALID_ARGUMENT
    # UUID v5
    with pytest.raises(ApiError) as exc:
        normalize_uuid("550e8400-e29b-51d4-a716-446655440000")
    assert exc.value.code == INVALID_ARGUMENT
    # UUID nil (v0)
    with pytest.raises(ApiError) as exc:
        normalize_uuid("00000000-0000-0000-0000-000000000000")
    assert exc.value.code == INVALID_ARGUMENT


def test_now_utc_format():
    value = now_utc()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value)
    # l'ordre lexicographique == l'ordre chronologique (format fixe)
    assert now_utc() >= value


def test_now_utc_offset():
    """The 900 s offset is strictly in the past; the null offset is
    close to the current instant (wide tolerance to avoid any boundary
    of a millisecond); the format is exact."""
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc)
    ts = now_utc_offset(0)
    after = datetime.now(timezone.utc)
    parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    assert before - timedelta(seconds=1) <= parsed <= after + timedelta(seconds=1)
    # the 900 s lookback dominates any measurement drift between the two calls
    assert now_utc_offset(900) < now_utc()
    # exact format expected by the specification
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", ts)


def test_normalize_organization_name_direct():
    """Appel direct : types invalides, longueurs, jeu de characters."""
    from synapse.validation import normalize_organization_name
    assert normalize_organization_name("Org-Dev".upper()) == "org-dev"  # minuscules
    for bad in (42, None, "ab", "x" * 65, "avec espace", "Accenté"):
        with pytest.raises(ApiError) as exc:
            normalize_organization_name(bad)
        assert exc.value.code == INVALID_ARGUMENT


def test_validate_bool_direct():
    """Direct call: only strict JSON booleans are accepted."""
    from synapse.validation import validate_bool
    assert validate_bool(True) is True
    assert validate_bool(False) is False
    assert validate_bool(None) is False  # default (opt-in)
    for bad in (1, 0, "true", "false", "oui"):
        with pytest.raises(ApiError) as exc:
            validate_bool(bad)
        assert exc.value.code == INVALID_ARGUMENT


def test_normalize_description_direct_non_string():
    """Direct validator call: a non-string type is refused (the
    envelope validation already intercepts it for JSON, but the validator
    stays defensive)."""
    from synapse.validation import normalize_description
    with pytest.raises(ApiError) as exc:
        normalize_description(42)
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiError) as exc:
        normalize_description(None)
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiError) as exc:
        normalize_description("a\u0000b")  # null character
    assert exc.value.code == INVALID_ARGUMENT


def test_required_parameter_missing():
    """A missing required parameter causes INVALID_ARGUMENT."""
    with pytest.raises(ApiError) as exc:
        validate_envelope({
            "api_version": "v2",
            "command": "get_messages",
            "parameters": {"my_password_auth": "x" * 12},
        })
    assert exc.value.code == INVALID_ARGUMENT


def test_envelope_request_must_be_object():
    for bad in ([], "texte", 42, None, True):
        with pytest.raises(ApiError) as exc:
            validate_envelope(bad)
        assert exc.value.code == INVALID_ARGUMENT


def test_parameters_must_be_object():
    for bad in ([], "texte", 42):
        with pytest.raises(ApiError) as exc:
            validate_envelope({
                "api_version": "v2",
                "command": "get_notifications",
                "parameters": bad,
            })
        assert exc.value.code == INVALID_ARGUMENT


def test_command_must_be_string():
    with pytest.raises(ApiError) as exc:
        validate_envelope({
            "api_version": "v2",
            "command": 42,
            "parameters": {},
        })
    assert exc.value.code == INVALID_ARGUMENT


def test_api_version_must_be_string():
    with pytest.raises(ApiError) as exc:
        validate_envelope({
            "api_version": 1,
            "command": "get_notifications",
            "parameters": {},
        })
    assert exc.value.code == INVALID_ARGUMENT


def test_parse_json_invalid_utf8():
    with pytest.raises(ApiError) as exc:
        parse_json_request(b"\xff\xfe\x00 invalid")
    assert exc.value.code == INVALID_ARGUMENT


def test_parse_json_truncated():
    with pytest.raises(ApiError) as exc:
        parse_json_request(b'{"api_version": "v2"')
    assert exc.value.code == INVALID_ARGUMENT


def test_parse_json_valid_unicode():
    data = parse_json_request('{"clé": "valeur é"}'.encode("utf-8"))
    assert data == {"clé": "valeur é"}


def test_limit_wrong_type_rejected(fx, raw_socket_client):
    """float or string limit: INVALID_ARGUMENT (wrong type)."""
    for bad in (50.0, "50", [50]):
        resp = raw_socket_client(
            json.dumps({
                "api_version": "v2",
                "command": "get_notifications",
                "parameters": {"my_name_auth": "alice", "my_password_auth": "x" * 12,
                               "limit": bad, "cursor": None},
            }) + "\n"
        )
        assert resp["error"]["code"] == INVALID_ARGUMENT


def test_status_wrong_case_rejected(fx, raw_socket_client):
    resp = raw_socket_client(
        json.dumps({
            "api_version": "v2",
            "command": "get_messages",
            "parameters": {"my_name_auth": "alice", "my_password_auth": "x" * 12,
                           "status": "READ", "sender_username": None,
                           "conversation_id": None, "limit": 50, "cursor": None},
        }) + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT
