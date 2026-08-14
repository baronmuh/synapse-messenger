"""Regression guard: the authenticated read path must not re-fetch the
account row after authentication.

The dispatch path used to call ``accounts.get`` three times per read
command (once inside ``_authenticate``, once again in ``_dispatch``, and
once via ``_org_of`` even for non-audited commands). ``_authenticate`` now
returns the row it already fetched, and ``_dispatch`` reuses it — a read
must trigger exactly ONE account lookup. This test would fail if the
redundant fetch were reintroduced.
"""

from __future__ import annotations

import json

from synapse.store import accounts


def _raw_get_messages(fx) -> dict:
    payload = (
        json.dumps(
            {
                "api_version": "v2",
                "command": "get_messages",
                "parameters": {
                    "my_name_auth": "alice",
                    "my_password_auth": "motdepasse-alice-1",
                    "status": None,
                    "sender_username": None,
                    "conversation_id": None,
                    "limit": 50,
                    "cursor": None,
                },
            }
        )
        + "\n"
    )
    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(fx.config.socket_path)
        sock.sendall(payload.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        sock.close()


def test_get_messages_read_performs_single_account_lookup(fx):
    """A read command must authenticate with exactly one accounts.get.

    Guards the optimization that threads the account row from
    ``_authenticate`` through ``_dispatch`` (no redundant re-fetch, no
    eager ``_org_of`` for non-audited commands).
    """
    # ensure an authenticated baseline so the auth cache is warm
    _raw_get_messages(fx)

    real_get = accounts.get
    counter = {"n": 0}

    def counting_get(conn, username):
        counter["n"] += 1
        return real_get(conn, username)

    accounts.get = counting_get
    try:
        resp = _raw_get_messages(fx)
    finally:
        accounts.get = real_get

    assert resp["success"] is True
    assert counter["n"] == 1, (
        f"get_messages triggered {counter['n']} accounts.get calls; "
        "expected exactly 1 (the row fetched by _authenticate and reused "
        "by _dispatch). A redundant re-fetch has been reintroduced."
    )


def test_get_messages_response_unchanged(fx):
    """The read still returns messages normally (behavioral guard)."""
    # alice has no messages yet; the command must still succeed with an
    # empty mailbox rather than erroring after the row-reuse change.
    resp = _raw_get_messages(fx)
    assert resp["success"] is True
    assert resp["data"]["messages"] == []
    assert resp["data"]["next_cursor"] is None
