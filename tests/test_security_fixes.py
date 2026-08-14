"""Regression tests for the flaws found by the independent security
audit: notification pagination (M1), restore lock
(M2), temporary files/EXDEV (M3), deep JSON (M4), size limit
(F2), stale lock (F5), connection limit (E1), backup header
(F4)."""

from __future__ import annotations

import json
import os
import socket

import pytest

from synapse.backup import BackupError, backup, restore
from synapse.config import Config
from synapse.errors import INVALID_ARGUMENT

from .conftest import (
    ORG_NAME,
    ORG_PASSWORD,
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    make_server,
)

INTERNAL_ERROR = "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# M1 — get_notifications: no needs_reply conversation lost during
# pagination, even with unread conversations interleaved
# ---------------------------------------------------------------------------


def test_notifications_pagination_no_loss_with_unread_interleaved(fx):
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Agent de test",  ORG_NAME, ORG_PASSWORD)
    fx.client.create_agent("dave",  "motdepasse-dave-1", "Agent de test",  ORG_NAME, ORG_PASSWORD)
    # carol -> bob: read; dave -> bob: read; then alice -> bob: UNREAD and
    # more recent (it ranks before carol/dave in the page order and
    # would consume an SQL slot in the old implementation)
    m_carol = fx.client.send_message(BOB, "lu carol", "cmid-fix-m1b", "carol", "motdepasse-carol-1")
    m_dave = fx.client.send_message(BOB, "lu dave", "cmid-fix-m1c", "dave", "motdepasse-dave-1")
    m_alice = fx.send(ALICE, ALICE_PASSWORD, BOB, "non lu", "cmid-fix-m1a")
    fx.client.read_message(m_carol["message_id"], BOB, BOB_PASSWORD)
    fx.client.read_message(m_dave["message_id"], BOB, BOB_PASSWORD)
    assert m_alice["created_at"] >= m_carol["created_at"]  # alice is the most recent

    seen = []
    cursor = None
    while True:
        page = fx.client.get_notifications(BOB, BOB_PASSWORD, limit=1, cursor=cursor)
        seen.extend(item["other_username"] for item in page["needs_reply"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    # carol AND dave require a reply: neither must be lost
    assert set(seen) == {"carol", "dave"}
    assert len(seen) == 2


# ---------------------------------------------------------------------------
# M4 — deep JSON: INVALID_ARGUMENT, not INTERNAL_ERROR
# ---------------------------------------------------------------------------


def test_deeply_nested_json_returns_invalid_argument(fx, raw_socket_client):
    deep = "[" * 20000 + "]" * 20000
    line = json.dumps({"api_version": "v2", "command": "get_notifications", "parameters": {}}) + "\n"
    # valid envelope but extreme nesting in parameters
    resp = raw_socket_client(
        '{"api_version":"v1","command":"get_notifications","parameters":' + deep + "}\n"
    )
    assert resp["error"]["code"] == INVALID_ARGUMENT
    assert resp["error"]["code"] != INTERNAL_ERROR
    _ = line


# ---------------------------------------------------------------------------
# M2 — restore: lock held during the operation, then released
# ---------------------------------------------------------------------------


def test_restore_releases_lock_after_success(fx, config):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-fix-m2")
    path = backup(config)
    fx.server.stop()
    restore(config, path)
    # the restore lock was released: a server can start
    server2 = make_server(config, org=False)
    try:
        assert server2.client.get_messages(ALICE, ALICE_PASSWORD) is not None
    finally:
        server2.stop()


def test_restore_refuses_when_lock_present(fx, config):
    path = backup(config)
    # the server is running: lock present -> refused
    with pytest.raises(BackupError):
        restore(config, path)


def test_restore_allowed_with_stale_lock(config):
    """A lock left by a dead process (crash) does not prevent
    restore: it is removed automatically."""
    from .conftest import make_server
    server = make_server(config, org=True)
    try:
        server.client.create_agent(ALICE,  ALICE_PASSWORD, "Agent de test",  ORG_NAME, ORG_PASSWORD)
        server.client.create_agent(BOB,  BOB_PASSWORD, "Agent de test",  ORG_NAME, ORG_PASSWORD)
        server.client.send_message(BOB, "contenu", "cmid-fix-lk-1", ALICE, ALICE_PASSWORD)
        path = backup(config)
    finally:
        server.stop()
    # simulate a crash: the lock remains with a dead PID
    with open(config.lock_path, "w") as fh:
        fh.write("99999999")
    restore(config, path)  # must not raise
    assert not os.path.exists(config.lock_path)  # lock released after restore


# ---------------------------------------------------------------------------
# M3 — temporary files in storage_dir (no EXDEV, no /tmp)
# ---------------------------------------------------------------------------


def test_backup_and_restore_use_storage_dir_for_temps(fx, config):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-fix-m3")
    path = backup(config)
    fx.server.stop()
    restore(config, path)
    # no temporary leftovers outside storage; storage contains only
    # the expected files
    entries = os.listdir(config.storage_dir)
    assert "synapse.db" in entries
    for name in entries:
        assert not name.startswith("synapse-bak-")
        assert not name.startswith("synapse-res-")


# ---------------------------------------------------------------------------
# F4 — invalid backup header: clean BackupError
# ---------------------------------------------------------------------------


def test_restore_header_without_cursor_key_rejected(fx, config):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from synapse.backup import _MAGIC, _NONCE_LENGTH
    from synapse.security import load_or_create_key

    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-fix-f4")
    fx.server.stop()
    key = load_or_create_key(config.backup_key_path)
    # header without cursor_key, encrypted with the right key
    header = json.dumps({"format": 1, "created_at": "20260101-000000"}).encode()
    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, header + b"\n" + b"faux sqlite", None)
    bad_path = os.path.join(config.backup_dir, "bad-header.synbk")
    os.makedirs(config.backup_dir, exist_ok=True)
    with open(bad_path, "wb") as fh:
        fh.write(_MAGIC + nonce + ciphertext)
    with pytest.raises(BackupError):
        restore(config, bad_path)


# ---------------------------------------------------------------------------
# F2 — exact size limit: a line of max+1 bytes is rejected
# ---------------------------------------------------------------------------


def test_line_exactly_over_limit_rejected(fx, config, raw_socket_client):
    max_bytes = config.max_request_bytes
    payload = b"A" * (max_bytes + 1) + b"\n"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(fx.config.socket_path)
        sock.sendall(payload)
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
# F5 — stale lock (dead PID): the server can start
# ---------------------------------------------------------------------------


def test_stale_lock_with_dead_pid_is_recovered(config):
    config = Config.from_dict(
        {
            "storage_dir": str(config.storage_dir) + "-stale",
            "socket_path": str(config.socket_path) + "-stale",
            "log_dir": str(config.log_dir) + "-stale",
            "backup_dir": str(config.backup_dir) + "-stale",
        }
    )
    os.makedirs(config.storage_dir, exist_ok=True)
    lock = config.lock_path
    with open(lock, "w") as fh:
        fh.write("99999999")  # PID almost certainly dead
    server = make_server(config, org=True)
    try:
        assert server.client.create_agent(ALICE,  ALICE_PASSWORD, "Agent de test",  ORG_NAME, ORG_PASSWORD)
    finally:
        server.stop()


def test_active_lock_blocks_server(fx, config):
    """A live server's lock prevents a second startup."""
    from synapse.server import SynapseServer

    second = SynapseServer(config)
    with pytest.raises(SystemExit) as exc:
        second.start()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# E1 — connection limit: the service stays available under a burst
# of idle connections
# ---------------------------------------------------------------------------


def test_server_survives_connection_flood(fx):
    idle = []
    try:
        for _ in range(40):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(fx.config.socket_path)  # deliberately idle connection
            idle.append(s)
        # the service remains reachable and functional
        data = fx.client.get_messages(ALICE, ALICE_PASSWORD)
        assert data == {"messages": [], "next_cursor": None}
    finally:
        for s in idle:
            try:
                s.close()
            except OSError:
                pass


def test_duplicate_json_keys_rejected(fx, raw_socket_client):
    """Duplicate keys in a JSON request: rejected with INVALID_ARGUMENT.

    The object_pairs_hook of parse_json_request forbids two values
    for the same parameter (anti-smuggling: no confusion possible
    between the validated value and an overwritten one). The request is
    built as raw text: a Python dict cannot carry two identical keys."""
    raw = ('{"api_version": "v2", "command": "get_notifications", '
           '"parameters": {"my_name_auth": "alice", '
           '"my_name_auth": "bob", '
           '"my_password_auth": "xxxxxxxxxxxx", "limit": 50, "cursor": null}}')
    resp = raw_socket_client(raw + "\n")
    assert resp["error"]["code"] == "INVALID_ARGUMENT"
    # an identical request without duplicates stays valid (no false positive)
    resp_ok = raw_socket_client(
        json.dumps({
            "api_version": "v2",
            "command": "get_notifications",
            "parameters": {
                "my_name_auth": "alice",
                "my_password_auth": "motdepasse-alice-1",
                "limit": 50,
                "cursor": None,
            },
        }) + "\n"
    )
    assert resp_ok["success"] is True
