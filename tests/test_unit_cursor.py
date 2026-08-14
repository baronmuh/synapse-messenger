"""Atomic tests of pagination cursors: encoding, signature,
decoding and all error paths."""

from __future__ import annotations

import pytest

from synapse.cursor import (
    build_payload,
    decode_cursor,
    encode_cursor,
    validate_cursor_binding,
)
from synapse.errors import INVALID_ARGUMENT, ApiError

SECRET = b"k" * 32


def _payload(**over):
    base = dict(
        v=1,
        cmd="get_messages",
        user="alice",
        boundary="2026-01-01T00:00:00.000Z",
        sort="desc",
        filters={"status": None},
        last=["2026-01-01T00:00:00.000Z", "m-id"],
    )
    base.update(over)
    return base


def test_roundtrip():
    payload = _payload()
    cursor = encode_cursor(SECRET, payload)
    decoded = decode_cursor(SECRET, cursor)
    assert decoded == payload


def test_roundtrip_without_filters_and_last():
    payload = build_payload(command="get_messages", username="alice",
                            boundary="2026-01-01T00:00:00.000Z", sort="desc")
    cursor = encode_cursor(SECRET, payload)
    decoded = decode_cursor(SECRET, cursor)
    assert decoded == payload
    assert "filters" not in decoded and "last" not in decoded


def test_decode_wrong_key():
    cursor = encode_cursor(SECRET, _payload())
    with pytest.raises(ApiError) as exc:
        decode_cursor(b"x" * 32, cursor)
    assert exc.value.code == INVALID_ARGUMENT


def test_decode_garbage():
    for bad in ("", "no-dot", "!!!", "abc.def", "a.b.c", "   "):
        with pytest.raises(ApiError) as exc:
            decode_cursor(SECRET, bad)
        assert exc.value.code == INVALID_ARGUMENT


def test_decode_non_string():
    with pytest.raises(ApiError) as exc:
        decode_cursor(SECRET, 123)
    assert exc.value.code == INVALID_ARGUMENT


def test_decode_tampered_body():
    """A payload modified without re-signature is rejected."""
    cursor = encode_cursor(SECRET, _payload())
    body, sig = cursor.split(".")
    from synapse.cursor import _b64url, _unb64url
    modified = _b64url(_unb64url(body).replace(b'"desc"', b'"asc"'))
    with pytest.raises(ApiError) as exc:
        decode_cursor(SECRET, f"{modified}.{sig}")
    assert exc.value.code == INVALID_ARGUMENT


def test_decode_wrong_version():
    from synapse.cursor import _b64url
    import json
    payload = _payload()
    payload["v"] = 999
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    import hashlib
    import hmac
    sig = _b64url(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    with pytest.raises(ApiError) as exc:
        decode_cursor(SECRET, f"{body}.{sig}")
    assert exc.value.code == INVALID_ARGUMENT


def test_decode_non_dict_payload():
    from synapse.cursor import _b64url
    import hashlib
    import hmac
    body = _b64url(b'["liste"]')
    sig = _b64url(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    with pytest.raises(ApiError) as exc:
        decode_cursor(SECRET, f"{body}.{sig}")
    assert exc.value.code == INVALID_ARGUMENT


def test_unb64url_bad_padding():
    from synapse.cursor import _unb64url
    with pytest.raises(ApiError) as exc:
        _unb64url("!!!not-base64!!!")
    assert exc.value.code == INVALID_ARGUMENT


def test_validate_binding_ok():
    payload = _payload()
    validate_cursor_binding(payload, command="get_messages", username="alice",
                            sort="desc", filters={"status": None})


def test_validate_binding_sort_mismatch():
    payload = _payload()
    with pytest.raises(ApiError) as exc:
        validate_cursor_binding(payload, command="get_messages", username="alice",
                                sort="asc", filters={"status": None})
    assert exc.value.code == INVALID_ARGUMENT


def test_validate_binding_command_mismatch():
    payload = _payload()
    with pytest.raises(ApiError) as exc:
        validate_cursor_binding(payload, command="get_conversation", username="alice",
                                sort="desc", filters={"status": None})
    assert exc.value.code == INVALID_ARGUMENT


def test_validate_binding_user_mismatch():
    payload = _payload()
    with pytest.raises(ApiError) as exc:
        validate_cursor_binding(payload, command="get_messages", username="bob",
                                sort="desc", filters={"status": None})
    assert exc.value.code == INVALID_ARGUMENT


def test_validate_binding_filters_mismatch():
    payload = _payload()
    with pytest.raises(ApiError) as exc:
        validate_cursor_binding(payload, command="get_messages", username="alice",
                                sort="desc", filters={"status": "read"})
    assert exc.value.code == INVALID_ARGUMENT
