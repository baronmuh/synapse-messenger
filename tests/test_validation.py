"""Tests of strict validation of the envelope, parameters and formats.

Covers section 2 (envelope, field casing, 1 MiB size) and the
format rules of section 3 (usernames, passwords) and 6
(content, client_message_id).
"""

from __future__ import annotations

import json
import socket

import pytest

from synapse.client import ApiClientError
from synapse.errors import INVALID_ARGUMENT, UNKNOWN_COMMAND, ApiError
from synapse.validation import (
    MAX_CONTENT_LEN,
    normalize_content,
    normalize_username,
    validate_client_message_id,
    validate_password,
)

from .conftest import ALICE, ALICE_PASSWORD


def assert_invalid(fn, *args, **kwargs) -> ApiError:
    """Runs ``fn`` and verifies it raises ``ApiError(INVALID_ARGUMENT)``."""
    with pytest.raises(ApiError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == INVALID_ARGUMENT
    return exc.value

# ---------------------------------------------------------------------------
# Enveloppe
# ---------------------------------------------------------------------------


def test_envelope_exact_keys_required(fx, raw_socket_client):
    resp = raw_socket_client(json.dumps({"api_version": "v2", "command": "get_notifications"}) + "\n")
    assert resp["success"] is False
    assert resp["error"]["code"] == INVALID_ARGUMENT  # parameters absent


def test_envelope_unknown_field(fx, raw_socket_client):
    resp = raw_socket_client(
        json.dumps(
            {
                "api_version": "v2",
                "command": "get_notifications",
                "parameters": {},
                "extra": 1,
            }
        )
        + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_envelope_bad_api_version(fx, raw_socket_client):
    resp = raw_socket_client(
        json.dumps(
            {"api_version": "v2", "command": "get_notifications", "parameters": {}}
        )
        + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_unknown_command(fx, raw_socket_client):
    resp = raw_socket_client(
        json.dumps({"api_version": "v2", "command": "delete_everything", "parameters": {}}) + "\n"
    )
    assert resp["error"]["code"] == UNKNOWN_COMMAND


def test_unknown_parameter_rejected(fx, raw_socket_client):
    resp = raw_socket_client(
        json.dumps(
            {
                "api_version": "v2",
                "command": "get_notifications",
                "parameters": {"my_name_auth": "alice", "my_password_auth": "x" * 12, "bogus": 1},
            }
        )
        + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_parameter_type_rejected(fx, raw_socket_client):
    resp = raw_socket_client(
        json.dumps(
            {
                "api_version": "v2",
                "command": "get_notifications",
                "parameters": {"my_name_auth": 42, "my_password_auth": "x" * 12},
            }
        )
        + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_bool_not_accepted_as_int(fx, raw_socket_client):
    resp = raw_socket_client(
        json.dumps(
            {
                "api_version": "v2",
                "command": "get_notifications",
                "parameters": {
                    "my_name_auth": "alice",
                    "my_password_auth": "x" * 12,
                    "limit": True,
                    "cursor": None,
                },
            }
        )
        + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_missing_optional_parameter_is_invalid(fx, raw_socket_client):
    """Any missing field (even optional) causes INVALID_ARGUMENT."""
    resp = raw_socket_client(
        json.dumps(
            {
                "api_version": "v2",
                "command": "get_messages",
                "parameters": {"my_name_auth": "alice", "my_password_auth": "x" * 12},
            }
        )
        + "\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_duplicate_json_key_rejected(fx, raw_socket_client):
    resp = raw_socket_client(
        '{"api_version":"v1","command":"get_notifications","parameters":'
        '{"my_name_auth":"alice","my_name_auth":"bob","my_password_auth":"xxxxxxxxxxxx"}}\n'
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_invalid_json(fx, raw_socket_client):
    resp = raw_socket_client("{pas du json}\n")
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_multiple_requests_one_connection(fx):
    """The server accepts several requests on the same connection."""
    import json as json_mod
    import socket as sockmod

    s = sockmod.socket(sockmod.AF_UNIX, sockmod.SOCK_STREAM)
    try:
        s.connect(fx.config.socket_path)
        for i in range(3):
            req = json_mod.dumps({
                "api_version": "v2",
                "command": "get_messages",
                "parameters": {"my_name_auth": ALICE, "my_password_auth": ALICE_PASSWORD,
                               "status": None, "sender_username": None,
                               "conversation_id": None, "limit": 50, "cursor": None},
            }).encode() + b"\n"
            s.sendall(req)
            buf = b""
            while not buf.endswith(b"\n"):
                buf += s.recv(65536)
            resp = json_mod.loads(buf)
            assert resp["success"] is True
    finally:
        s.close()


def test_oversized_request_rejected_before_auth(fx, raw_socket_client):
    """A request > 1 MiB is rejected INVALID_ARGUMENT, even without credentials."""
    huge = "A" * (1024 * 1024 + 10)
    envelope = {"api_version": "v2", "command": "send_message", "parameters": {"message": huge}}
    line = json.dumps(envelope, ensure_ascii=False).encode("utf-8") + b"\n"
    assert len(line) > 1024 * 1024
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(fx.config.socket_path)
        sock.sendall(line)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    resp = json.loads(b"".join(chunks))
    assert resp["error"]["code"] == INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# Usernames
# ---------------------------------------------------------------------------


def test_username_normalization_and_format():
    assert normalize_username("Alice") == "alice"
    assert normalize_username("agent-1_x") == "agent-1_x"


@pytest.mark.parametrize(
    "bad",
    [
        "ab",  # trop court
        "a" * 65,  # trop long
        "with space",
        "with\tcontrol",
        "accentuée",
        "emoji😀",
        "sla/sh",
        "",
    ],
)
def test_username_rejected(bad):
    assert_invalid(normalize_username, bad)


@pytest.mark.parametrize("good", ["abc", "a-9_Z", "x" * 64, "ABC-1"])
def test_username_accepted(good):
    result = normalize_username(good)
    assert result == good.lower()
    assert 3 <= len(result) <= 64


# ---------------------------------------------------------------------------
# Mots de passe
# ---------------------------------------------------------------------------


def test_password_min_length():
    assert_invalid(validate_password, "short")


def test_password_spaces_allowed():
    assert validate_password("password with spaces") == "password with spaces"


def test_password_never_normalized():
    pw = "abcdefghij\u00e9\u0301"  # 12 characters dont e + accent combinant
    assert validate_password(pw) == pw  # no normalization applied


@pytest.mark.parametrize(
    "bad",
    [
        "abcdefghijkl\n",  # line feed
        "abcdefghijkl\t",  # tab (control)
        "abcdefghijkl\x00",  # NUL
        "abcdefghijkl\x7f",  # DEL
        "\u2028abcdefghijk",  # line separator
    ],
)
def test_password_control_chars_rejected(bad):
    assert_invalid(validate_password, bad)


# ---------------------------------------------------------------------------
# Message content
# ---------------------------------------------------------------------------


def test_content_normalization_nfc_trim():
    assert normalize_content("  Bonjour  ") == "Bonjour"
    # e + combining accent -> é (NFC)
    assert normalize_content("e\u0301tude") == "\u00e9tude"


def test_content_too_long_rejected():
    assert_invalid(normalize_content, "x" * (MAX_CONTENT_LEN + 1))


def test_content_empty_after_trim_rejected():
    assert_invalid(normalize_content, "   \t  ")


def test_content_control_char_rejected():
    assert_invalid(normalize_content, "a\x00b")
    assert_invalid(normalize_content, "ligne1\nligne2")


def test_content_exact_max_ok():
    assert len(normalize_content("x" * MAX_CONTENT_LEN)) == MAX_CONTENT_LEN


def test_content_surrogate_rejected():
    assert_invalid(normalize_content, "\ud800")


# ---------------------------------------------------------------------------
# client_message_id
# ---------------------------------------------------------------------------


def test_client_message_id_valid_charset():
    assert validate_client_message_id("aZ09._:-") == "aZ09._:-"


def test_client_message_id_invalid_charset():
    assert_invalid(validate_client_message_id, "with space")
    assert_invalid(validate_client_message_id, "accentué")
    assert_invalid(validate_client_message_id, "")


# ---------------------------------------------------------------------------
# API integration: parameter validation over the socket
# ---------------------------------------------------------------------------


def test_send_message_validation_via_api(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("bob", "", "cmid-v", "alice", "x" * 12)
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("bob", "contenu", "bad id!", "alice", "x" * 12)
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message("bob", "contenu", "cmid-v", "alice", "mauvais")
    assert exc.value.code == "AUTH_FAILED"


def test_limit_range_enforced(fx):
    for bad_limit in (0, 101, -1):
        with pytest.raises(ApiClientError) as exc:
            # the validation error precedes authentication
            fx.client.get_messages("alice", "x" * 12, limit=bad_limit)
        assert exc.value.code == INVALID_ARGUMENT


def test_status_enum_enforced(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages("alice", "x" * 12, status="delivered")
    assert exc.value.code == INVALID_ARGUMENT


def test_invalid_uuid_filter_rejected(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages("alice", "x" * 12, conversation_id="not-a-uuid")
    assert exc.value.code == INVALID_ARGUMENT


def test_invalid_sender_filter_rejected(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_messages("alice", "x" * 12, sender_username="bad name!")
    assert exc.value.code == INVALID_ARGUMENT
