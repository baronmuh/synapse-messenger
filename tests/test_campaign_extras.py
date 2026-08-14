"""Exhaustive campaign — targeted tests on the last uncovered branches:
backup robustness, server internals, interactive installation, CLI,
transactions, idempotency races."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import stat
import sys

import pytest

from synapse import db
from synapse.backup import BackupError, backup, restore
from synapse.config import Config
from synapse.errors import INVALID_ARGUMENT, MESSAGE_ALREADY_EXISTS, ApiError
from synapse.validation import _validate_limit, validate_envelope

from .conftest import (
    ORG_NAME,
    ORG_PASSWORD,
    ALICE,
    ALICE_PASSWORD,
    BOB,
    make_server,
)


# ---------------------------------------------------------------------------
# Backup: robustness and error paths
# ---------------------------------------------------------------------------


def test_backup_wraps_sqlite_errors(config, monkeypatch):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("base corrompue")

    monkeypatch.setattr("synapse.backup.sqlite3.connect", boom)
    with pytest.raises(BackupError):
        backup(config)


def test_restore_wraps_os_errors(fx, config, monkeypatch):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "x", "cmid-ce-1")
    fx.server.stop()
    path = backup(config)

    def boom(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr("synapse.backup.os.open", boom)
    with pytest.raises(BackupError):
        restore(config, path)


def _craft_backup(config, header: dict, db_bytes: bytes = b"db") -> str:
    """Builds a hand-crafted encrypted backup with the correct key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from synapse.backup import _MAGIC, _NONCE_LENGTH
    from synapse.security import load_or_create_key

    key = load_or_create_key(config.backup_key_path)
    nonce = os.urandom(_NONCE_LENGTH)
    plaintext = json.dumps(header).encode() + b"\n" + db_bytes
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    os.makedirs(config.backup_dir, exist_ok=True)
    path = os.path.join(config.backup_dir, f"crafted-{os.urandom(4).hex()}.synbk")
    with open(path, "wb") as fh:
        fh.write(_MAGIC + nonce + ct)
    return path


def _good_header():
    return {"format": 1, "cursor_key": __import__("base64").b64encode(b"k" * 32).decode()}


def test_restore_header_without_separator(fx, config):
    fx.server.stop()
    from synapse.backup import _MAGIC, _NONCE_LENGTH
    from synapse.security import load_or_create_key
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = load_or_create_key(config.backup_key_path)
    nonce = os.urandom(_NONCE_LENGTH)
    ct = AESGCM(key).encrypt(nonce, b'{"format":1}', None)  # no \n separator
    path = os.path.join(config.backup_dir, "no-sep.synbk")
    os.makedirs(config.backup_dir, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_MAGIC + nonce + ct)
    with pytest.raises(BackupError):
        restore(config, path)


def test_restore_header_not_json(fx, config):
    fx.server.stop()
    path = _craft_backup(config, {})
    # replace the header with non-JSON via a hand-crafted backup
    from synapse.backup import _MAGIC, _NONCE_LENGTH
    from synapse.security import load_or_create_key
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = load_or_create_key(config.backup_key_path)
    nonce = os.urandom(_NONCE_LENGTH)
    ct = AESGCM(key).encrypt(nonce, b"{pas du json}\n" + b"db", None)
    with open(path, "wb") as fh:
        fh.write(_MAGIC + nonce + ct)
    with pytest.raises(BackupError):
        restore(config, path)


def test_restore_bad_cursor_key_b64(fx, config):
    fx.server.stop()
    path = _craft_backup(config, {"format": 1, "cursor_key": "!!!pas-b64!!!"})
    with pytest.raises(BackupError):
        restore(config, path)


def test_restore_corrupt_db_bytes(fx, config):
    fx.server.stop()
    path = _craft_backup(config, _good_header(), db_bytes=os.urandom(4096))
    with pytest.raises(BackupError):
        restore(config, path)


