"""Regression guard: read_message must not acquire the global writer
lock (db.begin_immediate) unless it actually writes.

Before this optimization, _read_message wrapped EVERY read in
db.begin_immediate — the application-wide write lock — even when
nothing was written: sender reads (the sender never marks read_at) and
already-read messages (mark_read_conditional matches nothing). Under
mixed load every such read serialized against every send/mark for zero
writes, producing the heavy read_message p99 tail (baseline
t_af15796f: p50 7.6 ms vs p99 251 ms, x33).

Now only the recipient's first read writes. The audit of read_message
(F11) still opens exactly one write transaction per SUCCESSFUL
command, so the expected counts are:

  sender read / already-read read : 1 (mandatory audit only)
  recipient first read            : 2 (read_at mark + audit)
  refused read (404)              : 0 (no handler write, no audit)

These tests fail if the write lock is reintroduced on the pure-read
paths (counts back to 2), or if the transaction discipline changes.
"""

from __future__ import annotations

import json
import socket

from synapse import db

ORG_NAME = "root_org"
ORG_PASSWORD = "mot-de-passe-org-123"
ALICE = "alice"
ALICE_PASSWORD = "motdepasse-alice-1"
BOB = "bob"
BOB_PASSWORD = "motdepasse-bob-1"
CAROL = "carol"
CAROL_PASSWORD = "motdepasse-carol-1"


def _raw_cmd(fx, command: str, parameters: dict) -> dict:
    payload = (
        json.dumps(
            {
                "api_version": "v2",
                "command": command,
                "parameters": parameters,
            }
        )
        + "\n"
    )
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


def _read_params(username: str, password: str, message_id: str) -> dict:
    return {
        "my_name_auth": username,
        "my_password_auth": password,
        "message_id": message_id,
    }


def _count_write_transactions(fx, fn):
    """Runs fn() with db.begin_immediate counting invocations."""
    real_begin = db.begin_immediate
    counter = {"n": 0}

    def counting_begin(conn):
        counter["n"] += 1
        return real_begin(conn)

    db.begin_immediate = counting_begin
    try:
        result = fn()
    finally:
        db.begin_immediate = real_begin
    return counter["n"], result


def test_read_message_sender_read_does_not_write(fx):
    """The sender reading its own (unread) message writes nothing: the
    only write transaction is the mandatory audit (F11)."""
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "lu par l'émetteur", "cmid-guard-s-1")
    # warm the auth cache so the count below isolates the read path
    _raw_cmd(fx, "read_message", _read_params(ALICE, ALICE_PASSWORD, m["message_id"]))

    n, resp = _count_write_transactions(
        fx,
        lambda: _raw_cmd(fx, "read_message", _read_params(ALICE, ALICE_PASSWORD, m["message_id"])),
    )
    assert resp["success"] is True
    assert n == 1, (
        f"sender read_message opened {n} write transactions; expected exactly 1 "
        "(the mandatory audit). The handler must not take the writer lock for a "
        "pure read."
    )
    # and the read stays unmarked for the recipient
    assert resp["data"]["status"] == "unread"
    assert resp["data"]["read_at"] is None


def test_read_message_already_read_does_not_write(fx):
    """Re-reading an already-read message writes nothing: only the audit."""
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "déjà lu", "cmid-guard-r-1")
    first = _raw_cmd(fx, "read_message", _read_params(BOB, BOB_PASSWORD, m["message_id"]))
    assert first["data"]["read_at"] is not None

    n, resp = _count_write_transactions(
        fx,
        lambda: _raw_cmd(fx, "read_message", _read_params(BOB, BOB_PASSWORD, m["message_id"])),
    )
    assert resp["success"] is True
    assert n == 1, (
        f"already-read read_message opened {n} write transactions; expected "
        "exactly 1 (the mandatory audit). mark_read_conditional is a no-op here "
        "and must not run under the writer lock."
    )
    assert resp["data"]["read_at"] == first["data"]["read_at"]  # first-read date stable


def test_read_message_first_read_writes_exactly_once(fx):
    """The recipient's first read is the only case that marks read_at:
    exactly the mark transaction + the audit."""
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "première lecture", "cmid-guard-f-1")

    n, resp = _count_write_transactions(
        fx,
        lambda: _raw_cmd(fx, "read_message", _read_params(BOB, BOB_PASSWORD, m["message_id"])),
    )
    assert resp["success"] is True
    assert n == 2, (
        f"first-read read_message opened {n} write transactions; expected "
        "exactly 2 (read_at mark + audit)."
    )
    assert resp["data"]["status"] == "read"
    assert resp["data"]["read_at"] is not None


def test_read_message_unknown_id_still_404(fx):
    """Behavioral guard: the lock-free path still hides unknown messages
    (and a refused read opens no write transaction at all)."""
    n, resp = _count_write_transactions(
        fx,
        lambda: _raw_cmd(
            fx,
            "read_message",
            _read_params(BOB, BOB_PASSWORD, "00000000-0000-4000-8000-000000000000"),
        ),
    )
    assert resp["success"] is False
    assert resp["error"]["code"] == "MESSAGE_NOT_FOUND"
    assert n == 0, (
        f"refused read_message opened {n} write transactions; expected 0 "
        "(no handler write, no audit for a failed command)."
    )


def test_read_message_not_participant_still_404(fx):
    """Behavioral guard: inaccessible messages stay indistinguishable
    from nonexistent ones (non-disclosure)."""
    fx.client.create_agent(CAROL, CAROL_PASSWORD, "Agent de test", ORG_NAME, ORG_PASSWORD)
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "confidentiel", "cmid-guard-c-1")
    _raw_cmd(fx, "read_message", _read_params(BOB, BOB_PASSWORD, m["message_id"]))

    n, resp = _count_write_transactions(
        fx,
        lambda: _raw_cmd(fx, "read_message", _read_params(CAROL, CAROL_PASSWORD, m["message_id"])),
    )
    assert resp["success"] is False
    assert resp["error"]["code"] == "MESSAGE_NOT_FOUND"
    assert n == 0, (
        f"inaccessible read_message opened {n} write transactions; expected 0."
    )


# The concurrent-first-read semantics (all readers observe the same
# first read_at) are guarded by tests/test_messages.py
# (test_read_message_concurrent_readers_same_date) and
# tests/test_concurrency.py (test_concurrent_reads_same_first_read_date).