def _config_file(tmp_path, config: Config) -> str:
    """Writes the configuration to a JSON file (for console entry points
    that load via --config)."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.to_dict()))
    return str(path)


def test_backup_main_success(fx, config, tmp_path, monkeypatch, capsys):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "x", "cmid-ce-bm")
    from synapse.backup import backup_main
    monkeypatch.setattr(sys, "argv", ["synapse-backup", "--config", _config_file(tmp_path, config)])
    backup_main()
    out = capsys.readouterr().out.strip()
    assert out.endswith(".synbk")
    assert os.path.exists(out)


def test_restore_main_success(fx, config, tmp_path, monkeypatch, capsys):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "x", "cmid-ce-rm")
    path = backup(config)
    fx.server.stop()
    from synapse.backup import restore_main
    monkeypatch.setattr(
        sys, "argv",
        ["synapse-restore", path, "--config", _config_file(tmp_path, config), "--force"],
    )
    restore_main()
    assert "Restore complete." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Server: framing, internal error, connection bound, locks
# ---------------------------------------------------------------------------


def test_server_empty_line_rejected(fx, raw_socket_client):
    resp = raw_socket_client("\n")
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_server_partial_line_at_eof(fx, raw_socket_client):
    """A trailing line without a newline is still processed (incomplete JSON -> error)."""
    import json as json_mod
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(fx.config.socket_path)
        sock.sendall(b'{"api_version": "v2"')  # no newline and no terminator
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    resp = json_mod.loads(b"".join(chunks))
    assert resp["error"]["code"] == INVALID_ARGUMENT


def test_server_internal_error_returns_envelope_and_logs(fx, config, monkeypatch, raw_socket_client):
    """An unexpected exception produces INTERNAL_ERROR plus an error log entry."""
    from synapse import service as service_mod

    def boom(self, raw):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(service_mod.Service, "process", boom)
    resp = raw_socket_client(
        json.dumps({"api_version": "v2", "command": "get_notifications",
                    "parameters": {"my_name_auth": ALICE, "my_password_auth": ALICE_PASSWORD,
                                   "limit": 50, "cursor": None}}) + "\n"
    )
    assert resp["success"] is False
    assert resp["error"]["code"] == "INTERNAL_ERROR"
    assert resp["data"] is None
    error_log = os.path.join(config.log_dir, "synapse.error.log")
    assert os.path.exists(error_log)
    assert "RuntimeError" in open(error_log, encoding="utf-8").read()


def test_server_connection_bound_rejects_excess(config, monkeypatch):
    """Beyond the connection bound, excess connections are closed; once
    slots are freed, the service responds again."""
    import time

    import synapse.server as server_mod

    monkeypatch.setattr(server_mod, "MAX_CONCURRENT_CONNECTIONS", 2)
    srv = make_server(config, org=True)
    opened = []
    try:
        for _ in range(3):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(config.socket_path)
            opened.append(s)
        # the 3rd excess connection is closed by the server
        deadline = time.time() + 5
        while time.time() < deadline:
            if opened[2].recv(1024) == b"":
                break
            time.sleep(0.05)
        else:
            pytest.fail("the excess connection was not closed")
        # free the two inactive connections: the service responds
        for s in opened[:2]:
            s.close()
        srv.client.create_agent(ALICE, ALICE_PASSWORD, "Agent", ORG_NAME, ORG_PASSWORD)
        assert srv.client.get_messages(ALICE, ALICE_PASSWORD) is not None
    finally:
        for s in opened:
            try:
                s.close()
            except OSError:
                pass
        srv.stop()


def test_lock_is_stale_permission_error(tmp_path, monkeypatch):
    from synapse.server import lock_is_stale
    lock = tmp_path / "l.lock"
    lock.write_text(str(os.getpid()))

    def denied(*args, **kwargs):
        raise PermissionError()

    monkeypatch.setattr(os, "kill", denied)
    assert lock_is_stale(lock) is False  # cautious: existing process not visible


def test_server_start_refuses_when_storage_is_file(config, tmp_path, monkeypatch):
    from synapse.server import SynapseServer
    blocker = tmp_path / "pas-un-repertoire"
    blocker.write_text("x")
    bad = Config.from_dict({
        "storage_dir": str(blocker),
        "socket_path": str(tmp_path / "s.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "bk"),
    })
    with pytest.raises(SystemExit) as exc:
        SynapseServer(bad).start()
    assert exc.value.code == 1


def test_server_stop_idempotent_with_missing_files(fx, config):
    """stop() does not raise if the socket or lock are already gone."""
    # remove the files before shutdown
    try:
        os.unlink(config.socket_path)
    except FileNotFoundError:
        pass
    try:
        os.unlink(config.lock_path)
    except FileNotFoundError:
        pass
    fx.server.stop()  # must not raise
    fx.server.stop()  # second call: idempotent


# ---------------------------------------------------------------------------
# Interactive installation
# ---------------------------------------------------------------------------


def test_org_init_main_interactive_success(config, tmp_path, monkeypatch, capsys):
    from synapse.install import org_init_main
    monkeypatch.setattr(
        sys, "argv", ["synapse-init-org", "--config", _config_file(tmp_path, config)]
    )
    inputs = iter(["BossAdmin", "motdepasse-admin-1", "motdepasse-admin-1"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("synapse.install.getpass.getpass", lambda prompt="": next(inputs))
    org_init_main()
    assert "bossadmin" in capsys.readouterr().out  # name normalized to lowercase
    # the organization exists with its default policies (closed)
    with db.connect(config) as conn:
        row = conn.execute(
            "SELECT organization_name, allow_incoming_external, allow_outgoing_external "
            "FROM organizations WHERE organization_name='bossadmin'"
        ).fetchone()
        assert tuple(row) == ("bossadmin", 0, 0)


def test_org_init_main_confirm_mismatch(config, tmp_path, monkeypatch, capsys):
    from synapse.install import org_init_main
    monkeypatch.setattr(
        sys, "argv", ["synapse-init-org", "--config", _config_file(tmp_path, config)]
    )
    inputs = iter(["BossAdmin", "motdepasse-admin-1", "different-123"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("synapse.install.getpass.getpass", lambda prompt="": next(inputs))
    with pytest.raises(SystemExit) as exc:
        org_init_main()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Validation: required parameter null, wrong-type limit
# ---------------------------------------------------------------------------


def test_required_parameter_null_rejected():
    """A required parameter present but null: INVALID_ARGUMENT."""
    with pytest.raises(ApiError) as exc:
        validate_envelope({
            "api_version": "v2",
            "command": "get_messages",
            "parameters": {
                "my_name_auth": None,
                "my_password_auth": "x" * 12,
                "status": None, "sender_username": None,
                "conversation_id": None, "limit": 50, "cursor": None,
            },
        })
    assert exc.value.code == INVALID_ARGUMENT


def test_validate_limit_wrong_type_direct():
    with pytest.raises(ApiError) as exc:
        _validate_limit("50")
    assert exc.value.code == INVALID_ARGUMENT
    with pytest.raises(ApiError) as exc:
        _validate_limit(True)
    assert exc.value.code == INVALID_ARGUMENT
    assert _validate_limit(None) == 50  # default
    assert _validate_limit(7) == 7


# ---------------------------------------------------------------------------
# Transactions: swallowed ROLLBACK failure (clean exit)
# ---------------------------------------------------------------------------


def test_begin_immediate_swallows_rollback_failure(config):
    """If ROLLBACK fails (closed connection), the transaction context
    manager exits cleanly by re-raising the original error."""
    conn = db.connect(config)
    with pytest.raises(RuntimeError):
        with db.begin_immediate(conn):
            conn.close()  # rend le ROLLBACK impossible
            raise RuntimeError("boom")
    # the closed connection does not prevent the context manager from exiting


def test_begin_read_swallows_rollback_failure(config):
    conn = db.connect(config)
    with pytest.raises(RuntimeError):
        with db.begin_read(conn):
            conn.close()
            raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Security: key-creation race (FileExistsError branch)
# ---------------------------------------------------------------------------


def test_load_or_create_key_race_branch(tmp_path, monkeypatch):
    """The "race" branch: the file appears between the existence check
    and the open — the second creator reuses the first one's key."""
    from synapse import security
    path = str(tmp_path / "race2.bin")
    real_open = os.open

    def racing_open(file, flags, mode=0o777):
        if not os.path.exists(file):
            # the other process creates the key right before us
            fd = real_open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, b"k" * 32)
            os.close(fd)
        raise FileExistsError()

    monkeypatch.setattr(os, "open", racing_open)
    key = security.load_or_create_key(path)
    assert key == b"k" * 32


# ---------------------------------------------------------------------------
# Service: idempotency IntegrityError branch (send race)
# ---------------------------------------------------------------------------


def test_send_idempotency_integrity_error_branch(fx, monkeypatch):
    """If the INSERT raises IntegrityError (race), the service re-reads the
    existing message and applies the idempotency rule."""
    from synapse.store import messages as messages_mod
    first = fx.send(ALICE, ALICE_PASSWORD, BOB, "contenu", "cmid-ce-idem")
    real_find = messages_mod.find_message_by_client_id
    calls = {"inserts": 0, "finds": 0}

    def racy_insert(conn, **kwargs):
        calls["inserts"] += 1
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    def blind_find(conn, sender, client_message_id):
        # the "race": the first check of each send does not see the
        # existing message; the re-read (2nd call) sees the truth
        calls["finds"] += 1
        if calls["finds"] % 2 == 1:
            return None
        return real_find(conn, sender, client_message_id)

    monkeypatch.setattr(messages_mod, "insert_message", racy_insert)
    monkeypatch.setattr(messages_mod, "find_message_by_client_id", blind_find)
    # same key + same content: the existing message is returned
    second = fx.send(ALICE, ALICE_PASSWORD, BOB, "contenu", "cmid-ce-idem")
    assert second["message_id"] == first["message_id"]
    assert calls["inserts"] >= 1  # the IntegrityError branch was reached
    # same key + different content: MESSAGE_ALREADY_EXISTS (via the client)
    from synapse.client import ApiClientError
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message(BOB, "autre contenu", "cmid-ce-idem", ALICE, ALICE_PASSWORD)
    assert exc.value.code == MESSAGE_ALREADY_EXISTS


# ---------------------------------------------------------------------------
# Cursor: valid signature over non-JSON body
# ---------------------------------------------------------------------------


def test_cursor_valid_signature_non_json_body():
    from synapse.cursor import _b64url, decode_cursor
    import hashlib
    import hmac
    body = _b64url(b"pas du json")
    sig = _b64url(hmac.new(b"k" * 32, body.encode(), hashlib.sha256).digest())
    with pytest.raises(ApiError) as exc:
        decode_cursor(b"k" * 32, f"{body}.{sig}")
    assert exc.value.code == INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# Permissions of files produced by the backup
# ---------------------------------------------------------------------------


def test_backup_file_permissions(fx, config):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "x", "cmid-ce-perm")
    path = backup(config)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
